import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {nativeGroupTextPaths} from './group-text-contract.mjs';
import {createChatQueue} from './chat-routing.mjs';
import {createGroupTextReadiness} from './group-text-readiness.mjs';

function fixture(t,overrides={}){
  const root=fs.mkdtempSync(path.resolve('artifacts/group-text-readiness-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const config={dataRoot:root,target:'owner',accountId:'account',nativeVoiceSources:{ownerNativeChatKey:'wechat:peer'}};
  const configPath=path.join(root,'config.json'),recoveryFile=path.join(root,'recovery.json');
  fs.writeFileSync(configPath,JSON.stringify(config));const configSha256=createHash('sha256').update(fs.readFileSync(configPath)).digest('hex');
  const queue=createChatQueue(),buffers=new Map();let revision=0,sessionId='original-session',running=false,settles=0,queries=0;
  const counts={incomingPending:0,outgoingPending:0,evidencePending:0,activeInputs:0,contextPending:0};
  const worker={version:1,mode:'ui',readsStopped:false,dirty:false,deferred:0};
  const options={config,nativeChatKey:'wechat:room@chatroom',target:'owner',routeForGroup:()=>({kind:'group',target:'owner',sessionId}),configPath,configSha256,recoveryFile,queue,buffers,getRevision:()=>revision,getSessionId:()=>sessionId,
    isReady:()=>true,dependenciesReady:()=>true,isClosed:()=>false,getWorkerStatus:()=>worker,
    history:{cutoverStatus:()=>({...counts}),async settleIncoming(){settles++;counts.incomingPending=0}},
    harness:{async isSessionRunning(id){assert.equal(id,sessionId);queries++;return running}},...overrides};
  const ready=createGroupTextReadiness(options);
  return {root,config,configPath,recoveryFile,queue,buffers,counts,worker,options,ready,
    revision:()=>revision++,session:value=>sessionId=value,running:value=>running=value,settles:()=>settles,queries:()=>queries};
}

test('constructor inert and activity only performs readonly checks without settling or Harness calls',t=>{
  const f=fixture(t);const original=fs.readFileSync;
  const mock=t.mock.method(fs,'readFileSync',()=>{throw Error('constructor must not read')});
  const meta=t.mock.method(fs,'lstatSync',()=>{throw Error('constructor must not inspect filesystem')});
  const second=createGroupTextReadiness(f.options);mock.mock.restore();meta.mock.restore();assert.equal(fs.readFileSync,original);
  assert.equal(second.activity().quiet,true);assert.equal(f.settles(),0);assert.equal(f.queries(),0);
  assert.deepEqual(fs.readdirSync(f.root).sort(),['config.json']);
  assert.deepEqual(second.activity().counts,{incomingPending:0,outgoingPending:0,evidencePending:0,activeInputs:0,contextPending:0,pending:0,active:false,paused:false,buffered:0,deferredPending:0});
});

test('real owner queue pause is allowed only explicitly while group queue is irrelevant',async t=>{
  const f=fixture(t),release=f.queue.pause('owner');
  assert.equal(f.ready.activity().quiet,true);assert.equal((await f.ready.inspect({settle:false})).code,'group-text-readiness-queue-paused');
  assert.equal((await f.ready.inspect({allowPaused:true,settle:false})).quiet,true);assert.equal(f.settles(),0);
  let releaseGroup;const group=f.queue('group',()=>new Promise(resolve=>releaseGroup=resolve));await new Promise(r=>setImmediate(r));
  release();assert.equal(f.ready.activity().quiet,true);releaseGroup();await group;
});

test('real pending/active owner task plus buffer and history counts deny quiet without leaking contents',async t=>{
  const f=fixture(t);let release;const task=f.queue('owner',()=>new Promise(resolve=>release=resolve));await new Promise(r=>setImmediate(r));
  f.counts.evidencePending=3;f.buffers.set('owner',[{text:'never-export-private-text'}]);
  const state=f.ready.activity();assert.equal(state.code,'group-text-readiness-queue-busy');assert.equal(state.counts.pending,1);assert.equal(state.counts.active,true);assert.equal(state.counts.evidencePending,3);assert.equal(state.counts.buffered,1);
  assert.equal(JSON.stringify(state).includes('never-export-private-text'),false);release();await task;
  f.counts.evidencePending=0;assert.equal(f.ready.activity().code,'group-text-readiness-buffer-pending');
});

test('inspect settles incoming journal then queries actual session-running method and rechecks',async t=>{
  const f=fixture(t);f.counts.incomingPending=1;assert.equal(f.ready.activity().quiet,false);
  const result=await f.ready.inspect();assert.equal(result.quiet,true);assert.equal(f.settles(),1);assert.equal(f.queries(),1);
  f.running(true);assert.equal((await f.ready.inspect()).code,'group-text-readiness-session-running');
  f.running({running:false});assert.equal((await f.ready.inspect()).code,'group-text-readiness-session-unverified');
});

test('GET settlefalse cannot drain journal; failed journals or session lookup remain nonquiet',async t=>{
  const f=fixture(t);f.counts.outgoingPending=1;assert.equal((await f.ready.inspect({settle:false})).code,'group-text-readiness-history-pending');assert.equal(f.settles(),0);assert.equal(f.queries(),0);
  f.counts.outgoingPending=0;
  const broken=createGroupTextReadiness({...f.options,history:{cutoverStatus:()=>f.counts,settleIncoming:async()=>{throw Error('private body')}}});
  assert.equal((await broken.inspect()).code,'group-text-readiness-inspection-failed');
  const unavailable=createGroupTextReadiness({...f.options,harness:{isSessionRunning:async()=>{throw Error('private path')}}});
  assert.equal((await unavailable.inspect()).code,'group-text-readiness-inspection-failed');
});

test('revision or session changes while awaiting readonly inspection deny quiet',async t=>{
  const f=fixture(t);
  for(const change of [f.revision,()=>f.session('rotated-session')]){
    const ready=createGroupTextReadiness({...f.options,harness:{isSessionRunning:async()=>{change();return false}}});
    assert.match((await ready.inspect()).code,/group-text-readiness-(activity|config)-changed/);
  }
});

test('exact loaded config SHA and bound account/key identity cannot change',t=>{
  const f=fixture(t);fs.appendFileSync(f.configPath,' ');assert.equal(f.ready.activity().code,'group-text-readiness-config-changed');
  fs.writeFileSync(f.configPath,JSON.stringify(f.config));f.config.accountId='other-account';
  assert.equal(f.ready.activity().code,'group-text-readiness-config-changed');
});

test('pending owner text recovery denies without claiming; claimed and group/media recovery remain allowed',t=>{
  const f=fixture(t);const message={id:'recovery-fixture',target:'owner',kind:'group',text:'never-export-private-text',sender:'Fixture'};
  const write=(state,messages)=>fs.writeFileSync(f.recoveryFile,JSON.stringify({version:1,state,messages}));
  write('pending',[message]);const bytes=fs.readFileSync(f.recoveryFile);
  const status=f.ready.activity();assert.equal(status.code,'group-text-readiness-recovery-group-text-pending');assert.equal(JSON.stringify(status).includes(message.text),false);assert.deepEqual(fs.readFileSync(f.recoveryFile),bytes);
  write('claimed',[message]);assert.equal(f.ready.activity().quiet,true);
  write('pending',[{...message,target:'group',kind:'group'},{...message,mediaType:'voice'}]);assert.equal(f.ready.activity().quiet,true);
  fs.writeFileSync(f.recoveryFile,'{partial');assert.equal(f.ready.activity().code,'group-text-readiness-recovery-unverified');
});

test('worker unknown/dirty/native states, readiness loss and ASR busy deny quiet',t=>{
  const f=fixture(t);
  for(const patch of [{mode:'native'},{mode:'blocked'},{version:0},{deferred:1},{dirty:true},{readsStopped:true}]){
    const ready=createGroupTextReadiness({...f.options,getWorkerStatus:()=>({...f.worker,...patch})});assert.equal(ready.activity().code,'group-text-readiness-worker-unverified');
  }
  for(const [name,value,code] of [['isClosed',()=>true,'closed'],['isReady',()=>false,'not-ready'],['dependenciesReady',()=>false,'not-ready'],['extraBusy',()=>true,'extra-busy']]){
    const ready=createGroupTextReadiness({...f.options,[name]:value});assert.equal(ready.activity().code,'group-text-readiness-'+code);
  }
  f.worker.mode='preparing';assert.equal(f.ready.activity().quiet,true);
});

test('real hardlinked config denied where supported',t=>{
  const f=fixture(t);try{fs.linkSync(f.configPath,path.join(f.root,'alias'))}catch(e){if(['EPERM','EACCES','ENOTSUP'].includes(e.code)){t.skip('OS denies hardlinks');return}throw e}
  assert.equal(f.ready.activity().code,'group-text-readiness-file-unverified');
});

test('config/recovery ancestor links fail even for missing recovery leaf; openhandle mutation denied',t=>{
  const f=fixture(t),original=fs.lstatSync;
  const mock=t.mock.method(fs,'lstatSync',function(file,...args){
    const result=original.call(fs,file,...args);
    if(String(file)!==f.root)return result;
    return new Proxy(result,{get(target,key){if(key==='isSymbolicLink')return ()=>true;const v=Reflect.get(target,key);return typeof v==='function'?v.bind(target):v}});
  });
  assert.equal(f.ready.activity().code,'group-text-readiness-file-unverified');mock.mock.restore();
  const parent=path.join(f.root,'recovery-parent');fs.mkdirSync(parent);
  const ready=createGroupTextReadiness({...f.options,recoveryFile:path.join(parent,'absent.json')});
  const recoveryMock=t.mock.method(fs,'lstatSync',function(file,...args){
    const info=original.call(fs,file,...args);if(String(file)!==parent)return info;
    return new Proxy(info,{get(target,key){if(key==='isSymbolicLink')return ()=>true;const v=Reflect.get(target,key);return typeof v==='function'?v.bind(target):v}});
  });
  assert.equal(ready.activity().code,'group-text-readiness-file-unverified');recoveryMock.mock.restore();
  const linkMock=t.mock.method(fs,'lstatSync',function(file,...args){const info=original.call(fs,file,...args);return String(file)===f.configPath?new Proxy(info,{get(target,key){if(key==='nlink')return 2;const v=Reflect.get(target,key);return typeof v==='function'?v.bind(target):v}}):info});
  assert.equal(f.ready.activity().code,'group-text-readiness-file-unverified');linkMock.mock.restore();
  const realFstat=fs.fstatSync;
  t.mock.method(fs,'fstatSync',function(...args){const info=realFstat.apply(fs,args);return {...info,ino:info.ino+1024}});
  assert.equal(f.ready.activity().code,'group-text-readiness-file-changed');
});

test('callback exceptions cannot inject arbitrary error text into fixed diagnostics',t=>{
  const f=fixture(t),ready=createGroupTextReadiness({...f.options,getRevision:()=>{throw Object.assign(Error('private-body'),{safeCode:'private-body'})}});
  const status=ready.activity();assert.equal(status.code,'group-text-readiness-unverified');assert.equal(JSON.stringify(status).includes('private-body'),false);
});

test('unresolved worker deferral backups block baseline using metadata only and never read or clear payload',t=>{
  const f=fixture(t),folder=path.join(nativeGroupTextPaths({dataRoot:f.root,nativeChatKey:'wechat:room@chatroom'}).directory,'deferred');fs.mkdirSync(folder,{recursive:true});
  const file=path.join(folder,'a'.repeat(32)+'.json');fs.writeFileSync(file,'unresolved-private-ui-input');
  const original=fs.readFileSync;t.mock.method(fs,'readFileSync',function(candidate,...args){if(String(candidate)===file)throw Error('must not read deferral body');return original.call(fs,candidate,...args)});
  const result=f.ready.activity();assert.equal(result.quiet,false);assert.equal(result.code,'group-text-cutover-pending-deferred');assert.equal(result.counts.deferredPending,1);
  assert.equal(JSON.stringify(result).includes('unresolved-private-ui-input'),false);assert.equal(fs.existsSync(file),true);
});

test('partial/unknown deferral files and linked directory fail closed without body reads',t=>{
  const f=fixture(t),folder=path.join(nativeGroupTextPaths({dataRoot:f.root,nativeChatKey:'wechat:room@chatroom'}).directory,'deferred');fs.mkdirSync(folder,{recursive:true});
  const partial=path.join(folder,'b'.repeat(32)+'.json.tmp');fs.writeFileSync(partial,'partial');
  assert.equal(f.ready.activity().code,'group-text-readiness-deferred-unverified');fs.unlinkSync(partial);
  const original=fs.lstatSync;t.mock.method(fs,'lstatSync',function(candidate,...args){const info=original.call(fs,candidate,...args);
    return String(candidate)===folder?new Proxy(info,{get(target,key){if(key==='isSymbolicLink')return ()=>true;const v=Reflect.get(target,key);return typeof v==='function'?v.bind(target):v}}):info});
  assert.equal(f.ready.activity().code,'group-text-readiness-deferred-unverified');
});
