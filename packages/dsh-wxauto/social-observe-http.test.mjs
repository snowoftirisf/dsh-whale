import test from 'node:test';
import assert from 'node:assert/strict';
import {Readable} from 'node:stream';
import {socialObservationHandler,registerSocialObservation,socialObservePath,nativeSocialObservePath,nativeSocialObservationHandler,createObservationAction,preparedFriendPlanPath} from './social-observe-http.mjs';

function request(body='{"purpose":"friend-search-owner"}',extra={}){
  return Object.assign(Readable.from([body]),{socket:{remoteAddress:'127.0.0.1'},method:'POST',
    headers:{'content-type':'application/json'},...extra});
}
function response(){return {writeHead(status){this.status=status},end(text){this.body=JSON.parse(text)}};}
const observed={purpose:'friend-search-owner',readOnly:true,queryValueMatched:true,profileIdentityMatched:true,
  responseFreshnessVerified:false,newSearchCompleted:false,accountMatched:false,applicationSubmitted:false,
  friendAccepted:false,sentMessages:false,liveCapabilityGranted:false,chatRestored:true,
  querySha256:'a'.repeat(64),clientSha256:'b'.repeat(64),restoreTargetSha256:'c'.repeat(64),
  clientVersion:'4.2.0.12',state:'profile-observed',controls:[{class:'mmui::XTextView',type:'TextControl',
    automationIdSha256:'d'.repeat(64),runtimeIdSha256:'e'.repeat(64)}]};
function setup(overrides={}){
  const calls=[];
  return {calls,handler:socialObservationHandler({connection:()=>({requestRejection:()=>undefined}),
    ownerTarget:'trusted-owner',request:async(...args)=>{calls.push(args);return observed},...overrides})};
}
test('fixed purpose derives owner target and only invokes observation RPC',async()=>{
  const {handler,calls}=setup(),res=response();await handler(request(),res);
  assert.equal(res.status,200);assert.equal(res.body.value.profileIdentityMatched,true);
  assert.deepEqual(calls,[['social-observe',{target:'trusted-owner',purpose:'friend-search-owner'}]]);
});
test('host authentication, missing/throwing gate and nonlocal peers fail before RPC',async()=>{
  for(const [extra,status] of [
    [{connection:()=>({requestRejection:()=>401})},401],
    [{connection:()=>({requestRejection:()=>403})},403],
    [{connection:()=>({})},503],
    [{connection:()=>{throw Error('private-path')}},503],
  ]){
    const {handler,calls}=setup(extra),res=response();await handler(request(),res);
    assert.equal(res.status,status);assert.deepEqual(calls,[]);
  }
  const {handler,calls}=setup(),res=response();await handler(request(undefined,{socket:{remoteAddress:'10.0.0.8'}}),res);
  assert.equal(res.status,403);assert.deepEqual(calls,[]);
});
test('no arbitrary account, target, operation or malformed request is accepted',async()=>{
  for(const body of ['{}','[]','null','broken','{"purpose":"execute"}',
    '{"purpose":"friend-search-owner","target":"group"}',
    '{"purpose":"friend-search-owner","account":"other"}',
    '{"purpose":"friend-search-owner","op":"social-execute"}']){
    const {handler,calls}=setup(),res=response();await handler(request(body),res);
    assert.equal(res.status,400);assert.deepEqual(calls,[]);
  }
});
test('body size, method and content type checked without observation',async()=>{
  for(const [req,status] of [[request('x'.repeat(257)),413],[request(undefined,{method:'GET'}),405],
    [request(undefined,{headers:{'content-type':'text/plain'}}),415]]){
    const {handler,calls}=setup(),res=response();await handler(req,res);assert.equal(res.status,status);assert.deepEqual(calls,[]);
  }
});
test('in-flight observation is not duplicated and a failure permits a later fresh request',async()=>{
  let finish;const calls=[];
  const {handler}=setup({request:(...args)=>{calls.push(args);return new Promise(resolve=>{finish=resolve})}});
  const first=response(),running=handler(request(),first);
  while(!finish)await new Promise(resolve=>setImmediate(resolve));
  const second=response();await handler(request(),second);assert.equal(second.status,409);assert.equal(calls.length,1);
  finish(observed);await running;assert.equal(first.status,200);
  const third=response(),next=handler(request(),third);
  while(calls.length<2)await new Promise(resolve=>setImmediate(resolve));
  finish(observed);await next;assert.equal(third.status,200);
});
test('only whitelisted observation metadata leaves the local API',async()=>{
  const {handler}=setup({request:async()=>({...observed,rawQuery:'private',chatTitle:'private',
    controls:[{...observed.controls[0],name:'private',path:'private'}]})}),res=response();
  await handler(request(),res);assert.equal(res.status,200);assert.deepEqual(res.body.value,observed);
  for(const value of [{...observed,applicationSubmitted:true},{...observed,querySha256:'private'},
    {...observed,controls:[{...observed.controls[0],class:'private text'}]}]){
    const one=setup({request:async()=>value}),out=response();await one.handler(request(),out);
    assert.equal(out.status,503);assert.equal(out.body.code,'social-observation-result-invalid');
  }
});
test('errors expose only sanitized codes',async()=>{
  for(const [error,expected] of [['social-reply-in-progress','social-reply-in-progress'],['secret C:/private/contact','social-observation-failed']]){
    const {handler}=setup({request:async()=>{throw Error(error)}}),res=response();await handler(request(),res);
    assert.deepEqual(res.body,{ok:false,code:expected});assert.equal(res.status,503);
  }
});
test('route uses scoped host registration and cleanup, no hardcoded port or model tool',()=>{
  const routes=[];let removed=0,native;
  const ctx={get:()=>({requestRejection:()=>undefined}),inject(names,run){
    if(names[0]==='connection')return run({connection:{fetch:{register(value){native=value}}}});
    assert.deepEqual(names,['webServer']);
    run({effect:fn=>{const dispose=fn();dispose()},webServer:{register(value){routes.push(value);return()=>removed++}}});}};
  registerSocialObservation(ctx,{request:async()=>{},ownerTarget:'owner'});
  assert.deepEqual(routes.map(route=>route.path),[socialObservePath,nativeSocialObservePath]);assert.equal(removed,2);
  assert.equal(native.path,nativeSocialObservePath);assert.deepEqual(native.methods,['POST']);assert.equal(native.requestBody,'buffered');
});

test('authenticated native fetch uses the same fixed-purpose action and rejects cross-origin/extra parameters',async()=>{
  let calls=0;const handler=nativeSocialObservationHandler(createObservationAction({ownerTarget:'owner',
    request:async(op,args)=>{calls++;assert.equal(op,'social-observe');assert.equal(args.target,'owner');return observed}}));
  const req=(body={purpose:'friend-search-owner'},headers={})=>new Request('https://127.0.0.1'+nativeSocialObservePath,
    {method:'POST',headers:{'content-type':'application/json',...headers},body:JSON.stringify(body)});
  let result=await handler(req());assert.equal(result.status,200);assert.deepEqual((await result.json()).value,observed);
  result=await handler(req({purpose:'friend-search-owner',target:'other'}));assert.equal(result.status,400);
  result=await handler(req(undefined,{origin:'https://attacker.example'}));assert.equal(result.status,403);
  assert.equal(calls,1);
});

test('shared action blocks overlap across native and HTTP transport without granting write capabilities',async()=>{
  let finish,calls=0;
  const observe=createObservationAction({ownerTarget:'owner',request:()=>{calls++;return new Promise(resolve=>{finish=resolve})}});
  const native=nativeSocialObservationHandler(observe),http=socialObservationHandler({connection:()=>({requestRejection:()=>undefined}),observe});
  const nativeRequest=new Request('dsh-app://app'+nativeSocialObservePath,{method:'POST',headers:{'content-type':'application/json'},body:'{"purpose":"friend-search-owner"}'});
  const active=native(nativeRequest);while(!finish)await new Promise(resolve=>setImmediate(resolve));
  const res=response();await http(request(),res);assert.equal(res.body.code,'social-observation-in-progress');assert.equal(calls,1);
  finish(observed);assert.equal((await active).status,200);
});

test('candidate lookup binds the exact pending plan and projects only known profile button metadata',async()=>{
  const planId='1'.repeat(32),hash='a'.repeat(64),calls=[];
  const value={...observed,purpose:'friend-search-plan',planId,planHash:hash,identityEvidence:'matched',
    profileButtons:[{class:'mmui::XButton',type:'ButtonControl',nameSha256:'f'.repeat(64),label:'add-contact',rawName:'private'}]};
  const handler=socialObservationHandler({connection:()=>({requestRejection:()=>undefined}),ownerTarget:'owner',
    request:async(...args)=>{calls.push(args);return value}}),res=response();
  await handler(request(JSON.stringify({purpose:'friend-search-plan',planId,hash})),res);
  assert.equal(res.status,200);assert.equal(res.body.value.profileButtons[0].rawName,undefined);
  assert.deepEqual(calls,[['social-observe',{target:'owner',purpose:'friend-search-plan',planId,hash}]]);
  const wrong=socialObservationHandler({connection:()=>({requestRejection:()=>undefined}),ownerTarget:'owner',
    request:async()=>({...value,planHash:'b'.repeat(64)})}),failed=response();
  await wrong(request(JSON.stringify({purpose:'friend-search-plan',planId,hash})),failed);
  assert.equal(failed.status,503);assert.equal(failed.body.code,'social-observation-result-invalid');
  const unavailable=socialObservationHandler({connection:()=>({requestRejection:()=>undefined}),ownerTarget:'owner',
    request:async()=>({...value,profileIdentityMatched:false,identityEvidence:'id-unavailable'})}),unmatched=response();
  await unavailable(request(JSON.stringify({purpose:'friend-search-plan',planId,hash})),unmatched);
  assert.equal(unmatched.status,200);assert.equal(unmatched.body.value.profileIdentityMatched,false);
  const inconsistent=socialObservationHandler({connection:()=>({requestRejection:()=>undefined}),ownerTarget:'owner',
    request:async()=>({...value,identityEvidence:'id-unavailable'})}),invalid=response();
  await inconsistent(request(JSON.stringify({purpose:'friend-search-plan',planId,hash})),invalid);
  assert.equal(invalid.status,503);
});

test('prepared account preview requires explicit owner admission for HTTP',async()=>{
  let route,calls=0,kind='agent';
  const ctx={get:()=>({admit:()=>({peer:{principal:{kind}}})}),inject(names,run){
    if(names[0]==='webServer')run({effect:fn=>fn(),webServer:{register(value){if(value.path===preparedFriendPlanPath)route=value;return()=>{}}}});
  }};
  registerSocialObservation(ctx,{ownerTarget:'owner',request:async()=>{},getPreparedPlan:async()=>{calls++;return {account:'_Test50K'}}});
  let res=response();await route.handler(request('',{method:'GET',url:preparedFriendPlanPath}),res);assert.equal(res.status,403);assert.equal(calls,0);
  kind='owner';res=response();await route.handler(request('',{method:'GET',url:preparedFriendPlanPath}),res);assert.equal(res.status,200);assert.equal(calls,1);
});
