import test from 'node:test';
import assert from 'node:assert/strict';
import {Readable} from 'node:stream';
import {createFriendAcceptanceActions,createFriendRequestAcceptanceActions,registerFriendAcceptance,friendApprovalPath,friendAcceptancePath,
  friendRequestApprovalPath,friendRequestAcceptancePath} from './friend-acceptance.mjs';
const planId='1'.repeat(32),hash='a'.repeat(64),args={planId,hash};
const plan={planId,hash,expectedProfileName:'test-person',status:'pending'};
function setup(extra={}){
  const calls=[],sent=[],results=[];
  return {calls,sent,results,actions:createFriendAcceptanceActions({getPreparedPlan:async()=>plan,ownerTarget:'owner',
    request:async(op,input)=>{calls.push([op,input]);return {claimed:true,planId,hash,code:'a1b2c3d4',ownerTarget:'owner',text:'fixed plan review'}},
    sendNotice:async value=>sent.push(value),onResult:value=>results.push(value),...extra})};
}
test('only the configured name-bound pending plan can send one literal private approval request',async()=>{
  const {actions,calls,sent}=setup();const result=await actions.approval(args);
  assert.equal(result.outcome,'sent');assert.equal(sent.length,1);assert.equal(sent[0].target,'owner');
  assert.match(sent[0].text,/首次加好友功能验收/);assert.match(sent[0].text,/尚未识别/);
  assert.deepEqual(calls.map(item=>item[0]),['social-approval-notice']);
  assert.equal(plan.status,'pending'); // Requesting approval never grants it.
  for(const input of [{...args,keywords:'other'}, {...args,action:'add_friend'}, {...args,hash:'b'.repeat(64)}])
    await assert.rejects(actions.approval(input),/invalid|mismatch/);
  assert.equal(sent.length,1);
});
test('unapproved or changed profile plan never reaches the acceptance worker',async()=>{
  for(const changed of [plan,{...plan,expectedProfileName:''},{...plan,planId:'2'.repeat(32)}]){
    const {actions,calls}=setup({getPreparedPlan:async()=>changed});
    await assert.rejects(actions.acceptance(args),/approval-required|mismatch/);assert.deepEqual(calls,[]);
  }
});
test('acceptance uses fixed purpose and preserves unknown outcome without claiming success',async()=>{
  let status='approved';const calls=[];
  const {actions,results}=setup({getPreparedPlan:async()=>({...plan,status}),request:async(op,input)=>{
    calls.push([op,input]);status='unknown';return {id:planId,hash,status:'unknown',executionStatus:'dispatched',
      resultCode:'social-post-action-evidence-missing',chatRestored:true,parameters:{private:'must omit'},
      diagnosticArtifact:{path:'backups/social-initial-contact/'+planId+'/'+'f'.repeat(32)+'.json',sha256:'b'.repeat(64),kind:'unverified-ui-observation',rawPrivate:'omit'}};
  }});
  const value=await actions.acceptance(args);assert.equal(value.status,'unknown');assert.equal(value.parameters,undefined);
  assert.equal(value.diagnosticArtifact.rawPrivate,undefined);
  assert.deepEqual(calls,[['social-acceptance',{target:'owner',purpose:'add-friend-initial-contact',planId,hash}]]);
  assert.equal(results.length,1);await assert.rejects(actions.acceptance(args),/approval-required/);assert.equal(calls.length,1);
});
test('unconfirmed diagnostic metadata cannot redirect the owner to arbitrary paths',async()=>{
  const {actions}=setup({getPreparedPlan:async()=>({...plan,status:'approved'}),request:async()=>({id:planId,hash,status:'unknown',executionStatus:'dispatched',
    diagnosticArtifact:{path:'../../private',sha256:'b'.repeat(64),kind:'unverified-ui-observation'}})});
  await assert.rejects(actions.acceptance(args),/result-invalid/);
});
test('plain optional click/preflight diagnostics distinguish primary failure and cleanup',async()=>{
  const raw={id:planId,hash,status:'failed',executionStatus:'dispatched',resultCode:'social-backend-preflight-failed',
    addContactClickAttempted:false,preflightObserved:false,failurePhase:'preflight',
    primaryFailureCode:'social-backend-preflight-failed',cleanupFailureCode:'social-chat-restore-failed'};
  const make=value=>setup({getPreparedPlan:async()=>({...plan,status:'approved'}),request:async()=>value}).actions;
  const result=await make(raw).acceptance(args);
  assert.equal(result.addContactClickAttempted,false);assert.equal(result.preflightObserved,false);
  assert.equal(result.primaryFailureCode,'social-backend-preflight-failed');assert.equal(result.cleanupFailureCode,'social-chat-restore-failed');
  const absent=await make({id:planId,hash,status:'approved',executionStatus:'not-dispatched'}).acceptance(args);
  assert.equal(Object.hasOwn(absent,'addContactClickAttempted'),false);
  for(const change of [{addContactClickAttempted:0},{preflightObserved:null},{failurePhase:'private error text'},
    {primaryFailureCode:'social-private-details'},{cleanupFailureCode:'arbitrary'}])
    await assert.rejects(make({...raw,...change}).acceptance(args),/result-invalid/);
});
test('approval remains busy until the private notice send settles and is not retried',async()=>{
  let finish;const {actions,sent}=setup({sendNotice:value=>{sent.push(value);return new Promise(resolve=>{finish=resolve})}});
  const pending=actions.approval(args);while(!finish)await new Promise(resolve=>setImmediate(resolve));
  await assert.rejects(actions.approval(args),/in-progress/);finish();await pending;assert.equal(sent.length,1);
});

function response(){return {writeHead(status){this.status=status},end(text){this.body=JSON.parse(text)}};}
function request(body=args,extra={}){return Object.assign(Readable.from([JSON.stringify(body)]),{
  method:'POST',url:friendApprovalPath,headers:{'content-type':'application/json'},socket:{remoteAddress:'127.0.0.1'},...extra});}
function routes(options={}){
  const http=new Map(),native=new Map();let principal='agent',rejection;
  const connection={admit:()=>({rejection,peer:{principal:{kind:principal}}})};
  registerFriendAcceptance({get:()=>connection,inject(names,run){
    if(names[0]==='webServer')run({effect:fn=>fn(),webServer:{register(route){http.set(route.path,route);return()=>{}}}});
    else run({connection:{fetch:{register(route){native.set(route.path,route)}}}});
  }},{getPreparedPlan:async()=>plan,ownerTarget:'owner',request:async()=>{throw Error('unexpected')},sendNotice:async()=>{},...options});
  return {http,native,setPrincipal:value=>{principal=value},setRejection:value=>{rejection=value}};
}
test('HTTP mutating routes require local authenticated owner before reading any plan',async()=>{
  let calls=0;const r=routes({getPreparedPlan:async()=>{calls++;return plan}}),handler=r.http.get(friendApprovalPath).handler;
  let res=response();await handler(request(),res);assert.equal(res.status,403);assert.equal(calls,0);
  r.setPrincipal('owner');r.setRejection(401);res=response();await handler(request(),res);assert.equal(res.status,401);assert.equal(calls,0);
  r.setRejection(undefined);res=response();await handler(request(args,{socket:{remoteAddress:'10.0.0.1'}}),res);assert.equal(res.status,403);assert.equal(calls,0);
  res=response();await handler(request({...args,action:'publish_moment'}),res);assert.equal(res.status,503);assert.equal(calls,0);
});
test('native routes are exact owner-admitted POST and reject cross-origin and arbitrary action',async()=>{
  let calls=0;const r=routes({getPreparedPlan:async()=>{calls++;return plan}});
  assert.deepEqual([...r.native.keys()],[friendApprovalPath,friendAcceptancePath,friendRequestApprovalPath,friendRequestAcceptancePath]);
  const route=r.native.get(friendApprovalPath);assert.deepEqual(route.methods,['POST']);assert.equal(route.requestBody,'buffered');
  const req=(body,headers={})=>new Request('dsh-app://app'+friendApprovalPath,{method:'POST',headers:{'content-type':'application/json',...headers},body:JSON.stringify(body)});
  let res=await route.fetch(req(args,{origin:'https://attacker.example'}));assert.equal(res.status,403);
  res=await route.fetch(req({...args,fingerprint:'forged'}));assert.equal(res.status,503);assert.equal(calls,0);
});

test('full request approval carries a fixed durable purpose and uses trusted complete notice',async()=>{
  const calls=[],sent=[];
  const actions=createFriendRequestAcceptanceActions({getPreparedPlan:async()=>plan,ownerTarget:'owner',
    request:async(op,input)=>{calls.push([op,input]);return {claimed:true,planId,hash,code:'1234abcd',ownerTarget:'owner',purpose:'add-friend-request',text:'complete trusted notice'}},
    sendNotice:async input=>sent.push(input)});
  assert.equal((await actions.approval(args)).outcome,'sent');
  assert.deepEqual(calls,[['social-approval-notice',{target:'owner',planId,hash,purpose:'add-friend-request'}]]);
  assert.equal(sent[0].text,'complete trusted notice');
});

test('full request refuses an unscoped notice and never calls it friendship success',async()=>{
  const options={getPreparedPlan:async()=>({...plan,status:'approved',approvalPurpose:'add-friend-request'}),ownerTarget:'owner',sendNotice:async()=>assert.fail('no notice')};
  for(const raw of [
    {status:'confirmed'},
    {status:'confirmed',confirmationKind:'friendship',applicationSubmittedVerified:true,friendshipConfirmed:true},
  ]){
    const actions=createFriendRequestAcceptanceActions({...options,request:async()=>({id:planId,hash,executionStatus:'dispatched',...raw})});
    await assert.rejects(actions.acceptance(args),/result-invalid/);
  }
  const calls=[];
  const actions=createFriendRequestAcceptanceActions({...options,request:async(op,input)=>{
    calls.push([op,input]);return {id:planId,hash,status:'confirmed',executionStatus:'dispatched',
      confirmationKind:'friend-request-submission',applicationSubmittedVerified:true,friendshipConfirmed:false,chatRestored:true}}});
  const value=await actions.acceptance(args);assert.equal(value.friendshipConfirmed,false);
  assert.equal(calls[0][1].purpose,'add-friend-request');
  const unscoped=createFriendRequestAcceptanceActions({...options,getPreparedPlan:async()=>plan,
    request:async()=>({claimed:true,planId,hash,code:'1234abcd',ownerTarget:'owner',text:'initial only'})});
  assert.equal((await unscoped.approval(args)).outcome,'failed-or-unknown');
  const oldApproved=createFriendRequestAcceptanceActions({...options,getPreparedPlan:async()=>({...plan,status:'approved'}),request:()=>assert.fail('old approval is not full approval')});
  await assert.rejects(oldApproved.approval(args),/approval-required/);
  await assert.rejects(oldApproved.acceptance(args),/approval-required/);
});

test('worker failure publishes a terminal diagnostic without fabricating the worker outcome or retrying',async()=>{
  const errors=[];let calls=0;
  const actions=createFriendRequestAcceptanceActions({getPreparedPlan:async()=>({...plan,status:'approved',approvalPurpose:'add-friend-request'}),
    ownerTarget:'owner',sendNotice:()=>assert.fail(),request:async()=>{calls++;throw Error('private raw error')},
    onError:(code,metadata)=>errors.push({code,...metadata})});
  await assert.rejects(actions.acceptance(args),/private raw error/);
  assert.equal(calls,1);assert.deepEqual(errors,[{code:'social-initial-contact-outcome-unknown',requestAttempted:true,automaticRetry:false}]);
});

test('full request HTTP routes enforce the same owner admission before loading a plan',async()=>{
  let inspected=0;const r=routes({getPreparedPlan:async()=>{inspected++;return plan}});
  for(const path of [friendRequestApprovalPath,friendRequestAcceptancePath]){
    const res=response();await r.http.get(path).handler(request(args,{url:path}),res);
    assert.equal(res.status,403);
  }
  assert.equal(inspected,0);
});
