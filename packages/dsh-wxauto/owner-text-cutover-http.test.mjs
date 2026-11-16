import test from 'node:test';
import assert from 'node:assert/strict';
import {Readable} from 'node:stream';
import {registerOwnerTextCutover,ownerTextCutoverPath as statusPath,ownerTextActivatePath as activatePath} from './owner-text-cutover-http.mjs';
const snapshot={authorityMode:'ui',canActivate:true,phase:'idle',nativeActive:false,quiet:{incomingPending:0}};
function fixture(options={}){
  const http=new Map(),native=new Map();let principal='owner',rejection;const calls=[];
  const connection={admit:()=>({rejection,peer:{principal:{kind:principal}}})};
  registerOwnerTextCutover({get:()=>connection,inject(names,run){
    if(names[0]==='webServer')run({effect:fn=>fn(),webServer:{register:route=>http.set(route.path,route)}});
    else run({connection:{fetch:{register:route=>native.set(route.path,route)}}});
  }},{status:async()=>{calls.push('status');return snapshot;},activate:async()=>{calls.push('activate');return {outcome:'active'};},...options});
  return {http,native,calls,principal:v=>principal=v,rejection:v=>rejection=v};
}
const request=(route,body={},extra={})=>Object.assign(Readable.from([JSON.stringify(body)]),{method:route===statusPath?'GET':'POST',url:route,
  headers:{'content-type':'application/json'},socket:{remoteAddress:'127.0.0.1'},...extra});
const response=()=>({writeHead(code){this.status=code;},end(body){this.body=JSON.parse(body);}});
async function http(f,route,body={},extra={}){const res=response();await f.http.get(route).handler(request(route,body,extra),res);return res;}
const nativeRequest=(route,body={},headers={})=>new Request('dsh-app://app'+route,{method:route===statusPath?'GET':'POST',headers:{'content-type':'application/json',...headers},
  ...(route===statusPath?{}:{body:JSON.stringify(body)})});

test('fixed GET reads projected metadata and POST alone activates, no callback at registration',async()=>{
  const f=fixture({status:async()=>({...snapshot,privateText:'omit',quiet:{incomingPending:0,secret:'omit'}})});
  assert.deepEqual(f.calls,[]);const read=await http(f,statusPath);assert.equal(read.status,200);assert.equal(read.body.value.authorityMode,'ui');
  assert.equal(JSON.stringify(read.body).includes('omit'),false);assert.deepEqual(f.calls,[]);
  const activated=await http(f,activatePath);assert.equal(activated.body.value.outcome,'active');assert.deepEqual(f.calls,['activate']);
});

test('HTTP owner admission/local/method/body gates precede activation and never accept caller-selected scope',async()=>{
  const f=fixture();f.principal('agent');assert.equal((await http(f,statusPath)).status,403);assert.equal((await http(f,activatePath)).status,403);
  f.principal('owner');f.rejection(401);assert.equal((await http(f,activatePath)).status,401);f.rejection(undefined);
  for(const [body,extra,code] of [[{}, {socket:{remoteAddress:'10.0.0.1'}},403],[{}, {method:'GET'},405],
    [{target:'other'},{},400],[[],{},400],[null,{},400],[{}, {headers:{}},415],[{padding:'x'.repeat(80)},{},413],
    [{},{url:activatePath+'?account=other'},405]])assert.equal((await http(f,activatePath,body,extra)).status,code);
  assert.deepEqual(f.calls,[]);
});

test('official native registration is exact owner carrier and enforces same-origin/json/empty args',async()=>{
  const f=fixture();assert.deepEqual(f.native.get(statusPath).methods,['GET']);assert.deepEqual(f.native.get(activatePath).methods,['POST']);
  let res=await f.native.get(statusPath).fetch(nativeRequest(statusPath));assert.equal(res.status,200);assert.deepEqual(f.calls,['status']);
  const handler=f.native.get(activatePath).fetch;
  for(const [body,headers,code] of [[{}, {origin:'https://evil.example'},403],[{}, {'sec-fetch-site':'same-site'},403],
    [{accountId:'other'},{},400],[{}, {'content-type':'text/plain'},415],[{padding:'x'.repeat(90)},{},413]])
    assert.equal((await handler(nativeRequest(activatePath,body,headers))).status,code);
  assert.deepEqual(f.calls,['status']);res=await handler(nativeRequest(activatePath));assert.equal(res.status,200);assert.deepEqual(f.calls,['status','activate']);
});

test('single activation remains busy across awaits; unknown result/error has no automatic retry',async()=>{
  let finish,count=0;const f=fixture({activate:()=>{count++;return new Promise(r=>finish=r);}});
  const first=http(f,activatePath);while(!finish)await new Promise(r=>setImmediate(r));
  assert.equal((await http(f,activatePath)).body.code,'owner-text-cutover-in-progress');finish({outcome:'unknown'});
  assert.equal((await first).body.value.outcome,'unknown');assert.equal(count,1);
  const fail=fixture({activate:async()=>{throw Error('private file path');}}),res=await http(fail,activatePath);
  assert.equal(res.status,503);assert.equal(res.body.code,'owner-text-cutover-outcome-unknown');
});

test('working job metadata projects same attempt on GET and never starts another activation while polling',async()=>{
  const attemptId='a'.repeat(32),f=fixture({activate:async()=>({outcome:'working',attemptId,phase:'capturing',private:'omit'}),
    status:async()=>({...snapshot,outcome:'working',attemptId,phase:'capturing',canActivate:false})});
  const started=await http(f,activatePath);assert.deepEqual(started.body.value,{outcome:'working',attemptId,phase:'capturing'});
  for(let i=0;i<3;i++){const value=(await http(f,statusPath)).body.value;assert.equal(value.attemptId,attemptId);assert.equal(value.outcome,'working');}
});
