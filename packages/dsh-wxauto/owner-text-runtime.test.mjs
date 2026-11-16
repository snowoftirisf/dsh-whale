import {fileURLToPath as __testFile} from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createNativeOwnerTextRuntime} from './owner-text-runtime.mjs';
import {createNativeOwnerTextInbox,ownerTextScopeDigest} from './native-owner-text-inbox.mjs';

function fixture(t,{baseline=true}={}){
  const dataRoot=fs.mkdtempSync(path.resolve('artifacts/owner-text-runtime-'));t.after(()=>fs.rmSync(dataRoot,{recursive:true,force:true}));
  const config={dataRoot,accountId:'fixture',target:'Owner',nativeVoiceSources:{ownerNativeChatKey:'wechat:peer'}};
  const catalog=messages=>({source:'authenticated-native-owner-text-catalog',complete:true,accountId:'fixture',nativeChatKey:'wechat:peer',
    scopeDigest:ownerTextScopeDigest('fixture','wechat:peer','peer'),snapshotId:'synthetic-fixture',messages});
  const inbox=createNativeOwnerTextInbox({dataRoot,accountId:'fixture',ownerNativeChatKey:'wechat:peer',expectedPeerUsername:'peer',now:()=>200000});
  if(baseline)inbox.initialize(catalog([]),{cutoverAtMs:100000});
  const state={worker:{version:1,mode:'ui'},reads:0,closes:0,requests:[],events:[],pending:null};
  const row={nativeId:'wechat-server:1',serverId:'1',nativeChatKey:'wechat:peer',senderId:'peer',senderDisplayName:'Peer',rawText:'synthetic ordinary text',
    timestampSeconds:101,localId:1,snapshotSourceId:'synthetic/row',direction:'incoming'};
  const runtime=createNativeOwnerTextRuntime({config,root:path.dirname(__testFile(import.meta.url)),requestOwner:async(op,args)=>{state.requests.push({op,args});return state.worker;},
    routeForOwner:()=>({target:'Owner',kind:'friend',sessionId:'original-session'}),enqueue:async e=>state.events.push(e),
    history:{incoming:async()=>({id:'synthetic-history'})},catalogClient:{catalog:async()=>{state.reads++;return state.pending?state.pending:catalog([row]);},close:()=>state.closes++}});
  return {config,inbox,state,runtime};
}

test('UI restore and constructor do no catalog IO or initialize an inbox',async t=>{
  const f=fixture(t,{baseline:false});assert.deepEqual(fs.readdirSync(f.config.dataRoot),[]);assert.equal(f.state.requests.length,0);
  assert.equal((await f.runtime.restore()).mode,'ui');assert.equal(f.state.reads,0);assert.deepEqual(fs.readdirSync(f.config.dataRoot),[]);
  assert.equal(f.runtime.getProcessor(),null);await f.runtime.refresh();assert.equal(f.state.reads,0);f.runtime.close();
});

test('native existing fixture restore coalesces catalog refresh and routes to original buffer without a model',async t=>{
  const f=fixture(t);f.state.worker={version:1,mode:'native',authorityEpoch:f.inbox.status().authorityEpoch};
  assert.equal((await f.runtime.restore()).active,true);assert.ok(f.runtime.getProcessor());
  const first=f.runtime.refresh(),second=f.runtime.refresh();assert.equal(first,second);await first;
  assert.equal(f.state.reads,1);assert.equal(f.state.events.length,1);assert.equal(f.inbox.status().submitted,0);
  assert.equal(f.runtime.acceptsHint({event:'native-owner-text-observed',target:'Owner',kind:'friend',authorityEpoch:f.state.worker.authorityEpoch}),true);
  assert.equal(f.runtime.acceptsHint({event:'native-owner-text-observed',target:'Other',kind:'friend',authorityEpoch:f.state.worker.authorityEpoch}),false);
  f.runtime.close();assert.equal(f.runtime.active(),false);
});

test('revocation during a pending catalog read prevents late buffering and heartbeat never autoactivates',async t=>{
  const f=fixture(t);const native={version:1,mode:'native',authorityEpoch:f.inbox.status().authorityEpoch};f.state.worker=native;
  await f.runtime.restore();let resolve;f.state.pending=new Promise(r=>resolve=r);const reading=f.runtime.refresh();
  f.runtime.handleStatus({version:1,mode:'blocked'});assert.equal(f.runtime.getProcessor(),null);assert.equal(f.runtime.active(),false);
  f.runtime.handleStatus(native);assert.equal(f.runtime.active(),false);
  resolve({complete:false});await reading;assert.equal(f.state.events.length,0);assert.equal(f.inbox.status().pending,0);f.runtime.close();
});

test('missing inbox or wrong worker epoch cannot initialize or silently fall back to UI',async t=>{
  for(const baseline of [false,true]){
    const f=fixture(t,{baseline});f.state.worker={version:1,mode:'native',authorityEpoch:'f'.repeat(32)};
    const result=await f.runtime.restore();assert.equal(result.mode,'blocked');assert.equal(result.active,false);assert.equal(f.state.reads,0);
    if(!baseline)assert.deepEqual(fs.readdirSync(f.config.dataRoot),[]);f.runtime.close();
  }
});
