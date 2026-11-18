import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {PassThrough,Writable} from 'node:stream';
import {createSyncManager} from './sync-manager.mjs';
import path from 'node:path';

function fixture({collectError=false,importError=false,eofAfterCollect=false,holdImport=false}={}) {
  const root=path.resolve('test-data-not-created');
  const candidate={jobId:'job-1',candidatePath:path.join(root,'snapshots/windows-live/snapshots/snap-1'),
    cursorSource:'wechat-snapshot:fixture-account',accountId:'fixture-account',
    captureReceipt:{snapshot_id:'snapshot-1',keys_persisted:false}};
  const commands=[],calls=[],states=[];let spawned=0,resolveImport,child;
  const spawn=()=>{
    spawned++;child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();child.exitCode=null;
    child.kill=()=>{child.exitCode=0;child.stdout.end();child.emit('exit',0);return true};
    child.stdin=new Writable({write(chunk,_encoding,done){
      const request=JSON.parse(chunk.toString());commands.push(request);
      setImmediate(()=>{
        if(child.exitCode!==null)return;
        const value=request.op==='collect'?candidate:request.op==='commit'?{committed:true,jobId:'job-1'}:{aborted:true};
        child.stdout.write(JSON.stringify(request.op==='collect'&&collectError?{id:request.id,ok:false,error:'capture-test-failed'}:{id:request.id,ok:true,value})+'\n');
        if(request.op==='collect'&&eofAfterCollect)setImmediate(()=>child.kill());
      });done();
    }});
    return child;
  };
  const imported={complete:true,snapshot_id:'snapshot-1',account_id:'fixture-account',scanned:2,inserted:2};
  const client={async request(op,args){
    calls.push({op,args});
    if(op==='status')return {cursors:{'wechat-snapshot:fixture-account':{snapshot_id:'previous'}}};
    if(op==='import_snapshot'){
      if(holdImport)return await new Promise(resolve=>{resolveImport=()=>resolve(imported)});
      if(importError)throw new Error('synthetic content must not enter status');
      return imported;
    }
    return {updated:true};
  }};
  const options={python:'fake-python',dataRoot:root,vaultClient:client,
    capture:{enabled:true,dbRoot:'explicit-db',sourceId:'explicit-source',accountId:'fixture-account'},
    isReady:()=>true,spawn,onState:value=>states.push(value),timeoutMs:1000};
  return {options,commands,calls,states,spawned:()=>spawned,releaseImport:()=>resolveImport?.(),hasImport:()=>!!resolveImport,getChild:()=>child};
}

test('success uses producer collect, sole vault writer import, then producer commit',async()=>{
  const f=fixture();const manager=createSyncManager(f.options);
  const result=await manager.refresh();await manager.stop();
  assert.equal(result.ok,true);assert.equal(f.spawned(),1);
  assert.deepEqual(f.calls.map(x=>x.op),['status','import_snapshot']);
  assert.deepEqual(f.commands.map(x=>x.op),['collect','commit','abort']);
  assert.equal(f.commands[1].args.importResult.complete,true);
  assert.ok(f.states.some(x=>x.state==='complete'));
  assert.ok(!JSON.stringify(f.states).includes('explicit-db'));
  assert.ok(!JSON.stringify(f.states).includes('fixture-account'));
});

test('disabled by default and not-ready client never start a collector',async()=>{
  const f=fixture();const a=createSyncManager({...f.options,capture:{}});a.start();await a.stop();
  const b=createSyncManager({...f.options,isReady:()=>false});
  assert.equal((await b.refresh()).code,'wechat-not-ready');await b.stop();assert.equal(f.spawned(),0);
});

test('audio capture is explicit and uses the same producer and sole history writer',async()=>{
  for(const enabled of [undefined,false,true]){
    const f=fixture();if(enabled!==undefined)f.options.capture.includeAudio=enabled;
    const manager=createSyncManager(f.options);
    assert.equal((await manager.refresh()).ok,true);await manager.stop();
    assert.equal(f.spawned(),1);
    assert.deepEqual(f.calls.map(x=>x.op),['status','import_snapshot']);
    assert.equal(f.commands[0].args.scope,'chat');
    if(enabled===true)assert.equal(f.commands[0].args.include_audio,true);
    else assert.equal(Object.hasOwn(f.commands[0].args,'include_audio'),false);
  }
  const f=fixture();f.options.capture.includeAudio='true';
  const manager=createSyncManager(f.options);
  assert.equal((await manager.refresh()).code,'capture-audio-option-invalid');
  await manager.stop();assert.equal(f.spawned(),0);
});

test('capture failure cannot import or publish',async()=>{
  const f=fixture({collectError:true});const manager=createSyncManager(f.options);
  assert.equal((await manager.refresh()).code,'capture-test-failed');await manager.stop();
  assert.equal(f.calls.length,0);assert.ok(!f.commands.some(x=>x.op==='commit'));
});

test('import failure restores old cursor through the same writer and aborts',async()=>{
  const f=fixture({importError:true});const manager=createSyncManager(f.options);
  assert.equal((await manager.refresh()).ok,false);await manager.stop();
  assert.deepEqual(f.calls.map(x=>x.op),['status','import_snapshot','set_cursor']);
  assert.deepEqual(f.calls.at(-1).args.cursor,{snapshot_id:'previous'});
  assert.ok(!f.commands.some(x=>x.op==='commit'));
  assert.ok(!JSON.stringify(f.states).includes('synthetic content'));
});

test('no old cursor uses clear_cursor instead of inventing a checkpoint',async()=>{
  const f=fixture({importError:true});const original=f.options.vaultClient.request;
  f.options.vaultClient.request=async(op,args)=>op==='status'?{cursors:{}}:original(op,args);
  const manager=createSyncManager(f.options);await manager.refresh();await manager.stop();
  assert.equal(f.calls.at(-1).op,'clear_cursor');
  assert.equal(f.calls.at(-1).args.source,'wechat-snapshot:fixture-account');
});

test('EOF between candidate and commit rolls back via sole writer',async()=>{
  const f=fixture({eofAfterCollect:true,holdImport:true});const manager=createSyncManager(f.options);
  const run=manager.refresh();
  while(!f.hasImport())await new Promise(resolve=>setImmediate(resolve));
  await new Promise(resolve=>setImmediate(resolve));await new Promise(resolve=>setImmediate(resolve));
  f.releaseImport();assert.equal((await run).ok,false);await manager.stop();
  assert.equal(f.calls.at(-1).op,'set_cursor');
});

test('overlapping refresh calls share one job; stop during import aborts without commit',async()=>{
  const f=fixture({holdImport:true});const manager=createSyncManager(f.options);
  const one=manager.refresh(),two=manager.refresh();assert.equal(one,two);
  while(!f.hasImport())await new Promise(resolve=>setImmediate(resolve));
  const stopping=manager.stop();f.releaseImport();
  assert.equal((await one).code,'sync-stopped');await stopping;
  assert.equal(f.spawned(),1);assert.equal(f.calls.at(-1).op,'set_cursor');
  assert.ok(!f.commands.some(x=>x.op==='commit'));
});

test('start refreshes immediately and schedules exactly five-minute refreshes after login readiness',async t=>{
  const f=fixture();let tick,delay,ready=false,cleared=false;
  t.mock.method(globalThis,'setInterval',(callback,ms)=>{tick=callback;delay=ms;return {unref(){}}});
  t.mock.method(globalThis,'clearInterval',()=>{cleared=true});
  const manager=createSyncManager({...f.options,isReady:()=>ready});manager.start();
  assert.equal(delay,300000);assert.equal(f.spawned(),0);
  ready=true;tick();await manager.refresh();assert.equal(f.spawned(),1);
  // The collector is reused after a clean commit (no new process per capture).
  tick();await manager.refresh();assert.equal(f.spawned(),1);
  await manager.stop();assert.equal(cleared,true);
});

test('exclusive lease precedes waiting old job and owns a distinct subsequent capture',async()=>{
  const f=fixture({holdImport:true}),manager=createSyncManager(f.options);let entered=false,saved;
  const prior=manager.refresh();
  while(!f.hasImport())await new Promise(r=>setImmediate(r));
  const lease=manager.exclusiveRefresh(async owner=>{entered=true;saved=owner;assert.equal(owner.status().exclusive,true);return await owner.refresh()});
  assert.equal(entered,false);
  assert.deepEqual(await manager.refresh(),{ok:false,code:'sync-exclusive-busy'});
  assert.deepEqual(await manager.exclusiveRefresh(()=>{}),{ok:false,code:'sync-exclusive-busy'});
  f.releaseImport();await prior;
  while(f.calls.filter(c=>c.op==='import_snapshot').length<2)await new Promise(r=>setImmediate(r));
  assert.equal(entered,true);assert.equal(f.spawned(),1);f.releaseImport();
  assert.equal((await lease).ok,true);assert.equal(manager.status().exclusive,false);
  assert.deepEqual(await saved.refresh(),{ok:false,code:'sync-exclusive-expired'});await manager.stop();
});

test('exclusive callback exception drains its owned capture and releases lease in finally',async()=>{
  const f=fixture(),manager=createSyncManager(f.options);
  await assert.rejects(manager.exclusiveRefresh(owner=>{void owner.refresh();throw Error('fixture callback')}),/fixture callback/);
  assert.equal(manager.status().exclusive,false);assert.equal(f.spawned(),1);
  assert.equal((await manager.refresh()).ok,true);assert.equal(f.spawned(),1);await manager.stop();
});

test('a failed capture discards the collector so the next run starts a fresh one',async()=>{
  const f=fixture({importError:true}),manager=createSyncManager(f.options);
  assert.equal((await manager.refresh()).ok,false);
  assert.equal((await manager.refresh()).ok,false);
  assert.equal(f.spawned(),2);await manager.stop();
});

test('stop while exclusive awaits previous capture does not invoke callback',async()=>{
  const f=fixture({holdImport:true}),manager=createSyncManager(f.options);let entered=false;
  const prior=manager.refresh();while(!f.hasImport())await new Promise(r=>setImmediate(r));
  const lease=manager.exclusiveRefresh(()=>{entered=true});const stopping=manager.stop();f.releaseImport();
  await prior;await stopping;assert.equal((await lease).code,'sync-stopped');assert.equal(entered,false);
  assert.equal((await manager.exclusiveRefresh(()=>{})).code,'sync-stopped');assert.equal(manager.status().exclusive,false);
});

test('a code-shaped vault error keeps its code; free text never enters status',async()=>{
  const f=fixture();const original=f.options.vaultClient.request;
  f.options.vaultClient.request=async(op,args)=>{if(op==='import_snapshot'){await original(op,args);throw new Error('vault-worker-restart-backoff');}return original(op,args);};
  const manager=createSyncManager(f.options);
  assert.equal((await manager.refresh()).code,'sync-failed:vault-worker-restart-backoff');await manager.stop();
  const g=fixture({importError:true});const other=createSyncManager(g.options);
  assert.equal((await other.refresh()).code,'sync-failed');await other.stop();
});
