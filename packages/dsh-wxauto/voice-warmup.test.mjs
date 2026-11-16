import test from 'node:test';
import assert from 'node:assert/strict';
import {createVoiceWarmup} from './voice-warmup.mjs';
test('only one ready status requests model warmup through GPU queue, without synthesis',async()=>{
  const calls=[],states=[];
  const warmup=createVoiceWarmup({runGpu:async work=>{calls.push('queue');return work()},
    request:async op=>{calls.push(op);return {ttsLoaded:true}},observe:s=>states.push(s.state)});
  assert.equal(warmup.consider({ready:false}),null);
  const pending=warmup.consider({ready:true});
  assert.equal(warmup.consider({ready:true}),null);
  assert.deepEqual(await pending,{state:'ready'});
  assert.deepEqual(calls,['queue','warmup']);assert.deepEqual(states,['queued','loading','ready']);
});
test('failed startup warmup never loops or blocks later real work',async()=>{
  let calls=0;
  const warmup=createVoiceWarmup({runGpu:work=>work(),request:()=>{calls++;throw Error('private-path')}});
  assert.deepEqual(await warmup.consider({ready:true}),{state:'failed',automaticRetry:false});
  assert.equal(warmup.consider({ready:true}),null);assert.equal(calls,1);
});
test('unconfirmed loading cannot be reported ready',async()=>{
  const warmup=createVoiceWarmup({runGpu:work=>work(),request:()=>({ttsLoaded:false})});
  assert.deepEqual(await warmup.consider({ready:true}),{state:'failed',automaticRetry:false});
});
test('shutdown while queued prevents model loading',async()=>{
  let work,closed=false,calls=0;
  const warmup=createVoiceWarmup({runGpu:fn=>{work=fn;return new Promise(resolve=>{work=()=>resolve(fn())})},
    request:()=>{calls++},isClosed:()=>closed});
  const pending=warmup.consider({ready:true});await Promise.resolve();
  closed=true;await work();assert.deepEqual(await pending,{state:'cancelled'});assert.equal(calls,0);
});
