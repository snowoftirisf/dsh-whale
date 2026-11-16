import test from 'node:test';
import assert from 'node:assert/strict';
import {createGpuQueue} from './gpu-queue.mjs';

test('TTS and singing do not overlap GPU work or poison the next task after failure',async()=>{
  const run=createGpuQueue();let active=0,peak=0;const order=[];
  const work=name=>async()=>{active++;peak=Math.max(peak,active);order.push(name);
    await new Promise(resolve=>setTimeout(resolve,10));active--;if(name==='bad')throw Error('expected');return name};
  const results=await Promise.allSettled([run(work('tts')),run(work('bad')),run(work('singing'))]);
  assert.equal(peak,1);assert.deepEqual(order,['tts','bad','singing']);
  assert.equal(results[1].status,'rejected');assert.equal(results[2].value,'singing');
});

test('shutdown prevents a queued GPU job from starting',async()=>{
  const abort=new AbortController(),run=createGpuQueue(abort.signal);let release,started=false;
  const first=run(()=>new Promise(resolve=>{release=resolve}));await Promise.resolve();
  const next=run(()=>{started=true});abort.abort();release();await first;
  await assert.rejects(next,{name:'AbortError'});assert.equal(started,false);
});
