import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createExternalVideoAudioStore} from './external-video-audio-store.mjs';
import {createContinuousDouyinSupervisor} from './continuous-douyin-supervisor.mjs';
const captureProof={protocolVersion:2,processSpecificCapture:true,captureScopeVersion:1,callbackFrameNormalizationVersion:1};

function fixture(options={}){
  let clock=1000,enabled=true,sources=[],page={video:{id:'1234567890123',paused:false,readyState:4}},reads=0;
  const clients=[],records=[],gaps=[];
  const manager=createContinuousDouyinSupervisor({now:()=>clock,enabled:()=>enabled,
    getSources:async()=>({sources}),onTranscript:async value=>records.push(value),onGap:value=>gaps.push(value),
    ...options,createClient:callbacks=>{
      const client={state:'idle',starts:[],pauses:0,stops:0,callbacks,captureProof,
        status:()=>({state:client.state,captureProof:client.captureProof}),start:pid=>{client.starts.push(pid);client.state='running';},
        pause:()=>{client.pauses++;client.state='paused';},resume:()=>{client.state='running';},
        stop:async()=>{client.stops++;client.state='exited';}};
      clients.push(client);return client;
    }});
  return {manager,clients,records,gaps,bind:()=>manager.registerSource(async()=>{reads++;return page;}),
    setTime:value=>clock=value,setEnabled:value=>enabled=value,setSources:value=>sources=value,setPage:value=>page=value,reads:()=>reads};
}

test('inert registration never opens a browser; only one existing public playing source starts capture',async()=>{
  const f=fixture();assert.equal(f.reads(),0);f.bind();assert.equal(f.reads(),0);
  await f.manager.tick();assert.equal(f.reads(),0);assert.equal(f.clients.length,0);
  f.setSources([{pid:12},{pid:13}]);await f.manager.tick();assert.equal(f.clients.length,0);
  f.setSources([{pid:12}]);await f.manager.tick();assert.deepEqual(f.clients[0].starts,[12]);
  f.setTime(2000);f.setPage({privateChatVisible:true,video:{id:'1234567890123',paused:false,readyState:4}});
  await f.manager.tick();assert.equal(f.clients[0].state,'paused');
  f.setEnabled(false);await f.manager.tick();assert.equal(f.clients[0].stops,1);await f.manager.stop();
});

test('removed source retains full late segment privately, without page data',async()=>{
  const saved=[];let finish;
  const f=fixture({onUnboundTranscript:value=>new Promise(resolve=>{saved.push(value);finish=resolve;})});
  const unbind=f.bind();f.setSources([{pid:12}]);await f.manager.tick();unbind();
  const segment={startedAt:1,endedAt:11,transcript:'synthetic full clip'};
  const delivery=f.clients[0].callbacks.onSegment(segment);
  assert.equal(f.manager.status().privateSegments,0);assert.equal(saved.length,1);
  assert.deepEqual(Object.keys(saved[0]).sort(),['captureProof','reason','segment']);
  assert.equal(saved[0].reason,'source-unavailable');assert.equal(saved[0].segment,segment);
  finish();await delivery;assert.equal(f.manager.status().privateSegments,1);assert.equal(f.records.length,0);
  await f.manager.stop();
});

test('private observation gap and source failure retain only through validated private callback',async()=>{
  const saved=[];const f=fixture({onUnboundTranscript:async value=>saved.push(value)});
  f.bind();f.setSources([{pid:12}]);await f.manager.tick();
  f.setTime(2000);f.setPage({privateChatVisible:true,secret:'must not leave source'});await f.manager.tick();
  f.setTime(3000);f.setPage({video:{id:'1234567890123',paused:false,readyState:4}});
  await f.clients[0].callbacks.onSegment({startedAt:1,endedAt:3,transcript:'synthetic'});
  assert.equal(saved[0].reason,'context-unproven');assert.equal(JSON.stringify(saved).includes('secret'),false);
  f.manager.registerSource(async()=>{throw Error('private source diagnostic');});
  await f.clients[0].callbacks.onSegment({startedAt:3,endedAt:4});
  assert.equal(saved[1].reason,'context-unavailable');assert.equal(f.manager.status().privateSegments,2);
  f.clients[0].captureProof=null;
  await f.clients[0].callbacks.onSegment({startedAt:4,endedAt:5});assert.equal(saved.length,2);
  await f.manager.stop();
});

test('private writer failure is counted after await; public failure never falls back privately',async()=>{
  let privateCalls=0;
  const f=fixture({onUnboundTranscript:async()=>{privateCalls++;throw Error('writer failed');},
    onTranscript:async()=>{throw Error('could have committed');}});
  const unbind=f.bind();f.setSources([{pid:12}]);await f.manager.tick();
  f.setTime(11000);await f.clients[0].callbacks.onSegment({startedAt:1,endedAt:11});
  assert.equal(f.manager.status().state,'public-transcript-failed');assert.equal(privateCalls,0);
  unbind();await f.clients[0].callbacks.onSegment({startedAt:11,endedAt:12});
  assert.equal(privateCalls,1);assert.equal(f.manager.status().privateSegments,0);
  assert.equal(f.manager.status().privateFailures,1);await f.manager.stop();
});

test('closed, disabled and old-client late results never reach either writer',async()=>{
  for(const mode of ['closed','disabled','replaced']){
    const saved=[];const f=fixture({onUnboundTranscript:async value=>saved.push(value)});
    f.bind();f.setSources([{pid:12}]);await f.manager.tick();const old=f.clients[0];let resolve;
    f.manager.registerSource(()=>new Promise(r=>resolve=r));
    const pending=old.callbacks.onSegment({startedAt:1,endedAt:2});
    if(mode==='closed')await f.manager.stop();
    else if(mode==='disabled')f.setEnabled(false);
    else {f.bind();f.setSources([{pid:13}]);f.setTime(3000);await f.manager.tick();}
    resolve({video:{id:'1234567890123',paused:false,readyState:4}});await pending;
    await old.callbacks.onSegment({startedAt:2,endedAt:3});
    assert.equal(saved.length,0);assert.equal(f.records.length,0);await f.manager.stop();
  }
});

test('cross-video audio stays complete and unattributed for the store; private intervals cannot publish',async()=>{
  const f=fixture();f.bind();f.setSources([{pid:12}]);await f.manager.tick();
  f.setTime(6000);f.setPage({video:{id:'9999999999999',paused:false,readyState:4}});await f.manager.tick();
  f.setTime(11000);await f.clients[0].callbacks.onSegment({startedAt:1.1,endedAt:10.9,transcript:'synthetic mixed content'});
  assert.equal(f.records.length,1);assert.equal(f.records[0].segment.transcript,'synthetic mixed content');
  assert.deepEqual(f.records[0].observations.map(o=>o.videoId),['1234567890123','9999999999999','9999999999999']);
  f.setTime(12000);f.setPage({privateChatVisible:true});await f.manager.tick();
  f.setTime(13000);f.setPage({video:{id:'9999999999999',paused:false,readyState:4}});await f.manager.tick();
  f.setTime(14000);await f.clients[0].callbacks.onSegment({startedAt:11,endedAt:13.5,transcript:'must not publish'});
  assert.equal(f.records.length,1);assert.equal(f.manager.status().unbound,1);await f.manager.stop();
});

test('stop during pending state read cannot start a worker or dispatch late material',async()=>{
  const f=fixture();f.setSources([{pid:12}]);let resolve;
  f.manager.registerSource(()=>new Promise(r=>resolve=r));
  const reading=f.manager.tick();await Promise.resolve();await f.manager.stop();resolve({video:{id:'1234567890123',paused:false,readyState:4}});
  await reading;assert.equal(f.clients.length,0);assert.equal(f.records.length,0);
});

test('an exited helper is not restarted repeatedly for the same browser PID',async()=>{
  const f=fixture();f.bind();f.setSources([{pid:12}]);await f.manager.tick();
  f.clients[0].state='exited';f.setTime(2000);await f.manager.tick();f.setTime(3000);await f.manager.tick();
  assert.equal(f.clients.length,1);assert.equal(f.manager.status().state,'worker-exited-no-retry');
  f.setTime(4000);f.setSources([{pid:13}]);await f.manager.tick();assert.equal(f.clients.length,2);
  assert.deepEqual(f.clients[1].starts,[13]);await f.manager.stop();
});

test('validated supervisor output crosses the real persistent store and is queryable after restart',async t=>{
  const root=fs.mkdtempSync(path.resolve('artifacts/continuous-audio-integration-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const producer=Symbol(),config={dataRoot:root,trustedProducer:producer,resolveAccess:id=>id==='group'?'group':null,now:()=>12000};
  const store=createExternalVideoAudioStore(config),writer=store.writerFor(producer);
  let clock=1000,page={video:{id:'1234567890123',paused:false,readyState:4}},callback;
  const manager=createContinuousDouyinSupervisor({now:()=>clock,getSources:async()=>({sources:[{pid:12}]}),
    onTranscript:value=>writer.recordContinuous(value),createClient:handlers=>{callback=handlers.onSegment;return{
      start(){},status:()=>({state:'running',captureProof}),pause(){},stop:async()=>{}};}});
  manager.registerSource(async()=>page);await manager.tick();
  clock=6000;page={video:{id:'9999999999999',paused:false,readyState:4}};await manager.tick();clock=11000;
  await callback({event:'segment',sessionId:'a'.repeat(32),pid:12,sequence:0,startedAt:1,endedAt:11,callbackObservedAt:11,
    timestampBasis:'callback-arrival-minus-audio-frames',durationSeconds:10,frames:480000,sampleRate:48000,channels:2,
    encoding:'pcm-s16le',wavSha256:'b'.repeat(64),audioRetained:false,asrStatus:'speech',transcript:'synthetic mixed dialogue',
    backend:'sensevoice-int8',videoIdentity:'unknown',playingProof:false});
  const restored=createExternalVideoAudioStore(config),entries=restored.query({sessionId:'group'}).entries;
  assert.equal(entries.length,1);assert.equal(entries[0].text,'synthetic mixed dialogue');
  assert.equal(entries[0].videoId,null);assert.equal(entries[0].playingProof,false);
  assert.equal(entries[0].videoIdentityStatus,'unattributed-or-mixed');await manager.stop();
});

test('public page observations alone cannot publish audio from an unverified process backend',async()=>{
  const f=fixture();f.bind();f.setSources([{pid:12}]);await f.manager.tick();
  f.clients[0].captureProof=null;
  await f.clients[0].callbacks.onSegment({startedAt:1,endedAt:2});
  assert.equal(f.records.length,0);assert.equal(f.manager.status().unbound,1);
  await f.manager.stop();
});

test('disable during source enumeration cannot read page or start capture',async()=>{
  let resolve;
  const f=fixture({getSources:()=>new Promise(r=>resolve=r)});f.bind();
  const pending=f.manager.tick();f.setEnabled(false);resolve({sources:[{pid:12}]});await pending;
  assert.equal(f.reads(),0);assert.equal(f.clients.length,0);await f.manager.stop();
});

test('stale page resolution cannot start or resume after removal, replacement or same-function registration',async()=>{
  for(const mode of ['removed','replaced','same-function','disabled']){
    const f=fixture();f.bind();f.setSources([{pid:12}]);await f.manager.tick();
    let resolve;const getter=()=>new Promise(r=>resolve=r);
    const unregister=f.manager.registerSource(getter);const pending=f.manager.tick();await Promise.resolve();
    if(mode==='disabled')f.setEnabled(false);
    else if(mode==='removed')unregister();
    else if(mode==='replaced')f.bind();
    else {f.manager.registerSource(getter);unregister();} // old handle must not remove newer registration
    resolve({video:{id:'1111111111111',paused:false,readyState:4}});await pending;
    assert.equal(f.clients.length,1);assert.equal(f.clients[0].state,'paused');
    // Prove the stale sample did not become the before boundary for a later clip.
    f.setEnabled(true);f.manager.registerSource(async()=>({video:{id:'2222222222222',paused:false,readyState:4}}));
    f.setTime(3000);await f.clients[0].callbacks.onSegment({startedAt:1,endedAt:3});
    assert.equal(f.records.length,0);assert.equal(f.manager.status().unbound,1);
    await f.manager.stop();
  }
});

test('same-function re-registration invalidates receive span and old unregister handle',async()=>{
  const saved=[];const f=fixture({onUnboundTranscript:async value=>saved.push(value)});
  f.bind();f.setSources([{pid:12}]);await f.manager.tick();let resolve;
  const getter=()=>new Promise(r=>resolve=r);const oldUnregister=f.manager.registerSource(getter);
  const pending=f.clients[0].callbacks.onSegment({startedAt:1,endedAt:3});
  f.manager.registerSource(getter);oldUnregister();
  resolve({video:{id:'1234567890123',paused:false,readyState:4}});await pending;
  assert.equal(f.records.length,0);assert.equal(saved.length,1);assert.equal(saved[0].reason,'context-unproven');
  await f.manager.stop();
});
