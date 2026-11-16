import test from 'node:test';
import assert from 'node:assert/strict';
import {measureVoicePreparation} from './voice-timing.mjs';

test('separates reply queue, GPU queue and RPC; receipt excludes private fields',async()=>{
  let clock=40,seen;
  const audio={file:'private.wav',durationSeconds:2,timings:{modelReloaded:false,
    inferenceSeconds:.01,reference:'private',text:'private',totalSeconds:Infinity}};
  const result=await measureVoicePreparation({queuedAt:10,taskStartedAt:30,now:()=>clock,
    runGpu:async work=>{clock+=50;return work()},synthesize:async()=>{clock+=100;return audio},
    observe:timing=>{seen=timing}});
  assert.equal(result,audio);
  assert.deepEqual(seen,{replyQueueMs:20,gpuQueueMs:50,speechRpcMs:100,audioSeconds:2,
    backend:{modelReloaded:false,inferenceSeconds:.01}});
  assert.ok(!JSON.stringify(seen).includes('private'));
});

test('failed synthesis does not publish a success timing or retry',async()=>{
  let calls=0,observations=0;
  await assert.rejects(measureVoicePreparation({queuedAt:0,taskStartedAt:0,now:()=>0,
    runGpu:work=>work(),synthesize:()=>{calls++;throw Error('fixture')},
    observe:()=>observations++}),/fixture/);
  assert.equal(calls,1);assert.equal(observations,0);
});

test('failed timing observer preserves successful audio',async()=>{
  const audio={durationSeconds:1};
  assert.equal(await measureVoicePreparation({queuedAt:0,taskStartedAt:0,now:()=>1,
    runGpu:work=>work(),synthesize:()=>audio,observe:()=>{throw Error('disk')}}),audio);
});
