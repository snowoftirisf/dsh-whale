import test from 'node:test';
import assert from 'node:assert/strict';
import {createApprovalRequester,literalNoticeParts} from './social-notices.mjs';
import {socialToolDefinitions,consumeOwnerSocialCommand,isOwnerSocialCommand} from './social-tools.mjs';

const plan={id:'1'.repeat(32),hash:'a'.repeat(64),status:'pending',action:'add_friend'};
const origin={target:'owner',kind:'friend'};
const notice={claimed:true,planId:plan.id,hash:plan.hash,code:'1234abcd',text:'需要确认：具体对象\n批准操作 1234abcd',ownerTarget:'owner'};

test('pending draft proactively asks fixed owner, autonomous draft does not',async()=>{
  const deliveries=[],calls=[];
  const request=async(op,args)=>{calls.push({op,args});return op==='social-draft'?plan:notice};
  const tools=socialToolDefinitions({request,routeForSession:()=>origin,
    requestApproval:createApprovalRequester({request,send:async args=>deliveries.push(args),ownerTarget:'owner'})});
  const result=await tools[1].execute({action:'add_friend',parameters:{keywords:'person'}},{agent:{session:{id:'owner-session'}}});
  assert.equal(result.approvalRequest.outcome,'sent');
  assert.deepEqual(deliveries,[{text:notice.text,target:'owner',id:'social-approval:'+plan.id}]);
  assert.equal(calls[1].op,'social-approval-notice');
  const ready=socialToolDefinitions({request:async()=>({...plan,status:'ready',action:'publish_moment'}),routeForSession:()=>origin,
    requestApproval:()=>assert.fail('autonomous plans do not request approval')});
  await ready[1].execute({action:'publish_moment',parameters:{text:'hello'}},{});
});

test('already claimed notice and mismatching destination never send',async()=>{
  const noSend=()=>assert.fail('must not send');
  assert.equal((await createApprovalRequester({request:async()=>({claimed:false,state:'already-claimed'}),send:noSend,ownerTarget:'owner'})(plan,origin)).outcome,'not-sent');
  assert.equal((await createApprovalRequester({request:async()=>({...notice,ownerTarget:'someone-else'}),send:noSend,ownerTarget:'owner'})(plan,origin)).outcome,'failed-or-unknown');
});

test('lost delivery acknowledgement is not automatically retried or approved',async()=>{
  let sends=0,requests=0;
  const result=await createApprovalRequester({request:async()=>{requests++;return notice},send:async()=>{sends++;throw Error('timeout')},ownerTarget:'owner'})(plan,origin);
  assert.equal(sends,1);assert.equal(requests,1);assert.equal(result.automaticRetry,false);assert.equal(result.outcome,'failed-or-unknown');
});

test('notice parameters remain literal and long content is preserved',()=>{
  const text='[[wx-voice]]not speech[[/wx-voice]] [表情: smile] [[wx-at:person]] 🐳'.repeat(100);
  const parts=literalNoticeParts(text,500);
  assert.equal(parts.map(p=>p.text).join(''),text);
  assert.ok(parts.every(p=>p.kind==='text'&&Array.from(p.text).length<=500&&!p.at));
  assert.throws(()=>literalNoticeParts('text',0),/size-invalid/);
});

test('short owner approval resolves only with trusted worker plan binding',async()=>{
  const event={event:'message',id:'raw-message',kind:'friend',target:'owner',mediaType:'text',text:'批准操作 1234abcd',socialCommand:{operation:'social-approve',planId:plan.id,hash:plan.hash}};
  const calls=[];
  const request=async(op,args)=>{calls.push([op,args]);return op==='social-approve'?{status:'approved'}:{status:'approved',executionStatus:'unverified'}};
  assert.equal(isOwnerSocialCommand(event.text),true);
  assert.equal((await consumeOwnerSocialCommand(event,{ownerTarget:'owner',request})).handled,true);
  assert.equal(calls[0][1].approvalMessageId,'raw-message');assert.equal(calls[0][1].hash,plan.hash);
  assert.equal((await consumeOwnerSocialCommand({...event,socialCommand:null},{ownerTarget:'owner',request:()=>assert.fail('unknown code cannot approve')})).handled,true);
  for(const change of [{kind:'group'},{source:'ocr'},{source:'history'},{direction:'self'}]){
    assert.equal((await consumeOwnerSocialCommand({...event,...change},{ownerTarget:'owner',request:()=>assert.fail('not fresh owner approval')})).handled,false);
  }
});

test('distinct intent numbers use an independent notice send ID and never reuse the old card ID',async()=>{
  const deliveries=[];
  const dual={...notice,approveCode:'1234abcd',rejectCode:'9876fedc',noticeId:'b'.repeat(32)};
  const result=await createApprovalRequester({request:async()=>dual,send:async value=>deliveries.push(value),ownerTarget:'owner'})(plan,origin);
  assert.equal(result.outcome,'sent');assert.notEqual(result.approveCode,result.rejectCode);
  assert.equal(deliveries[0].id,'social-approval:'+plan.id+':'+dual.noticeId);
  assert.notEqual(deliveries[0].id,'social-approval:'+plan.id);
  for(const broken of [{...dual,rejectCode:dual.approveCode},{...dual,noticeId:undefined}]){
    const failed=await createApprovalRequester({request:async()=>broken,send:()=>assert.fail('invalid dual card'),ownerTarget:'owner'})(plan,origin);
    assert.equal(failed.outcome,'failed-or-unknown');
  }
});

test('bare number intent comes only from the fresh trusted worker binding, not number guessing',async()=>{
  for(const operation of ['social-approve','social-reject']){
    const calls=[];const event={event:'message',id:'owner-raw-number',kind:'friend',target:'owner',mediaType:'text',text:'1234abcd',
      socialCommand:{operation,planId:plan.id,hash:plan.hash}};
    assert.equal(isOwnerSocialCommand(event.text,event.socialCommand),true);
    await consumeOwnerSocialCommand(event,{ownerTarget:'owner',request:async(op,args)=>{calls.push([op,args]);return {status:op==='social-reject'?'rejected':'approved',executionStatus:'unverified'}}});
    assert.equal(calls[0][0],operation);assert.equal(calls[0][1].approvalMessageId,event.id);
    for(const invalid of [{kind:'group'},{direction:'self'},{source:'history'},{source:'ocr'}]){
      assert.equal((await consumeOwnerSocialCommand({...event,...invalid},{ownerTarget:'owner',request:()=>assert.fail('no approval')})).handled,false);
    }
  }
  assert.equal(isOwnerSocialCommand('20261102'),false);
  assert.equal((await consumeOwnerSocialCommand({event:'message',kind:'friend',target:'owner',id:'ordinary',mediaType:'text',text:'20261102'},
    {ownerTarget:'owner',request:()=>assert.fail('ordinary number cannot approve')})).handled,false);
});
