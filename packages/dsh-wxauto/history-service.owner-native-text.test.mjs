import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHistoryService} from './history-service.mjs';
import {createNativeOwnerTextInbox,ownerTextScopeDigest} from './native-owner-text-inbox.mjs';
import {createNativeOwnerTextProcessor} from './native-owner-text-processor.mjs';

function fixture(t,{client}={}){
  const root=path.dirname(fileURLToPath(import.meta.url)),dataRoot=fs.mkdtempSync(path.resolve('artifacts/owner-text-history-'));
  const config={dataRoot,target:'owner',accountId:'synthetic-owner-account',chatKeys:{owner:'existing-canonical-owner-key',group:'group-key'},
    nativeVoiceSources:{ownerNativeChatKey:'wechat:synthetic_owner'},python:(process.env.WHALE_TEST_PYTHON||path.resolve(root,'../../.venv-wxauto/Scripts/python.exe'))};
  const routes=new Map([['owner',{target:'owner',kind:'friend'}],['group',{target:'group',kind:'group'}]]);
  const reports=[],service=createHistoryService({config,root,routes,routeSessionId:r=>r.kind==='friend'?'owner-session':'group-session',
    currentSessionId:()=> 'owner-session',report:(...args)=>reports.push(args),client});
  t.after(()=>service.close());
  const event={id:'native-owner-text:'+'1'.repeat(32),target:'owner',kind:'friend',sender:'Synthetic owner',sourceKind:'wechat-native-text',
    nativeAccountId:config.accountId,nativeChatKey:'wechat:synthetic_owner',nativeSenderId:'synthetic_owner',nativeId:'wechat-server:987654321',
    nativeOwnerTextClaimId:'1'.repeat(32),authorityEpoch:'2'.repeat(32),nativeSnapshotId:'synthetic-authenticated-snapshot',
    timestamp:1700000000000,direction:'incoming',mediaType:'text',text:'synthetic exact owner native text',nativeSourceText:'synthetic exact owner native text',
    rawText:'synthetic exact owner native text',images:[],files:[]};
  return {service,config,event,reports,dataRoot};
}

test('real history journal/sole worker preserves native owner ID/source and canonical chat, dedup and current source',async t=>{
  const f=fixture(t),canonical=await f.service.chat('owner');
  const saved=await f.service.incoming(f.event);assert.ok(saved?.id);
  const repeated=await f.service.incoming({...f.event,id:'native-owner-text:alternate-journal-source'});assert.equal(saved.id,repeated.id);
  const rows=(await f.service.query('owner-session',{query:'synthetic exact owner native text'})).messages;
  assert.equal(rows.length,1);const row=rows[0];
  assert.equal(row.chat_id,canonical.id);assert.equal(row.source,'wechat-native-text');assert.equal(row.native_id,f.event.nativeId);
  assert.equal(row.sender_id,f.event.nativeSenderId);assert.equal(row.timestamp,f.event.timestamp);assert.equal(row.text,f.event.rawText);
  assert.deepEqual(row.metadata,{routeKind:'friend',mediaError:null,nativeOwnerTextClaimId:f.event.nativeOwnerTextClaimId,
    authorityEpoch:f.event.authorityEpoch,nativeAccountId:f.config.accountId,nativeSnapshotId:f.event.nativeSnapshotId,nativeChatKey:f.event.nativeChatKey});
  const token=await f.service.beginInput('owner-session','owner',[f.event]);
  assert.deepEqual(f.service.currentSource('owner-session'),{chatId:canonical.id,messageIds:[saved.id],sourceKind:'wechat-native-text'});
  assert.equal(f.service.currentSource('group-session'),null);f.service.endInput('owner-session',token);
  assert.equal(f.service.currentSource('owner-session'),null);
  assert.equal((await f.service.query('group-session',{query:'synthetic exact owner native text'})).messages.length,0);
  await assert.rejects(()=>f.service.query('group-session',{chat:'owner',query:''}),/history-chat-denied/);
  assert.deepEqual(fs.readdirSync(path.join(f.dataRoot,'snapshots/realtime-inbox')),[]);
});

test('spoofed owner text is refused before journal and before establishing any source',async t=>{
  const writes=[],f=fixture(t,{client:{request:async(op,args)=>{writes.push({op,args});return {id:'unexpected'};},close(){}}});
  const invalid=[{nativeAccountId:'other'},{nativeChatKey:'wechat:other'},{nativeSenderId:'other'},
    {direction:'outgoing'},{direction:'self'},{direction:'unknown'},{direction:undefined},
    {target:'group',kind:'group'},{mediaType:'voice'},{images:[{path:'secret.png'}]},{files:[{path:'secret.txt'}]},
    {audio:[{}]},{nativeId:'wechat-server:0'},{nativeOwnerTextClaimId:'x'},{authorityEpoch:'x'},
    {nativeSnapshotId:''},{timestamp:NaN},{nativeSourceText:'different'},{rawText:'different'},{rawText:undefined}];
  for(const [i,change] of invalid.entries()){
    const event={...f.event,...change,id:'invalid-'+i};assert.equal(await f.service.incoming(event),null);
    await assert.rejects(()=>f.service.beginInput('owner-session',event.target,[event]),/native-owner-text-event-invalid/);
  }
  assert.equal(writes.length,0);assert.equal(fs.existsSync(path.join(f.dataRoot,'snapshots/realtime-inbox')),false);
  assert.equal(f.service.currentSource('owner-session'),null);
});

test('staged real inbox to processor to sole Vault journal to active memory source is exact and restart-safe',async t=>{
  const f=fixture(t),identity={accountId:f.config.accountId,ownerNativeChatKey:f.event.nativeChatKey,
    expectedPeerUsername:f.event.nativeSenderId,ownerTarget:f.config.target};
  const inboxConfig={...identity,dataRoot:f.dataRoot,now:()=>1700000001000};
  const inbox=createNativeOwnerTextInbox(inboxConfig);
  const full=messages=>({source:'authenticated-native-owner-text-catalog',accountId:identity.accountId,
    snapshotId:'synthetic-complete-owner-snapshot',nativeChatKey:identity.ownerNativeChatKey,complete:true,
    scopeDigest:ownerTextScopeDigest(identity.accountId,identity.ownerNativeChatKey,identity.expectedPeerUsername),messages});
  // Explicit isolated authority fixture. This does not initialize or switch a
  // production channel, admit a social command, or invoke a Harness model.
  inbox.initialize(full([]),{cutoverAtMs:1700000000000});
  const source={nativeId:f.event.nativeId,serverId:f.event.nativeId.slice('wechat-server:'.length),
    nativeChatKey:identity.ownerNativeChatKey,senderId:identity.expectedPeerUsername,senderDisplayName:'Synthetic owner',
    rawText:f.event.rawText,timestampSeconds:1700000000,localId:1,snapshotSourceId:'synthetic-db/owner/1',direction:'incoming'};
  const activeAuthority=()=>({active:true,authorityEpoch:inbox.status().authorityEpoch,accountId:identity.accountId,
    ownerNativeChatKey:identity.ownerNativeChatKey});
  const events=[],options={...identity,inbox,history:f.service,readCatalog:async()=>full([source]),activeAuthority,
    routeForOwner:()=>({kind:'friend',target:'owner',sessionId:'owner-session'}),
    enqueue:async event=>{
      const rows=(await f.service.query('owner-session',{query:source.rawText})).messages;
      assert.equal(rows.length,1);assert.equal(rows[0].native_id,source.nativeId); // persisted BEFORE buffering
      events.push(event);
    }};
  const processor=createNativeOwnerTextProcessor(options);t.after(()=>processor.close());
  assert.equal((await processor.refresh()).buffered,1);assert.equal(events.length,1);
  const [event]=events;assert.equal(event.nativeOwnerTextClaimId,inbox.pending()[0].claimId);
  assert.equal(event.direction,'incoming');assert.equal(event.text,source.rawText);
  assert.equal(inbox.status().submitted,0);assert.deepEqual(processor.markSubmitting(events),{submitted:1});
  const token=await f.service.beginInput('owner-session','owner',events),current=f.service.currentSource('owner-session');
  const saved=(await f.service.query('owner-session',{query:source.rawText})).messages[0];
  assert.equal(current.sourceKind,'wechat-native-text');assert.deepEqual(current.messageIds,[saved.id]);
  assert.equal(current.chatId,(await f.service.chat('owner')).id);f.service.endInput('owner-session',token);
  processor.close();
  const restarted=createNativeOwnerTextProcessor({...options,inbox:createNativeOwnerTextInbox(inboxConfig)});
  t.after(()=>restarted.close());assert.equal((await restarted.refresh()).buffered,0);assert.equal(events.length,1);
  assert.equal((await f.service.query('group-session',{query:source.rawText})).messages.length,0);
});

test('settle barrier uses real sole Vault and reports active input instead of false quiet',async t=>{
  const f=fixture(t);const saved=await f.service.incoming(f.event);assert.ok(saved?.id);
  assert.deepEqual(await f.service.settleIncoming(),{incomingPending:0,outgoingPending:0,evidencePending:0,activeInputs:0,contextPending:0});
  const token=await f.service.beginInput('owner-session','owner',[f.event]);
  assert.equal((await f.service.settleIncoming()).activeInputs,1);
  f.service.endInput('owner-session',token);assert.equal(f.service.cutoverStatus().activeInputs,0);
});

test('settled failures remain counted in all actual journal folders without replaying or reading bodies',async t=>{
  const operations=[],f=fixture(t,{client:{request:async(op,args)=>{
    operations.push(op);if(op==='ensure_chat')return {id:'synthetic-canonical'};
    if(op==='status')return {ok:true};throw Error('synthetic write unavailable');
  },close(){}}});
  assert.equal(await f.service.incoming(f.event),null);
  await f.service.outgoing('owner',{kind:'text',text:'synthetic failed outgoing'},'synthetic-outgoing');
  const image=path.join(f.dataRoot,'media/synthetic.png');fs.mkdirSync(path.dirname(image),{recursive:true});fs.writeFileSync(image,'fixture');
  await f.service.evidence({id:'synthetic-evidence',target:'owner',kind:'friend',images:[{path:image,evidence:{status:'fixture'}}]});
  const counts=await f.service.settleIncoming();
  assert.deepEqual(counts,{incomingPending:1,outgoingPending:1,evidencePending:1,activeInputs:0,contextPending:0});
  assert.equal(operations.at(-1),'status');
  assert.equal(JSON.stringify(counts).includes('synthetic'),false);
  const folder=path.join(f.dataRoot,'snapshots/realtime-inbox');fs.writeFileSync(path.join(folder,'00000000-0000-0000-0000-000000000000.tmp'),'partial');
  assert.equal(f.service.cutoverStatus().incomingPending,2);
  fs.writeFileSync(path.join(folder,'unexpected'),'unverified');assert.throws(()=>f.service.cutoverStatus(),/journal-unverified/);
});
