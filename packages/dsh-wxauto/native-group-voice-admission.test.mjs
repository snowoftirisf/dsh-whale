import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createNativeGroupInbox,nativeGroupClaimHash} from './native-group-inbox.mjs';
import {createNativeVoiceInbox} from './native-voice-inbox.mjs';
import {createNativeGroupVoiceAdmission} from './native-group-voice-admission.mjs';

const proof=q=>({nativeClaimId:q.claimId,nativeClaimHash:q.claimHash,nativeRenderedMessages:q.messages.map(m=>({nativeId:m.nativeId,text:m.text}))});
function fixture(t,types=['voice']){
  const dataRoot=fs.mkdtempSync(path.join(os.tmpdir(),'group-voice-admission-'));t.after(()=>fs.rmSync(dataRoot,{recursive:true,force:true}));
  const accountId='account',record={nativeChatKey:'wechat:room@chatroom',target:'Synthetic group',sessionId:'synthetic-session'};
  const message=(id,type='voice')=>({nativeId:'wechat-server:'+id,serverId:id,senderId:'member',senderDisplayName:'Member',type,text:type==='voice'?'[voice payload]':'synthetic text',timestampSeconds:1793597379,snapshotSourceId:'shard-row-'+id,contextOnly:type==='system',direction:'incoming'});
  const catalogVoice=id=>{const m=message(id);return {nativeId:m.nativeId,serverId:id,nativeChatKey:record.nativeChatKey,senderId:m.senderId,senderDisplayName:m.senderDisplayName,rawText:m.text,timestampSeconds:m.timestampSeconds,localId:Number(id),snapshotSourceId:m.snapshotSourceId,direction:'incoming'};};
  const groupInbox=createNativeGroupInbox({dataRoot,accountId}),voiceInbox=createNativeVoiceInbox({dataRoot,accountId});
  const catalog={source:'authenticated-native-voice-catalog',accountId,snapshotId:'original-snapshot',voices:[catalogVoice('1'),...types.flatMap((typ,i)=>typ==='voice'?[catalogVoice(String(i+2))]:[])]};
  voiceInbox.initialize({...catalog,voices:[]});
  const unread={state:'ready',source:'authenticated-wechat-native-unread',accountId,nativeChatKey:record.nativeChatKey,snapshotId:catalog.snapshotId,unreadCount:types.length,serverIds:types.map((_,i)=>String(i+2)),messages:types.map((typ,i)=>message(String(i+2),typ))};
  const queued=groupInbox.enqueue(record,unread),calls=[];
  const make=(readCatalog=async(key,options)=>{calls.push({key,options});return structuredClone(catalog);})=>createNativeGroupVoiceAdmission({groupInbox,voiceInbox,readCatalog});
  return {dataRoot,record,groupInbox,voiceInbox,catalog,queued,calls,make,catalogVoice};
}

test('voice-only requires durable delegation, baselines older voice, and never makes a placeholder input',async t=>{
  const f=fixture(t);assert.throws(()=>f.groupInbox.events(f.record,f.queued,proof(f.queued)),/delegation-required/);
  assert.throws(()=>f.groupInbox.submit(f.record,f.queued.claimId),/delegation-required/);
  const before=structuredClone(f.queued),q=await f.make().prepare(f.record,f.queued.claimId);
  assert.equal(q.claimHash,before.claimHash);assert.deepEqual(q.messages,before.messages);assert.deepEqual(q.serverIds,before.serverIds);assert.equal(nativeGroupClaimHash(q),before.claimHash);
  assert.deepEqual(f.voiceInbox.pending().map(c=>c.serverId),['2']);assert.equal(f.voiceInbox.lookup(f.record.nativeChatKey,['1'])[0].state,'baseline');
  assert.deepEqual(f.groupInbox.events(f.record,q,proof(q)),[]);
  assert.equal(f.calls[0].options.snapshotId,'original-snapshot');assert.equal(q.voiceDelegation.voices[0].state,'queued');
});

test('mixed batch only exports text/system through native unread, voice owns a separate exact source',async t=>{
  const f=fixture(t,['text','voice','system']),q=await f.make().prepare(f.record,f.queued.claimId);
  const events=f.groupInbox.events(f.record,q,proof(q));assert.deepEqual(events.map(e=>e.mediaType),['text','system']);assert.ok(events.every(e=>e.sourceKind==='wechat-native-unread'));
  assert.deepEqual(f.voiceInbox.pending().map(c=>c.serverId),['3']);assert.equal(q.voiceDelegation.voices.length,1);
});

test('repeat and restart preserve claim IDs; submitted unknown voices never become new group input',async t=>{
  const f=fixture(t);let q=await f.make().prepare(f.record,f.queued.claimId);const voiceId=q.voiceDelegation.voices[0].claimId;
  f.voiceInbox.submit([voiceId]);q=await f.make().prepare(f.record,f.queued.claimId);assert.equal(q.voiceDelegation.voices[0].state,'submitted');
  assert.deepEqual(f.voiceInbox.pending(),[]);f.groupInbox.submit(f.record,q.claimId);
  const restartedGroup=createNativeGroupInbox({dataRoot:f.dataRoot,accountId:'account'}),restartedVoice=createNativeVoiceInbox({dataRoot:f.dataRoot,accountId:'account'});
  const helper=createNativeGroupVoiceAdmission({groupInbox:restartedGroup,voiceInbox:restartedVoice,readCatalog:()=>{throw Error('must not reopen submitted');}});
  const done=await helper.prepare(f.record,q.claimId);assert.equal(done.state,'submitted');assert.equal(restartedVoice.lookup(f.record.nativeChatKey,['2'])[0].claimId,voiceId);
  assert.deepEqual(restartedGroup.events(f.record,done,proof(done)),[]);
});

test('every raw source field must match exact original snapshot catalog before adoption',async t=>{
  const f=fixture(t);const changes=[{nativeId:'wechat-server:999'},{nativeChatKey:'wechat:other@chatroom'},{senderId:'other'},{timestampSeconds:1},{rawText:'changed'},{snapshotSourceId:'other-shard'}];
  for(const change of changes){const cat=structuredClone(f.catalog);Object.assign(cat.voices[1],change);
    await assert.rejects(f.make(async()=>cat).prepare(f.record,f.queued.claimId),/catalog-invalid|source-conflict/);
    assert.equal(f.voiceInbox.hasChat(f.record.nativeChatKey),false);assert.equal(f.groupInbox.read(f.record).voiceDelegation,undefined);
  }
  await assert.rejects(f.make(async()=>({...f.catalog,snapshotId:'later'})).prepare(f.record,f.queued.claimId),/catalog-invalid/);
});

test('known group enqueues new voices rather than rebaselining them',async t=>{
  const f=fixture(t);f.voiceInbox.adoptChat({...f.catalog,voices:[f.catalogVoice('1')]},{nativeChatKey:f.record.nativeChatKey,unreadServerIds:[]});
  const later=structuredClone(f.catalog);later.voices.push(f.catalogVoice('99'));
  const q=await f.make(async()=>later).prepare(f.record,f.queued.claimId);
  assert.deepEqual(f.voiceInbox.pending().map(c=>c.serverId),['2','99']);assert.equal(q.voiceDelegation.voices[0].serverId,'2');
});

test('receipt tamper, omitted subset or forged claim ownership cannot authorize group submission',async t=>{
  const f=fixture(t),q=await f.make().prepare(f.record,f.queued.claimId),bad=structuredClone(q);bad.voiceDelegation.voices[0].state='submitted';
  assert.throws(()=>f.groupInbox.events(f.record,bad,proof(bad)),/delegation-invalid/);
  assert.throws(()=>f.groupInbox.delegateVoices(f.record,q.claimId,[]),/delegation-invalid/);
  assert.throws(()=>f.groupInbox.delegateVoices(f.record,q.claimId,[{...q.voiceDelegation.voices[0],nativeChatKey:'wechat:foreign@chatroom'}]),/delegation-invalid/);
  const folder=path.join(f.dataRoot,'config/auto-group-inbox'),file=path.join(folder,fs.readdirSync(folder)[0]);fs.writeFileSync(file,JSON.stringify(bad));
  assert.throws(()=>f.groupInbox.read(f.record),/delegation-invalid/);assert.throws(()=>f.groupInbox.submit(f.record,q.claimId),/delegation-invalid/);
});

test('text-only old path performs no voice catalog or adoption',async t=>{
  const f=fixture(t,['text']),q=await f.make(()=>{throw Error('must not read catalog');}).prepare(f.record,f.queued.claimId);
  assert.deepEqual(q,f.queued);assert.equal(f.voiceInbox.status().chatBaselines.length,0);assert.equal(f.groupInbox.events(f.record,q,proof(q)).length,1);
});

test('failed delegation save keeps raw claim intact and durable voice ownership can be retried',async t=>{
  const f=fixture(t),folder=path.join(f.dataRoot,'config/auto-group-inbox'),file=path.join(folder,fs.readdirSync(folder)[0]),before=fs.readFileSync(file),rename=fs.renameSync;
  fs.renameSync=(...args)=>{if(args[1]===file)throw Error('fixture-receipt-save-failed');return rename(...args);};
  try{await assert.rejects(f.make().prepare(f.record,f.queued.claimId),/fixture-receipt-save-failed/);}finally{fs.renameSync=rename;}
  assert.deepEqual(fs.readFileSync(file),before);assert.deepEqual(f.voiceInbox.pending().map(c=>c.serverId),['2']);
  const claimId=f.voiceInbox.pending()[0].claimId,q=await f.make().prepare(f.record,f.queued.claimId);
  assert.equal(q.voiceDelegation.voices[0].claimId,claimId);assert.deepEqual(f.groupInbox.events(f.record,q,proof(q)),[]);
});

test('claim changes while catalog reader waits cannot adopt source or issue receipt',async t=>{
  const f=fixture(t);let resume;const waiting=new Promise(r=>{resume=r;});
  const work=f.make(async()=>{await waiting;return f.catalog;}).prepare(f.record,f.queued.claimId);
  const folder=path.join(f.dataRoot,'config/auto-group-inbox'),file=path.join(folder,fs.readdirSync(folder)[0]);
  const value=JSON.parse(fs.readFileSync(file));value.snapshotId='different-original';value.claimHash=nativeGroupClaimHash(value);fs.writeFileSync(file,JSON.stringify(value));resume();
  await assert.rejects(work,/claim-changed/);assert.equal(f.voiceInbox.hasChat(f.record.nativeChatKey),false);
});
