import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createNativeVoiceInbox} from './native-voice-inbox.mjs';

test('strict all-queued submit rejects partial reuse atomically while default remains idempotent',t=>{
  const f=fixture(t);f.inbox.initialize(f.catalog([]),{nativeChatKeys:['wechat:room@chatroom']});
  f.inbox.enqueue(f.catalog(['1','2']));const [first,second]=f.inbox.pending();f.inbox.submit([first.claimId]);
  const before=fs.readFileSync(f.file,'utf8'),other=createNativeVoiceInbox(f.config);
  assert.throws(()=>other.submit([second.claimId,first.claimId],{requireAllQueued:true}),/claim-already-submitted/);
  assert.equal(fs.readFileSync(f.file,'utf8'),before);assert.deepEqual(other.pending().map(c=>c.claimId),[second.claimId]);
  assert.deepEqual(other.submit([first.claimId]),{submitted:0});
  assert.deepEqual(other.submit([second.claimId],{requireAllQueued:true}),{submitted:1});
  assert.throws(()=>f.inbox.submit([second.claimId],{requireAllQueued:true}),/claim-already-submitted/);
});

function fixture(t,options={}){
  const dataRoot=fs.mkdtempSync(path.join(os.tmpdir(),'native-voice-inbox-'));
  t.after(()=>fs.rmSync(dataRoot,{recursive:true,force:true}));
  const config={dataRoot,accountId:'account',...options},inbox=createNativeVoiceInbox(config);
  const voice=id=>({nativeId:'wechat-server:'+id,serverId:id,nativeChatKey:'wechat:room@chatroom',senderId:'member',senderDisplayName:'Display',rawText:'[voice payload]',timestampSeconds:1793597379,localId:Number(id),snapshotSourceId:'db/message/row'+id,direction:'incoming'});
  const catalog=(ids,snapshotId='snapshot-1')=>({source:'authenticated-native-voice-catalog',accountId:'account',snapshotId,voices:ids.map(voice)});
  const file=path.join(dataRoot,'config/native-voice-inbox.json');
  return {dataRoot,config,inbox,voice,catalog,file};
}

test('baseline never becomes input, cannot be reset, and initialization survives restart',t=>{
  const f=fixture(t);assert.equal(f.inbox.status().initialized,false);
  assert.deepEqual(f.inbox.initialize(f.catalog(['1','2'])),{initialized:true,baselined:2});
  assert.deepEqual(f.inbox.pending(),[]);
  const restored=createNativeVoiceInbox(f.config);assert.equal(restored.status().identities,2);
  assert.throws(()=>restored.initialize(f.catalog([]),{nativeChatKeys:['wechat:room@chatroom']}),/already-initialized/);
  assert.deepEqual(restored.enqueue(f.catalog(['1','2'])),{queued:0,refreshed:0,ignored:2});
  assert.deepEqual(restored.pending(),[]);
});

test('stable claim refreshes only snapshot source and preserves first display name',t=>{
  const f=fixture(t);f.inbox.initialize(f.catalog(['1']));f.inbox.enqueue(f.catalog(['1','2']));
  const before=f.inbox.pending()[0],input=f.catalog(['2'],'snapshot-2');input.voices[0].snapshotSourceId='new-shard/row';input.voices[0].senderDisplayName='new display';
  assert.deepEqual(f.inbox.enqueue(input),{queued:0,refreshed:1,ignored:0});
  const after=f.inbox.pending()[0];assert.equal(before.claimId,after.claimId);assert.equal(after.snapshotId,'snapshot-2');assert.equal(after.snapshotSourceId,'new-shard/row');assert.equal(after.senderDisplayName,'Display');
  after.rawText='local mutation';assert.equal(f.inbox.pending()[0].rawText,before.rawText);
});

test('submitted before ask is durable; unknown outcome cannot replay after restart',t=>{
  const f=fixture(t);f.inbox.initialize(f.catalog([]),{nativeChatKeys:['wechat:room@chatroom']});f.inbox.enqueue(f.catalog(['1']));const id=f.inbox.pending()[0].claimId;
  assert.deepEqual(f.inbox.submit([id]),{submitted:1});
  const restarted=createNativeVoiceInbox(f.config);assert.deepEqual(restarted.pending(),[]);
  assert.deepEqual(restarted.enqueue(f.catalog(['1'],'later-snapshot')),{queued:0,refreshed:0,ignored:1});
  assert.deepEqual(restarted.submit([id]),{submitted:0});assert.deepEqual(f.inbox.pending(),[]);
});

test('same native ID across chat or immutable fields conflicts without partial writes',t=>{
  const f=fixture(t);f.inbox.initialize(f.catalog([]),{nativeChatKeys:['wechat:room@chatroom']});f.inbox.enqueue(f.catalog(['1']));const bytes=fs.readFileSync(f.file);
  for(const changes of [{nativeChatKey:'wechat:else@chatroom'},{senderId:'another'},{rawText:'changed'},{timestampSeconds:1793597380},{localId:2}]){
    const input=f.catalog(['2','1']);Object.assign(input.voices[1],changes);
    assert.throws(()=>f.inbox.enqueue(input),/native-identity-conflict/);assert.deepEqual(fs.readFileSync(f.file),bytes);
  }
});

test('wrong account, duplicate catalog IDs and nonincoming voices cannot enter queue',t=>{
  const f=fixture(t);f.inbox.initialize(f.catalog([]),{nativeChatKeys:['wechat:room@chatroom']});
  assert.throws(()=>f.inbox.enqueue({...f.catalog(['1']),accountId:'other'}),/catalog-invalid/);
  assert.throws(()=>f.inbox.enqueue(f.catalog(['1','1'])),/duplicate-native-id/);
  const outgoing=f.catalog(['1']);outgoing.voices[0].direction='outgoing';assert.throws(()=>f.inbox.enqueue(outgoing),/voice-invalid/);
  assert.equal(f.inbox.status().identities,0);
});

test('capacity fails explicitly without evicting old identities',t=>{
  const f=fixture(t,{maxIdentities:2});f.inbox.initialize(f.catalog(['1']));f.inbox.enqueue(f.catalog(['2']));const bytes=fs.readFileSync(f.file);
  assert.throws(()=>f.inbox.enqueue(f.catalog(['3'])),/capacity-exceeded/);assert.deepEqual(fs.readFileSync(f.file),bytes);
  assert.equal(f.inbox.status().identities,2);
});

test('failed atomic save retains pending and original bytes, including failed submit',t=>{
  const f=fixture(t);f.inbox.initialize(f.catalog([]),{nativeChatKeys:['wechat:room@chatroom']});f.inbox.enqueue(f.catalog(['1']));const id=f.inbox.pending()[0].claimId,bytes=fs.readFileSync(f.file);
  const original=fs.renameSync;
  fs.renameSync=(...args)=>{if(args[1]===f.file)throw Error('fixture-rename-failure');return original(...args);};
  try{
    assert.throws(()=>f.inbox.submit([id]),/fixture-rename-failure/);
    assert.throws(()=>f.inbox.enqueue(f.catalog(['2'])),/fixture-rename-failure/);
  }finally{fs.renameSync=original;}
  assert.deepEqual(fs.readFileSync(f.file),bytes);assert.deepEqual(f.inbox.pending().map(c=>c.claimId),[id]);
  assert.deepEqual(fs.readdirSync(path.dirname(f.file)),['native-voice-inbox.json']);
});

test('corrupt digest or foreign account state fails closed rather than rebaseline',t=>{
  const f=fixture(t);f.inbox.initialize(f.catalog(['1']));const state=JSON.parse(fs.readFileSync(f.file));state.claims[0].rawText='tampered';fs.writeFileSync(f.file,JSON.stringify(state));
  assert.throws(()=>f.inbox.pending(),/state-invalid/);assert.throws(()=>f.inbox.initialize(f.catalog([]),{nativeChatKeys:['wechat:room@chatroom']}),/state-invalid/);
});

test('unknown or baseline submit rejects entire set and leaves real queued claim pending',t=>{
  const f=fixture(t);f.inbox.initialize(f.catalog(['1']));f.inbox.enqueue(f.catalog(['2']));const id=f.inbox.pending()[0].claimId,bytes=fs.readFileSync(f.file);
  assert.throws(()=>f.inbox.submit([id,'f'.repeat(32)]),/claim-not-queued/);assert.deepEqual(fs.readFileSync(f.file),bytes);assert.equal(f.inbox.pending().length,1);
});

test('existing lock is never removed or bypassed',t=>{
  const f=fixture(t);f.inbox.initialize(f.catalog([]),{nativeChatKeys:['wechat:room@chatroom']});fs.writeFileSync(f.file+'.lock','fixture');
  assert.throws(()=>f.inbox.enqueue(f.catalog(['1'])),/inbox-busy/);assert.equal(fs.readFileSync(f.file+'.lock','utf8'),'fixture');assert.equal(f.inbox.status().identities,0);
});

test('hardlinked state rejected before reading or overwriting',t=>{
  const f=fixture(t);f.inbox.initialize(f.catalog([]),{nativeChatKeys:['wechat:room@chatroom']});
  try{fs.linkSync(f.file,f.file+'.linked');}catch{t.skip('host hardlinks unavailable');return;}
  assert.throws(()=>f.inbox.pending(),/file-invalid/);assert.throws(()=>f.inbox.enqueue(f.catalog(['1'])),/file-invalid/);
});

test('zero-voice route must be explicitly initialized or adopted before enqueue',t=>{
  const f=fixture(t);f.inbox.initialize(f.catalog([]));const bytes=fs.readFileSync(f.file);
  assert.equal(f.inbox.hasChat('wechat:room@chatroom'),false);
  assert.throws(()=>f.inbox.enqueue(f.catalog(['1'])),/chat-not-adopted/);assert.deepEqual(fs.readFileSync(f.file),bytes);
  assert.deepEqual(f.inbox.adoptChat(f.catalog([]),{nativeChatKey:'wechat:room@chatroom',unreadServerIds:[]}),{adopted:true,baselined:0,queued:0,preserved:0});
  assert.equal(f.inbox.hasChat('wechat:room@chatroom'),true);assert.equal(createNativeVoiceInbox(f.config).hasChat('wechat:room@chatroom'),true);
  f.inbox.enqueue(f.catalog(['1']));assert.equal(f.inbox.pending().length,1);
});

test('adoption baselines old voices, queues exact unread subset, and normal later scan cannot replay history',t=>{
  const f=fixture(t);f.inbox.initialize(f.catalog([]));
  assert.deepEqual(f.inbox.adoptChat(f.catalog(['1','2','3']),{nativeChatKey:'wechat:room@chatroom',unreadServerIds:['3']}),{adopted:true,baselined:2,queued:1,preserved:0});
  assert.deepEqual(f.inbox.pending().map(c=>c.serverId),['3']);
  const restarted=createNativeVoiceInbox(f.config);restarted.enqueue(f.catalog(['1','2','3','4']));
  assert.deepEqual(restarted.pending().map(c=>c.serverId),['3','4']);
});

test('adoption preserves previous baseline/submitted/queued and identities absent from new catalog',t=>{
  const f=fixture(t);f.inbox.initialize(f.catalog(['1']));f.inbox.enqueue(f.catalog(['2','3']));
  f.inbox.submit([f.inbox.pending().find(c=>c.serverId==='2').claimId]);
  const before=JSON.parse(fs.readFileSync(f.file)),old=new Map(before.claims.map(c=>[c.serverId,c]));
  f.inbox.adoptChat(f.catalog(['1','2','3','4'],'new-snapshot'),{nativeChatKey:'wechat:room@chatroom',unreadServerIds:['1','2','4']});
  const after=JSON.parse(fs.readFileSync(f.file));for(const id of ['1','2','3'])assert.deepEqual(after.claims.find(c=>c.serverId===id),old.get(id));
  assert.deepEqual(f.inbox.pending().map(c=>c.serverId),['3','4']);
  f.inbox.adoptChat(f.catalog(['4']),{nativeChatKey:'wechat:room@chatroom',unreadServerIds:[]});
  assert.equal(f.inbox.status().identities,4);
});

test('adoption rejects other-chat rows, missing unread proofs, duplicate unread or cross-chat server IDs atomically',t=>{
  const f=fixture(t);f.inbox.initialize(f.catalog(['1']));const bytes=fs.readFileSync(f.file);
  for(const unreadServerIds of [['999'],['2','2'],[2],['0']])assert.throws(()=>f.inbox.adoptChat(f.catalog(['2']),{nativeChatKey:'wechat:room@chatroom',unreadServerIds}),/unread-source-missing|adoption-invalid/);
  assert.throws(()=>f.inbox.adoptChat(f.catalog(['2']),{nativeChatKey:'wechat:other@chatroom',unreadServerIds:['2']}),/adoption-invalid/);
  const other=f.catalog(['1']);other.voices[0].nativeChatKey='wechat:other@chatroom';
  assert.throws(()=>f.inbox.adoptChat(other,{nativeChatKey:'wechat:other@chatroom',unreadServerIds:['1']}),/native-identity-conflict/);
  assert.deepEqual(fs.readFileSync(f.file),bytes);
});

test('legacy digest validates before inferred chat baselines and read-only load does not rewrite it',t=>{
  const f=fixture(t);f.inbox.initialize(f.catalog(['1']));const value=JSON.parse(fs.readFileSync(f.file));delete value.digest;delete value.chatBaselines;
  value.digest=createHash('sha256').update(JSON.stringify(value)).digest('hex');fs.writeFileSync(f.file,JSON.stringify(value));const bytes=fs.readFileSync(f.file);
  const restarted=createNativeVoiceInbox(f.config);assert.deepEqual(restarted.status().chatBaselines,['wechat:room@chatroom']);assert.deepEqual(fs.readFileSync(f.file),bytes);
  assert.throws(()=>restarted.enqueue({...f.catalog(['2']),voices:[{...f.voice('2'),nativeChatKey:'wechat:zero@chatroom'}]}),/chat-not-adopted/);
  restarted.adoptChat(f.catalog([]),{nativeChatKey:'wechat:zero@chatroom',unreadServerIds:[]});
  assert.deepEqual(JSON.parse(fs.readFileSync(f.file)).chatBaselines,['wechat:room@chatroom','wechat:zero@chatroom']);
  assert.deepEqual(restarted.pending(),[]);
});

test('failed adoption save cannot record chat baseline or consume old voices',t=>{
  const f=fixture(t);f.inbox.initialize(f.catalog([]));const bytes=fs.readFileSync(f.file),original=fs.renameSync;
  fs.renameSync=(...args)=>{if(args[1]===f.file)throw Error('adoption-save-failed');return original(...args);};
  try{assert.throws(()=>f.inbox.adoptChat(f.catalog(['1','2']),{nativeChatKey:'wechat:room@chatroom',unreadServerIds:['2']}),/adoption-save-failed/);}finally{fs.renameSync=original;}
  assert.deepEqual(fs.readFileSync(f.file),bytes);assert.deepEqual(f.inbox.status().chatBaselines,[]);assert.deepEqual(f.inbox.pending(),[]);
});

test('lookup returns only requested same-chat ownership copies and never raw text',t=>{
  const f=fixture(t);f.inbox.initialize(f.catalog(['1']));f.inbox.enqueue(f.catalog(['2']));
  const owners=f.inbox.lookup('wechat:room@chatroom',['2','1']);assert.deepEqual(owners.map(c=>c.state),['queued','baseline']);
  assert.equal(Object.hasOwn(owners[0],'rawText'),false);owners[0].state='submitted';assert.equal(f.inbox.lookup('wechat:room@chatroom',['2'])[0].state,'queued');
  assert.throws(()=>f.inbox.lookup('wechat:other@chatroom',['2']),/lookup-missing/);
  assert.throws(()=>f.inbox.lookup('wechat:room@chatroom',['2','2']),/lookup-invalid/);
});

