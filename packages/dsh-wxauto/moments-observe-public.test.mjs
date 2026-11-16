import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {publicMomentsObservation,publicMomentsDiagnostic} from './moments-observe-public.mjs';
import {socialRpcClient} from './social-tools.mjs';
import {createObservationAction,nativeSocialObservationHandler} from './social-observe-http.mjs';
const digest='a'.repeat(64),label={sha256:digest,length:12};
function fixture(){return {purpose:'moments-feed-metadata',version:1,source:'wxauto-uia-readonly-metadata',scope:'moments-feed',
  state:'post-reader-unobserved',readOnly:true,sentMessages:false,liveCapabilityGranted:false,chatRestored:true,
  clientVersion:'4.2.0.12',clientSha256:digest,restoreTargetSha256:digest,observedAt:1700000000,
  postIdentityVerified:false,authorVerified:false,headBoundaryVerified:false,emptyTimelineVerified:false,
  creationTimeGranularity:'unobserved',visitedNodes:1,truncated:false,nodes:[{index:0,parentIndex:null,depth:0,rootIndex:0,
    class:'mmui::SNSContentView',type:'GroupControl',runtimeIdSha256:digest,bounds:[1,2,10,20],name:label,automationId:null,
    legacy:{available:true,Name:label},value:{available:false},text:{available:true,documentText:label,textMayBeTruncated:false}}]};}
const failureFacts=()=>({purpose:'moments-feed-metadata',attemptId:'b'.repeat(32),phase:'locating-feed',completed:false,
  chatRestored:true,expectedClass:'mmui::SNSContentView',matches:0,code:'social-moments-control-missing-or-ambiguous',durationMs:4123});

test('failed-stage projection preserves actual restoration and fixed selector facts without raw data',()=>{
  const input={...failureFacts(),rawName:'fixture secret'};
  assert.deepEqual(publicMomentsDiagnostic(input),failureFacts());
  for(const change of [{chatRestored:'yes'},{phase:'a private title'},{expectedClass:'arbitrary'},
    {matches:-1},{code:'C:/private/path'},{durationMs:Infinity},{completed:true,chatRestored:null}])
    assert.throws(()=>publicMomentsDiagnostic({...input,...change}),/result-invalid/);
  assert.equal(publicMomentsDiagnostic({...input,chatRestored:null}).chatRestored,null);
});

test('RPC failure metadata is correlated to the pending Moments request, not another operation',async()=>{
  for(const purpose of ['moments-feed-metadata','friend-search-owner']){
    let command;const client=socialRpcClient({isReady:()=>true,write:line=>{command=JSON.parse(line);},timeoutMs:1000});
    const pending=client.request('social-observe',{target:'fixed-owner',purpose});
    client.handle({event:'social-result',id:command.id,ok:false,code:'social-moments-control-missing-or-ambiguous',
      observation:{...failureFacts(),rawName:'fixture secret'}});
    await assert.rejects(pending,error=>{
      assert.match(error.message,/control-missing/);
      assert.deepEqual(error.observation,purpose==='moments-feed-metadata'?failureFacts():undefined);return true;
    });
    assert.equal(client.handle({event:'social-result',id:command.id,ok:false,observation:failureFacts()}),false);client.close();
  }
});

test('Moments failure facts reach the private status callback while HTTP retains a fixed error',async()=>{
  const errors=[],error=Error('social-moments-control-missing-or-ambiguous');error.observation={...failureFacts(),rawName:'fixture secret'};
  const action=createObservationAction({ownerTarget:'fixed-owner',request:async()=>{throw error;},onError:(...args)=>errors.push(args)});
  const handler=nativeSocialObservationHandler(action);
  const response=await handler(new Request('dsh-app://app/api/local-dsh-wxauto/social/observe',{
    method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({purpose:'moments-feed-metadata'})}));
  assert.equal(response.status,503);
  assert.deepEqual(await response.json(),{ok:false,code:'social-moments-control-missing-or-ambiguous'});
  assert.deepEqual(errors,[[error.message,failureFacts()]]);
});

test('metadata projection excludes raw content and does not promote a post or action receipt',()=>{
  const input=fixture();input.privateText='fixture body';input.nodes[0].rawText='fixture body';
  input.nodes[0].name.raw='fixture body';input.nodes[0].legacy.Name.raw='fixture body';
  const value=publicMomentsObservation(input);
  assert.equal(JSON.stringify(value).includes('fixture body'),false);assert.equal(value.postIdentityVerified,false);
  assert.equal(value.liveCapabilityGranted,false);assert.equal(value.sentMessages,false);
  value.nodes[0].bounds[0]=999;assert.equal(input.nodes[0].bounds[0],1);
});

test('invalid metadata and fabricated success claims are rejected',()=>{
  for(const change of [{postIdentityVerified:true},{authorVerified:true},{emptyTimelineVerified:true},
    {headBoundaryVerified:true},{liveCapabilityGranted:true},{sentMessages:true},{chatRestored:false},
    {clientVersion:'other'},{scope:'own-album'},{state:'published'},{visitedNodes:0},{observedAt:Infinity}])
    assert.throws(()=>publicMomentsObservation({...fixture(),...change}),/result-invalid/);
  for(const change of [{name:{sha256:'raw',length:1}},{parentIndex:0},{bounds:[0,0,0,1]},
    {class:'contains spaces'},{legacy:{available:true,Name:{sha256:digest,length:32001}}},{rootIndex:1}]){
    const input=fixture();Object.assign(input.nodes[0],change);assert.throws(()=>publicMomentsObservation(input),/result-invalid/);
  }
});

test('fixed Moments purpose uses original owner queue and rejects caller-selected targets or plans',async()=>{
  const calls=[],observe=createObservationAction({ownerTarget:'fixed-owner',request:async(...args)=>{calls.push(args);return fixture();}});
  const value=await observe({purpose:'moments-feed-metadata'});assert.equal(value.chatRestored,true);
  assert.deepEqual(calls,[['social-observe',{target:'fixed-owner',purpose:'moments-feed-metadata'}]]);
  for(const change of [{target:'other'},{account:'other'},{planId:'a'.repeat(32)},{op:'social-execute'},{publish:true}])
    await assert.rejects(observe({purpose:'moments-feed-metadata',...change}),/invalid-request/);
  assert.equal(calls.length,1);
});

test('Moments and existing friend observations share the same no-overlap action',async()=>{
  let finish;let count=0;
  const observe=createObservationAction({ownerTarget:'fixed-owner',request:()=>{count++;return new Promise(r=>finish=r);}});
  const running=observe({purpose:'moments-feed-metadata'});
  await assert.rejects(observe({purpose:'friend-search-owner'}),/in-progress/);
  finish(fixture());await running;assert.equal(count,1);
});

test('native endpoint accepts only exact fixed-purpose request through the existing admission carrier',async()=>{
  const calls=[],observe=createObservationAction({ownerTarget:'fixed-owner',request:async(...args)=>{calls.push(args);return fixture();}});
  const handler=nativeSocialObservationHandler(observe),url='dsh-app://app/api/local-dsh-wxauto/social/observe';
  const request=(body,headers={})=>new Request(url,{method:'POST',headers:{'content-type':'application/json',...headers},body:JSON.stringify(body)});
  assert.equal((await handler(request({purpose:'moments-feed-metadata'}))).status,200);
  assert.equal((await handler(request({purpose:'moments-feed-metadata',target:'other'}))).status,400);
  assert.equal((await handler(request({purpose:'moments-feed-metadata'},{origin:'https://other.example'}))).status,403);
  assert.equal(calls.length,1);
});

test('settings transport sends the fixed Moments request and retains shared in-flight protection',async()=>{
  let module,finish;const calls=[];
  vm.runInNewContext(fs.readFileSync(new URL('./client.js',import.meta.url),'utf8'),{
    window:{__ModuleLoader__:{load:value=>{module=value.factory(()=>({}));}}},AbortSignal,
  });
  const transport=module.createObservationTransport({location:{protocol:'dsh-app:',hostname:'app'},fetch:(...args)=>{
    calls.push(args);return new Promise(resolve=>finish=resolve);
  }});
  const running=transport.observeMoments();await assert.rejects(transport.observe(),/in-progress/);
  assert.equal(calls[0][0],'/api/local-dsh-wxauto/social/observe');
  assert.deepEqual(JSON.parse(calls[0][1].body),{purpose:'moments-feed-metadata'});
  finish({ok:true,json:async()=>({ok:true,value:fixture()})});await running;assert.equal(calls.length,1);
});
