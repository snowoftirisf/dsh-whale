import test from 'node:test';
import assert from 'node:assert/strict';
import {consumeOwnerSocialCommand,parseOwnerSocialCommand,socialRpcClient,socialToolDefinitions,registerSocialTools} from './social-tools.mjs';

const planId='1'.repeat(32),hash='a'.repeat(64);
const ownerEvent={event:'message',id:'owner-message',kind:'friend',target:'owner',mediaType:'text',text:`批准操作 ${planId} ${hash}`};

test('tools have no approve/reject entry and target is derived from session',async()=>{
  const calls=[];
  const tools=socialToolDefinitions({request:async(...args)=>{calls.push(args);return {ok:true}},routeForSession:id=>id==='owner-session'?{kind:'friend',target:'owner'}:null});
  assert.deepEqual(tools.map(tool=>tool.name),['whale_social_status','whale_social_draft','whale_social_inspect','whale_social_execute']);
  await tools[1].execute({action:'add_friend',parameters:{keywords:'person'}},{agent:{session:{id:'owner-session'}}});
  assert.equal(calls[0][1].target,'owner');
  assert.throws(()=>tools[1].execute({action:'add_friend',parameters:{keywords:'person'},target:'other'},{agent:{session:{id:'owner-session'}}}));
  assert.throws(()=>tools[0].execute({},{agent:{session:{id:'unknown'}}}),/session-denied/);
  assert.throws(()=>tools[3].execute({planId,hash,parameters:{text:'changed'}},{agent:{session:{id:'owner-session'}}}));
});
test('register helper returns cleanup for all tools',()=>{
  let created=0,removed=0;
  const remove=registerSocialTools({tools:{register:()=>{created++;return()=>removed++}}},{request:()=>{},routeForSession:()=>null});
  assert.equal(created,4);remove();assert.equal(removed,4);
});
test('RPC envelopes correlate responses and reject unavailable/closed worker',async()=>{
  const writes=[];
  const client=socialRpcClient({write:(line,callback)=>{writes.push(JSON.parse(line));callback?.()},timeoutMs:1000});
  const result=client.request('social-status',{target:'owner'});
  const request=writes[0];assert.match(request.id,/^social:/);
  assert.equal(client.handle({event:'ack',id:request.id,ok:true}),false);
  assert.equal(client.handle({event:'social-result',id:request.id,ok:true,value:{nativeBackendReady:false}}),true);
  assert.deepEqual(await result,{nativeBackendReady:false});
  const failure=client.request('social-inspect',{target:'owner',planId});
  client.handle({event:'social-result',id:writes[1].id,ok:false,code:'social-plan-not-found'});
  await assert.rejects(failure,/plan-not-found/);
  client.close();await assert.rejects(client.request('social-status'),/worker-not-ready/);
  const unavailable=socialRpcClient({write:()=>assert.fail('must not write'),isReady:()=>false});
  await assert.rejects(unavailable.request('social-status'),/worker-not-ready/);
});
test('RPC timeout/write failure/exit are not automatically retried',async()=>{
  let writes=0;
  const client=socialRpcClient({write:()=>writes++,timeoutMs:5});
  await assert.rejects(client.request('social-execute',{target:'owner',planId,hash}),/outcome-unknown/);
  assert.equal(writes,1);client.close();
  const failed=socialRpcClient({write:(_line,callback)=>callback(Error('broken'))});
  await assert.rejects(failed.request('social-status'),/write-failed/);failed.close();
  const exited=socialRpcClient({write:()=>{}});const pending=exited.request('social-status');exited.close();await assert.rejects(pending,/worker-exited/);
});
test('only direct owner private text consumes explicit approval',async()=>{
  for(const change of [{kind:'group'},{target:'other'},{mediaType:'image'},{mediaType:'quote'},{source:'history'},{source:'self-account'},{source:'snapshot'},{direction:'outgoing'},{text:'他说'+ownerEvent.text}]){
    const result=await consumeOwnerSocialCommand({...ownerEvent,...change},{ownerTarget:'owner',request:()=>assert.fail('must not request')});
    assert.equal(result.handled,false);
  }
  assert.equal(parseOwnerSocialCommand(`批准操作 ${planId}`),null);
});
test('explicit approval forwards actual message id then cannot claim unverified backend worked',async()=>{
  const calls=[];
  const result=await consumeOwnerSocialCommand(ownerEvent,{ownerTarget:'owner',request:async(op,args)=>{
    calls.push([op,args]);return op==='social-approve'?{status:'approved'}:{status:'approved',executionStatus:'unverified'};
  }});
  assert.equal(result.handled,true);assert.match(result.reply,/未执行/);
  assert.equal(calls[0][0],'social-approve');assert.equal(calls[0][1].approvalMessageId,'owner-message');
  assert.equal(calls[1][0],'social-execute');assert.equal(calls[1][1].target,'owner');
});
test('expired approval and rejection never dispatch execute',async()=>{
  let calls=0;
  const expired=await consumeOwnerSocialCommand(ownerEvent,{ownerTarget:'owner',request:async()=>{calls++;return {status:'expired'}}});
  assert.equal(calls,1);assert.match(expired.reply,/过期/);
  const rejected=await consumeOwnerSocialCommand({...ownerEvent,text:`拒绝操作 ${planId} ${hash}`},{ownerTarget:'owner',request:async op=>{assert.equal(op,'social-reject');return {status:'rejected'}}});
  assert.equal(rejected.execution,null);assert.match(rejected.reply,/未执行/);
});

test('a prepared first-contact test can acknowledge actual private approval without executing an unverified normal action',async()=>{
  const calls=[];
  const result=await consumeOwnerSocialCommand(ownerEvent,{ownerTarget:'owner',request:async(op,args)=>{
    calls.push([op,args]);return {id:planId,hash,action:'add_friend',status:'approved'};
  },deferExecution:async decision=>decision.id===planId&&decision.hash===hash});
  assert.equal(result.handled,true);assert.equal(result.execution,null);assert.match(result.reply,/尚未开始/);
  assert.deepEqual(calls.map(item=>item[0]),['social-approve']);
});
test('approval transport failure remains consumed and never falls through to a model reply',async()=>{
  let calls=0;
  const result=await consumeOwnerSocialCommand(ownerEvent,{ownerTarget:'owner',request:async()=>{calls++;throw Error('social-outcome-unknown')}});
  assert.equal(result.handled,true);assert.equal(calls,1);assert.equal(result.errorCode,'social-outcome-unknown');
  assert.match(result.reply,/未自动重试/);assert.equal(result.execution,null);
});
