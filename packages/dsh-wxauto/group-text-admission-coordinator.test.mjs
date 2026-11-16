import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import path from 'node:path';import {createGroupTextAdmissionCoordinator} from './group-text-admission-coordinator.mjs';
function fixture(t){const dataRoot=fs.mkdtempSync(path.resolve('artifacts/new-group-coordinator-'));t.after(()=>fs.rmSync(dataRoot,{recursive:true,force:true}));const record={accountId:'fixture',nativeChatKey:'wechat:r@chatroom',target:'room',sessionId:'same-session',chatId:'canonical'},epoch='c'.repeat(32),calls=[];let active=false,failAck=false;
 const queued={state:'queued',accountId:'fixture',nativeChatKey:record.nativeChatKey,claimId:'a'.repeat(32),claimHash:'b'.repeat(64),messages:[{type:'text',nativeId:'wechat-server:2',text:'fixture'}]};const authority=()=>({version:1,accountId:'fixture',nativeChatKey:record.nativeChatKey,target:'room',mode:'native',authorityEpoch:epoch,admission:{rawClaimId:queued.claimId,rawClaimHash:queued.claimHash}});
 const options={dataRoot,accountId:'fixture',groupInbox:{read:()=>structuredClone(queued),events(_r,_q,h){assert.equal(h.verified,true);calls.push('handoff-validated');return []}},textAdmission:{async prepare(){calls.push('delegate');queued.textDelegation={accountId:'fixture',nativeChatKey:record.nativeChatKey,claimId:queued.claimId,claimHash:queued.claimHash,authorityEpoch:epoch,ownershipMode:'new-admission',texts:[{claimId:'d'.repeat(32)}]};return queued;}},getRoute:()=>({...record,kind:'group',active}),runtime:{async restore(){calls.push('restore')},active:()=>active},requestAdmit:async()=>{calls.push('admit');if(failAck)throw Error('lost ack');return authority()},requestStatus:async()=>{calls.push('status');return authority()},pump:async()=>calls.push('pump')};
 const callbacks={restoreUiBinding:async(record,authorityEpoch)=>({nativeMonitorRestored:true,actualUiGroupConfirmed:true,accountId:record.accountId,nativeChatKey:record.nativeChatKey,nativeTextAuthorityEpoch:authorityEpoch}),activateRoute:async()=>{calls.push('persist-active');active=true},deliverLegacy:async()=>calls.push('raw-submit-no-text'),enableRoute:async()=>calls.push('enable')};return {dataRoot,record,queued,calls,options,callbacks,coordinator:createGroupTextAdmissionCoordinator(options),loseAck:()=>failAck=true};}
test('delegation precedes verified UI handoff, admit precedes active parent and shared pump',async t=>{const f=fixture(t);await f.coordinator.prepare(f.record,f.queued.claimId);await f.coordinator.commit(f.record,f.queued.claimId,{verified:true,ok:true,actualUiGroupConfirmed:true,checkedThisCall:true,originalChatRestored:true,nativeQueuedHandoff:true,nativeClaimId:f.queued.claimId,nativeClaimHash:f.queued.claimHash,nativeRenderedMessages:f.queued.messages.map(m=>({nativeId:m.nativeId,text:m.text}))},f.callbacks);assert.deepEqual(f.calls,['delegate','handoff-validated','admit','persist-active','raw-submit-no-text','enable','restore','pump']);assert.equal(f.coordinator.status(f.record).phase,'complete');});
test('lost admit ACK restarts from durable attempt and readonly matching marker only, never re-register/admit',async t=>{const f=fixture(t);await f.coordinator.prepare(f.record,f.queued.claimId);f.loseAck();await assert.rejects(()=>f.coordinator.commit(f.record,f.queued.claimId,{verified:true,ok:true,actualUiGroupConfirmed:true,checkedThisCall:true,originalChatRestored:true,nativeQueuedHandoff:true,nativeClaimId:f.queued.claimId,nativeClaimHash:f.queued.claimHash,nativeRenderedMessages:f.queued.messages.map(m=>({nativeId:m.nativeId,text:m.text}))},f.callbacks));assert.equal(f.coordinator.status(f.record).phase,'requested');const restarted=createGroupTextAdmissionCoordinator(f.options);assert.equal((await restarted.prepare(f.record,f.queued.claimId)).resume,true);await restarted.resume(f.record,f.callbacks);assert.equal(f.calls.filter(x=>x==='admit').length,1);assert.equal(f.calls.filter(x=>x==='status').length,1);assert.equal(f.calls.filter(x=>x==='delegate').length,1);});
test('changed raw claim never gains pending RPC grant or fallback, no-text retains explicit legacy',async t=>{const f=fixture(t);f.queued.messages=[];assert.equal((await f.coordinator.prepare(f.record,f.queued.claimId)).legacy,true);assert.deepEqual(f.calls,[]);f.queued.messages=[{type:'text',nativeId:'wechat-server:2',text:'fixture'}];await f.coordinator.prepare(f.record,f.queued.claimId);f.loseAck();await assert.rejects(()=>f.coordinator.commit(f.record,f.queued.claimId,{verified:true,ok:true,actualUiGroupConfirmed:true,checkedThisCall:true,originalChatRestored:true,nativeQueuedHandoff:true,nativeClaimId:f.queued.claimId,nativeClaimHash:f.queued.claimHash,nativeRenderedMessages:f.queued.messages.map(m=>({nativeId:m.nativeId,text:m.text}))},f.callbacks));f.queued.claimHash='e'.repeat(64);await assert.rejects(()=>f.coordinator.resume(f.record,f.callbacks),/claim-changed/);assert.equal(f.calls.includes('persist-active'),false);});

test('existing native inbox ownership uses readonly status, tolerates old admission and journals each raw batch separately',async t=>{
 const f=fixture(t);let nativeEpoch='c'.repeat(32);
 f.options.textAdmission.prepare=async()=>{f.calls.push('delegate');f.queued.textDelegation={accountId:'fixture',nativeChatKey:f.record.nativeChatKey,claimId:f.queued.claimId,claimHash:f.queued.claimHash,authorityEpoch:'c'.repeat(32),ownershipMode:'existing-inbox',texts:[{claimId:'d'.repeat(32),state:'submitted'}]};};
 f.options.requestStatus=async()=>{f.calls.push('status');return {version:1,accountId:'fixture',nativeChatKey:f.record.nativeChatKey,target:f.record.target,mode:'native',authorityEpoch:nativeEpoch};};
 const c=createGroupTextAdmissionCoordinator(f.options);
 await c.prepare(f.record,f.queued.claimId);await c.commit(f.record,f.queued.claimId,{verified:true,ok:true,actualUiGroupConfirmed:true,checkedThisCall:true,originalChatRestored:true,nativeQueuedHandoff:true,nativeClaimId:f.queued.claimId,nativeClaimHash:f.queued.claimHash,nativeRenderedMessages:f.queued.messages.map(m=>({nativeId:m.nativeId,text:m.text}))},f.callbacks);
 assert.equal(f.calls.includes('admit'),false);assert.equal(c.status(f.record).resumeRequired,false);
 f.queued.claimId='1'.repeat(32);f.queued.claimHash='2'.repeat(64);delete f.queued.textDelegation;
 assert.equal((await c.prepare(f.record,f.queued.claimId)).delegated,true);
 await c.commit(f.record,f.queued.claimId,{verified:true,ok:true,actualUiGroupConfirmed:true,checkedThisCall:true,originalChatRestored:true,nativeQueuedHandoff:true,nativeClaimId:f.queued.claimId,nativeClaimHash:f.queued.claimHash,nativeRenderedMessages:f.queued.messages.map(m=>({nativeId:m.nativeId,text:m.text}))},f.callbacks);
 assert.equal(f.calls.filter(x=>x==='status').length,2);assert.equal(f.calls.filter(x=>x==='raw-submit-no-text').length,2);
});
test('existing-inbox without matching native authority stays durable pending, never fallback or admit',async t=>{
 const f=fixture(t);await f.coordinator.prepare(f.record,f.queued.claimId);f.queued.textDelegation.ownershipMode='existing-inbox';
 f.options.requestStatus=async()=>({version:1,accountId:'fixture',nativeChatKey:f.record.nativeChatKey,target:f.record.target,mode:'ui',authorityEpoch:'c'.repeat(32)});
 const c=createGroupTextAdmissionCoordinator(f.options);
 await assert.rejects(()=>c.commit(f.record,f.queued.claimId,{verified:true,ok:true,actualUiGroupConfirmed:true,checkedThisCall:true,originalChatRestored:true,nativeQueuedHandoff:true,nativeClaimId:f.queued.claimId,nativeClaimHash:f.queued.claimHash,nativeRenderedMessages:f.queued.messages.map(m=>({nativeId:m.nativeId,text:m.text}))},f.callbacks),/native-outcome-unverified/);
 assert.equal(f.calls.includes('admit'),false);assert.equal(f.calls.includes('persist-active'),false);assert.equal(c.status(f.record).resumeRequired,true);
});
test('handled-only text remains legacy without creating a text authority or journal',async t=>{
 const f=fixture(t);f.queued.messages=[{type:'text',serverId:'already'}];f.queued.handledServerIds=['already'];
 assert.equal((await f.coordinator.prepare(f.record,f.queued.claimId)).legacy,true);assert.deepEqual(f.calls,[]);
});
test('fully delegated new source still requires original worker handoff before any durable admit attempt',async t=>{
 const f=fixture(t);await f.coordinator.prepare(f.record,f.queued.claimId);
 await assert.rejects(()=>f.coordinator.commit(f.record,f.queued.claimId,undefined,f.callbacks),/handoff-unverified/);
 assert.equal(f.calls.includes('admit'),false);assert.equal(f.coordinator.status(f.record).phase,'unissued');
});
test('no queued raw claim restores monitor through exact native epoch proof without admit or raw history replay',async t=>{
 const f=fixture(t);f.options.groupInbox.read=()=>null;f.options.readTextStatus=()=>({initialized:true,accountId:f.record.accountId,nativeChatKey:f.record.nativeChatKey,authorityEpoch:'c'.repeat(32)});
 const callbacks={...f.callbacks,deliverLegacy:async()=>f.calls.push('raw-noop'),restoreUiBinding:async(record,epoch)=>{f.calls.push('restore-ui-binding');return {nativeMonitorRestored:true,actualUiGroupConfirmed:true,accountId:record.accountId,nativeChatKey:record.nativeChatKey,nativeTextAuthorityEpoch:epoch};}};
 const c=createGroupTextAdmissionCoordinator(f.options);await c.restoreMonitor(f.record,callbacks);
 assert.deepEqual(f.calls,['status','restore-ui-binding','persist-active','raw-noop','enable','restore','pump']);assert.equal(c.status(f.record).phase,'unissued');
});
test('native UI binding mismatch stops before parent activation, even after matching source epoch',async t=>{
 const f=fixture(t);f.options.groupInbox.read=()=>null;f.options.readTextStatus=()=>({initialized:true,accountId:f.record.accountId,nativeChatKey:f.record.nativeChatKey,authorityEpoch:'c'.repeat(32)});
 const c=createGroupTextAdmissionCoordinator(f.options);await assert.rejects(()=>c.restoreMonitor(f.record,{...f.callbacks,restoreUiBinding:async()=>({nativeMonitorRestored:true,actualUiGroupConfirmed:true,accountId:f.record.accountId,nativeChatKey:f.record.nativeChatKey,nativeTextAuthorityEpoch:'f'.repeat(32)})}),/ui-binding-restore-unverified/);
 assert.equal(f.calls.includes('persist-active'),false);assert.equal(f.calls.includes('admit'),false);
});
test('new handoff accepts authenticated sender prefix stripped rendering, but retains exact source IDs',async t=>{
 const f=fixture(t);f.queued.messages=[{type:'text',nativeId:'wechat-server:2',text:'[内部标识已隐藏]:\nhello'}];await f.coordinator.prepare(f.record,f.queued.claimId);
 const h={verified:true,ok:true,actualUiGroupConfirmed:true,checkedThisCall:true,originalChatRestored:true,nativeQueuedHandoff:true,nativeClaimId:f.queued.claimId,nativeClaimHash:f.queued.claimHash,nativeRenderedMessages:[{nativeId:'wechat-server:2',text:'hello'}]};
 await f.coordinator.commit(f.record,f.queued.claimId,h,f.callbacks);assert.equal(f.calls.filter(x=>x==='admit').length,1);
});

test('admission barrier holds through raw submission and parallel pumps until worker enable ACK',async t=>{
 const f=fixture(t);let pending=false,enabled=false,asks=0;
 const tryPump=()=>{if(!pending&&enabled)asks++;};
 f.options.runtime={restore:async()=>{assert.equal(enabled,true);assert.equal(pending,false);f.calls.push('restore')},active:()=>enabled&&!pending};
 const c=createGroupTextAdmissionCoordinator(f.options);await c.prepare(f.record,f.queued.claimId);
 const h={verified:true,ok:true,actualUiGroupConfirmed:true,checkedThisCall:true,originalChatRestored:true,nativeQueuedHandoff:true,nativeClaimId:f.queued.claimId,nativeClaimHash:f.queued.claimHash,nativeRenderedMessages:f.queued.messages.map(m=>({nativeId:m.nativeId,text:m.text}))};
 await c.commit(f.record,f.queued.claimId,h,{activateRoute:async()=>{pending=true;tryPump();},deliverLegacy:async()=>{tryPump();await Promise.resolve();tryPump();assert.equal(asks,0);},enableRoute:async()=>{tryPump();assert.equal(asks,0);enabled=true;pending=false;}});
 assert.equal(asks,0);tryPump();assert.equal(asks,1);
});
test('worker enable failure retains admission barrier, never activates processor or pumps',async t=>{
 const f=fixture(t);let pending=false,active=false,restored=0;f.options.runtime={restore:async()=>{restored++},active:()=>active};
 const c=createGroupTextAdmissionCoordinator(f.options);await c.prepare(f.record,f.queued.claimId);
 const h={verified:true,ok:true,actualUiGroupConfirmed:true,checkedThisCall:true,originalChatRestored:true,nativeQueuedHandoff:true,nativeClaimId:f.queued.claimId,nativeClaimHash:f.queued.claimHash,nativeRenderedMessages:f.queued.messages.map(m=>({nativeId:m.nativeId,text:m.text}))};
 await assert.rejects(()=>c.commit(f.record,f.queued.claimId,h,{activateRoute:async()=>{pending=true;active=true},deliverLegacy:async()=>{},enableRoute:async()=>{active=false;throw Error('enable denied')}}),/enable denied/);
 assert.equal(pending,true);assert.equal(active,false);assert.equal(restored,0);assert.equal(f.calls.includes('pump'),false);assert.equal(c.status(f.record).resumeRequired,true);
});

function handoff(f){return {verified:true,ok:true,actualUiGroupConfirmed:true,checkedThisCall:true,originalChatRestored:true,nativeQueuedHandoff:true,nativeClaimId:f.queued.claimId,nativeClaimHash:f.queued.claimHash,nativeRenderedMessages:f.queued.messages.map(m=>({nativeId:m.nativeId,text:m.text}))};}
function notIssuedFixture(t){
 const f=fixture(t),brands=new WeakMap();let issued=0,ready=true,tick=100000,alwaysBusy=false;
 const proof={workerInstanceId:'1'.repeat(32),requestId:'actual-fixture-rpc-id',requestFingerprint:'f'.repeat(64)};
 f.options.consumeNotIssued=e=>{const value=brands.get(e);brands.delete(e);return value??null;};
 f.options.requestAdmit=async()=>{issued++;if(issued===1||alwaysBusy){const e=Error('group-text-worker-busy');brands.set(e,proof);throw e;}return {version:1,...f.record,mode:'native',authorityEpoch:'c'.repeat(32),admission:{rawClaimId:f.queued.claimId,rawClaimHash:f.queued.claimHash}};};
 f.options.requestStatus=async()=>({version:1,...f.record,mode:'ui',workerInstanceId:proof.workerInstanceId,admitReady:ready});f.options.now=()=>tick;f.options.wait=async ms=>{tick+=ms};f.options.retryWaitMs=250;
 return {...f,issued:()=>issued,ready:v=>ready=v,alwaysBusy:()=>alwaysBusy=true};
}
test('real not-issued proof retries same claim once automatically, without re-registering UI',async t=>{
 const f=notIssuedFixture(t),c=createGroupTextAdmissionCoordinator(f.options);await c.prepare(f.record,f.queued.claimId);await c.commit(f.record,f.queued.claimId,handoff(f),f.callbacks);
 assert.equal(f.issued(),2);assert.equal(c.status(f.record).phase,'complete');
});
test('busy wait timeout retains in-memory permission and next same-worker resume automatically completes',async t=>{
 const f=notIssuedFixture(t);f.ready(false);const c=createGroupTextAdmissionCoordinator(f.options);await c.prepare(f.record,f.queued.claimId);
 await assert.rejects(()=>c.commit(f.record,f.queued.claimId,handoff(f),f.callbacks),/unissued-wait-pending/);assert.equal(c.status(f.record).phase,'unissued');assert.equal(f.issued(),1);
 f.ready(true);await c.resume(f.record,f.callbacks);assert.equal(f.issued(),2);assert.equal(c.status(f.record).phase,'complete');
});
test('second definite busy exhausts the only retry, while unknown ACK never gets a retry',async t=>{
 const f=notIssuedFixture(t);f.alwaysBusy();const c=createGroupTextAdmissionCoordinator(f.options);await c.prepare(f.record,f.queued.claimId);
 await assert.rejects(()=>c.commit(f.record,f.queued.claimId,handoff(f),f.callbacks),/retry-exhausted/);await assert.rejects(()=>c.resume(f.record,f.callbacks),/retry-exhausted/);assert.equal(f.issued(),2);
 const unknown=fixture(t);unknown.loseAck();const u=createGroupTextAdmissionCoordinator(unknown.options);await u.prepare(unknown.record,unknown.queued.claimId);await assert.rejects(()=>u.commit(unknown.record,unknown.queued.claimId,handoff(unknown),unknown.callbacks));assert.equal(u.status(unknown.record).phase,'requested');assert.equal(unknown.calls.filter(x=>x==='admit').length,1);
});
test('restart cannot recover a retry brand from JSON and needs a newly confirmed exact raw UI handoff',async t=>{
 const f=notIssuedFixture(t);f.ready(false);const c=createGroupTextAdmissionCoordinator(f.options);await c.prepare(f.record,f.queued.claimId);await assert.rejects(()=>c.commit(f.record,f.queued.claimId,handoff(f),f.callbacks),/wait-pending/);
 c.close();f.ready(true);const restarted=createGroupTextAdmissionCoordinator(f.options);await assert.rejects(()=>restarted.resume(f.record,f.callbacks),/reconfirmation-required/);assert.equal(f.issued(),1);
 let confirmed=0;await restarted.resume(f.record,{...f.callbacks,reconfirmUiHandoff:async()=>{confirmed++;return handoff(f)}});assert.equal(confirmed,1);assert.equal(f.issued(),2);
});
test('changed raw binding and failure to persist unissued evidence cannot authorize a retry',async t=>{
 const f=notIssuedFixture(t);f.ready(false);const c=createGroupTextAdmissionCoordinator(f.options);await c.prepare(f.record,f.queued.claimId);await assert.rejects(()=>c.commit(f.record,f.queued.claimId,handoff(f),f.callbacks),/wait-pending/);f.queued.claimHash='0'.repeat(64);await assert.rejects(()=>c.resume(f.record,f.callbacks),/claim-changed/);assert.equal(f.issued(),1);
 const g=notIssuedFixture(t),d=createGroupTextAdmissionCoordinator(g.options);await d.prepare(g.record,g.queued.claimId);const original=fs.renameSync;
 fs.renameSync=()=>{throw Error('fixture journal unavailable')};try{await assert.rejects(()=>d.commit(g.record,g.queued.claimId,handoff(g),g.callbacks),/journal unavailable/);}finally{fs.renameSync=original;}assert.equal(g.issued(),1);assert.equal(d.status(g.record).phase,'requested');
});
test('unknown ACK of the only retry stays requested and never issues a third admit',async t=>{
 const f=notIssuedFixture(t),original=f.options.requestAdmit;f.options.requestAdmit=async args=>{const value=await original(args);if(f.issued()===2)throw Error('retry outcome unknown');return value;};
 const c=createGroupTextAdmissionCoordinator(f.options);await c.prepare(f.record,f.queued.claimId);await assert.rejects(()=>c.commit(f.record,f.queued.claimId,handoff(f),f.callbacks),/retry outcome unknown/);
 assert.equal(c.status(f.record).phase,'requested');await assert.rejects(()=>c.resume(f.record,f.callbacks),/native-outcome-unverified/);assert.equal(f.issued(),2);
});
