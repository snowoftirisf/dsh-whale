import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {randomUUID,createHash} from 'node:crypto';
import {createSongJobs,songJobToolDefinitions,readRecentSongReceipts} from './song-jobs.mjs';
import {songChatKey} from './song-delivery.mjs';

const hash=b=>createHash('sha256').update(b).digest('hex');
const id=()=>randomUUID().replaceAll('-','');
const sourceUrl='https://www.bilibili.com/video/BV1234567890/';
const args={sourceUrl,startSeconds:0,durationSeconds:1};
const exec=session=>({agent:{session:{id:session}}});

test('read-only recent receipts survive restart, stay scoped and never replay songs',t=>{
  const f=fixture(t),saved=[];
  for(const [target,kind,status] of [['owner','friend','sent'],['owner','friend','unknown'],['group','group','failed']]){
    const jobId=id(),chatKey=songChatKey(target),file=f.jobFile(chatKey,jobId);
    fs.mkdirSync(path.dirname(file),{recursive:true});
    fs.writeFileSync(file,JSON.stringify({format:'whale-song-job',version:1,jobId,chatKey,target,kind,
      sessionId:target+'-old-session',status,phase:'complete',createdAt:new Date(1700000000000+saved.length*1000).toISOString(),parameters:args}));
    saved.push({file,sha:hash(fs.readFileSync(file))});
  }
  const owner=readRecentSongReceipts(f.root,{target:'owner',kind:'friend'});
  assert.deepEqual(owner.items.map(row=>row.status),['unknown','sent']);assert.equal(owner.automaticRetry,false);
  assert.ok(owner.items.every(row=>row.target==='owner'&&row.listeningAccepted===false));
  const group=readRecentSongReceipts(f.root,{target:'group',kind:'group'});assert.deepEqual(group.items.map(row=>row.status),['failed']);
  assert.deepEqual(readRecentSongReceipts(f.root,{target:'other',kind:'group'}).items,[]);
  assert.throws(()=>readRecentSongReceipts(f.root,null),/session-denied/);
  for(const entry of saved)assert.equal(hash(fs.readFileSync(entry.file)),entry.sha);
  assert.deepEqual(f.calls,{prepare:0,render:0,send:0});assert.deepEqual(f.reports,[]);
});
function wave() {
  const b=Buffer.alloc(44+132300);b.write('RIFF');b.writeUInt32LE(b.length-8,4);b.write('WAVE',8);
  b.write('fmt ',12);b.writeUInt32LE(16,16);b.writeUInt16LE(1,20);b.writeUInt16LE(1,22);
  b.writeUInt32LE(44100,24);b.writeUInt32LE(132300,28);b.writeUInt16LE(3,32);b.writeUInt16LE(24,34);
  b.write('data',36);b.writeUInt32LE(132300,40);return b;
}
function fixture(t) {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'whale-song-jobs-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const voice=path.join(root,'voice');fs.mkdirSync(voice);
  fs.writeFileSync(path.join(voice,'reference.wav'),wave());
  const settings={approved:true,referenceAudio:'voice/reference.wav',referenceAudioBase:'dataRoot'},reports=[];
  const calls={prepare:0,render:0,send:0},guides=new Map();
  const routes={'owner-session':{kind:'friend',target:'owner'},'group-session':{kind:'group',target:'group'}};
  function makeGuide(chatKey) {
    const guideId=id(),folder=path.join(voice,'song-guides',chatKey,guideId),bytes=wave();fs.mkdirSync(folder,{recursive:true});
    fs.writeFileSync(path.join(folder,'guide.wav'),bytes);
    const metadata={schemaVersion:1,kind:'whale-song-guide',guideId,jobId:guideId,chatKey,guideFile:'guide.wav',
      verified:true,audioKind:'isolated-vocal',guideSha256:hash(bytes),sourceAudioSha256:'f'.repeat(64),sourceUrl,
      sourceStartSeconds:0,sourceDurationSeconds:1,durationSeconds:1,sampleRate:44100,channels:1};
    fs.writeFileSync(path.join(folder,'metadata.json'),JSON.stringify(metadata));guides.set(guideId,metadata);return metadata;
  }
  const options={dataRoot:root,routeForSession:session=>routes[session],voiceSettings:()=>({...settings}),nativeVoiceReady:()=>true,
    guideRequest:async(op,request)=>{calls.prepare++;assert.equal(op,'prepare');return makeGuide(request.chatKey)},
    svcRequest:async(op,{guideId,chatKey})=>{
      calls.render++;assert.equal(op,'synthesize');const guide=guides.get(guideId),folder=path.join(voice,'outbox/singing',id());
      fs.mkdirSync(folder,{recursive:true});const file=path.join(folder,'segment-001.wav'),bytes=wave();fs.writeFileSync(file,bytes);
      return {backend:'seed-vc-44k-svc',sampleRate:44100,durationSeconds:1,segmentCount:1,file,
        segments:[{file,name:'segment-001.wav',durationSeconds:1,offsetSeconds:0,sha256:hash(bytes)}],
        metadata:{guideId,chatKey,actorReferenceSha256:hash(fs.readFileSync(path.join(voice,'reference.wav'))),
          guideSha256:guide.guideSha256,sourceAudioSha256:guide.sourceAudioSha256,sourceUrl,sourceStartSeconds:0,
          sourceDurationSeconds:1,audioKind:'voice-converted-guide'}};
    },
    sendAudio:async audio=>{await audio.beforeSend();calls.send++;return {outcome:'sent'}},
    onResult:async result=>{reports.push(result)},
  };
  const create=(changes={})=>createSongJobs({...options,...changes});
  return {root,options,calls,reports,settings,makeGuide,create,
    jobFile:(chatKey,jobId)=>path.join(voice,'song-jobs',chatKey,jobId+'.json')};
}

test('background submit returns before preparation and report only records original chat',async t=>{
  const f=fixture(t);let release,entered;
  const hold=new Promise(resolve=>release=resolve),started=new Promise(resolve=>entered=resolve);
  const jobs=f.create({guideRequest:async(op,request)=>{f.calls.prepare++;entered();await hold;return f.makeGuide(request.chatKey)}});
  const submission=await jobs.submit(args,exec('group-session'));
  assert.equal(submission.status,'started');assert.deepEqual(f.calls,{prepare:0,render:0,send:0});
  await started;
  assert.equal(jobs.status(submission.jobId,exec('group-session')).phase,'preparing');
  assert.throws(()=>jobs.status(submission.jobId,exec('owner-session')),/not-found/);
  // Another ordinary turn is not held by the preparation promise.
  assert.equal(await Promise.resolve('same chat is available'),'same chat is available');
  release();await jobs.whenIdle();
  assert.equal(jobs.status(submission.jobId,exec('group-session')).status,'sent');
  assert.deepEqual(f.calls,{prepare:1,render:1,send:1});assert.equal(f.reports.length,1);
  assert.equal(f.reports[0].sessionId,'group-session');assert.equal(f.reports[0].target,'group');
  assert.equal(jobs.status(submission.jobId,exec('group-session')).reportStatus,'delivered');jobs.close();
});

test('submission cancellation checked before commit but normal submitting turn end does not cancel accepted job',async t=>{
  const f=fixture(t),cancelled=new AbortController();cancelled.abort();const jobs=f.create();
  await assert.rejects(()=>jobs.submit(args,{...exec('owner-session'),signal:cancelled.signal}),/request-aborted/);
  assert.equal(f.calls.prepare,0);
  const done=new AbortController(),accepted=await jobs.submit(args,{...exec('owner-session'),signal:done.signal});
  done.abort();await jobs.whenIdle();
  assert.equal(jobs.status(accepted.jobId,exec('owner-session')).status,'sent');jobs.close();
});

test('close while preparing persists interrupted and never renders, sends or reports',async t=>{
  const f=fixture(t);let release,entered;const hold=new Promise(r=>release=r),started=new Promise(r=>entered=r);
  const jobs=f.create({guideRequest:async(op,request)=>{entered();await hold;return f.makeGuide(request.chatKey)}});
  const accepted=await jobs.submit(args,exec('owner-session'));await started;jobs.close();
  assert.equal(jobs.status(accepted.jobId,exec('owner-session')).status,'interrupted');
  release();await jobs.whenIdle();assert.equal(f.calls.render,0);assert.equal(f.calls.send,0);assert.equal(f.reports.length,0);
});

test('restart marks running interrupted and never replays work or an uncertain result callback',async t=>{
  const f=fixture(t),jobId=id(),chatKey=songChatKey('owner'),file=f.jobFile(chatKey,jobId);
  fs.mkdirSync(path.dirname(file),{recursive:true});
  fs.writeFileSync(file,JSON.stringify({format:'whale-song-job',version:1,jobId,chatKey,target:'owner',kind:'friend',
    sessionId:'owner-session',parameters:args,status:'running',phase:'singing',createdAt:new Date().toISOString(),reportStatus:'claimed'}));
  const restarted=f.create();await restarted.whenIdle();
  assert.equal(restarted.status(jobId,exec('owner-session')).status,'interrupted');
  assert.deepEqual(f.calls,{prepare:0,render:0,send:0});assert.equal(f.reports.length,0);restarted.close();
});

test('failed rendering is never called sent and uncertain result callback is not repeated on reload',async t=>{
  const f=fixture(t);let reports=0;
  const options={svcRequest:async()=>{throw Error('fixture render failed')},onResult:async()=>{reports++;throw Error('lost callback acknowledgement')}};
  const jobs=f.create(options),accepted=await jobs.submit(args,exec('owner-session'));await jobs.whenIdle();
  const result=jobs.status(accepted.jobId,exec('owner-session'));
  assert.equal(result.status,'failed');assert.equal(result.reportStatus,'unknown');assert.equal(f.calls.send,0);assert.equal(reports,1);
  jobs.close();const restarted=f.create(options);await restarted.whenIdle();assert.equal(reports,1);restarted.close();
});

test('reference selection changed while preparing refuses singing with a new voice',async t=>{
  const f=fixture(t),jobs=f.create({guideRequest:async(op,request)=>{
    f.settings.voiceId='changed selection';return f.makeGuide(request.chatKey);
  }});
  const accepted=await jobs.submit(args,exec('owner-session'));await jobs.whenIdle();
  const result=jobs.status(accepted.jobId,exec('owner-session'));
  assert.equal(result.status,'failed');assert.equal(result.code,'song-job-voice-changed');assert.equal(f.calls.render,0);assert.equal(f.calls.send,0);jobs.close();
});

test('tool contract is optional, narrow, and cannot specify recipient or foreign job path',async t=>{
  const f=fixture(t),jobs=f.create(),tools=songJobToolDefinitions({jobs});
  assert.deepEqual(tools.map(tool=>tool.name),['whale_start_song','whale_song_status']);
  await assert.rejects(()=>tools[0].execute({...args,target:'other'},exec('owner-session')),/arguments-invalid/);
  assert.throws(()=>tools[1].execute({jobId:'../foreign'},exec('owner-session')),/id-invalid/);
  assert.throws(()=>tools[1].execute({jobId:id(),target:'other'},exec('owner-session')),/arguments-invalid/);
  jobs.close();
});

test('global lifecycle abort interrupts accepted preparation without a late native send',async t=>{
  const f=fixture(t),controller=new AbortController();let release,entered;
  const hold=new Promise(r=>release=r),started=new Promise(r=>entered=r);
  const jobs=f.create({signal:controller.signal,guideRequest:async(op,request)=>{
    entered();await hold;return f.makeGuide(request.chatKey);
  }});
  const accepted=await jobs.submit(args,exec('owner-session'));await started;
  controller.abort();release();await jobs.whenIdle();
  assert.equal(jobs.status(accepted.jobId,exec('owner-session')).status,'interrupted');
  assert.equal(f.calls.send,0);assert.equal(f.calls.render,0);assert.equal(f.reports.length,0);
});

test('native acknowledgement loss remains unknown in persisted job and original-chat report',async t=>{
  const f=fixture(t),jobs=f.create({sendAudio:async audio=>{await audio.beforeSend();throw Error('acknowledgement unknown')}});
  const accepted=await jobs.submit(args,exec('owner-session'));await jobs.whenIdle();
  const result=jobs.status(accepted.jobId,exec('owner-session'));
  assert.equal(result.status,'unknown');assert.equal(result.deliveryStatus,'unknown');
  assert.equal(f.reports.length,1);assert.equal(f.reports[0].status,'unknown');
  jobs.close();const restarted=f.create();await restarted.whenIdle();
  assert.equal(restarted.status(accepted.jobId,exec('owner-session')).status,'unknown');
  assert.equal(f.reports.length,1);restarted.close();
});

test('partial crashed job record is held without replay or breaking adapter initialization',async t=>{
  const f=fixture(t),jobId=id(),file=f.jobFile(songChatKey('owner'),jobId);
  fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,'{"format":');
  const jobs=f.create();await jobs.whenIdle();
  assert.equal(jobs.diagnostics().recoveryIssues,1);
  assert.deepEqual(f.calls,{prepare:0,render:0,send:0});assert.equal(f.reports.length,0);
  assert.equal(fs.readFileSync(file,'utf8'),'{"format":');
  assert.throws(()=>jobs.status(jobId,exec('owner-session')));jobs.close();
});
