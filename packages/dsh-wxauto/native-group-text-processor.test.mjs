import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createNativeGroupTextInbox} from './native-group-text-inbox.mjs';
import {nativeGroupTextScopeDigest} from './group-text-contract.mjs';
import {createNativeGroupTextProcessor} from './native-group-text-processor.mjs';

const identity={accountId:'fixture',nativeChatKey:'wechat:group@chatroom'};
const message=(id,text='ordinary group text',change={})=>({nativeId:'wechat-server:'+id,serverId:String(id),nativeChatKey:identity.nativeChatKey,
  senderId:'sender-'+id,senderDisplayName:'Sender '+id,senderNumericId:id,rawText:text,renderedText:text,renderStatus:'raw-preserved',
  timestampSeconds:100+id,localId:id,snapshotSourceId:'db/'+id,direction:'incoming',...change});
const catalog=(messages=[],unresolved=[])=>({source:'authenticated-native-group-text-catalog',complete:true,...identity,
  snapshotId:'snapshot',scopeDigest:nativeGroupTextScopeDigest(identity.accountId,identity.nativeChatKey),messages,unresolved,
  observedNativeIds:[...messages,...unresolved].filter(m=>m.nativeId!==null).map(m=>m.nativeId)});
function fixture(t,extra={}){
  const root=fs.mkdtempSync(path.resolve('artifacts/group-text-processor-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const config={...identity,dataRoot:root,now:()=>200000},inbox=createNativeGroupTextInbox(config);
  inbox.baseline({catalog:catalog(),authorityEpoch:'e'.repeat(32),cutoverAtMs:100000});
  let current=catalog([message(1)]),authority={active:true,...identity,authorityEpoch:'e'.repeat(32)};
  let route={target:'Original group',kind:'group',sessionId:'group-session',chatId:'canonical-group'};
  const events=[],saved=[],reports=[],calls=[];
  const options={...identity,inbox,readCatalog:async()=>{calls.push('catalog');return current;},activeAuthority:()=>authority,
    routeForGroup:key=>{assert.equal(key,identity.nativeChatKey);return route;},
    history:{incoming:async event=>{saved.push(event);return {messageIds:['ledger:'+event.id]};}},
    enqueue:async event=>events.push(event),report:(code,value)=>reports.push({code,...value}),...extra};
  return {root,config,inbox,options,processor:createNativeGroupTextProcessor(options),events,saved,reports,calls,
    setCatalog:value=>{current=value;},setAuthority:value=>{authority=value;},setRoute:value=>{route=value;}};
}

test('multiple real senders and approval-looking text remain ordinary group inputs; history precedes buffering and no model/social dispatch exists',async t=>{
  const f=fixture(t,{onControl:()=>assert.fail('groups must never dispatch approval')});
  f.setCatalog(catalog([message(3,'批准操作 deadbeef'),message(1,'deadbeef'),message(2,'raw prefix',{renderedText:'rendered body',renderStatus:'verified-sender-prefix-stripped'})]));
  const result=await f.processor.refresh();assert.equal(result.buffered,3);assert.equal(f.saved.length,3);
  assert.deepEqual(f.events.map(e=>e.nativeSenderId),['sender-1','sender-2','sender-3']);
  assert.deepEqual(f.events,f.saved);const event=f.events[1];
  assert.equal(event.sourceKind,'wechat-native-group-text');assert.equal(event.kind,'group');assert.equal(event.text,'rendered body');
  assert.equal(event.rawText,'raw prefix');assert.equal(event.nativeSourceText,'raw prefix');assert.equal(event.nativeSenderNumericId,2);
  assert.equal(f.inbox.status().submitted,0);assert.deepEqual(f.processor.markSubmitting(f.events,{target:'Original group',sessionId:'group-session'}),{submitted:3});
  // A subsequent failed/unknown ask cannot turn durable submitted back into queued.
  f.processor.close();const restarted=createNativeGroupTextProcessor({...f.options,inbox:createNativeGroupTextInbox(f.config)});
  await restarted.refresh();assert.equal(f.events.length,3);assert.equal(f.inbox.status().submitted,3);
});

test('paused, wrong account/group/epoch or inactive route performs no catalog IO and never initializes another inbox',async t=>{
  const f=fixture(t);
  for(const authority of [null,{active:false,...identity,authorityEpoch:'e'.repeat(32)},
    {active:true,...identity,accountId:'other',authorityEpoch:'e'.repeat(32)},
    {active:true,...identity,nativeChatKey:'wechat:other@chatroom',authorityEpoch:'e'.repeat(32)}]){
    f.setAuthority(authority);assert.equal((await f.processor.refresh()).inactive,true);
  }
  assert.equal(f.calls.length,0);
  f.setAuthority({active:true,...identity,authorityEpoch:'e'.repeat(32)});f.setRoute({target:'Owner',kind:'friend',sessionId:'owner'});
  await f.processor.refresh();assert.equal(f.calls.length,0);assert.equal(f.saved.length,0);
});

test('catalog refresh coalesces and late route/authority revocation prevents stale dispatch',async t=>{
  let resolve;const f=fixture(t,{readCatalog:()=>new Promise(r=>{resolve=r;})});
  const first=f.processor.refresh(),second=f.processor.refresh();assert.equal(first,second);
  f.setAuthority(null);resolve(catalog([message(1)]));await first;assert.equal(f.events.length,0);assert.equal(f.inbox.status().pending,0);
  let savedResolve;const g=fixture(t,{history:{incoming:()=>new Promise(r=>{savedResolve=r;})}});
  const pending=g.processor.refresh();await new Promise(r=>setImmediate(r));g.processor.close();savedResolve({id:'durable'});await pending;
  assert.equal(g.events.length,0);assert.equal(g.inbox.status().submitted,0);
});

test('unresolved and old boundary rows never reach history, while a later exact resolved sender does once',async t=>{
  const f=fixture(t),row=message(2);const unresolved={nativeId:row.nativeId,serverId:row.serverId,nativeChatKey:row.nativeChatKey,
    rawText:row.rawText,timestampSeconds:row.timestampSeconds,localId:row.localId,snapshotSourceId:row.snapshotSourceId,senderNumericId:row.senderNumericId,
    reason:'sender-unresolved',direction:'unknown'};
  f.setCatalog(catalog([],[unresolved]));await f.processor.refresh();assert.equal(f.saved.length,0);
  f.setCatalog(catalog([row]));await f.processor.refresh();await f.processor.refresh();assert.equal(f.events.length,1);
});

test('pure submit validates exact batch/current group scope; changing target/session/chat or envelope cannot consume claims',async t=>{
  const f=fixture(t);await f.processor.refresh();const event=f.events[0],revision=f.inbox.status().revision;
  assert.deepEqual(f.processor.validateSubmitting([event],{sessionId:'group-session',target:'Original group'}),[event.nativeGroupTextClaimId]);
  for(const changed of [{...event,target:'Other'},{...event,kind:'friend'},{...event,nativeSenderId:'owner'},
    {...event,nativeAccountId:'other'},{...event,text:'other'},{...event,images:[{file:'invalid'}]}])assert.throws(()=>f.processor.markSubmitting([changed]),/submit-invalid/);
  assert.throws(()=>f.processor.markSubmitting([event,event]),/batch-invalid/);
  f.setRoute({target:'Original group',kind:'group',sessionId:'rotated',chatId:'canonical-group'});
  assert.throws(()=>f.processor.markSubmitting([event]),/submit-invalid/);assert.equal(f.inbox.status().revision,revision);
  assert.deepEqual(f.processor.release([event]),{released:1});await f.processor.refresh();assert.equal(f.events.length,2);
  assert.equal(f.processor.markSubmitting([f.events[1]]).submitted,1);assert.equal(f.processor.release([f.events[1]]).released,0);
});

test('history failure stays unissued; enqueue rejection retains exact prepared event until explicit release',async t=>{
  const f=fixture(t,{history:{incoming:async()=>null}});assert.equal((await f.processor.refresh()).failed,1);
  assert.equal(f.events.length,0);assert.equal(f.inbox.status().pending,1);assert.equal(f.inbox.status().submitted,0);
  const committed=[];const g=fixture(t,{enqueue:async event=>{committed.push(event);throw Error('fixture buffer unknown');}});
  assert.equal((await g.processor.refresh()).failed,1);await g.processor.refresh();assert.equal(committed.length,1);
  assert.equal(g.processor.release(committed).released,1);await g.processor.refresh();assert.equal(committed.length,2);
});
