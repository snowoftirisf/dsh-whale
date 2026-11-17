import test from 'node:test';import assert from 'node:assert/strict';import {Readable} from 'node:stream';import {registerGroupTextCutover,groupTextCutoverPath,groupTextActivatePath,groupTextRoutesPath} from './group-text-cutover-http.mjs';
function fixture({groups=()=>[]}={}){const native=new Map(),http=new Map(),calls=[];let principal='owner';registerGroupTextCutover({get:()=>({admit:()=>({peer:{principal:{kind:principal}}})}),inject(n,f){if(n[0]==='webServer')f({effect:g=>g(),webServer:{register:r=>http.set(r.path,r)}});else f({connection:{fetch:{register:r=>native.set(r.path,r)}}});}},{groups,status:async key=>{calls.push(['status',key]);return {authorityMode:'ui',canActivate:true,nativeActive:false,phase:'idle'}},activate:async key=>{calls.push(['activate',key]);return {outcome:'working',attemptId:'a'.repeat(32)}}});return {native,http,calls,setPrincipal:v=>principal=v};}
const request=(path,body)=>new Request('dsh-app://app'+path,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
test('authenticated native carrier accepts exact group key only; no selector/account/target',async()=>{const f=fixture(),handler=f.native.get(groupTextActivatePath).fetch;for(const args of [{},{nativeChatKey:'wechat:owner'},{nativeChatKey:'wechat:r@chatroom',target:'other'}])assert.equal((await handler(request(groupTextActivatePath,args))).status,400);assert.deepEqual(f.calls,[]);const res=await handler(request(groupTextActivatePath,{nativeChatKey:'wechat:r@chatroom'}));assert.equal(res.status,200);assert.deepEqual(f.calls,[['activate','wechat:r@chatroom']]);});
test('HTTP local-only owner gate precedes all activation and readonly status does not activate',async()=>{const f=fixture();const invoke=async(path,body)=>{const req=Object.assign(Readable.from([JSON.stringify(body)]),{method:'POST',url:path,headers:{'content-type':'application/json'},socket:{remoteAddress:'127.0.0.1'}});const res={writeHead(c){this.status=c},end(b){this.value=JSON.parse(b)}};await f.http.get(path).handler(req,res);return res};f.setPrincipal('agent');assert.equal((await invoke(groupTextActivatePath,{nativeChatKey:'wechat:r@chatroom'})).status,403);assert.deepEqual(f.calls,[]);f.setPrincipal('owner');assert.equal((await invoke(groupTextCutoverPath,{nativeChatKey:'wechat:r@chatroom'})).status,200);assert.deepEqual(f.calls,[['status','wechat:r@chatroom']]);});

test('fixed readonly group list projects/deduplicates trusted active identities without status/capture/activation',async()=>{let reads=0;const f=fixture({groups:()=>{reads++;return [{nativeChatKey:'wechat:r@chatroom',target:'Room',accountId:'omit',path:'omit'},{nativeChatKey:'wechat:r@chatroom',target:'Room'}]}});assert.equal(reads,0);assert.deepEqual(f.native.get(groupTextRoutesPath).methods,['GET']);const handler=f.native.get(groupTextRoutesPath).fetch;
 const res=await handler(new Request('dsh-app://app'+groupTextRoutesPath));assert.equal(res.status,200);assert.deepEqual((await res.json()).value,[{nativeChatKey:'wechat:r@chatroom',target:'Room'}]);assert.equal(reads,1);assert.deepEqual(f.calls,[]);
 const bad=await handler(new Request('dsh-app://app'+groupTextRoutesPath,{headers:{origin:'https://evil.example'}}));assert.equal(bad.status,403);assert.equal(reads,1);
});
test('list refuses ambiguous scope and HTTP requires actual owner admission before reading routes',async()=>{let reads=0;const f=fixture({groups:()=>{reads++;return [{nativeChatKey:'wechat:r@chatroom',target:'Room'},{nativeChatKey:'wechat:r@chatroom',target:'Other'}]}});
 const invoke=async()=>{const req=Object.assign(Readable.from([]),{method:'GET',url:groupTextRoutesPath,headers:{},socket:{remoteAddress:'127.0.0.1'}});const res={writeHead(c){this.status=c},end(b){this.body=JSON.parse(b)}};await f.http.get(groupTextRoutesPath).handler(req,res);return res};
 f.setPrincipal('agent');assert.equal((await invoke()).status,403);assert.equal(reads,0);f.setPrincipal('owner');const res=await invoke();assert.equal(res.status,503);assert.equal(res.body.code,'group-text-routes-ambiguous');assert.equal(reads,1);assert.deepEqual(f.calls,[]);
});

test('reset route calls only reset and projects the blocked reason; status shows a reason',async()=>{
  const calls=[],native=new Map();
  registerGroupTextCutover({get:()=>({admit:()=>({peer:{principal:{kind:'owner'}}})}),inject(n,f){if(n[0]!=='webServer')f({connection:{fetch:{register:r=>native.set(r.path,r)}}});}},
    {status:async key=>{calls.push(['status',key]);return {authorityMode:'blocked',canActivate:false,nativeActive:false,blockedReason:'route-session-or-chat-changed',secret:'x'}},
     activate:async()=>{calls.push(['activate']);return {outcome:'refused'}},
     reset:async key=>{calls.push(['reset',key]);return {reset:true,authorityMode:'ui',canActivate:false,nativeActive:false}}});
  const resetPath=groupTextCutoverPath+'/reset';
  const status=await (await native.get(groupTextCutoverPath).fetch(request(groupTextCutoverPath,{nativeChatKey:'wechat:r@chatroom'}))).json();
  assert.equal(status.value.blockedReason,'route-session-or-chat-changed');assert.equal(status.value.secret,undefined);
  const reset=await (await native.get(resetPath).fetch(request(resetPath,{nativeChatKey:'wechat:r@chatroom'}))).json();
  assert.deepEqual(reset.value,{reset:true,authorityMode:'ui',canActivate:false,nativeActive:false});
  assert.deepEqual(calls,[['status','wechat:r@chatroom'],['reset','wechat:r@chatroom']]);
});
