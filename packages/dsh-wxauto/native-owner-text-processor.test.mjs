import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createNativeOwnerTextInbox,ownerTextScopeDigest} from './native-owner-text-inbox.mjs';
import {createNativeOwnerTextProcessor} from './native-owner-text-processor.mjs';

const identity={accountId:'fixture',ownerNativeChatKey:'wechat:peer',expectedPeerUsername:'peer',ownerTarget:'Owner'};
const message=(n,text='ordinary text')=>({nativeId:'wechat-server:'+n,serverId:String(n),nativeChatKey:'wechat:peer',senderId:'peer',
  senderDisplayName:'Peer',rawText:text,timestampSeconds:100+n,localId:n,snapshotSourceId:'snapshot:'+n,direction:'incoming'});
const catalog=messages=>({source:'authenticated-native-owner-text-catalog',complete:true,accountId:'fixture',
  snapshotId:'snapshot',scopeDigest:ownerTextScopeDigest('fixture','wechat:peer','peer'),nativeChatKey:'wechat:peer',messages});
function fixture(t,extra={}){
  const root=fs.mkdtempSync(path.resolve('artifacts/owner-text-processor-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const inbox=createNativeOwnerTextInbox({...identity,dataRoot:root,now:()=>200000});inbox.initialize(catalog([]),{cutoverAtMs:100000});
  let current=catalog([message(1)]),authority={active:true,authorityEpoch:inbox.status().authorityEpoch,accountId:'fixture',ownerNativeChatKey:'wechat:peer'};
  let route={target:'Owner',kind:'friend',sessionId:'original-whale-session'};
  const events=[],saved=[],reports=[],calls=[];
  const processor=createNativeOwnerTextProcessor({...identity,inbox,readCatalog:async()=>{calls.push('catalog');return current;},
    activeAuthority:()=>authority,routeForOwner:()=>route,history:{incoming:async event=>{saved.push(event);return{id:'history:'+event.id};}},
    enqueue:async event=>events.push(event),report:(code,value)=>reports.push({code,...value}),...extra});
  return {processor,inbox,events,saved,reports,calls,setCatalog:v=>{current=v;},setAuthority:v=>{authority=v;},setRoute:v=>{route=v;}};
}

test('inactive or mismatched authority cannot read a catalog, persist or buffer',async t=>{
  const f=fixture(t);f.setAuthority(null);assert.equal((await f.processor.refresh()).inactive,true);
  f.setAuthority({active:true,authorityEpoch:'a'.repeat(32),accountId:'fixture',ownerNativeChatKey:'wechat:peer'});
  await f.processor.refresh();assert.equal(f.calls.length,0);assert.equal(f.saved.length,0);assert.equal(f.events.length,0);
});

test('pure submit preflight returns exact pending IDs and rejects wrong caller session or target',async t=>{
  const f=fixture(t);await f.processor.refresh();const event=f.events[0],revision=f.inbox.status().revision;
  assert.deepEqual(f.processor.validateSubmitting([event],{sessionId:'original-whale-session',target:'Owner'}),[event.nativeOwnerTextClaimId]);
  assert.deepEqual(f.processor.validateSubmitting([]),[]);
  for(const scope of [{sessionId:'wrong'},{target:'wrong'}])assert.throws(()=>f.processor.validateSubmitting([event],scope),/submit-invalid/);
  assert.equal(f.inbox.status().revision,revision);assert.equal(f.inbox.status().submitted,0);
});

test('history is durable before buffering; exact event submits once and stays consumed on restart',async t=>{
  const f=fixture(t);await f.processor.refresh();assert.equal(f.saved.length,1);assert.deepEqual(f.events[0],f.saved[0]);
  const e=f.events[0];assert.equal(e.sourceKind,'wechat-native-text');assert.equal(e.rawText,e.nativeSourceText);
  assert.equal(e.nativeSenderId,'peer');assert.equal(e.authorityEpoch,f.inbox.status().authorityEpoch);
  assert.equal(f.inbox.status().submitted,0);
  assert.deepEqual(f.processor.markSubmitting([e]),{submitted:1});
  assert.throws(()=>f.processor.markSubmitting([e]),/submit-invalid/);
  f.processor.close();await f.processor.refresh();assert.equal(f.events.length,1);assert.equal(f.inbox.status().submitted,1);
});

test('coalesced catalog read revoked during await never enqueues',async t=>{
  let resolve;const f=fixture(t,{readCatalog:()=>new Promise(r=>{resolve=r;})});
  const first=f.processor.refresh(),second=f.processor.refresh();assert.equal(first,second);
  f.setAuthority(null);resolve(catalog([message(1)]));await first;
  assert.equal(f.saved.length,0);assert.equal(f.inbox.pending().length,0);
});

test('history failure and route change do not authorize buffering or submission',async t=>{
  const f=fixture(t,{history:{incoming:async()=>null}});assert.equal((await f.processor.refresh()).failed,1);
  assert.equal(f.events.length,0);assert.equal(f.inbox.status().submitted,0);
  let resolve;const g=fixture(t,{history:{incoming:()=>new Promise(r=>{resolve=r;})}});
  const pending=g.processor.refresh();await new Promise(r=>setImmediate(r));g.setRoute({target:'Other',kind:'friend',sessionId:'different'});
  resolve({id:'saved'});await pending;assert.equal(g.events.length,0);assert.equal(g.inbox.status().submitted,0);
});

test('forged, duplicated or changed batch and revoked route cannot obtain a submit permit',async t=>{
  const f=fixture(t);await f.processor.refresh();const e=f.events[0];
  for(const batch of [[{...e,text:'forged'}],[e,e],[{...e,sourceKind:'wxauto-incoming'}]])
    assert.throws(()=>f.processor.markSubmitting(batch),/submit-invalid|batch-invalid/);
  assert.equal(f.inbox.status().submitted,0);f.setRoute({target:'Owner',kind:'group',sessionId:'group'});
  assert.throws(()=>f.processor.markSubmitting([e]),/authority-inactive/);
});

test('approval-looking text stays queued without trusted admission and never reaches the LLM buffer',async t=>{
  const f=fixture(t);f.setCatalog(catalog([message(1,'deadbeef'),message(2,'批准操作 deadbeef')]));
  const result=await f.processor.refresh();assert.equal(result.deferredControls,2);
  assert.equal(f.events.length,0);assert.equal(f.inbox.status().submitted,0);assert.equal(f.inbox.status().pending,2);
});

test('control handler observes durable submit before invocation and unknown outcome is never reissued',async t=>{
  let inbox,calls=0;
  const f=fixture(t,{onControl:async()=>{calls++;assert.equal(inbox.status().submitted,1);throw Error('unknown');}});inbox=f.inbox;
  f.setCatalog(catalog([message(1,'deadbeef')]));const result=await f.processor.refresh();assert.equal(result.failed,1);
  await f.processor.refresh();assert.equal(calls,1);assert.equal(f.events.length,0);assert.equal(f.inbox.pending().length,0);
});

test('uncertain buffer write is not duplicated; explicit unasked release permits recomputation',async t=>{
  let count=0;const f=fixture(t,{enqueue:async()=>{count++;throw Error('may have appended');}});
  await f.processor.refresh();await f.processor.refresh();assert.equal(count,1);assert.equal(f.inbox.status().pending,1);
  // The exact envelope saved in history is the only acceptable release proof.
  assert.deepEqual(f.processor.release([f.saved[0]]),{released:1});await f.processor.refresh();assert.equal(count,2);
  assert.equal(f.inbox.status().submitted,0);
});

test('another consumer winning between inspection and strict submit cannot consume the rest of the batch',async t=>{
  const f=fixture(t);f.setCatalog(catalog([message(1),message(2)]));await f.processor.refresh();
  const original=f.inbox;
  const guarded={...original,submit(ids,options){
    assert.equal(options.requireAllQueued,true);
    original.submit(ids.slice(0,1));
    return original.submit(ids,options);
  }};
  // A separate processor models the competing consumer's local prepared batch.
  const events=[];const second=createNativeOwnerTextProcessor({...identity,inbox:guarded,readCatalog:async()=>catalog([message(1),message(2)]),
    history:{incoming:async e=>({id:e.id})},enqueue:async e=>events.push(e),
    routeForOwner:()=>({target:'Owner',kind:'friend',sessionId:'original-whale-session'}),
    activeAuthority:()=>({active:true,authorityEpoch:original.status().authorityEpoch,accountId:'fixture',ownerNativeChatKey:'wechat:peer'})});
  await second.refresh();assert.equal(events.length,2);
  assert.throws(()=>second.markSubmitting(events),/claim-already-submitted/);
  assert.equal(original.status().submitted,1);assert.equal(original.status().pending,1);
});
