import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createNativeGroupInbox,nativeGroupClaimHash} from './native-group-inbox.text-candidate.mjs';
import {createNativeGroupTextInbox} from './native-group-text-inbox.admission-candidate.mjs';
import {createNativeGroupTextAdmission} from './native-group-text-admission.mjs';
import {createNativeGroupVoiceAdmission} from './native-group-voice-admission.mjs';
import {createNativeVoiceInbox} from './native-voice-inbox.mjs';
import {nativeGroupTextPaths,nativeGroupTextScopeDigest} from './group-text-contract.mjs';
const sha=v=>createHash('sha256').update(v).digest('hex');
const proof=q=>({nativeClaimId:q.claimId,nativeClaimHash:q.claimHash,nativeRenderedMessages:q.messages.map(m=>({nativeId:m.nativeId,text:m.text}))});
function fixture(t,types=['text'],options={}){
 const dataRoot=fs.mkdtempSync(path.resolve('artifacts/group-text-admission-'));t.after(()=>fs.rmSync(dataRoot,{recursive:true,force:true}));
 const accountId='account',record={nativeChatKey:'wechat:room@chatroom',target:'Group',sessionId:'same-session'};
 const message=(id,type='text')=>({nativeId:'wechat-server:'+id,serverId:String(id),senderId:'wxid_member',senderDisplayName:'Member',type,
  text:type==='text'?'fixture text '+id:'['+type+']',timestampSeconds:100+Number(id),snapshotSourceId:'db/'+id,contextOnly:type==='system',direction:'incoming'});
 const catalogMessage=(id,changes={})=>{const m=message(id);return {nativeId:m.nativeId,serverId:m.serverId,nativeChatKey:record.nativeChatKey,
  senderId:m.senderId,senderDisplayName:m.senderDisplayName,senderNumericId:7,rawText:m.text,renderedText:m.text,renderStatus:'raw-preserved',
  timestampSeconds:m.timestampSeconds,localId:Number(id),snapshotSourceId:m.snapshotSourceId,direction:'incoming',...changes};};
 const config={dataRoot,accountId,nativeChatKey:record.nativeChatKey,now:()=>200000};
 const groupInbox=createNativeGroupInbox({dataRoot,accountId,textDelegation:true}),textInbox=createNativeGroupTextInbox(config);
 const messages=types.map((type,i)=>message(i+2,type)),unread={state:'ready',source:'authenticated-wechat-native-unread',accountId,
  nativeChatKey:record.nativeChatKey,snapshotId:'snapshot',unreadCount:messages.length,serverIds:messages.map(m=>m.serverId),messages};
 let queued=groupInbox.enqueue(record,unread);
 const catalog={source:'authenticated-native-group-text-catalog',complete:true,accountId,nativeChatKey:record.nativeChatKey,
  snapshotId:'snapshot',scopeDigest:nativeGroupTextScopeDigest(accountId,record.nativeChatKey),messages:[catalogMessage(1),...messages.filter(m=>m.type==='text').map(m=>catalogMessage(m.serverId))],
  unresolved:[],observedNativeIds:['wechat-server:1',...messages.filter(m=>m.type==='text').map(m=>m.nativeId)],
  admission:{claimId:queued.claimId,claimHash:queued.claimHash,snapshotId:'snapshot',verified:true}};
 const calls=[];
 const make=(readCatalog=async(key,args)=>{calls.push({key,args});return structuredClone(catalog);})=>createNativeGroupTextAdmission({groupInbox,textInbox,readCatalog,now:()=>200000});
 return {dataRoot,accountId,record,config,groupInbox,textInbox,queued,catalog,message,catalogMessage,unread,make,calls,
  rawFile:path.join(dataRoot,'config/auto-group-inbox',fs.readdirSync(path.join(dataRoot,'config/auto-group-inbox'))[0]),textFile:nativeGroupTextPaths(config).file};
}

test('new exact unread owns only new text, baselines old history and stores admission with ownership in one inode',async t=>{
 const f=fixture(t);assert.throws(()=>f.groupInbox.events(f.record,f.queued,proof(f.queued)),/text-delegation-required/);
 const q=await f.make().prepare(f.record,f.queued.claimId),receipt=q.textDelegation;
 assert.equal(receipt.ownershipMode,'new-admission');assert.equal(receipt.accountId,'account');assert.equal(receipt.claimHash,f.queued.claimHash);
 assert.equal(f.textInbox.pending().length,1);assert.equal(f.textInbox.lookup(['wechat-server:1'])[0].state,'baseline');
 const body=JSON.parse(fs.readFileSync(f.textFile));assert.equal(body.admission.rawClaimId,q.claimId);assert.deepEqual(body.admission.unreadNativeIds,['wechat-server:2']);
 assert.deepEqual(receipt.newTextClaimIds,[f.textInbox.pending()[0].claimId]);assert.deepEqual(f.groupInbox.events(f.record,q),[]);
 assert.equal(f.calls[0].args.snapshotId,'snapshot');assert.deepEqual(f.calls[0].args.admissionProof,{claimId:q.claimId,claimHash:q.claimHash});
});

test('native-second unread boundary preserves unseen post-UI arrivals in the same second while all original history stays baseline',async t=>{
 const f=fixture(t);f.catalog.messages.push(f.catalogMessage(90,{timestampSeconds:150}));f.catalog.observedNativeIds.push('wechat-server:90');
 await f.make().prepare(f.record,f.queued.claimId);
 assert.equal(f.textInbox.status().cutoverAtMs,102000);assert.equal(f.textInbox.status().cutoverFloorSeconds,102);
 assert.equal(f.textInbox.lookup(['wechat-server:90'])[0].state,'baseline');
 const current={...f.catalog,snapshotId:'after-ui',messages:[...f.catalog.messages,f.catalogMessage(3,{timestampSeconds:102}),f.catalogMessage(4,{timestampSeconds:101})],
  observedNativeIds:[...f.catalog.observedNativeIds,'wechat-server:3','wechat-server:4']};
 f.textInbox.enqueue(current);
 assert.deepEqual(f.textInbox.pending().map(c=>c.serverId).sort(),['2','3']);assert.equal(f.textInbox.lookup(['wechat-server:4'])[0].state,'held');
 assert.equal(f.textInbox.lookup(['wechat-server:1','wechat-server:90']).every(c=>c.state==='baseline'),true);
});

test('new adoption rejects a future native boundary and any caller-expanded cutoff without writing an inbox',async t=>{
 const f=fixture(t),future=structuredClone(f.catalog),q=JSON.parse(fs.readFileSync(f.rawFile));
 q.messages[0].timestampSeconds=201;q.claimHash=nativeGroupClaimHash(q);fs.writeFileSync(f.rawFile,JSON.stringify(q));
 future.messages[1].timestampSeconds=201;future.admission.claimHash=q.claimHash;
 await assert.rejects(f.make(async()=>future).prepare(f.record,q.claimId),/native-boundary-invalid/);assert.equal(f.textInbox.status().initialized,false);
 const g=fixture(t);
 assert.throws(()=>g.textInbox.admit({catalog:g.catalog,rawClaimId:g.queued.claimId,rawClaimHash:g.queued.claimHash,
  unreadNativeIds:['wechat-server:2'],authorityEpoch:'e'.repeat(32),cutoverAtMs:101000}),/native-boundary-invalid/);
 assert.equal(g.textInbox.status().initialized,false);
 const h=fixture(t,['text','text']),mixed=structuredClone(h.catalog),raw=JSON.parse(fs.readFileSync(h.rawFile));
 raw.messages[1].timestampSeconds=231;raw.claimHash=nativeGroupClaimHash(raw);fs.writeFileSync(h.rawFile,JSON.stringify(raw));
 mixed.messages[2].timestampSeconds=231;mixed.admission.claimHash=raw.claimHash;
 await assert.rejects(h.make(async()=>mixed).prepare(h.record,raw.claimId),/admission-future-source/);assert.equal(h.textInbox.status().initialized,false);
});

test('verified protocol raw vs legacy redacted text keeps two distinct digests and actual local ID without guessing',async t=>{
 const f=fixture(t),raw='wxid_member:\nhello',queuedText='[内部标识已隐藏]:\nhello';
 const q=JSON.parse(fs.readFileSync(f.rawFile));q.messages[0].text=queuedText;q.claimHash=nativeGroupClaimHash(q);fs.writeFileSync(f.rawFile,JSON.stringify(q));
 const catalog={...f.catalog,messages:[f.catalog.messages[0],f.catalogMessage(2,{rawText:raw,renderedText:'hello',renderStatus:'verified-sender-prefix-stripped'})],
  admission:{...f.catalog.admission,claimHash:q.claimHash}};
 const next=await f.make(async()=>catalog).prepare(f.record,q.claimId),item=next.textDelegation.texts[0];
 assert.equal(item.rawSha256,sha(raw));assert.equal(item.queuedTextSha256,sha(queuedText));assert.notEqual(item.rawSha256,item.queuedTextSha256);
 assert.equal(item.localId,2);assert.equal(f.textInbox.pending()[0].rawText,raw);
});

test('handled text remains baseline while only the truly unread subset is queued',async t=>{
 const f=fixture(t,['text','text']),q=JSON.parse(fs.readFileSync(f.rawFile));q.handledServerIds=['2'];fs.writeFileSync(f.rawFile,JSON.stringify(q));
 const next=await f.make().prepare(f.record,q.claimId);
 assert.deepEqual(f.textInbox.pending().map(c=>c.serverId),['3']);assert.equal(f.textInbox.lookup(['wechat-server:2'])[0].state,'baseline');
 assert.equal(next.textDelegation.newTextClaimIds.length,1);assert.deepEqual(f.groupInbox.events(f.record,next),[]);
});

test('receipt failure is repaired after restart without reinitializing or changing text claims; submitted stays consumed',async t=>{
 const f=fixture(t),original=fs.renameSync;fs.renameSync=(a,b)=>{if(b===f.rawFile)throw Error('fixture receipt write');return original(a,b);};
 try{await assert.rejects(f.make().prepare(f.record,f.queued.claimId),/fixture receipt write/);}finally{fs.renameSync=original;}
 const before=fs.readFileSync(f.textFile),claim=f.textInbox.pending()[0];assert.equal(f.groupInbox.read(f.record).textDelegation,undefined);
 const restored=createNativeGroupTextInbox(f.config),raw=createNativeGroupInbox({dataRoot:f.dataRoot,accountId:f.accountId,textDelegation:true});
 const helper=createNativeGroupTextAdmission({groupInbox:raw,textInbox:restored,readCatalog:async()=>f.catalog,now:()=>200000});
 let q=await helper.prepare(f.record,f.queued.claimId);assert.deepEqual(fs.readFileSync(f.textFile),before);
 restored.submit([claim.claimId],{requireAllQueued:true});q=await helper.prepare(f.record,f.queued.claimId);
 assert.equal(q.textDelegation.texts[0].state,'submitted');assert.equal(restored.pending().length,0);
 raw.submit(f.record,q.claimId);const submitted=await helper.prepare(f.record,q.claimId);assert.equal(submitted.state,'submitted');
});

test('existing quiet/native inbox owns IDs without authority/admission rewrite or promoting known baseline',async t=>{
 const f=fixture(t,['text','text']);f.textInbox.baseline({catalog:{...f.catalog,messages:[f.catalogMessage(2)],observedNativeIds:['wechat-server:2']},authorityEpoch:'e'.repeat(32),cutoverAtMs:100000});
 const q=await f.make().prepare(f.record,f.queued.claimId);
 assert.equal(q.textDelegation.ownershipMode,'existing-inbox');assert.equal(q.textDelegation.authorityEpoch,'e'.repeat(32));assert.deepEqual(q.textDelegation.newTextClaimIds,[]);
 assert.equal(f.textInbox.lookup(['wechat-server:2'])[0].state,'baseline');assert.equal(f.textInbox.lookup(['wechat-server:3'])[0].state,'queued');
 assert.equal(JSON.parse(fs.readFileSync(f.textFile)).admission,undefined);assert.deepEqual(f.groupInbox.events(f.record,q),[]);
});

test('existing held unread is explicit pending and cannot fall back to old raw event',async t=>{
 const f=fixture(t);f.textInbox.baseline({catalog:{...f.catalog,messages:[],observedNativeIds:[]},authorityEpoch:'e'.repeat(32),cutoverAtMs:200000});
 await assert.rejects(f.make().prepare(f.record,f.queued.claimId),/held-source-pending/);
 assert.equal(f.textInbox.status().held,2);assert.throws(()=>f.groupInbox.events(f.record,f.groupInbox.read(f.record),proof(f.queued)),/text-delegation-required/);
 assert.throws(()=>f.groupInbox.delegateTexts(f.record,f.queued.claimId,{authorityEpoch:'e'.repeat(32),ownershipMode:'existing-inbox',
  texts:f.textInbox.lookup(['wechat-server:2']),newTextClaimIds:[]}),/held-pending/);
});

test('expanded raw claim preserves original adoption/epoch and exposes only existing ownership, never a second authority',async t=>{
 const f=fixture(t),first=await f.make().prepare(f.record,f.queued.claimId),original=JSON.parse(fs.readFileSync(f.textFile)).admission;
 const nextMessage={...f.message(3),timestampSeconds:201},source={...f.unread,snapshotId:'snapshot-2',unreadCount:2,serverIds:['2','3'],messages:[f.message(2),nextMessage]};
 const expanded=f.groupInbox.enqueue(f.record,source);assert.notEqual(expanded.claimId,first.claimId);
 const catalog={...f.catalog,snapshotId:'snapshot-2',messages:[...f.catalog.messages,f.catalogMessage(3,{timestampSeconds:201})],
  observedNativeIds:[...f.catalog.observedNativeIds,'wechat-server:3'],admission:{claimId:expanded.claimId,claimHash:expanded.claimHash,snapshotId:'snapshot-2',verified:true}};
 const next=await f.make(async()=>catalog).prepare(f.record,expanded.claimId),body=JSON.parse(fs.readFileSync(f.textFile));
 assert.deepEqual(body.admission,original);assert.equal(body.authorityEpoch,first.textDelegation.authorityEpoch);
 assert.equal(next.textDelegation.ownershipMode,'existing-inbox');assert.deepEqual(next.textDelegation.newTextClaimIds,[]);
 assert.deepEqual(f.textInbox.pending().map(c=>c.serverId).sort(),['2','3']);assert.deepEqual(f.groupInbox.events(f.record,next),[]);
});

test('text+voice delegates both owners, requires both receipts, resumes without transient proof only if no original event remains',async t=>{
 const f=fixture(t,['text','voice']);let q=await f.make().prepare(f.record,f.queued.claimId);
 assert.throws(()=>f.groupInbox.events(f.record,q),/voice-delegation-required/);assert.throws(()=>f.groupInbox.submit(f.record,q.claimId),/voice-delegation-required/);
 const voiceInbox=createNativeVoiceInbox({dataRoot:f.dataRoot,accountId:'account'});voiceInbox.initialize({source:'authenticated-native-voice-catalog',accountId:'account',snapshotId:'snapshot',voices:[]});
 const v={...f.catalogMessage(3),rawText:'[voice]'};delete v.renderedText;delete v.renderStatus;delete v.senderNumericId;
 q=await createNativeGroupVoiceAdmission({groupInbox:f.groupInbox,voiceInbox,readCatalog:async()=>({source:'authenticated-native-voice-catalog',accountId:'account',snapshotId:'snapshot',voices:[v]})}).prepare(f.record,q.claimId);
 assert.deepEqual(f.groupInbox.events(f.record,q),[]);assert.ok(f.groupInbox.submit(f.record,q.claimId));
 const system=fixture(t,['text','system']),sq=await system.make().prepare(system.record,system.queued.claimId);
 assert.throws(()=>system.groupInbox.events(system.record,sq),/render-proof-invalid/);assert.deepEqual(system.groupInbox.events(system.record,sq,proof(sq)).map(e=>e.mediaType),['system']);
});

test('cold voice-only durable delegation and all-handled batches need no transient render proof; remaining system still does',async t=>{
 const f=fixture(t,['voice']);assert.throws(()=>f.groupInbox.events(f.record,f.queued),/voice-delegation-required/);
 const voiceInbox=createNativeVoiceInbox({dataRoot:f.dataRoot,accountId:'account'});voiceInbox.initialize({source:'authenticated-native-voice-catalog',accountId:'account',snapshotId:'snapshot',voices:[]});
 const v={...f.catalogMessage(2),rawText:'[voice]'};delete v.renderedText;delete v.renderStatus;delete v.senderNumericId;
 const q=await createNativeGroupVoiceAdmission({groupInbox:f.groupInbox,voiceInbox,readCatalog:async()=>({source:'authenticated-native-voice-catalog',accountId:'account',snapshotId:'snapshot',voices:[v]})}).prepare(f.record,f.queued.claimId);
 assert.equal(q.textDelegation,undefined);assert.deepEqual(f.groupInbox.events(f.record,q),[]);
 const corrupt=structuredClone(q);corrupt.voiceDelegation.digest='bad';assert.throws(()=>f.groupInbox.events(f.record,corrupt),/voice-delegation-invalid/);
 for(const types of [['text'],['voice'],['text','voice'],['system']]){
  const handled=fixture(t,types),value=JSON.parse(fs.readFileSync(handled.rawFile));value.handledServerIds=[...value.serverIds];fs.writeFileSync(handled.rawFile,JSON.stringify(value));
  const actual=handled.groupInbox.read(handled.record);assert.equal(actual.textDelegation,undefined);assert.equal(actual.voiceDelegation,undefined);
  assert.deepEqual(handled.groupInbox.events(handled.record,actual),[]);assert.ok(handled.groupInbox.submit(handled.record,actual.claimId));
 }
 const system=fixture(t,['voice','system']),v3={...system.catalogMessage(2),rawText:'[voice]'};delete v3.renderedText;delete v3.renderStatus;delete v3.senderNumericId;
 const vi=createNativeVoiceInbox({dataRoot:system.dataRoot,accountId:'account'});vi.initialize({source:'authenticated-native-voice-catalog',accountId:'account',snapshotId:'snapshot',voices:[]});
 const sq=await createNativeGroupVoiceAdmission({groupInbox:system.groupInbox,voiceInbox:vi,readCatalog:async()=>({source:'authenticated-native-voice-catalog',accountId:'account',snapshotId:'snapshot',voices:[v3]})}).prepare(system.record,system.queued.claimId);
 assert.throws(()=>system.groupInbox.events(system.record,sq),/render-proof-invalid/);
 assert.deepEqual(system.groupInbox.events(system.record,sq,proof(sq)).map(e=>e.mediaType),['system']);
});

test('wrong proof, source identity or known legacy local mismatch cannot initialize an owner',async t=>{
 const f=fixture(t);
 for(const admission of [undefined,{...f.catalog.admission,verified:false},{...f.catalog.admission,claimHash:'a'.repeat(64)},{...f.catalog.admission,snapshotId:'later'}])
  await assert.rejects(f.make(async()=>({...f.catalog,admission})).prepare(f.record,f.queued.claimId),/proof-invalid/);
 for(const changes of [{senderId:'other'},{timestampSeconds:1},{snapshotSourceId:'other'}]){
  const catalog=structuredClone(f.catalog);Object.assign(catalog.messages[1],changes);await assert.rejects(f.make(async()=>catalog).prepare(f.record,f.queued.claimId),/source-conflict/);
 }
 const q=JSON.parse(fs.readFileSync(f.rawFile));q.messages[0].localId=999;fs.writeFileSync(f.rawFile,JSON.stringify(q));
 await assert.rejects(f.make().prepare(f.record,q.claimId),/source-conflict/);assert.equal(f.textInbox.status().initialized,false);
});

test('no unread text retains old SDK route without catalog IO or baseline; default constructor remains compatible',async t=>{
 const f=fixture(t,['system']),q=await f.make(()=>assert.fail('no text catalog')).prepare(f.record,f.queued.claimId);
 assert.equal(f.textInbox.status().initialized,false);assert.equal(f.groupInbox.events(f.record,q,proof(q)).length,1);
 const legacy=fixture(t),old=createNativeGroupInbox({dataRoot:legacy.dataRoot,accountId:'account'});
 assert.equal(old.events(legacy.record,legacy.queued,proof(legacy.queued)).length,1);assert.ok(old.submit(legacy.record,legacy.queued.claimId));
});

test('claim change while catalog awaits refuses before durable adoption; receipt tamper cannot authorize raw submit',async t=>{
 const f=fixture(t);let finish;const pending=f.make(()=>new Promise(r=>finish=r)).prepare(f.record,f.queued.claimId);
 const q=JSON.parse(fs.readFileSync(f.rawFile));q.messages[0].snapshotSourceId='tampered';fs.writeFileSync(f.rawFile,JSON.stringify(q));finish(f.catalog);
 await assert.rejects(pending,/claim-changed/);assert.equal(f.textInbox.status().initialized,false);
 const g=fixture(t),next=await g.make().prepare(g.record,g.queued.claimId),bad=structuredClone(next);
 bad.textDelegation.texts[0].state='submitted';assert.throws(()=>g.groupInbox.events(g.record,bad),/text-delegation-invalid/);
 const raw=createNativeGroupInbox({dataRoot:g.dataRoot,accountId:'account'});assert.deepEqual(raw.events(g.record,next),[]);
});
