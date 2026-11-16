import {createHash} from 'node:crypto';
import test from 'node:test';import assert from 'node:assert/strict';import {groupTextAdmissionRpcClient} from './group-text-admission-rpc.mjs';
function fixture(){const args={nativeChatKey:'wechat:r@chatroom',claimId:'a'.repeat(32),claimHash:'b'.repeat(64),authorityEpoch:'c'.repeat(32)},lines=[];let route={...args,target:'room',sessionId:'same-session',chatId:'canonical',accountId:'fixture',kind:'group',active:false,admitAllowed:true};const rpc=groupTextAdmissionRpcClient({write:l=>lines.push(JSON.parse(l)),pendingRoute:()=>route});return {rpc,args,lines,route:v=>route=v};}
test('dedicated admit command only uses exact trusted suspended handoff binding',async()=>{const f=fixture();for(const args of [{...f.args,target:'other'},{...f.args,claimHash:'bad'},{...f.args,op:'group-text-commit'}])await assert.rejects(f.rpc.request(args),/request-invalid/);const p=f.rpc.request(f.args);assert.deepEqual({...f.lines[0],id:undefined},{op:'group-text-admit',id:undefined,target:'room',...f.args});f.rpc.handle({event:'group-text-result',id:f.lines[0].id,ok:true,value:{mode:'native'}});assert.equal((await p).mode,'native');f.rpc.close();});
test('changed prepared canonical scope rejects late ACK and close never retries unknown',async()=>{const f=fixture(),p=f.rpc.request(f.args);f.route(null);f.rpc.handle({event:'group-text-result',id:f.lines[0].id,ok:true,value:{mode:'native'}});await assert.rejects(p,/route-changed/);assert.equal(f.lines.length,1);f.rpc.close();const x=fixture(),pending=x.rpc.request(x.args);x.rpc.close();await assert.rejects(pending,/outcome-unknown/);assert.equal(x.lines.length,1);});

test('private status may read exact active ownership while admit remains suspended-only',async()=>{
 const f=fixture();f.route({...f.args,target:'room',sessionId:'same-session',chatId:'canonical',accountId:'fixture',kind:'group',active:true,admitAllowed:false});
 await assert.rejects(f.rpc.request(f.args),/route-unverified/);const pending=f.rpc.status(f.args);
 assert.deepEqual({...f.lines[0],id:undefined},{op:'group-text-status',id:undefined,target:'room',nativeChatKey:f.args.nativeChatKey});
 f.rpc.handle({event:'group-text-result',id:f.lines[0].id,ok:true,value:{mode:'native'}});assert.equal((await pending).mode,'native');f.rpc.close();
});
test('no-raw monitor status remains an exact private key/epoch capability and cannot send admit',async()=>{
 const f=fixture(),args={nativeChatKey:f.args.nativeChatKey,authorityEpoch:f.args.authorityEpoch};
 f.route({...args,target:'room',sessionId:'same-session',chatId:'canonical',accountId:'fixture',kind:'group',active:false,admitAllowed:false});
 await assert.rejects(f.rpc.request(args),/request-invalid/);const pending=f.rpc.status(args);
 assert.equal(f.lines[0].op,'group-text-status');f.rpc.handle({event:'group-text-result',id:f.lines[0].id,ok:true,value:{mode:'native'}});assert.equal((await pending).mode,'native');f.rpc.close();
});

const wireFingerprint=w=>createHash('sha256').update(JSON.stringify([w.op,w.id,w.target,w.nativeChatKey,w.claimId,w.claimHash,w.authorityEpoch])).digest('hex');
test('only exact current-generation real busy ACK creates a one-shot unissued proof',async()=>{
 const args={nativeChatKey:'wechat:r@chatroom',claimId:'a'.repeat(32),claimHash:'b'.repeat(64),authorityEpoch:'c'.repeat(32)},generation='1'.repeat(32);let wire;
 const route={...args,target:'room',sessionId:'session',chatId:'chat',accountId:'account',kind:'group',active:false,admitAllowed:true};
 const rpc=groupTextAdmissionRpcClient({write:l=>wire=JSON.parse(l),pendingRoute:()=>route,workerInstanceId:()=>generation});
 const p=rpc.request(args);rpc.handle({event:'group-text-result',id:wire.id,ok:false,code:'group-text-worker-busy',issuance:'not-issued',phase:'before-admit',workerInstanceId:generation,requestFingerprint:wireFingerprint(wire)});
 let actual;try{await p}catch(e){actual=e}assert.equal(rpc.consumeNotIssued(Error('group-text-worker-busy'),args),null);
 assert.equal(rpc.consumeNotIssued(actual,{...args,claimHash:'e'.repeat(64)}),null);assert.equal(rpc.consumeNotIssued(actual,args).workerInstanceId,generation);assert.equal(rpc.consumeNotIssued(actual,args),null);rpc.close();
});
test('missing ready generation, forged fingerprint, changed worker and generic errors never authorize retry',async()=>{
 for(const mode of ['missing-ready','fingerprint','changed-worker','generic']){
  const args={nativeChatKey:'wechat:r@chatroom',claimId:'a'.repeat(32),claimHash:'b'.repeat(64),authorityEpoch:'c'.repeat(32)};let wire,generation=mode==='missing-ready'?null:'1'.repeat(32);
  const rpc=groupTextAdmissionRpcClient({write:l=>wire=JSON.parse(l),pendingRoute:()=>({...args,target:'room',sessionId:'s',chatId:'c',accountId:'a',kind:'group',active:false,admitAllowed:true}),workerInstanceId:()=>generation});
  const p=rpc.request(args);if(mode==='changed-worker')generation='2'.repeat(32);
  rpc.handle({event:'group-text-result',id:wire.id,ok:false,code:mode==='generic'?'group-text-operation-denied':'group-text-worker-busy',issuance:'not-issued',phase:'before-admit',workerInstanceId:'1'.repeat(32),requestFingerprint:mode==='fingerprint'?'f'.repeat(64):wireFingerprint(wire)});
  let error;try{await p}catch(e){error=e}assert.equal(rpc.consumeNotIssued(error,args),null,mode);rpc.close();
 }
});
