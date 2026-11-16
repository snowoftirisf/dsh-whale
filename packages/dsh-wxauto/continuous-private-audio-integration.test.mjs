import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createExternalVideoAudioStore} from './external-video-audio-store.mjs';
import {createContinuousDouyinSupervisor} from './continuous-douyin-supervisor.mjs';
import {registerVideoAudioEvidenceTools} from './video-audio-evidence.mjs';

test('unbound supervisor transcript survives restart and the real model tools restrict it to owner context',async t=>{
  const root=fs.mkdtempSync(path.resolve('artifacts/unbound-audio-tool-integration-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const privateProducer=Symbol(),trustedProducer=Symbol();
  const config={dataRoot:root,privateProducer,trustedProducer,now:()=>14000,
    resolveAccess:id=>id==='original-whale-session'?'owner':id==='existing-group-session'?'group':null};
  const store=createExternalVideoAudioStore(config);
  const privateWriter=store.writerFor(privateProducer),publicWriter=store.writerFor(trustedProducer);
  let clock=1000,page={video:{id:'1234567890123',paused:false,readyState:4}},receive;
  let publicCalls=0,privateCalls=0;
  const captureProof={protocolVersion:2,processSpecificCapture:true,captureScopeVersion:1,callbackFrameNormalizationVersion:1};
  const manager=createContinuousDouyinSupervisor({now:()=>clock,getSources:async()=>({sources:[{pid:12}]}),
    onTranscript:input=>{publicCalls++;return publicWriter.recordContinuous(input);},
    onUnboundTranscript:input=>{privateCalls++;return privateWriter.recordUnboundContinuous(input);},
    createClient:callbacks=>{receive=callbacks.onSegment;return {start(){},pause(){},stop:async()=>{},
      status:()=>({state:'running',captureProof})};}});
  t.after(()=>manager.stop());manager.registerSource(async()=>page);await manager.tick();
  clock=6000;page={privateChatVisible:true};await manager.tick();
  clock=11000;page={video:{id:'9999999999999',paused:false,readyState:4}};
  const segment={event:'segment',sessionId:'a'.repeat(32),pid:12,sequence:0,startedAt:1,endedAt:11,callbackObservedAt:11,
    timestampBasis:'callback-arrival-minus-audio-frames',durationSeconds:10,frames:480000,sampleRate:48000,channels:2,
    encoding:'pcm-s16le',wavSha256:'b'.repeat(64),audioRetained:false,asrStatus:'speech',
    transcript:'Synthetic fixture: retain every word without guessing which video.',backend:'sensevoice-int8',videoIdentity:'unknown',playingProof:false};
  await receive(segment);
  assert.equal(publicCalls,0);assert.equal(privateCalls,1);assert.equal(manager.status().privateSegments,1);
  const tools=new Map(),reopened=createExternalVideoAudioStore(config);
  registerVideoAudioEvidenceTools({tools:{register:tool=>{tools.set(tool.name,tool);return()=>{};}}},reopened);
  const owner={agent:{session:{id:'original-whale-session'}}},group={agent:{session:{id:'existing-group-session'}}};
  const search=tools.get('whale_video_audio_search'),read=tools.get('whale_video_audio_read');
  const entries=await search.execute({query:'every word'},owner);assert.equal(entries.entries.length,1);
  const entry=entries.entries[0];assert.equal(entry.videoId,null);assert.equal(entry.scope,'owner-private');
  assert.equal(entry.videoIdentityStatus,'unattributed');assert.equal(entry.playingProof,false);
  assert.equal(entry.text,segment.transcript);
  assert.equal((await read.execute({entryId:entry.entryId},owner)).text,segment.transcript);
  assert.equal((await search.execute({},group)).entries.length,0);
  assert.throws(()=>read.execute({entryId:entry.entryId},group),/entry-unavailable/);
  assert.equal(fs.existsSync(path.join(root,'voice/recent-video-audio.local.json')),false);
});
