import test from 'node:test';
import assert from 'node:assert/strict';
import {residentSingingRequest} from './resident-speech.mjs';
for(const activeIndex of [0,1])test(`singing preserves selected backend ${activeIndex}`,async()=>{
  const calls=[];
  const backends=[0,1].map(n=>({available:()=>true,request:async op=>calls.push(`${n}:${op}`)}));
  const request=residentSingingRequest({runGpu:work=>work(),activeSpeech:()=>backends[activeIndex],
    alternatives:()=>backends,request:async op=>{calls.push(op);return 'ok'}});
  assert.equal(await request('status',{}),'ok');assert.deepEqual(calls,['status']);calls.length=0;
  assert.equal(await request('synthesize',{}),'ok');
  assert.deepEqual(calls,[`${1-activeIndex}:release`,'synthesize']);
});
test('GPU queue rejection never releases either backend or starts conversion',async()=>{
  let calls=0;
  const request=residentSingingRequest({runGpu:()=>Promise.reject(Error('closed')),
    activeSpeech:()=>null,alternatives:()=>{calls++;return []},request:()=>calls++});
  await assert.rejects(request('synthesize',{}),/closed/);assert.equal(calls,0);
});
