import test from 'node:test';
import assert from 'node:assert/strict';
import {finishNativeOwnerDecision} from './native-owner-decision.mjs';

const event={sourceKind:'wechat-native-text',kind:'friend',target:'owner',direction:'incoming',mediaType:'text',
  nativeOwnerTextClaimId:'1'.repeat(32),authorityEpoch:'2'.repeat(32),nativeId:'wechat-server:1234'};
const result={handled:true,decisionStatus:'approved',planId:'3'.repeat(32),hash:'a'.repeat(64),
  action:'add_friend',approvalMessageId:event.nativeId};
const plan={id:result.planId,hash:result.hash,action:result.action,status:'approved',approvedBy:'owner',
  approvalMessageId:event.nativeId,origin:{kind:'friend',target:'owner'}};

test('native approval inspects exact current plan and executes existing generic policy without reapproving',async()=>{
  const calls=[];
  const value=await finishNativeOwnerDecision(event,result,{ownerTarget:'owner',request:async(op,args)=>{
    calls.push([op,args]);return op==='social-inspect'?plan:{status:'confirmed'};}});
  assert.deepEqual(calls,[['social-inspect',{target:'owner',planId:plan.id}],['social-execute',{target:'owner',planId:plan.id,hash:plan.hash}]]);
  assert.match(value.reply,/执行并核实/);
});

test('prepared friend acceptance remains deferred and rejected or unknown decisions never execute',async()=>{
  let calls=0;
  const options={ownerTarget:'owner',request:async op=>{assert.equal(op,'social-inspect');calls++;return plan;},deferExecution:async()=>true};
  const deferred=await finishNativeOwnerDecision(event,result,options);assert.match(deferred.reply,/当前未执行添加/);assert.equal(calls,1);
  await finishNativeOwnerDecision(event,{...result,decisionStatus:'rejected'},{...options,request:()=>assert.fail()});
  await finishNativeOwnerDecision(event,{handled:true,decisionStatus:'unknown'},{...options,request:()=>assert.fail()});
});

test('wrong owner, native identity or changed approved plan cannot redirect or repeat execution',async()=>{
  for(const change of [{target:'group'},{direction:'outgoing'},{sourceKind:'snapshot'},{nativeId:'invalid'}])
    await finishNativeOwnerDecision({...event,...change},result,{ownerTarget:'owner',request:()=>assert.fail()});
  for(const change of [{hash:'b'.repeat(64)},{approvalMessageId:'other-native'},{approvedBy:'other'},
    {origin:{kind:'group',target:'owner'}},{status:'unknown'},{status:'claimed'},{status:'expired'}]){
    let calls=0;await finishNativeOwnerDecision(event,result,{ownerTarget:'owner',request:async op=>{
      calls++;assert.equal(op,'social-inspect');return {...plan,...change};}});assert.equal(calls,1);
  }
});

test('postdecision transport uncertainty returns one fixed reply and does not retry',async()=>{
  let calls=0;
  const value=await finishNativeOwnerDecision(event,result,{ownerTarget:'owner',request:async()=>{calls++;throw Error('private failure');}});
  assert.equal(calls,1);assert.equal(value.execution,null);assert.equal(value.reply.includes('private'),false);
});
