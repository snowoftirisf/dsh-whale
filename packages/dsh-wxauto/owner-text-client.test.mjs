import {fileURLToPath as __testFile} from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {EventEmitter} from 'node:events';
import {ownerTextRpcClient,createOwnerTextCatalogClient} from './owner-text-client.mjs';
import {ownerTextScopeDigest} from './native-owner-text-inbox.mjs';

test('fixed owner RPC supports only exact narrow schemas and matching result envelope',async()=>{
  const writes=[],rpc=ownerTextRpcClient({ownerTarget:'Owner',write:(line,cb)=>{writes.push(JSON.parse(line));cb?.();}});
  for(const [op,args] of [['owner-text-status',{}],['owner-text-prepare',{}],['owner-text-abort',{ticket:'a'.repeat(32)}],
    ['owner-text-commit',{ticket:'a'.repeat(32),authorityEpoch:'b'.repeat(32)}],['owner-text-decision',{claimId:'a'.repeat(32),authorityEpoch:'b'.repeat(32)}]]){
    const work=rpc.request(op,args),command=writes.at(-1);assert.equal(command.target,'Owner');assert.equal(command.op,op);
    assert.equal(rpc.handle({event:'social-result',id:command.id,ok:true,value:{}}),false);
    assert.equal(rpc.handle({event:'owner-text-result',id:command.id,ok:true,value:{fixture:true}}),true);
    assert.deepEqual(await work,{fixture:true});
  }
  for(const [op,args] of [['social-execute',{}],['owner-text-status',{target:'Other'}],['owner-text-abort',{}],
    ['owner-text-decision',{claimId:'bad',authorityEpoch:'b'.repeat(32)}]])await assert.rejects(rpc.request(op,args),/request-invalid/);
  assert.equal(writes.length,5);rpc.close();
});

test('RPC timeout/write failure/close are fixed and never retry or leak arbitrary worker error',async()=>{
  let count=0;const rpc=ownerTextRpcClient({ownerTarget:'Owner',write:()=>count++,timeoutMs:5});
  await assert.rejects(rpc.request('owner-text-status'),/outcome-unknown/);assert.equal(count,1);rpc.close();
  const bad=ownerTextRpcClient({ownerTarget:'Owner',write:(_,cb)=>cb(Error('private path'))});
  await assert.rejects(bad.request('owner-text-status'),/write-failed/);bad.close();
  let command;const closing=ownerTextRpcClient({ownerTarget:'Owner',write:line=>command=JSON.parse(line)});
  const failed=closing.request('owner-text-status');closing.handle({event:'owner-text-result',id:command.id,ok:false,code:'private path'});
  await assert.rejects(failed,/operation-failed/);
  const pending=closing.request('owner-text-status');closing.close();await assert.rejects(pending,/worker-exited/);
});

function catalogFixture(responder,timeoutMs=1000){
  const children=[],calls=[];
  const client=createOwnerTextCatalogClient({root:path.dirname(__testFile(import.meta.url)),dataRoot:path.resolve('artifacts/fixture-no-io'),
    python:(process.env.WHALE_TEST_PYTHON||path.resolve('.venv-wxauto/Scripts/python.exe')),accountId:'fixture',ownerNativeChatKey:'wechat:peer',expectedPeerUsername:'peer',timeoutMs,
    spawnProcess:(exe,args,options)=>{
      const child=new EventEmitter();child.stdout=new EventEmitter();child.stderr={resume(){}};child.pid=12345;child.killed=false;child.kills=0;
      child.kill=()=>{child.killed=true;child.kills++;queueMicrotask(()=>child.emit('close',-1));};
      child.stdin=new EventEmitter();child.stdin.end=line=>{child.input=JSON.parse(line);responder?.(child,children.length-1);};
      children.push(child);calls.push({exe,args,options});return child;
    }});
  return {client,children,calls};
}
const page=(overrides={})=>({source:'authenticated-native-owner-text-catalog-page',accountId:'fixture',nativeChatKey:'wechat:peer',
  snapshotId:'pinned-snapshot',scopeDigest:ownerTextScopeDigest('fixture','wechat:peer','peer'),scanScopeDigest:'a'.repeat(64),
  messages:[],observedNativeIds:[],scanned:1,complete:true,nextCursor:null,position:{descriptorIndex:0,rowid:1},...overrides});
const respond=(child,value)=>queueMicrotask(()=>{child.stdout.emit('data',Buffer.from(JSON.stringify({ok:true,value})));child.emit('close',0);});

test('catalog constructor inert, private scan secret stays in stdin and later page pins snapshot/cursor',async()=>{
  const f=catalogFixture((child,index)=>respond(child,index===0?page({complete:false,nextCursor:'opaque',position:{descriptorIndex:0,rowid:1}}):page({position:{descriptorIndex:0,rowid:2}})));
  assert.equal(f.calls.length,0);const result=await f.client.catalog();assert.equal(result.complete,true);assert.equal(f.calls.length,2);
  assert.match(f.children[0].input.scanKey,/^[a-f0-9]{64}$/);assert.equal(f.children[1].input.scanKey,f.children[0].input.scanKey);
  assert.equal(f.children[1].input.snapshotId,'pinned-snapshot');assert.equal(f.children[1].input.cursor,'opaque');
  assert.equal(JSON.stringify(f.calls).includes(f.children[0].input.scanKey),false);
  assert.equal(f.calls[0].options.windowsHide,true);f.client.close();
});

test('catalog refuses changed snapshot rather than silently mixing pages',async()=>{
  const f=catalogFixture((child,index)=>respond(child,index===0?page({complete:false,nextCursor:'opaque'}):page({snapshotId:'other',position:{descriptorIndex:0,rowid:2}})));
  await assert.rejects(f.client.catalog(),/page-invalid/);f.client.close();
});

test('catalog close, abort and timeout stop only their spawned helper and never retry',async()=>{
  for(const mode of ['close','abort','timeout']){
    const f=catalogFixture(null,5),abort=new AbortController(),work=f.client.catalog({signal:abort.signal});
    if(mode==='close')f.client.close();else if(mode==='abort')abort.abort();
    await assert.rejects(work,/stopped|timeout/);assert.equal(f.children.length,1);assert.equal(f.children[0].kills,1);f.client.close();
  }
});

test('catalog byte budget cancels owned helper rather than retaining an unbounded response',async()=>{
  const f=catalogFixture(child=>queueMicrotask(()=>{
    const chunk=Buffer.alloc(1024*1024,32);
    for(let i=0;i<65&&!child.killed;i++)child.stdout.emit('data',chunk);
  }));
  await assert.rejects(f.client.catalog(),/output-limit/);assert.equal(f.children[0].kills,1);f.client.close();
});
