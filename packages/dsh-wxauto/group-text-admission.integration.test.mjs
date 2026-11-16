import {createHash} from 'node:crypto';
import {groupTextAdmissionRpcClient} from './group-text-admission-rpc.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';import path from 'node:path';
import {createNativeGroupInbox} from './native-group-inbox.text-candidate.mjs';
import {createNativeGroupTextInbox} from './native-group-text-inbox.admission-candidate.mjs';
import {createNativeGroupTextAdmission} from './native-group-text-admission.mjs';
import {createGroupTextAdmissionCoordinator} from './group-text-admission-coordinator.mjs';
import {nativeGroupTextScopeDigest} from './group-text-contract.mjs';
function fixture(t,existing=false){
 const dataRoot=fs.mkdtempSync(path.resolve('artifacts/admission-coordinator-integration-'));t.after(()=>fs.rmSync(dataRoot,{recursive:true,force:true}));
 const record={accountId:'account',nativeChatKey:'wechat:room@chatroom',target:'room',sessionId:'session',chatId:'canonical'};
 const raw=createNativeGroupInbox({dataRoot,accountId:'account',textDelegation:true});
 const inbox=createNativeGroupTextInbox({dataRoot,accountId:'account',nativeChatKey:record.nativeChatKey,now:()=>200000});
 const msg=id=>({nativeId:'wechat-server:'+id,serverId:String(id),senderId:'wxid_member',senderDisplayName:'Member',type:'text',text:'fixture '+id,timestampSeconds:100+id,snapshotSourceId:'db/'+id,direction:'incoming',contextOnly:false});
 const cm=id=>{const m=msg(id);return {...m,nativeChatKey:record.nativeChatKey,rawText:m.text,renderedText:m.text,renderStatus:'raw-preserved',senderNumericId:2,localId:id};};
 const catalog={source:'authenticated-native-group-text-catalog',accountId:'account',nativeChatKey:record.nativeChatKey,snapshotId:'snapshot',scopeDigest:nativeGroupTextScopeDigest('account',record.nativeChatKey),complete:true,messages:[cm(1),cm(2)],observedNativeIds:['wechat-server:1','wechat-server:2'],unresolved:[]};
 if(existing)inbox.baseline({catalog:{...catalog,messages:[cm(1)],observedNativeIds:['wechat-server:1']},authorityEpoch:'e'.repeat(32),cutoverAtMs:100000});
 const queued=raw.enqueue(record,{state:'ready',source:'authenticated-wechat-native-unread',accountId:'account',nativeChatKey:record.nativeChatKey,snapshotId:'snapshot',unreadCount:1,serverIds:['2'],messages:[msg(2)]});
 catalog.admission={claimId:queued.claimId,claimHash:queued.claimHash,snapshotId:'snapshot',verified:true};
 let active=false,admitCalls=0,statusCalls=0,buffers=0;const marker={};
 const textAdmission=createNativeGroupTextAdmission({groupInbox:raw,textInbox:inbox,readCatalog:async()=>structuredClone(catalog),now:()=>200000});
 const options={dataRoot,accountId:'account',groupInbox:raw,textAdmission,getRoute:()=>({...record,kind:'group',active}),runtime:{restore:async()=>{},active:()=>active},
  requestAdmit:async a=>{admitCalls++;Object.assign(marker,{version:1,...record,mode:'native',authorityEpoch:a.authorityEpoch,admission:{rawClaimId:a.claimId,rawClaimHash:a.claimHash}});return marker;},
  requestStatus:async()=>{statusCalls++;return existing?{version:1,...record,mode:'native',authorityEpoch:'e'.repeat(32)}:marker;},pump:async()=>{}};
 const callbacks={restoreUiBinding:async(record,authorityEpoch)=>({nativeMonitorRestored:true,actualUiGroupConfirmed:true,accountId:record.accountId,nativeChatKey:record.nativeChatKey,nativeTextAuthorityEpoch:authorityEpoch}),activateRoute:async()=>{active=true},deliverLegacy:async()=>{const q=raw.read(record),events=raw.events(record,q);buffers+=events.length;assert.ok(raw.submit(record,q.claimId));},enableRoute:async()=>{}};
 return {record,raw,inbox,queued,options,callbacks,counts:()=>({admitCalls,statusCalls,buffers})};
}
test('real raw/text inbox + admission helper + coordinator transfers first unread only, raw never emits a duplicate',async t=>{
 const f=fixture(t),c=createGroupTextAdmissionCoordinator(f.options);
 await c.prepare(f.record,f.queued.claimId);await c.commit(f.record,f.queued.claimId,{ok:true,actualUiGroupConfirmed:true,checkedThisCall:true,originalChatRestored:true,nativeQueuedHandoff:true,nativeClaimId:f.queued.claimId,nativeClaimHash:f.queued.claimHash,nativeRenderedMessages:f.queued.messages.map(m=>({nativeId:m.nativeId,text:m.text}))},f.callbacks);
 assert.deepEqual(f.counts(),{admitCalls:1,statusCalls:0,buffers:0});assert.equal(f.raw.read(f.record).state,'submitted');
 const claim=f.inbox.pending()[0];assert.equal(claim.nativeId,'wechat-server:2');assert.equal(f.inbox.lookup(['wechat-server:1'])[0].state,'baseline');
 f.inbox.submit([claim.claimId],{requireAllQueued:true});
 const restart=createGroupTextAdmissionCoordinator(f.options);assert.equal(restart.status(f.record).resumeRequired,false);assert.equal(f.inbox.pending().length,0);
});
test('real existing quiet native inbox owns new raw unread, keeps epoch and never creates a new authority',async t=>{
 const f=fixture(t,true),c=createGroupTextAdmissionCoordinator(f.options);
 await c.prepare(f.record,f.queued.claimId);assert.equal(f.raw.read(f.record).textDelegation.ownershipMode,'existing-inbox');
 await c.commit(f.record,f.queued.claimId,{ok:true,actualUiGroupConfirmed:true,checkedThisCall:true,originalChatRestored:true,nativeQueuedHandoff:true,nativeClaimId:f.queued.claimId,nativeClaimHash:f.queued.claimHash,nativeRenderedMessages:f.queued.messages.map(m=>({nativeId:m.nativeId,text:m.text}))},f.callbacks);
 assert.deepEqual(f.counts(),{admitCalls:0,statusCalls:1,buffers:0});assert.equal(f.inbox.status().authorityEpoch,'e'.repeat(32));assert.equal(f.inbox.pending().length,1);
});



test('actual RPC one-shot busy brand flows through durable coordinator to exactly one retry',async t=>{
 const f=fixture(t);let c,requests=0,statusReads=0;const generation='1'.repeat(32);
 const rpc=groupTextAdmissionRpcClient({workerInstanceId:()=>generation,pendingRoute:args=>c.pendingRoute(args),write:line=>{
  const w=JSON.parse(line);queueMicrotask(()=>{
   if(w.op==='group-text-status'){statusReads++;rpc.handle({event:'group-text-result',id:w.id,ok:true,value:{version:1,...f.record,mode:'ui',workerInstanceId:generation,admitReady:true}});return;}
   requests++;if(requests===1){const fp=createHash('sha256').update(JSON.stringify([w.op,w.id,w.target,w.nativeChatKey,w.claimId,w.claimHash,w.authorityEpoch])).digest('hex');rpc.handle({event:'group-text-result',id:w.id,ok:false,code:'group-text-worker-busy',issuance:'not-issued',phase:'before-admit',workerInstanceId:generation,requestFingerprint:fp});}
   else rpc.handle({event:'group-text-result',id:w.id,ok:true,value:{version:1,...f.record,mode:'native',authorityEpoch:w.authorityEpoch,admission:{rawClaimId:w.claimId,rawClaimHash:w.claimHash}}});
  });
 }});
 f.options.requestAdmit=args=>rpc.request(args);f.options.requestStatus=args=>rpc.status(args);f.options.consumeNotIssued=(e,args)=>rpc.consumeNotIssued(e,args);c=createGroupTextAdmissionCoordinator(f.options);
 await c.prepare(f.record,f.queued.claimId);await c.commit(f.record,f.queued.claimId,{ok:true,actualUiGroupConfirmed:true,checkedThisCall:true,originalChatRestored:true,nativeQueuedHandoff:true,nativeClaimId:f.queued.claimId,nativeClaimHash:f.queued.claimHash,nativeRenderedMessages:f.queued.messages.map(m=>({nativeId:m.nativeId,text:m.text}))},f.callbacks);
 assert.equal(requests,2);assert.equal(statusReads,1);assert.equal(f.raw.read(f.record).state,'submitted');assert.equal(f.inbox.pending().length,1);assert.equal(f.counts().buffers,0);assert.equal(c.status(f.record).phase,'complete');rpc.close();
});
