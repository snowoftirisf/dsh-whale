import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {PassThrough,Writable} from 'node:stream';
import {createContinuousDouyinClient} from './continuous-douyin-client.mjs';

const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const sessionId='a'.repeat(32),browserPid=123,workerPid=45678;
function fixture(t,updates={}){
  const calls=[],writes=[],segments=[],gaps=[],reports=[];
  const worker=new EventEmitter();worker.pid=workerPid;worker.exitCode=null;worker.kills=0;
  worker.stdout=new PassThrough();worker.stderr=new PassThrough();
  worker.stdin=new Writable({write(chunk,_encoding,done){writes.push(chunk.toString());done();}});
  worker.kill=()=>{worker.kills++;};
  const spawnProcess=(command,args,options)=>{calls.push({command,args,options});return command==='taskkill.exe'?new EventEmitter():worker;};
  const client=createContinuousDouyinClient({python:'C:/fixture/python.exe',script:'C:/fixture/continuous.py',
    cacheRoot:'C:/fixture/cache',modelRoot:'C:/fixture/models',spawnProcess,
    report:(code,facts)=>reports.push({code,facts}),onSegment:value=>segments.push(value),onGap:value=>gaps.push(value),
    platform:'win32',shutdownGraceMs:100,...updates});
  const line=value=>worker.stdout.write(JSON.stringify({sessionId,pid:browserPid,...value})+'\n');
  const started=(changes={})=>line({event:'started',sampleRate:48000,channels:2,chunkFrames:480000,videoIdentity:'unknown',playingProof:false,
    protocolVersion:2,processSpecificCapture:true,captureScopeVersion:1,callbackFrameNormalizationVersion:1,...changes});
  const segment=(sequence=0,changes={})=>({event:'segment',sequence,startedAt:1000+sequence*10,
    endedAt:1010+sequence*10,callbackObservedAt:1010+sequence*10,
    timestampBasis:'callback-arrival-minus-audio-frames',durationSeconds:10,frames:480000,
    sampleRate:48000,channels:2,encoding:'pcm-s16le',wavSha256:'b'.repeat(64),audioRetained:false,
    asrStatus:'speech',transcript:'synthetic external speech',backend:'sensevoice-int8',videoIdentity:'unknown',playingProof:false,...changes});
  const exit=(code=0)=>{worker.exitCode=code;worker.emit('exit',code,null);};
  t.after(()=>{if(worker.exitCode===null){client.stop();exit();}worker.stdout.destroy();worker.stderr.destroy();});
  return {client,calls,writes,segments,gaps,reports,worker,line,started,segment,exit};
}

test('constructor is inert; explicit start owns exactly one helper and sends fixed configuration',t=>{
  const f=fixture(t);assert.equal(f.client.status().state,'idle');assert.equal(f.calls.length,0);
  f.client.start(browserPid);f.client.start(browserPid);assert.equal(f.calls.length,1);
  assert.deepEqual(JSON.parse(f.writes[0]),{op:'start',pid:browserPid,cacheRoot:'C:/fixture/cache',modelRoot:'C:/fixture/models'});
  assert.equal(f.calls[0].options.windowsHide,true);assert.equal(f.calls[0].options.env.CUDA_VISIBLE_DEVICES,'');
  f.started();assert.equal(f.client.status().state,'running');assert.equal(f.client.status().sessionId,sessionId);
});

test('multiple valid segments preserve frame/time/hash evidence and all three ASR states',t=>{
  const f=fixture(t);f.client.start(browserPid);f.started();f.line(f.segment(0));
  f.line(f.segment(1,{transcript:'',asrStatus:'no-speech'}));f.line(f.segment(2,{transcript:'',asrStatus:'no-transcript'}));
  assert.equal(f.segments.length,3);assert.deepEqual(f.segments.map(x=>x.sequence),[0,1,2]);
  assert.equal(f.segments[0].frames,480000);assert.equal(f.segments[0].playingProof,false);
  assert.equal(f.segments[0].videoIdentity,'unknown');assert.equal(f.client.status().segments,3);
  f.worker.stderr.write('PRIVATE TRANSCRIPT THAT MUST NEVER BE REPORTED');
  assert.ok(!JSON.stringify(f.reports).includes('PRIVATE'));assert.ok(!JSON.stringify(f.reports).includes('synthetic external speech'));
});

test('malformed, replayed, wrong-process/session or false-video evidence never reaches the callback',t=>{
  const f=fixture(t);f.client.start(browserPid);f.started();f.line(f.segment(0));
  for(const changes of [{pid:999},{sessionId:'c'.repeat(32)},{frames:480001},{durationSeconds:9},
    {endedAt:2000},{wavSha256:'invalid'},{sampleRate:44100},{videoIdentity:'a-real-video'},
    {playingProof:true},{asrStatus:'speech',transcript:''},{asrStatus:'no-transcript',transcript:'text'},
    {privateFilePath:'C:/must-never-be-propagated.wav'}])f.line(f.segment(1,changes));
  f.line(f.segment(0));f.worker.stdout.write('not JSON containing PRIVATE\n');
  assert.equal(f.segments.length,1);assert.equal(f.client.status().rejected,14);
  f.line(f.segment(1));assert.equal(f.segments.length,2);
  assert.ok(!JSON.stringify(f.reports).includes('must-never'));assert.ok(!JSON.stringify(f.reports).includes('PRIVATE'));
});

test('unverified process scope or old callback protocol stops the helper without admitting audio',t=>{
  for(const changed of [{processSpecificCapture:false},{captureScopeVersion:0},
    {callbackFrameNormalizationVersion:0},{protocolVersion:undefined}]){
    const f=fixture(t);f.client.start(browserPid);f.started(changed);f.line(f.segment());
    assert.equal(f.segments.length,0);assert.equal(f.client.status().captureProof,null);
    assert.equal(f.client.status().state,'stopping');
    assert.equal(JSON.parse(f.writes.at(-1)).op,'stop');assert.equal(f.worker.stdin.writableEnded,true);
  }
});

test('a confirmed pause tail retains its actual frame duration instead of pretending ten seconds',t=>{
  const f=fixture(t);f.client.start(browserPid);f.started();
  f.line(f.segment(0,{frames:96000,durationSeconds:2,endedAt:1002,callbackObservedAt:1002}));
  assert.equal(f.segments.length,1);assert.equal(f.segments[0].durationSeconds,2);
  assert.deepEqual(f.client.status().captureProof,{protocolVersion:2,processSpecificCapture:true,captureScopeVersion:1,callbackFrameNormalizationVersion:1});
  for(const change of [{frames:0,durationSeconds:0,endedAt:1010},{frames:480001},
    {frames:96000,durationSeconds:2,endedAt:1020},{frames:96000,durationSeconds:10}])f.line(f.segment(1,change));
  assert.equal(f.segments.length,1);
});

test('pause/resume control is real protocol, while already captured paused ASR remains distinguishable',t=>{
  const f=fixture(t);f.client.start(browserPid);f.started();assert.equal(f.client.pause(),true);
  assert.equal(JSON.parse(f.writes.at(-1)).op,'pause');
  f.line({event:'paused',queuedSegments:1,captureStopped:true});assert.equal(f.client.status().state,'paused');
  f.line(f.segment());assert.equal(f.segments.length,1); // pre-pause captured evidence, not continued playback.
  assert.equal(f.client.resume(),true);assert.equal(JSON.parse(f.writes.at(-1)).op,'resume');
  f.started();assert.equal(f.client.status().state,'running');assert.equal(f.client.status().rejected,0);
});

test('gap validation reports lost audio without exposing arbitrary helper fields',t=>{
  const f=fixture(t);f.client.start(browserPid);f.started();
  f.line({event:'gap',sequence:2,frames:480000,durationSeconds:10,droppedFrames:480000,reason:'asr-backpressure',transcript:'PRIVATE'});
  assert.equal(f.gaps.length,1);assert.equal(f.gaps[0].frames,480000);assert.ok(!JSON.stringify(f.gaps).includes('PRIVATE'));
  f.line({event:'gap',frames:480000,durationSeconds:11,droppedFrames:480000,reason:'asr-backpressure'});
  assert.equal(f.gaps.length,1);assert.equal(f.client.status().rejected,1);
});

test('stop suppresses new segments immediately and resolves only after actual process exit',async t=>{
  const f=fixture(t);f.client.start(browserPid);f.started();const stop=f.client.stop();
  assert.equal(f.client.stop(),stop);assert.equal(JSON.parse(f.writes.at(-1)).op,'stop');assert.equal(f.worker.stdin.writableEnded,true);
  let done=false;stop.then(()=>{done=true;});f.line(f.segment());f.line({event:'stopped',captureStopped:true});await delay(5);
  assert.equal(done,false);assert.equal(f.segments.length,0);
  f.exit();const result=await stop;assert.equal(result.state,'exited');assert.equal(result.exitCode,0);
  assert.equal(f.client.pause(),false);assert.equal(f.client.resume(),false);
});

test('natural exit cancels shutdown kill and never automatically starts another helper',async t=>{
  const f=fixture(t,{shutdownGraceMs:10});f.client.start(browserPid);f.started();const stop=f.client.stop();
  f.exit();await stop;await delay(30);assert.equal(f.calls.length,1);assert.equal(f.worker.kills,0);
  assert.throws(()=>f.client.start(browserPid),/already-started/);
});

test('timeout kills only this owned worker tree, never browser PID, and still waits for exit',async t=>{
  const f=fixture(t,{shutdownGraceMs:10});f.client.start(browserPid);f.started();const stop=f.client.stop();
  let done=false;stop.then(()=>{done=true;});await delay(30);
  assert.equal(f.calls.length,2);assert.equal(f.calls[1].command,'taskkill.exe');
  assert.deepEqual(f.calls[1].args,['/PID',String(workerPid),'/T','/F']);assert.ok(!f.calls[1].args.includes(String(browserPid)));
  assert.equal(f.calls[1].options.windowsHide,true);assert.equal(done,false);assert.equal(f.segments.length,0);
  f.exit(124);assert.equal((await stop).exitCode,124);assert.equal(f.calls.length,2);
});

test('an unverified worker PID cannot be used as a browser-killing fallback',async t=>{
  const f=fixture(t,{shutdownGraceMs:10});f.worker.pid=browserPid;f.client.start(browserPid);f.started();
  const stop=f.client.stop();await delay(30);assert.equal(f.calls.length,1);assert.equal(f.worker.kills,0);
  f.exit();await stop;
});

test('spawn failure closes the client without restart or transcript diagnostics',async t=>{
  const f=fixture(t);f.worker.pid=undefined;f.client.start(browserPid);
  f.worker.emit('error',Error('C:/private-path-with-transcript'));
  await f.client.stop();assert.equal(f.client.status().state,'exited');assert.equal(f.calls.length,1);
  assert.ok(!JSON.stringify(f.reports).includes('private-path'));
});

test('actual pre-session main error is reported without authorizing unbound audio',t=>{
  const f=fixture(t);f.client.start(browserPid);
  f.worker.stdout.write(JSON.stringify({event:'error',code:'continuous-audio-process-denied'})+'\n');
  assert.ok(f.reports.some(r=>r.code==='continuous-audio-process-denied'));
  assert.equal(f.client.status().rejected,0);assert.equal(f.client.status().sessionId,null);
  f.worker.stdout.write(JSON.stringify(f.segment())+'\n');
  assert.equal(f.segments.length,0);assert.equal(f.client.status().rejected,1);
  f.worker.stdout.write(JSON.stringify({event:'error',code:'continuous-audio-unavailable',transcript:'PRIVATE'})+'\n');
  assert.equal(f.client.status().rejected,2);assert.ok(!JSON.stringify(f.reports).includes('PRIVATE'));
  f.started();f.line(f.segment());assert.equal(f.segments.length,1);
});
