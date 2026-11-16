import test from 'node:test';
import assert from 'node:assert/strict';
import {preparedFriendLookup} from './friend-lookup-plan.mjs';
import {createFriendAcceptanceActions} from './friend-acceptance.mjs';
const planId='1'.repeat(32),hash='a'.repeat(64);
const plan={id:planId,hash,action:'add_friend',status:'pending',expiresAt:2000,
  origin:{kind:'friend',target:'owner'},parameters:{keywords:'_Test50K',addmsg:'hello'}};
function options(extra={}){return {readPointer:()=>({planId,hash}),ownerTarget:'owner',now:()=>1000000,
  request:async()=>plan,...extra};}
test('literal candidate remains unchanged and only the configured pointer is inspected',async()=>{
  const calls=[];const value=await preparedFriendLookup(options({request:async(...args)=>{calls.push(args);return plan}}));
  assert.equal(value.account,'_Test50K');assert.equal(value.status,'pending');
  assert.deepEqual(calls,[['social-inspect',{target:'owner',planId}]]);
});

test('projection distinguishes full-request approval from a claimed initial-only notice without leaking codes',async()=>{
  for(const purpose of [undefined,'add-friend-request','untrusted']){
    const approvalNotice={state:'claimed',noticeId:'c'.repeat(32),approveCode:'private',...(purpose?{purpose}:{})};
    const value=await preparedFriendLookup(options({request:async()=>({...plan,approvalNotice})}));
    assert.equal(value.approvalNoticeClaimed,true);
    assert.equal(value.approvalPurpose,purpose==='add-friend-request'?purpose:undefined);
    assert.equal(Object.hasOwn(value,'approvalNotice'),false);
    assert.equal(Object.hasOwn(value,'approveCode'),false);
  }
});
test('changed plan, expiry, group origin and wrong action cannot produce lookup candidates',async()=>{
  for(const changed of [{...plan,hash:'b'.repeat(64)},{...plan,status:'confirmed'},{...plan,expiresAt:900},
    {...plan,origin:{kind:'group',target:'group'}},{...plan,action:'accept_friend'}]){
    await assert.rejects(preparedFriendLookup(options({request:async()=>changed})),/prepared-plan-mismatch/);
  }
});
test('malformed pointer or user supplied target never reaches the worker',async()=>{
  for(const pointer of [{planId,hash,target:'other'},{planId:'../private',hash},null]){
    await assert.rejects(preparedFriendLookup(options({readPointer:()=>pointer,request:()=>assert.fail('no RPC')})),/unavailable/);
  }
});

test('owner-approved terminal plans remain readable without becoming executable or approving a new action',async()=>{
  for(const status of ['failed','unknown','confirmed']){
    const terminal={...plan,status,approvedBy:'owner',approvalMessageId:'actual-owner-input',
      parameters:{...plan.parameters,expectedProfileName:'Fixture Friend'}};
    const value=await preparedFriendLookup(options({request:async()=>terminal}));
    assert.equal(value.status,status);assert.equal(value.account,plan.parameters.keywords);
    assert.equal(Object.hasOwn(value,'approvalMessageId'),false);
    const actions=createFriendAcceptanceActions({getPreparedPlan:async()=>value,ownerTarget:'owner',
      request:()=>assert.fail('terminal plan cannot dispatch a write or fresh approval'),sendNotice:()=>assert.fail('no new card')});
    await assert.rejects(actions.acceptance({planId,hash}),/owner-approval-required/);
    await assert.rejects(actions.approval({planId,hash}),/owner-approval-required/);
    for(const rejected of [{...terminal,approvedBy:'other'},{...terminal,approvalMessageId:''},
      {...terminal,status:'rejected'},{...terminal,status:'expired'},{...terminal,expiresAt:900}])
      await assert.rejects(preparedFriendLookup(options({request:async()=>rejected})),/prepared-plan-mismatch/);
  }
});
test('expected emoji nickname is preserved and uses the same codepoint limit as the plan store',async()=>{
  const nickname='🛌'.repeat(128);
  const value=await preparedFriendLookup(options({request:async()=>({...plan,parameters:{...plan.parameters,expectedProfileName:nickname}})}));
  assert.equal(value.expectedProfileName,nickname);
  await assert.rejects(preparedFriendLookup(options({request:async()=>({...plan,parameters:{...plan.parameters,expectedProfileName:nickname+'x'}})})),/mismatch/);
});

test('recorded pending verification is visible without reopening an unknown plan for execution',async()=>{
  const observation={source:'wxauto-uia',requestState:'pending-verification',friendshipConfirmed:false,
    automaticRetry:false,normalCapabilityPromoted:false,observedAt:'1970-01-01T00:15:00.000Z',
    evidencePath:`backups/social-initial-contact/${planId}/verified-request-pending.json`,evidenceSha256:'c'.repeat(64)};
  const terminal={...plan,createdAt:500,status:'unknown',approvedBy:'owner',approvalMessageId:'real-native-input',
    parameters:{...plan.parameters,expectedProfileName:'Fixture Friend'},postActionObservation:observation};
  const value=await preparedFriendLookup(options({request:async()=>terminal}));
  assert.equal(value.status,'unknown');
  assert.deepEqual(value.lastRequestObservation,{state:'pending-verification',observedAt:observation.observedAt});
  assert.equal(Object.hasOwn(value,'approvalMessageId'),false);
  assert.equal(Object.hasOwn(value,'evidencePath'),false);
  const actions=createFriendAcceptanceActions({getPreparedPlan:async()=>value,ownerTarget:'owner',
    request:()=>assert.fail('historical pending observation cannot authorize a new request'),sendNotice:()=>assert.fail('no duplicate card')});
  await assert.rejects(actions.acceptance({planId,hash}),/owner-approval-required/);
  for(const bad of [{evidencePath:`backups/social-initial-contact/${'2'.repeat(32)}/verified-request-pending.json`},
    {friendshipConfirmed:true},{automaticRetry:true},{observedAt:'invalid'},{observedAt:'1970-01-01T00:30:00.000Z'},
    {observedAt:'1970-01-01T00:00:10.000Z'},{evidenceSha256:'not-a-hash'}]){
    const candidate=await preparedFriendLookup(options({request:async()=>({...terminal,postActionObservation:{...observation,...bad}})}));
    assert.equal(candidate.status,'unknown');assert.equal(Object.hasOwn(candidate,'lastRequestObservation'),false);
  }
});
