import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createExternalVideoAudioStore} from './external-video-audio-store.mjs';
import {recordVideoAudio,registerVideoAudioEvidenceTools} from './video-audio-evidence.mjs';

test('capture bridge persists actual duration and group tools recover only autonomous evidence after restart',t=>{
  const root=fs.mkdtempSync(path.resolve('artifacts/video-audio-bridge-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const producer=Symbol(),privateProducer=Symbol(),now=Date.now();
  const config={dataRoot:root,trustedProducer:producer,privateProducer,resolveAccess:id=>id==='owner'?'owner':id==='group'?'group':null};
  const store=createExternalVideoAudioStore(config);
  const edges={videoId:'123',before:{videoId:'123',playing:true,observedAtMs:now-2000},after:{videoId:'123',playing:true,observedAtMs:now}};
  const audio={source:'dedicated-douyin-browser-audio',backend:'sensevoice-int8',capturedSeconds:1.99,text:'synthetic heard dialogue',audioSha256:'a'.repeat(64)};
  const receipt=recordVideoAudio(store.writerFor(producer),{...edges,audio});
  store.writerFor(privateProducer).record({platform:'douyin',...edges,durationSeconds:1.99,asr:{status:'speech',text:'private fixture',backend:'sensevoice-int8'}});
  const tools=new Map(),removed=[];
  const remove=registerVideoAudioEvidenceTools({tools:{register:tool=>{tools.set(tool.name,tool);return ()=>removed.push(tool.name);}}},createExternalVideoAudioStore(config));
  const search=tools.get('whale_video_audio_search'),read=tools.get('whale_video_audio_read');
  const result=search.execute({sessionId:'owner'},{agent:{session:{id:'group'}}});
  assert.equal(result.entries.length,1);assert.equal(result.entries[0].durationSeconds,1.99);
  assert.equal(result.entries[0].audioSha256,audio.audioSha256);
  assert.equal(read.execute({entryId:receipt.entryId},{agent:{session:{id:'group'}}}).text,audio.text);
  assert.equal(search.execute({}, {agent:{session:{id:'owner'}}}).entries.length,2);
  assert.throws(()=>search.execute({sessionId:'owner'},{}),/session-denied/);
  remove();assert.equal(removed.length,2);
});

test('empty decoder output does not claim silence; invalid source and contradictory result never persist',()=>{
  const values=[],writer={record:value=>{values.push(value);return {entryId:'fixture'};}};
  const args={videoId:'123',before:{videoId:'123',playing:true,observedAtMs:1},after:{videoId:'123',playing:true,observedAtMs:2},audio:{source:'dedicated-douyin-browser-audio',backend:'sensevoice-int8',capturedSeconds:1.99,text:''}};
  recordVideoAudio(writer,args);assert.equal(values[0].asr.status,'no-transcript');
  recordVideoAudio(writer,{...args,audio:{...args.audio,audioStatus:'no-detected-speech'}});
  assert.equal(values[1].asr.status,'no-speech');assert.equal(values[0].audioSha256,undefined);
  assert.throws(()=>recordVideoAudio(writer,{...args,audio:{...args.audio,source:'desktop'}}),/result-invalid/);
  assert.throws(()=>recordVideoAudio(writer,{...args,audio:{...args.audio,text:'words',audioStatus:'no-detected-speech'}}),/contradictory/);
  assert.equal(values.length,2);
});
