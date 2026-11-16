import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {randomUUID,createHash} from 'node:crypto';
import {createSongDelivery,songChatKey,wavInfo} from './song-delivery.mjs';
import {songToolDefinitions} from './song-tools.mjs';

const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const id=()=>randomUUID().replaceAll('-','');
const route={kind:'friend',target:'owner'};
const url='https://www.bilibili.com/video/BV1234567890/';
function wav(seconds=1) {
  const frames=Math.round(seconds*44100),data=frames*3,bytes=Buffer.alloc(44+data+(data%2));
  bytes.write('RIFF');bytes.writeUInt32LE(bytes.length-8,4);bytes.write('WAVE',8);
  bytes.write('fmt ',12);bytes.writeUInt32LE(16,16);bytes.writeUInt16LE(1,20);bytes.writeUInt16LE(1,22);
  bytes.writeUInt32LE(44100,24);bytes.writeUInt32LE(132300,28);bytes.writeUInt16LE(3,32);bytes.writeUInt16LE(24,34);
  bytes.write('data',36);bytes.writeUInt32LE(data,40);return bytes;
}
function fixture(t) {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'whale-song-test-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  fs.mkdirSync(path.join(root,'voice'),{recursive:true});
  const reference=path.join(root,'voice/reference.wav');fs.writeFileSync(reference,wav());
  const settings={approved:true,referenceAudio:'voice/reference.wav',referenceAudioBase:'dataRoot'};
  const calls={render:0,send:0},guides=new Map();
  function guide(target='owner',guideId=id(),seconds=1) {
    const chatKey=songChatKey(target),folder=path.join(root,'voice/song-guides',chatKey,guideId),bytes=wav(seconds);
    fs.mkdirSync(folder,{recursive:true});fs.writeFileSync(path.join(folder,'guide.wav'),bytes);
    const metadata={schemaVersion:1,kind:'whale-song-guide',guideId,jobId:guideId,chatKey,guideFile:'guide.wav',
      verified:true,audioKind:'isolated-vocal',guideSha256:sha(bytes),sourceAudioSha256:'f'.repeat(64),
      sourceUrl:url,sourceStartSeconds:0,sourceDurationSeconds:seconds,durationSeconds:seconds,sampleRate:44100,channels:1,
      musicalSourceVerified:false,listeningAccepted:false,vocalIsolation:'model-estimated'};
    fs.writeFileSync(path.join(folder,'metadata.json'),JSON.stringify(metadata));guides.set(guideId,{folder,metadata});
    return metadata;
  }
  function render(guideId,chatKey) {
    const guide=guides.get(guideId).metadata,folder=path.join(root,'voice/outbox/singing',id());
    fs.mkdirSync(folder,{recursive:true});const file=path.join(folder,'segment-001.wav'),bytes=wav();fs.writeFileSync(file,bytes);
    return {backend:'seed-vc-44k-svc',sampleRate:44100,durationSeconds:1,segmentCount:1,file,
      segments:[{name:'segment-001.wav',file,durationSeconds:1,offsetSeconds:0,sha256:sha(bytes)}],
      metadata:{guideId,chatKey,actorReferenceSha256:sha(fs.readFileSync(reference)),guideSha256:guide.guideSha256,
        sourceAudioSha256:guide.sourceAudioSha256,sourceUrl:url,sourceStartSeconds:0,sourceDurationSeconds:guide.sourceDurationSeconds,
        audioKind:'voice-converted-guide',listeningAccepted:false}};
  }
  const options={dataRoot:root,voiceSettings:()=>({...settings}),nativeVoiceReady:()=>true,
    svcRequest:async(op,{guideId,chatKey})=>{assert.equal(op,'synthesize');calls.render++;return render(guideId,chatKey)},
    sendAudio:async args=>{await args.beforeSend();assert.equal(args.target,route.target);calls.send++;return {outcome:'sent'}}};
  return {root,settings,reference,calls,guide,render,options,guides,
    receipts:guideId=>fs.readdirSync(guides.get(guideId).folder).filter(f=>f.startsWith('delivery-')).map(f=>JSON.parse(fs.readFileSync(path.join(guides.get(guideId).folder,f))))};
}

test('persistent sent claim survives a new controller and identical reference rename',async t=>{
  const f=fixture(t),g=f.guide();
  const first=await createSongDelivery(f.options)(route,g.guideId);assert.equal(first.status,'sent',JSON.stringify(first));
  assert.equal(f.receipts(g.guideId)[0].state,'sent');
  fs.copyFileSync(f.reference,path.join(f.root,'voice/copy.wav'));f.settings.referenceAudio='voice/copy.wav';
  assert.equal((await createSongDelivery(f.options)(route,g.guideId)).status,'already-sent');
  assert.deepEqual(f.calls,{render:1,send:1});
});

test('native minimum checks actual WAV frames as well as declared segment duration',async t=>{
  // Both renders have one-second total, so a tiny final segment cannot hide
  // behind a valid overall guide duration. All files have real PCM24 headers.
  for(const [actual,declared] of [[[.1,.9],[.1,.9]],[[.49,.51],[.5,.5]],[[.5,.5],[.5,.5]]]) {
    const f=fixture(t),g=f.guide();
    const options={...f.options,svcRequest:async(op,args)=>{
      f.calls.render++;const result=f.render(args.guideId,args.chatKey),folder=path.dirname(result.file);
      let offset=0;
      result.segments=actual.map((seconds,index)=>{
        const name=`segment-${String(index+1).padStart(3,'0')}.wav`,file=path.join(folder,name),bytes=wav(seconds);
        fs.writeFileSync(file,bytes);
        const part={name,file,durationSeconds:declared[index],offsetSeconds:offset,sha256:sha(bytes)};
        offset+=declared[index];return part;
      });result.segmentCount=result.segments.length;return result;
    }};
    const result=await createSongDelivery(options)(route,g.guideId);
    assert.equal(result.status,actual[0]===.5?'sent':'failed',JSON.stringify(result));
    assert.equal(f.calls.send,actual[0]===.5?1:0);
  }
});

test('exactly 55 seconds and the runtime 55-seconds-plus-one-frame split remain valid',async t=>{
  // Matches voice_audio.segments at 44.1kHz: for a one-frame overflow the
  // boundary moves back, leaving a sendable exactly-half-second tail.
  for(const frameCounts of [[2425500],[2403451,22050]]) {
    const f=fixture(t),g=f.guide('owner',id(),55);let observed;
    const options={...f.options,svcRequest:async(op,args)=>{
      f.calls.render++;const result=f.render(args.guideId,args.chatKey),folder=path.dirname(result.file);
      let offset=0;
      result.segments=frameCounts.map((frames,index)=>{
        const name=`segment-${String(index+1).padStart(3,'0')}.wav`,file=path.join(folder,name),bytes=wav(frames/44100);
        fs.writeFileSync(file,bytes);
        const part={name,file,durationSeconds:frames/44100,offsetSeconds:offset/44100,sha256:sha(bytes)};
        offset+=frames;return part;
      });result.segmentCount=frameCounts.length;result.durationSeconds=offset/44100;return result;
    },sendAudio:async args=>{await args.beforeSend();observed=args.segments;f.calls.send++;return {outcome:'sent'}}};
    const result=await createSongDelivery(options)(route,g.guideId);
    assert.equal(result.status,'sent',JSON.stringify(result));assert.equal(f.calls.send,1);
    assert.deepEqual(observed.map(s=>Math.round(s.durationSeconds*44100)),frameCounts);
    assert(observed.every(s=>s.durationSeconds>=.5&&s.durationSeconds<=55));
  }
});

test('concurrent controllers claim once before rendering outside the send queue',async t=>{
  const f=fixture(t),g=f.guide();let release,started;
  const ready=new Promise(resolve=>started=resolve),hold=new Promise(resolve=>release=resolve);
  const options={...f.options,svcRequest:async(op,args)=>{f.calls.render++;started();await hold;return f.render(args.guideId,args.chatKey)}};
  const first=createSongDelivery(options)(route,g.guideId);await ready;
  assert.equal(f.calls.send,0);
  assert.equal((await createSongDelivery(options)(route,g.guideId)).status,'not-dispatched');
  release();assert.equal((await first).status,'sent');assert.deepEqual(f.calls,{render:1,send:1});
});

test('lost native acknowledgement persists unknown and never retries across instances',async t=>{
  const f=fixture(t),g=f.guide();
  const options={...f.options,sendAudio:async args=>{await args.beforeSend();f.calls.send++;throw Error('ack lost')}};
  assert.equal((await createSongDelivery(options)(route,g.guideId)).status,'unknown');
  assert.equal(f.receipts(g.guideId)[0].state,'unknown');
  assert.equal((await createSongDelivery(options)(route,g.guideId)).status,'not-dispatched');
  assert.deepEqual(f.calls,{render:1,send:1});
});

test('group cannot read another chat guide or choose target/reference/file arguments',async t=>{
  const f=fixture(t),g=f.guide();
  await assert.rejects(()=>createSongDelivery(f.options)({kind:'group',target:'group'},g.guideId));
  const tools=songToolDefinitions({...f.options,routeForSession:()=>route,guideRequest:()=>assert.fail('must not prepare')});
  for(const extra of [{target:'group'},{chatKey:songChatKey('group')},{reference:'other'},{filepath:'other'}])
    await assert.rejects(()=>tools[1].execute({guideId:g.guideId,...extra},{}),/arguments-invalid/);
  assert.deepEqual(f.calls,{render:0,send:0});
});

test('approval revoked or reference changed during render prevents native send',async t=>{
  for(const mode of ['revoke','reference']) {
    const f=fixture(t),g=f.guide();
    const options={...f.options,svcRequest:async(op,args)=>{
      f.calls.render++;const result=f.render(args.guideId,args.chatKey);
      if(mode==='revoke')f.settings.approved=false;else fs.writeFileSync(f.reference,Buffer.from('changed reference'));
      return result;
    }};
    assert.equal((await createSongDelivery(options)(route,g.guideId)).status,'failed');
    assert.equal(f.calls.send,0);assert.equal(f.receipts(g.guideId)[0].state,'failed');
  }
});

test('revocation while waiting for UI queue is rechecked by injected beforeSend',async t=>{
  const f=fixture(t),g=f.guide();
  const options={...f.options,sendAudio:async args=>{f.settings.approved=false;await args.beforeSend();f.calls.send++;return {outcome:'sent'}}};
  assert.equal((await createSongDelivery(options)(route,g.guideId)).status,'unknown');assert.equal(f.calls.send,0);
});

test('native capability is independent of TTS backend, but absent native transport never renders',async t=>{
  const f=fixture(t),g=f.guide();f.settings.backend='disabled-tts';
  await assert.rejects(()=>createSongDelivery({...f.options,nativeVoiceReady:()=>false})(route,g.guideId),/native-transport/);
  assert.equal(f.calls.render,0);
  assert.equal((await createSongDelivery(f.options)(route,g.guideId)).status,'sent');
});

test('render corruption, segment bounds and metadata substitutions fail without fallback',async t=>{
  for(const mode of ['hash','duration','scope','filepath','wave']) {
    const f=fixture(t),g=f.guide();
    const options={...f.options,svcRequest:async(op,args)=>{
      f.calls.render++;const r=f.render(args.guideId,args.chatKey);
      if(mode==='hash')r.segments[0].sha256='a'.repeat(64);
      if(mode==='duration')r.segments[0].durationSeconds=56;
      if(mode==='scope')r.metadata.chatKey=songChatKey('other');
      if(mode==='filepath')r.segments[0].file=f.reference;
      if(mode==='wave'){fs.writeFileSync(r.file,'not a wave');r.segments[0].sha256=sha(Buffer.from('not a wave'))}
      return r;
    }};
    assert.equal((await createSongDelivery(options)(route,g.guideId)).status,'failed');assert.equal(f.calls.send,0);
    assert.equal((await createSongDelivery(options)(route,g.guideId)).status,'not-dispatched');assert.equal(f.calls.render,1);
  }
});

test('prepare derives group chatKey and prepares without a voice or transport',async t=>{
  const f=fixture(t);let requestArgs;
  const tools=songToolDefinitions({...f.options,routeForSession:id=>id==='group-session'?{kind:'group',target:'group'}:null,
    guideRequest:async(op,args)=>{requestArgs=args;assert.equal(op,'prepare');return f.guide('group')}});
  const result=await tools[0].execute({sourceUrl:url,startSeconds:0,durationSeconds:1},{agent:{session:{id:'group-session'}}});
  assert.equal(result.status,'prepared');assert.equal(requestArgs.chatKey,songChatKey('group'));
  assert.equal(result.listeningAccepted,false);assert.deepEqual(f.calls,{render:0,send:0});
  await assert.rejects(()=>tools[0].execute({sourceUrl:url,startSeconds:0,durationSeconds:56},{agent:{session:{id:'group-session'}}}),/slice-invalid/);
});

test('guide symlink is rejected before any rendering',async t=>{
  const f=fixture(t),g=f.guide(),file=path.join(f.guides.get(g.guideId).folder,'guide.wav');
  fs.unlinkSync(file);
  try{fs.symlinkSync(f.reference,file,'file')}catch(error){if(['EPERM','EACCES'].includes(error.code)){t.skip('OS denies symlink creation');return}throw error}
  await assert.rejects(()=>createSongDelivery(f.options)(route,g.guideId),/link-denied/);
  assert.equal(f.calls.render,0);
});

test('wave parser checks real duration and truncated payload',()=>{
  assert.equal(wavInfo(wav()).durationSeconds,1);
  assert.throws(()=>wavInfo(wav().subarray(0,45)),/truncated/);
});

test('directory junction/reparse guide roots are denied even on Windows without file-symlink privilege',async t=>{
  const f=fixture(t),g=f.guide(),folder=f.guides.get(g.guideId).folder,real=folder+'-original';
  fs.renameSync(folder,real);
  try{fs.symlinkSync(real,folder,process.platform==='win32'?'junction':'dir')}
  catch(error){if(['EPERM','EACCES'].includes(error.code)){t.skip('OS denies directory-link creation');return}throw error}
  await assert.rejects(()=>createSongDelivery(f.options)(route,g.guideId),/link-denied/);
  assert.equal(f.calls.render,0);
});

test('stored guide metadata cannot point to an unrelated source platform',async t=>{
  const f=fixture(t),g=f.guide(),folder=f.guides.get(g.guideId).folder;
  fs.writeFileSync(path.join(folder,'metadata.json'),JSON.stringify({...g,sourceUrl:'https://example.org/private'}));
  await assert.rejects(()=>createSongDelivery(f.options)(route,g.guideId),/source-invalid/);
  assert.deepEqual(f.calls,{render:0,send:0});
});

test('cancelled tool never renders or claims, and cancellation after render never sends',async t=>{
  const f=fixture(t),g=f.guide(),cancelled=new AbortController();cancelled.abort();
  const tools=songToolDefinitions({...f.options,routeForSession:()=>route});
  await assert.rejects(()=>tools[1].execute({guideId:g.guideId},{signal:cancelled.signal}),/request-aborted/);
  assert.deepEqual(f.calls,{render:0,send:0});assert.equal(f.receipts(g.guideId).length,0);
  const controller=new AbortController();
  const options={...f.options,svcRequest:async(op,args)=>{
    f.calls.render++;const result=f.render(args.guideId,args.chatKey);controller.abort();return result;
  }};
  const result=await createSongDelivery(options)(route,g.guideId,{signal:controller.signal});
  assert.equal(result.status,'failed');assert.equal(result.code,'song-request-aborted');assert.equal(f.calls.send,0);
  assert.equal(f.receipts(g.guideId)[0].state,'failed');
});

test('cancellation while native send awaits its UI queue is checked inside beforeSend',async t=>{
  const f=fixture(t),g=f.guide(),controller=new AbortController();
  const result=await createSongDelivery({...f.options,sendAudio:async args=>{
    controller.abort();await args.beforeSend();f.calls.send++;return {outcome:'sent'};
  }})(route,g.guideId,{signal:controller.signal});
  assert.equal(result.status,'unknown');assert.equal(result.code,'song-request-aborted');assert.equal(f.calls.send,0);
});

test('late prepare result is not returned after execution or lifecycle cancellation',async t=>{
  const f=fixture(t),controller=new AbortController();
  const tools=songToolDefinitions({...f.options,signal:controller.signal,routeForSession:()=>route,
    guideRequest:async()=>{const prepared=f.guide();controller.abort();return prepared}});
  await assert.rejects(()=>tools[0].execute({sourceUrl:url,startSeconds:0,durationSeconds:1},{}),/request-aborted/);
  assert.deepEqual(f.calls,{render:0,send:0});
});
