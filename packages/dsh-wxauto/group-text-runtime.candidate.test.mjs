// Staged candidate only: these imports never replace/load the live runtime.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createNativeGroupTextRuntime} from './group-text-runtime.candidate.mjs';
import {createNativeGroupTextInbox} from './native-group-text-inbox.mjs';
import {createNativeGroupTextProcessor} from './native-group-text-processor.mjs';
import {nativeGroupTextScopeDigest} from './group-text-contract.mjs';
const key='wechat:room@chatroom',epoch='a'.repeat(32);
function fixture(extra={}){
 let value={version:1,mode:'ui',readsStopped:false,accountId:'fixture',nativeChatKey:key,target:'room'},route={target:'room',kind:'group',sessionId:'session',chatId:'canonical-chat'},keys=[key],error=false;
 const counts={inbox:0,client:0,processor:0,closed:0};
 const runtime=createNativeGroupTextRuntime({config:{dataRoot:'/fixture',accountId:'fixture'},root:'/fixture',requestGroup:async()=>{if(error)throw Error('fixture unavailable');return value;},
  routeForGroup:()=>route,groupKeys:()=>keys,enqueue(){},history:{},
  inboxFactory:()=>{counts.inbox++;return {status:()=>({initialized:true,authorityEpoch:epoch})};},
  clientFactory:()=>{counts.client++;return {catalog(){},close(){counts.closed++;}};},
  processorFactory:()=>{counts.processor++;return {refresh:async()=>({buffered:0,failed:0}),close(){counts.closed++;}};},...extra});
 return {runtime,counts,key,epoch,set:v=>value={...value,...v},route:v=>route=v,keys:v=>keys=v,error:v=>error=v,
  status:v=>runtime.handleStatus(key,{...value,...v})};
}

test('cold trusted blocked/readsStopped/native-without-epoch guards its target without opening processors',()=>{
 for(const state of [{mode:'blocked'},{mode:'ui',readsStopped:true},{mode:'native',authorityEpoch:null}]){
  const f=fixture();f.status(state);assert.equal(f.runtime.status().groups[0].mode,'blocked');
  assert.equal(f.runtime.status().recoveryGuardVersion,2);
  assert.deepEqual([...f.runtime.guardedTargets()],['room']);assert.equal(f.runtime.active(key),false);assert.equal(f.counts.inbox,0);f.runtime.close();
 }
 const foreign=fixture();foreign.status({mode:'blocked',accountId:'other'});
 assert.equal(foreign.runtime.status().groups[0].mode,'unavailable');assert.deepEqual([...foreign.runtime.guardedTargets()],[]);
 const missingChat=fixture();missingChat.route({target:'room',kind:'group',sessionId:'session'});
 missingChat.status({mode:'native',authorityEpoch:epoch});assert.equal(missingChat.runtime.status().groups[0].mode,'blocked');
 assert.deepEqual([...missingChat.runtime.guardedTargets()],['room']);assert.equal(missingChat.runtime.active(key),false);assert.equal(missingChat.counts.inbox,0);
});

test('unproved RPC failure keeps default UI recovery, but explicit authority stop cannot be downgraded to UI',async()=>{
 const f=fixture();f.error(true);await f.runtime.restore();assert.equal(f.runtime.status().groups[0].mode,'unavailable');assert.deepEqual([...f.runtime.guardedTargets()],[]);
 const abort=fixture();abort.status({mode:'preparing'});assert.deepEqual([...abort.runtime.guardedTargets()],['room']);
 abort.status({mode:'ui'});assert.deepEqual([...abort.runtime.guardedTargets()],[]);
 const stopped=fixture();stopped.status({mode:'blocked'});stopped.status({mode:'ui'});
 assert.equal(stopped.runtime.status().groups[0].mode,'blocked');assert.deepEqual([...stopped.runtime.guardedTargets()],['room']);
});

test('temporarily missing route/RPC retains all previous guarded names without granting activity',async()=>{
 const f=fixture();f.set({mode:'native',authorityEpoch:epoch});await f.runtime.restore();
 f.route({target:'renamed',kind:'group',sessionId:'session',chatId:'canonical-chat'});f.status({mode:'native',authorityEpoch:epoch,target:'renamed'});
 f.route(null);f.error(true);await f.runtime.restore();assert.equal(f.runtime.active(key),false);
 assert.deepEqual([...f.runtime.guardedTargets()].sort(),['renamed','room']);f.keys([]);await f.runtime.restore();assert.deepEqual([...f.runtime.guardedTargets()].sort(),['renamed','room']);
});

test('trusted target-only rename preserves epoch/processor, accepts only new target hints and keeps old recovery guarded',async()=>{
 const f=fixture();f.set({mode:'native',authorityEpoch:epoch});await f.runtime.restore();const processor=f.runtime.getProcessor(key);
 f.route({target:'renamed',kind:'group',sessionId:'session',chatId:'canonical-chat'});f.status({mode:'native',authorityEpoch:epoch,target:'renamed'});
 assert.equal(f.runtime.getProcessor(key),processor);assert.equal(f.counts.closed,0);assert.equal(f.counts.processor,1);
 assert.equal(f.runtime.acceptsHint({event:'native-group-text-observed',nativeChatKey:key,kind:'group',target:'room',authorityEpoch:epoch}),false);
 assert.equal(f.runtime.acceptsHint({event:'native-group-text-observed',nativeChatKey:key,kind:'group',target:'renamed',authorityEpoch:epoch}),true);
 assert.deepEqual([...f.runtime.guardedTargets()].sort(),['renamed','room']);
});

test('session/chat/epoch changes immediately revoke activity and cannot restore the old authority or release UI guard',async()=>{
 for(const change of [{sessionId:'new-session'},{chatId:'other-chat'},{}]){
  const f=fixture();f.set({mode:'native',authorityEpoch:epoch});await f.runtime.restore();
  f.route({target:'room',kind:'group',sessionId:'session',chatId:'canonical-chat',...change});
  if(Object.keys(change).length)assert.equal(f.runtime.active(key),false);
  f.status({mode:'native',authorityEpoch:Object.keys(change).length?epoch:'b'.repeat(32)});
  assert.equal(f.runtime.active(key),false);assert.equal(f.runtime.status().groups[0].mode,'blocked');assert.deepEqual([...f.runtime.guardedTargets()],['room']);
 }
});

test('real processor keeps queued claim across rename and rejects its old prepared envelope before durable submit',async t=>{
 const directory=fs.mkdtempSync(path.resolve('artifacts/group-runtime-candidate-'));t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
 const empty={source:'authenticated-native-group-text-catalog',complete:true,accountId:'fixture',nativeChatKey:key,scopeDigest:nativeGroupTextScopeDigest('fixture',key),
  snapshotId:'baseline',messages:[],unresolved:[],observedNativeIds:[]};
 const inbox=createNativeGroupTextInbox({dataRoot:directory,accountId:'fixture',nativeChatKey:key,now:()=>200000});
 inbox.baseline({catalog:empty,authorityEpoch:epoch,cutoverAtMs:100000});
 const row={nativeId:'wechat-server:1',serverId:'1',nativeChatKey:key,senderId:'member',senderDisplayName:'Member',senderNumericId:1,
  rawText:'fixture',renderedText:'fixture',renderStatus:'raw-preserved',timestampSeconds:101,localId:1,snapshotSourceId:'db/1',direction:'incoming'};
 const current={...empty,snapshotId:'current',messages:[row],observedNativeIds:[row.nativeId]},events=[];
 const f=fixture({inboxFactory:()=>inbox,processorFactory:createNativeGroupTextProcessor,
  clientFactory:()=>({catalog:async()=>current,close(){}}),history:{incoming:async()=>({id:'ledger'})},enqueue:async event=>events.push(event)});
 f.set({mode:'native',authorityEpoch:epoch});await f.runtime.restore();await f.runtime.refresh();const processor=f.runtime.getProcessor(key),old=events[0];
 f.route({target:'renamed',kind:'group',sessionId:'session',chatId:'canonical-chat'});f.status({mode:'native',authorityEpoch:epoch,target:'renamed'});
 assert.throws(()=>processor.markSubmitting([old]),/submit-invalid/);assert.equal(inbox.status().submitted,0);
 assert.equal(processor.release([old]).released,1);await f.runtime.refresh();assert.equal(events[1].target,'renamed');
 assert.equal(processor.markSubmitting([events[1]]).submitted,1);assert.equal(inbox.status().submitted,1);
});
