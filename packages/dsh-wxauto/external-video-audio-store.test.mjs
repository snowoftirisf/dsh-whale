import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createExternalVideoAudioStore} from './external-video-audio-store.mjs';

function continuous(f,changes={},observations){
  const end=f.config.now()/1000-1,start=end-10;
  return {segment:{sessionId:'a'.repeat(32),pid:123,event:'segment',sequence:0,startedAt:start,endedAt:end,
    callbackObservedAt:end,timestampBasis:'callback-arrival-minus-audio-frames',durationSeconds:10,
    frames:480000,sampleRate:48000,channels:2,encoding:'pcm-s16le',wavSha256:'b'.repeat(64),audioRetained:false,
    asrStatus:'speech',transcript:'synthetic continuous words',backend:'sensevoice-int8',videoIdentity:'unknown',playingProof:false,...changes},
    observations:observations??[{videoId:'1234567890123',playing:true,observedAtMs:start*1000-1},
      {videoId:'1234567890123',playing:true,observedAtMs:end*1000+1}]};
}

function unbound(f,changes={}){
  return {segment:continuous(f,changes).segment,
    captureProof:{protocolVersion:2,processSpecificCapture:true,captureScopeVersion:1,callbackFrameNormalizationVersion:1},
    reason:'context-unproven'};
}

test('unbound continuous audio persists privately with exact partial duration and no invented context',t=>{
  const f=fixture(t),input=unbound(f),frames=317;
  input.segment.frames=frames;input.segment.durationSeconds=frames/48000;
  input.segment.startedAt=input.segment.endedAt-input.segment.durationSeconds;
  const result=f.privateWriter.recordUnboundContinuous(input);
  assert.equal(result.scope,'owner-private');assert.equal(result.videoId,null);assert.equal(result.videoIdentityStatus,'unattributed');
  assert.equal(f.privateWriter.recordUnboundContinuous(structuredClone(input)).duplicate,true);
  const reopened=createExternalVideoAudioStore(f.config),value=reopened.getEntry({sessionId:'owner',entryId:result.entryId});
  assert.equal(value.version,3);assert.equal(value.frames,frames);assert.equal(value.durationSeconds,frames/48000);
  assert.equal(value.text,input.segment.transcript);assert.equal(value.audioSha256,input.segment.wavSha256);
  assert.equal(value.audioHashStatus,'producer-supplied-unverified');assert.equal(value.playingProof,false);
  assert.equal(value.timeBasis,'callback-arrival-minus-audio-frames');assert.deepEqual(value.captureProof,input.captureProof);
  assert.equal(Object.hasOwn(value,'observations'),false);assert.equal(value.reason,'context-unproven');
  assert.match(value.interpretation,/Owner-private unattributed/);assert.match(value.interpretation,/not playback proof/);
  const directory=path.join(f.root,'voice/video-audio-evidence/owner-private');
  const stored=JSON.parse(fs.readFileSync(path.join(directory,fs.readdirSync(directory)[0],'entry.json')));
  assert.equal(Object.hasOwn(stored.capture,'observations'),false);assert.equal(stored.capture.videoId,null);
});

test('unbound capture requires private capability, verified protocol and strict context-free schema',t=>{
  const f=fixture(t),input=unbound(f);
  assert.throws(()=>f.publicWriter.recordUnboundContinuous(input),/unbound-private-required/);
  for(const change of [{captureProof:undefined},{captureProof:null},{captureProof:{...input.captureProof,protocolVersion:1}},
    {captureProof:{...input.captureProof,processSpecificCapture:false}},{captureProof:{...input.captureProof,callbackFrameNormalizationVersion:0}},
    {reason:'private-page-title'},{context:'raw private UI name'},{observations:continuous(f).observations},{path:'D:/private.wav'}])
    assert.throws(()=>f.privateWriter.recordUnboundContinuous({...input,...change}));
  for(const change of [{frames:0},{frames:480001},{durationSeconds:2},{videoId:'1234567890123'},{playingProof:true}])
    assert.throws(()=>f.privateWriter.recordUnboundContinuous({...input,segment:{...input.segment,...change}}));
  for(const reason of ['context-unproven','context-unavailable','source-unavailable'])
    assert.equal(f.store.getEntry({sessionId:'owner',entryId:f.privateWriter.recordUnboundContinuous({...input,reason}).entryId}).reason,reason);
});

test('groups never open unbound private files even with known entry ID or owner cursor',t=>{
  const f=fixture(t,{scanBudget:1}),privateEntry=f.privateWriter.recordUnboundContinuous(unbound(f));
  f.publicWriter.recordContinuous(continuous(f));
  const ownerPage=f.store.query({sessionId:'owner',limit:1});assert.ok(ownerPage.nextCursor);
  const opened=[],original=fs.openSync;
  const spy=t.mock.method(fs,'openSync',function(file,...args){opened.push(String(file));return original.call(fs,file,...args);});
  const group=f.store.query({sessionId:'group-a'});assert.equal(group.entries.length,1);assert.notEqual(group.entries[0].entryId,privateEntry.entryId);
  assert.throws(()=>f.store.getEntry({sessionId:'group-a',entryId:privateEntry.entryId}),/entry-unavailable/);
  assert.throws(()=>f.store.query({sessionId:'group-a',cursor:ownerPage.nextCursor}),/cursor-invalid/);
  assert.equal(opened.some(file=>file.includes('owner-private')),false);spy.mock.restore();
});

test('owner unbound records paginate with v1/v2, empty filtered pages and distinct empty-ASR statuses',t=>{
  const f=fixture(t,{scanBudget:1});f.privateWriter.record(f.capture('legacy'));
  f.publicWriter.recordContinuous(continuous(f));
  for(const [sequence,status] of [[1,'no-speech'],[2,'no-transcript']])
    f.privateWriter.recordUnboundContinuous(unbound(f,{sequence,asrStatus:status,transcript:''}));
  const expected=f.privateWriter.recordUnboundContinuous(unbound(f,{sequence:3,transcript:'only unbound needle'}));
  let cursor,found=[],pages=0;
  do{const page=f.store.query({sessionId:'owner',query:'needle',dateFrom:'2026-11-02',cursor});found.push(...page.entries);cursor=page.nextCursor;pages++;}while(cursor);
  assert.equal(pages,5);assert.deepEqual(found.map(e=>e.entryId),[expected.entryId]);
  let all=[],next;
  do{const page=f.store.query({sessionId:'owner',cursor:next});all.push(...page.entries);next=page.nextCursor;}while(next);
  assert.deepEqual(all.filter(e=>e.version===3&&e.asrStatus!=='speech').map(e=>e.asrStatus).sort(),['no-speech','no-transcript']);
});

test('unbound stored scope/proof/hash tampering is corrupt without losing other versions',t=>{
  const f=fixture(t),legacy=f.publicWriter.record(f.capture('legacy-public'));
  const saved=f.privateWriter.recordUnboundContinuous(unbound(f));
  const directory=path.join(f.root,'voice/video-audio-evidence/owner-private');
  const file=path.join(directory,fs.readdirSync(directory)[0],'entry.json'),original=JSON.parse(fs.readFileSync(file));
  for(const change of [{capture:{...original.capture,videoId:'1234567890123'}},
    {capture:{...original.capture,captureProof:{...original.capture.captureProof,processSpecificCapture:false}}},
    {scope:'public-autonomous-video'},{id:'f'.repeat(64)}]){
    fs.writeFileSync(file,JSON.stringify({...original,...change}));
    const restarted=createExternalVideoAudioStore(f.config);
    assert.throws(()=>restarted.getEntry({sessionId:'owner',entryId:saved.entryId}),/entry-corrupt/);
    assert.equal(restarted.query({sessionId:'owner'}).corrupt,1);
    assert.equal(restarted.getEntry({sessionId:'group-a',entryId:legacy.entryId}).videoId,'legacy-public');
  }
});

test('continuous stable state is public partial audio, never playback proof; v1 survives restart',t=>{
  const f=fixture(t),legacy=f.privateWriter.record(f.capture('legacy-private'));
  const input=continuous(f),saved=f.publicWriter.recordContinuous(input);
  assert.equal(saved.videoIdentityStatus,'state-associated');assert.equal(saved.videoId,'1234567890123');
  assert.equal(f.publicWriter.recordContinuous(structuredClone(input)).duplicate,true);
  const restarted=createExternalVideoAudioStore(f.config),entry=restarted.getEntry({sessionId:'group-a',entryId:saved.entryId});
  assert.equal(entry.frames,480000);assert.equal(entry.durationSeconds,10);assert.equal(entry.playingProof,false);
  assert.equal(entry.videoIdentityStatus,'state-associated');assert.equal(entry.audioHashStatus,'producer-supplied-unverified');
  assert.equal(entry.timeBasis,'callback-arrival-minus-audio-frames');assert.match(entry.interpretation,/not playback proof/);
  assert.equal(entry.text,input.segment.transcript);assert.equal(restarted.getEntry({sessionId:'owner',entryId:legacy.entryId}).videoId,'legacy-private');
  assert.throws(()=>restarted.getEntry({sessionId:'group-a',entryId:legacy.entryId}),/entry-unavailable/);
});

test('cross-video and middle transition segments retain every ASR word with null attribution',t=>{
  const f=fixture(t),input=continuous(f),a=input.observations[0],b=input.observations[1];
  for(const observations of [[a,{...b,videoId:'9876543210987'}],[a,{videoId:'9876543210987',playing:false,observedAtMs:(a.observedAtMs+b.observedAtMs)/2},b]]){
    const result=f.publicWriter.recordContinuous({...input,observations});
    const entry=f.store.getEntry({sessionId:'group-a',entryId:result.entryId});
    assert.equal(entry.videoId,null);assert.equal(entry.videoIdentityStatus,'unattributed-or-mixed');assert.equal(entry.text,input.segment.transcript);
  }
  assert.equal(f.store.query({sessionId:'group-a',videoId:a.videoId}).entries.length,0);
  assert.equal(f.store.query({sessionId:'group-a',query:'continuous',dateFrom:'2026-11-02'}).entries.length,2);
});

test('continuous capability and strict helper schema reject private scope, file paths and fabricated proof',t=>{
  const f=fixture(t),input=continuous(f);
  assert.throws(()=>f.privateWriter.recordContinuous(input),/continuous-public-required/);
  for(const patch of [{frames:479999},{durationSeconds:9.99},{sampleRate:44100},{channels:1},{playingProof:true},
    {videoIdentity:'1234567890123'},{wavSha256:'bad'},{sessionId:'other'},{sequence:-1},{startedAt:NaN},
    {startedAt:input.segment.startedAt+1},{backend:'untrusted'},{path:'private.wav'},{scope:'owner-private'}])
    assert.throws(()=>f.publicWriter.recordContinuous({...input,segment:{...input.segment,...patch}}));
  assert.throws(()=>f.publicWriter.recordContinuous({...input,scope:'owner-private'}));
  for(const observations of [[],[input.observations[0]],Array(41).fill(input.observations[0]),input.observations.toReversed(),
    [{...input.observations[0],videoId:'private' },input.observations[1]],
    [{...input.observations[0],observedAtMs:input.segment.startedAt*1000+1},input.observations[1]]])
    assert.throws(()=>f.publicWriter.recordContinuous({...input,observations}));
});

test('short pause-tail audio survives restart with its actual frame count and unchanged public scope',t=>{
  const f=fixture(t),input=continuous(f);input.segment.startedAt=input.segment.endedAt-2;
  input.segment.frames=96000;input.segment.durationSeconds=2;
  const saved=f.publicWriter.recordContinuous(input);
  const restarted=createExternalVideoAudioStore(f.config),entry=restarted.getEntry({sessionId:'group-a',entryId:saved.entryId});
  assert.equal(entry.frames,96000);assert.equal(entry.durationSeconds,2);
  assert.equal(entry.text,input.segment.transcript);assert.equal(entry.playingProof,false);
  assert.throws(()=>f.publicWriter.recordContinuous({...input,segment:{...input.segment,frames:0}}));
  assert.throws(()=>f.publicWriter.recordContinuous({...input,segment:{...input.segment,frames:96001}}));
});

test('process-specific capture proof persists and cannot be silently relabelled as verified',t=>{
  const f=fixture(t),captureProof={protocolVersion:2,processSpecificCapture:true,captureScopeVersion:1,callbackFrameNormalizationVersion:1};
  const saved=f.publicWriter.recordContinuous({...continuous(f),captureProof});
  const reopened=createExternalVideoAudioStore(f.config).getEntry({sessionId:'group-a',entryId:saved.entryId});
  assert.deepEqual(reopened.captureProof,captureProof);
  for(const patch of [{processSpecificCapture:false},{protocolVersion:1},{captureScopeVersion:0},{callbackFrameNormalizationVersion:0}]){
    assert.throws(()=>f.publicWriter.recordContinuous({...continuous(f),captureProof:{...captureProof,...patch}}));
  }
  const old=f.publicWriter.recordContinuous(continuous(f));
  assert.equal(Object.hasOwn(f.store.getEntry({sessionId:'group-a',entryId:old.entryId}),'captureProof'),false);
});

test('continuous empty-ASR statuses remain distinct and non-playing snapshots are no proof',t=>{
  const f=fixture(t);
  for(const asrStatus of ['no-speech','no-transcript']){
    const input=continuous(f,{asrStatus,transcript:''});input.observations[0].playing=false;
    const saved=f.publicWriter.recordContinuous(input),entry=f.store.getEntry({sessionId:'owner',entryId:saved.entryId});
    assert.equal(entry.asrStatus,asrStatus);assert.equal(entry.text,'');assert.equal(entry.playingProof,false);
    assert.match(entry.interpretation,/does not establish silence/);
    assert.throws(()=>f.publicWriter.recordContinuous(continuous(f,{asrStatus,transcript:'not empty'})));
  }
  assert.throws(()=>f.publicWriter.recordContinuous(continuous(f,{asrStatus:'speech',transcript:' '})));
});

test('continuous mixed records paginate across empty video filters without disappearing',t=>{
  const f=fixture(t,{scanBudget:1}),ids=[];
  for(let n=0;n<3;n++){
    const input=continuous(f,{sequence:n});input.observations[1].videoId=n===2?'1234567890123':'9876543210987';
    ids.push(f.publicWriter.recordContinuous(input).entryId);
  }
  let cursor,pages=0,found=[];
  do{const result=f.store.query({sessionId:'group-a',videoId:'1234567890123',cursor});found.push(...result.entries);cursor=result.nextCursor;pages++;}while(cursor);
  assert.equal(pages,3);assert.equal(found.length,1);
  assert.equal(f.store.query({sessionId:'group-a',query:'synthetic'}).scanned,1);
});

test('continuous stored identity/status/time tamper is rejected rather than relabelled',t=>{
  const f=fixture(t),saved=f.publicWriter.recordContinuous(continuous(f)),folder=path.join(f.root,'voice/video-audio-evidence/public-autonomous-video');
  const file=path.join(folder,fs.readdirSync(folder)[0],'entry.json'),original=JSON.parse(fs.readFileSync(file));
  for(const change of [{videoId:'9876543210987'},{playingProof:true},{estimatedEndedAtMs:original.capture.estimatedEndedAtMs+1},
    {timeBasis:'exact-playback'},{videoIdentityStatus:'watched'}]){
    fs.writeFileSync(file,JSON.stringify({...original,capture:{...original.capture,...change}}));
    assert.throws(()=>f.store.getEntry({sessionId:'owner',entryId:saved.entryId}),/entry-corrupt/);
  }
});

function fixture(t,options={}){
  const parent=path.resolve('artifacts');fs.mkdirSync(parent,{recursive:true});
  const root=fs.mkdtempSync(path.join(parent,'external-video-audio-test-'));
  t.after(()=>fs.rmSync(root,{force:true,recursive:true}));
  const privateProducer={},trustedProducer={},now=Date.parse('2026-11-02T12:00:00Z');
  const access=new Map([['owner','owner'],['group-a','group'],['group-b','group']]);
  const config={dataRoot:root,privateProducer,trustedProducer,now:()=>now,resolveAccess:id=>access.get(id),...options};
  const store=createExternalVideoAudioStore(config);
  const capture=(videoId='123456789',changes={})=>({platform:'douyin',videoId,
    before:{videoId,playing:true,observedAtMs:now-3000},after:{videoId,playing:true,observedAtMs:now-1000},
    durationSeconds:2,asr:{status:'speech',text:'synthetic video speech',backend:'sensevoice-int8'},...changes});
  return {root,store,config,privateProducer,trustedProducer,access,capture,
    privateWriter:store.writerFor(privateProducer),publicWriter:store.writerFor(trustedProducer)};
}

test('constructor is inert, does not import legacy cache, and capabilities cannot be selected by arguments',t=>{
  const f=fixture(t),folder=path.join(f.root,'voice','video-audio-evidence');
  fs.writeFileSync(path.join(f.root,'recent-audio.json'),JSON.stringify([{text:'legacy-private-cache'}]));
  const spy=t.mock.method(fs,'lstatSync',()=>{throw Error('constructor must not read');});
  createExternalVideoAudioStore(f.config);spy.mock.restore();
  assert.equal(fs.existsSync(folder),false);
  assert.throws(()=>f.store.writerFor({}),/producer-denied/);
  assert.throws(()=>createExternalVideoAudioStore({...f.config,privateProducer:f.trustedProducer}),/config-invalid/);
  assert.throws(()=>f.privateWriter.record({...f.capture(),scope:'public-autonomous-video'}),/capture-invalid/);
  assert.equal(fs.existsSync(folder),false);
  assert.deepEqual(f.store.query({sessionId:'group-a'}).entries,[]);
});

test('real playing proof and strict ASR statuses are required; absent position/hash are honestly unknown',t=>{
  const f=fixture(t);
  for(const update of [{before:{videoId:'other',playing:true,observedAtMs:1}},
    {after:{videoId:'123456789',playing:false,observedAtMs:1}}, {durationSeconds:0},
    {durationSeconds:NaN},{durationSeconds:Infinity},{durationSeconds:30.01},
    {asr:{status:'speech',text:' ',backend:'sensevoice-int8'}},
    {asr:{status:'no-speech',text:'not empty',backend:'sensevoice-int8'}},
    {filePath:'D:/private-file.wav'},{cookies:'must not persist'}])
    assert.throws(()=>f.privateWriter.record(f.capture('123456789',update)));
  for(const status of ['no-speech','no-transcript']){
    const record=f.privateWriter.record(f.capture(status,{asr:{status,text:'',backend:'sensevoice-int8'}}));
    const entry=f.store.getEntry({sessionId:'owner',entryId:record.entryId});
    assert.equal(entry.asrStatus,status);assert.equal(entry.audioHashStatus,'unavailable');
    assert.equal(entry.audioSha256,null);assert.deepEqual(entry.samplePosition,{status:'unknown'});
    assert.match(entry.interpretation,/does not establish silence/);
  }
  const actual=f.privateWriter.record(f.capture('actual-duration',{durationSeconds:1.99}));
  assert.equal(f.store.getEntry({sessionId:'owner',entryId:actual.entryId}).durationSeconds,1.99);
  const restarted=createExternalVideoAudioStore(f.config);
  assert.equal(restarted.query({sessionId:'owner',videoId:'actual-duration'}).entries[0].durationSeconds,1.99);
});

test('private and public writers are distinct; groups never open private files or infer private entry IDs',t=>{
  const f=fixture(t),privateEntry=f.privateWriter.record(f.capture('private-video'));
  const publicEntry=f.publicWriter.record(f.capture('public-video',{audioSha256:'a'.repeat(64),samplePosition:{startSeconds:12,endSeconds:14}}));
  const opened=[],original=fs.openSync;
  const spy=t.mock.method(fs,'openSync',function(file,...args){opened.push(String(file));return original.call(fs,file,...args);});
  assert.deepEqual(f.store.query({sessionId:'group-a'}).entries.map(e=>e.entryId),[publicEntry.entryId]);
  assert.equal(f.store.getEntry({sessionId:'group-b',entryId:publicEntry.entryId}).audioHashStatus,'producer-supplied-unverified');
  assert.throws(()=>f.store.getEntry({sessionId:'group-a',entryId:privateEntry.entryId}),/entry-unavailable/);
  assert.equal(opened.some(p=>p.includes('owner-private')),false);spy.mock.restore();
  assert.equal(f.store.query({sessionId:'owner'}).entries.length,2);
  assert.throws(()=>f.store.query({sessionId:'unknown'}),/session-denied/);
});

test('immutable records deduplicate exactly and remain readable after restart and relocated copy',t=>{
  const f=fixture(t),input=f.capture(),first=f.privateWriter.record(input);
  assert.equal(f.privateWriter.record(structuredClone(input)).duplicate,true);
  const fresh=createExternalVideoAudioStore(f.config);
  assert.equal(fresh.getEntry({sessionId:'owner',entryId:first.entryId}).text,input.asr.text);
  const copy=path.join(f.root,'portable-copy');fs.cpSync(path.join(f.root,'voice'),path.join(copy,'voice'),{recursive:true});
  const restored=createExternalVideoAudioStore({...f.config,dataRoot:copy});
  assert.equal(restored.getEntry({sessionId:'owner',entryId:first.entryId}).entryId,first.entryId);
  const folder=path.join(f.root,'voice','video-audio-evidence','owner-private');
  const file=path.join(folder,fs.readdirSync(folder)[0],'entry.json'),bytes=fs.readFileSync(file);
  assert.equal(bytes.includes(Buffer.from(f.root)),false);
  fs.writeFileSync(file,'corrupt existing record');
  assert.throws(()=>f.privateWriter.record(input));
  assert.equal(fs.readFileSync(file,'utf8'),'corrupt existing record');
});

test('scan-budget cursor advances even through empty filtered pages and is scope/filter bound',t=>{
  const f=fixture(t,{scanBudget:1});
  for(let n=0;n<4;n++)f.publicWriter.record(f.capture('video'+n,{
    before:{videoId:'video'+n,playing:true,observedAtMs:f.config.now()-10000+n*2000},
    after:{videoId:'video'+n,playing:true,observedAtMs:f.config.now()-8000+n*2000},
    asr:{status:'speech',text:n===3?'needle synthetic':'different synthetic',backend:'sensevoice-int8'}}));
  let cursor,found=[],pages=0,first;
  do{
    const page=f.store.query({sessionId:'group-a',query:'needle',dateFrom:'2026-11-02',dateTo:'2026-11-02',cursor});
    if(!first)first=page;
    assert.equal(page.scanned,1);found.push(...page.entries);cursor=page.nextCursor;pages++;
  }while(cursor);
  assert.equal(pages,4);assert.equal(found.length,1);assert.equal(found[0].videoId,'video3');assert.equal(first.entries.length,0);
  assert.throws(()=>f.store.query({sessionId:'owner',query:'needle',dateFrom:'2026-11-02',dateTo:'2026-11-02',cursor:first.nextCursor}),/cursor-invalid/);
  assert.throws(()=>f.store.query({sessionId:'group-a',query:'other',cursor:first.nextCursor}),/cursor-invalid/);
  assert.throws(()=>f.store.query({sessionId:'group-a',cursor:'malformed'}),/cursor-invalid/);
  assert.throws(()=>f.store.query({sessionId:'owner',dateFrom:'2026-02-30'}),/filter-invalid/);
  assert.equal(f.store.query({sessionId:'owner',platform:'bilibili'}).entries.length,0);
});

test('Unicode text expansion uses codepoints and never advances beyond returned text',t=>{
  const f=fixture(t),text='甲😀乙🎵丙'.repeat(100),r=f.publicWriter.record(f.capture('long-video',{asr:{status:'speech',text,backend:'sensevoice-int8'}}));
  let offset=0,rebuilt='';
  do{
    const part=f.store.getEntry({sessionId:'group-a',entryId:r.entryId,offset,maxChars:7});
    rebuilt+=part.text;
    assert.equal(part.textOffset,offset);assert.equal(part.totalCharacters,Array.from(text).length);
    if(part.nextTextOffset!==null)assert.equal(part.nextTextOffset,offset+Array.from(part.text).length);
    offset=part.nextTextOffset;
  }while(offset!==null);
  assert.equal(rebuilt,text);
  assert.throws(()=>f.store.getEntry({sessionId:'owner',entryId:r.entryId,offset:501}),/offset-invalid/);
});

test('unfinished temp files and corrupt final files do not discard valid history or stall pagination',t=>{
  const f=fixture(t,{scanBudget:1}),r=f.publicWriter.record(f.capture());
  const folder=path.join(f.root,'voice','video-audio-evidence','public-autonomous-video');
  fs.writeFileSync(path.join(folder,'.pending-interrupted.tmp'),'incomplete JSON');
  const broken=path.join(folder,'0000000000000001-'+'f'.repeat(64)+'.entry');fs.mkdirSync(broken);fs.writeFileSync(path.join(broken,'entry.json'),'{');
  const first=f.store.query({sessionId:'group-a'});
  assert.equal(first.corrupt,1);assert.equal(first.entries.length,0);assert.ok(first.nextCursor);
  const fresh=createExternalVideoAudioStore({...f.config,scanBudget:1});
  const second=fresh.query({sessionId:'group-a',cursor:first.nextCursor});
  assert.equal(second.entries[0].entryId,r.entryId);assert.equal(second.nextCursor,null);
});

test('route revocation is checked before access and again before returning private evidence',t=>{
  const f=fixture(t);f.privateWriter.record(f.capture());let checks=0;
  const changing=createExternalVideoAudioStore({...f.config,resolveAccess:()=>++checks===1?'owner':'group'});
  assert.throws(()=>changing.query({sessionId:'rotating-owner'}),/session-denied/);
  f.access.delete('owner');assert.throws(()=>f.store.query({sessionId:'owner'}),/session-denied/);
});

test('restricted paths and hard-linked records are rejected without opening unrelated files',t=>{
  const f=fixture(t);
  assert.throws(()=>createExternalVideoAudioStore({...f.config,dataRoot:'relative-root'}),/config-invalid/);
  assert.throws(()=>createExternalVideoAudioStore({...f.config,dataRoot:f.root+'/../outside'}),/config-invalid/);
  const r=f.publicWriter.record(f.capture());const folder=path.join(f.root,'voice','video-audio-evidence','public-autonomous-video');
  const file=path.join(folder,fs.readdirSync(folder)[0],'entry.json');
  const lstat=fs.lstatSync;
  t.mock.method(fs,'lstatSync',function(p,...args){const value=lstat.call(fs,p,...args);if(String(p)===file)Object.defineProperty(value,'nlink',{value:2});return value;});
  assert.throws(()=>f.store.getEntry({sessionId:'owner',entryId:r.entryId}),/entry-corrupt/);
  assert.equal(f.store.query({sessionId:'group-a'}).corrupt,1);
  assert.match(createHash('sha256').update(fs.readFileSync(file)).digest('hex'),/^[a-f0-9]{64}$/);
});

test('publication failure preserves prior history and a second instance wins a racing identical record safely',t=>{
  const f=fixture(t),old=f.privateWriter.record(f.capture('old-video'));
  const original=fs.renameSync,rename=t.mock.method(fs,'renameSync',()=>{throw Error('fixture write interruption');});
  const input=f.capture('new-video');
  assert.throws(()=>f.privateWriter.record(input),/video-audio-write-failed/);rename.mock.restore();
  assert.equal(f.store.getEntry({sessionId:'owner',entryId:old.entryId}).videoId,'old-video');
  const rival=createExternalVideoAudioStore(f.config).writerFor(f.privateProducer);let racing=false,won;
  const race=t.mock.method(fs,'renameSync',function(source,destination){
    if(!racing){racing=true;won=rival.record(input);}
    return original.call(fs,source,destination);
  });
  const record=f.privateWriter.record(input);race.mock.restore();
  assert.equal(record.entryId,won.entryId);assert.equal(record.duplicate,true);
  assert.equal(f.store.query({sessionId:'owner'}).entries.length,2);
});
