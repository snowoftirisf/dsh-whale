import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createNativeVoiceInbox} from './native-voice-inbox.mjs';
import {createNativeVoiceProcessor} from './native-voice-processor.mjs';

const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
function fixture(t,{baseline=true}={}){
  const parent=path.resolve('artifacts');fs.mkdirSync(parent,{recursive:true});
  const dataRoot=fs.mkdtempSync(path.join(parent,'native-voice-processor-test-'));
  t.after(()=>fs.rmSync(dataRoot,{recursive:true,force:true}));
  const config={dataRoot,accountId:'fixture-account'},inbox=createNativeVoiceInbox(config);
  const voice=(id,changes={})=>({nativeId:'wechat-server:'+id,serverId:String(id),nativeChatKey:'wechat:group@chatroom',
    senderId:'fixture-member',senderDisplayName:'Synthetic member',rawText:'[语音]',timestampSeconds:1700000000,
    localId:Number(id),snapshotSourceId:'fixture-shard',direction:'incoming',...changes});
  const catalog=(voices=[])=>({source:'authenticated-native-voice-catalog',accountId:config.accountId,snapshotId:'fixture-snapshot',voices});
  if(baseline)inbox.initialize(catalog(),{nativeChatKeys:['wechat:group@chatroom','wechat:private']});
  const state={voices:[],events:[],reports:[],calls:[],routes:new Map([
    ['wechat:group@chatroom',{target:'Fixture group',kind:'group',sessionId:'fixture-group'}],
    ['wechat:private',{target:'Fixture owner',kind:'friend',sessionId:'fixture-owner'}],
  ])};
  const deps={inbox,readCatalog:async()=>{state.calls.push('catalog');return catalog(state.voices);},
    routeForNativeKey:key=>state.routes.get(key),report:(code,facts)=>state.reports.push({code,...facts}),
    extract:async c=>{state.calls.push('extract:'+c.serverId);return {file:path.join(dataRoot,c.serverId+'.silk'),
      metadata:{serverId:c.serverId,nativeChatKey:c.nativeChatKey,senderId:c.senderId,timestampSeconds:c.timestampSeconds,
        localId:c.localId,snapshotId:c.snapshotId,sha256:'a'.repeat(64),durationMilliseconds:2000}};},
    decode:async source=>{state.calls.push('decode');return {file:source.file+'.wav',sha256:'b'.repeat(64),sourceSha256:source.metadata.sha256};},
    transcribe:async()=>{state.calls.push('asr');return {text:'synthetic transcript',backend:'sensevoice-int8'};},
    history:{incoming:async event=>{state.calls.push('history:'+event.nativeId);return {messageIds:[event.nativeId+':source',event.nativeId+':transcript']};}},
    enqueue:event=>{state.calls.push('buffer:'+event.nativeId);state.events.push(event);},
  };
  return {dataRoot,config,inbox,voice,catalog,state,deps,make:overrides=>createNativeVoiceProcessor({...deps,...overrides})};
}

test('uninitialized refuses without reading catalog; baseline voices never replay',async t=>{
  const f=fixture(t,{baseline:false}),p=f.make();
  await assert.rejects(p.refresh(),/baseline-required/);assert.deepEqual(f.state.calls,[]);
  f.inbox.initialize(f.catalog([f.voice('1')]));f.state.voices=[f.voice('1'),f.voice('2')];
  await p.refresh();assert.deepEqual(f.state.events.map(e=>e.nativeId),['wechat-server:2']);
  assert.equal(f.inbox.pending().length,1);
});

test('submit preflight is pure and binds caller session plus target to captured active route',async t=>{
  const f=fixture(t);f.state.voices=[f.voice('1')];const p=f.make();await p.refresh();
  const event=f.state.events[0],revision=f.inbox.status().revision;
  assert.deepEqual(p.validateSubmitting([event],{sessionId:'fixture-group',target:'Fixture group'}),[event.nativeVoiceClaimId]);
  for(const scope of [{sessionId:'wrong'},{target:'wrong'}])assert.throws(()=>p.validateSubmitting([event],scope),/submit-invalid/);
  assert.equal(f.inbox.status().revision,revision);assert.equal(f.inbox.pending().length,1);
});

test('concurrent refresh is coalesced and pending is timestamp/local ordered; buffer never submits',async t=>{
  const f=fixture(t),gate=deferred();f.state.voices=[f.voice('3'),f.voice('2'),f.voice('1',{timestampSeconds:1699999999})];
  const p=f.make({readCatalog:async()=>{await gate.promise;return f.catalog(f.state.voices);}});
  const first=p.refresh(),second=p.refresh();assert.equal(first,second);gate.resolve();
  assert.equal((await first).dispatched,3);
  assert.deepEqual(f.state.events.map(e=>e.nativeId),['wechat-server:1','wechat-server:2','wechat-server:3']);
  assert.equal(f.inbox.pending().length,3);await p.refresh();assert.equal(f.state.events.length,3);
  const event=f.state.events[0];assert.equal(event.mediaType,'voice');assert.equal(event.timestamp,1699999999000);
  assert.equal(event.id,'native-voice:'+event.nativeVoiceClaimId);assert.equal(event.nativeSourceText,'[语音]');
  assert.equal(event.text,'[本地语音转文字，可能有误]\nsynthetic transcript');
  assert.deepEqual(event.audio.map(a=>a.role),['source','decoded']);
  assert.ok(f.state.calls.indexOf('history:wechat-server:1')<f.state.calls.indexOf('buffer:wechat-server:1'));
});

test('only exact active native route is processed, including private without group scope leakage',async t=>{
  const f=fixture(t);
  f.inbox.adoptChat(f.catalog(),{nativeChatKey:'wechat:else@chatroom',unreadServerIds:[]});
  f.state.voices=[f.voice('1'),f.voice('2',{nativeChatKey:'wechat:private'}),f.voice('3',{nativeChatKey:'wechat:else@chatroom'})];
  f.state.routes.set('wechat:group@chatroom',{target:'Fixture group',kind:'group',active:false});
  await f.make().refresh();assert.deepEqual(f.state.events.map(e=>[e.target,e.kind]),[['Fixture owner','friend']]);
  assert.equal(f.inbox.pending().length,3);
});

test('extract identity and decode source hash mismatch fail one item and continue other chat',async t=>{
  const f=fixture(t);f.state.voices=[f.voice('1'),f.voice('2'),f.voice('3',{nativeChatKey:'wechat:private'})];
  const p=f.make({extract:async c=>{const value=await f.deps.extract(c);if(c.serverId==='1')value.metadata.senderId='wrong';return value;},
    decode:async source=>{const value=await f.deps.decode(source);if(source.file.endsWith('2.silk'))value.sourceSha256='c'.repeat(64);return value;}});
  assert.equal((await p.refresh()).failed,2);assert.deepEqual(f.state.events.map(e=>e.nativeId),['wechat-server:3']);
  assert.deepEqual(f.state.reports.map(r=>r.code),['native-voice-source-identity-invalid','native-voice-decoded-identity-invalid']);
  assert.equal(f.inbox.pending().length,3);
});

test('empty speech and nondurable history do not buffer or submit, report never contains backend exception',async t=>{
  const f=fixture(t);f.state.voices=[f.voice('1'),f.voice('2'),f.voice('3')];let n=0;
  const p=f.make({transcribe:async()=>++n===1?{text:' ',backend:'sensevoice-int8'}:{text:'fixture only',backend:'sensevoice-int8'},
    history:{incoming:async event=>{if(event.nativeId.endsWith(':2'))return {messageIds:['only-one']};throw Error('secret fixture path and transcript');}}});
  assert.equal((await p.refresh()).failed,3);assert.equal(f.state.events.length,0);assert.equal(f.inbox.pending().length,3);
  assert.deepEqual(f.state.reports.map(r=>r.code),['native-voice-no-speech','native-voice-history-not-durable','native-voice-history-failed']);
  assert.ok(!JSON.stringify(f.state.reports).includes('secret'));
});

test('markSubmitting validates exact buffered events atomically before ask, submitted unknown never replays',async t=>{
  const f=fixture(t);f.state.voices=[f.voice('1'),f.voice('2')];const p=f.make();await p.refresh();
  const changed=structuredClone(f.state.events);changed[1].target='Another chat';
  await assert.rejects(p.markSubmitting(changed),/submit-invalid/);assert.equal(f.inbox.pending().length,2);
  assert.deepEqual(await p.markSubmitting([...f.state.events,{id:'ordinary',text:'not voice'}]),{submitted:2});
  // Caller may now ask; crash/unknown ask outcome deliberately remains submitted.
  assert.equal(f.inbox.pending().length,0);await assert.rejects(p.markSubmitting(f.state.events),/submit-invalid/);
  const restarted=f.make({inbox:createNativeVoiceInbox(f.config)});await restarted.refresh();
  assert.equal(f.state.events.length,2);assert.deepEqual(p.release(f.state.events),{released:0});
});

test('release unasked exact events permits recomputation; forged release does not duplicate buffer',async t=>{
  const f=fixture(t);f.state.voices=[f.voice('1')];const p=f.make();await p.refresh();
  assert.deepEqual(p.release([{...f.state.events[0],transcript:'tampered'}]),{released:0});
  await p.refresh();assert.equal(f.state.events.length,1);
  assert.deepEqual(p.release(f.state.events),{released:1});await p.refresh();assert.equal(f.state.events.length,2);
  assert.deepEqual(await p.markSubmitting([f.state.events[1]]),{submitted:1});
});

test('queued restart recomputes once with the same event id, never persists prepared transcript package',async t=>{
  const f=fixture(t);f.state.voices=[f.voice('1')];await f.make().refresh();
  const p=f.make({inbox:createNativeVoiceInbox(f.config)});await p.refresh();
  assert.equal(f.state.events.length,2);assert.equal(f.state.events[0].id,f.state.events[1].id);
  assert.ok(!fs.readFileSync(path.join(f.dataRoot,'config/native-voice-inbox.json'),'utf8').includes('synthetic transcript'));
  await p.refresh();assert.equal(f.state.events.length,2);
});

test('close during ASR prevents late history/buffer; route revocation after history prevents dispatch',async t=>{
  const f=fixture(t);f.state.voices=[f.voice('1')];const started=deferred(),gate=deferred();
  const p=f.make({transcribe:async()=>{started.resolve();await gate.promise;return {text:'late',backend:'fixture'};}});
  const work=p.refresh();await started.promise;p.close();gate.resolve();await work;
  assert.equal(f.state.events.length,0);assert.ok(!f.state.calls.some(c=>c.startsWith('history:')));
  const second=f.make({history:{incoming:async()=>{f.state.routes.delete('wechat:group@chatroom');return {messageIds:['a','b']};}}});
  await second.refresh();assert.equal(f.state.events.length,0);assert.equal(f.inbox.pending().length,1);
});

test('route rotation between buffering and ask rejects submit, and buffer failure is retryable',async t=>{
  const f=fixture(t);f.state.voices=[f.voice('1')];let fail=true;
  const p=f.make({enqueue:event=>{if(fail)throw Error('fixture buffer failure');f.state.events.push(event);}});
  await p.refresh();assert.equal(p.status().dispatched,0);fail=false;await p.refresh();assert.equal(f.state.events.length,1);
  f.state.routes.set('wechat:group@chatroom',{target:'Fixture group',kind:'group',sessionId:'rotated'});
  await assert.rejects(p.markSubmitting(f.state.events),/submit-invalid/);assert.equal(f.inbox.pending().length,1);
});

test('history exception cannot authorize submit; close blocks submit even after buffering',async t=>{
  const f=fixture(t);f.state.voices=[f.voice('1')];const p=f.make();await p.refresh();p.close();
  await assert.rejects(p.markSubmitting(f.state.events),/processor-closed/);assert.equal(f.inbox.pending().length,1);
});

test('no-speech cools independently, new voice still submits, retry is bounded and queued survives restart',async t=>{
  const f=fixture(t);let time=0,bad=true;
  f.state.voices=[f.voice('1')];
  const transcribe=async decoded=>{f.state.calls.push('asr:'+path.basename(decoded.file));return {text:bad&&decoded.file.endsWith('1.silk.wav')?' ':'fixture',backend:'fixture'};};
  const p=f.make({now:()=>time,transcribe});
  assert.equal((await p.refresh()).failed,1);
  f.state.voices.push(f.voice('2',{nativeChatKey:'wechat:private'}));
  const second=await p.refresh();assert.equal(second.deferred,1);assert.equal(second.nextRetryAt,60000);assert.equal(second.dispatched,1);
  assert.equal(f.state.calls.filter(c=>c==='extract:1').length,1);
  await p.markSubmitting(f.state.events);assert.equal(f.inbox.pending().length,1);
  time=60000;assert.equal((await p.refresh()).failed,1);
  assert.equal((await p.refresh()).nextRetryAt,180000);
  time=180000;await p.refresh();assert.equal((await p.refresh()).nextRetryAt,420000);
  time=420000;await p.refresh();assert.equal((await p.refresh()).nextRetryAt,720000);
  // Restart drops only resource cooldown; queued identity remains eligible, submitted voice never repeats.
  bad=false;const restarted=f.make({now:()=>time,transcribe});assert.equal((await restarted.refresh()).dispatched,1);
  assert.deepEqual(f.state.events.map(e=>e.nativeId),['wechat-server:2','wechat-server:1']);
});

test('identity and transient failures back off per claim; route rotation does not inherit cooldown',async t=>{
  const f=fixture(t);let time=0,broken=true;
  f.state.voices=[f.voice('1'),f.voice('2')];
  const p=f.make({now:()=>time,extract:async c=>{
    if(c.serverId==='2'&&broken)throw Error('private backend diagnostic');
    const value=await f.deps.extract(c);if(c.serverId==='1'&&broken)value.metadata.senderId='wrong';return value;
  }});
  assert.equal((await p.refresh()).failed,2);
  assert.deepEqual(await p.refresh(),{dispatched:0,failed:0,skipped:0,deferred:2,nextRetryAt:15000});
  time=15000;assert.equal((await p.refresh()).failed,1);
  assert.equal((await p.refresh()).nextRetryAt,45000);
  time=45000;await p.refresh();assert.equal((await p.refresh()).nextRetryAt,60000);
  broken=false;f.state.routes.set('wechat:group@chatroom',{target:'Fixture group',kind:'group',sessionId:'rotated'});
  assert.equal((await p.refresh()).dispatched,2);
  assert.ok(!JSON.stringify(f.state.reports).includes('private backend'));
});

test('history failure remains immediately retryable and successful ASR clears former cooldown',async t=>{
  const f=fixture(t);let time=0,empty=true,historyFails=true;
  f.state.voices=[f.voice('1')];
  const p=f.make({now:()=>time,transcribe:async()=>({text:empty?' ':'fixture',backend:'fixture'}),history:{incoming:async()=>{
    if(historyFails)throw Error('fixture');return {messageIds:['source','transcript']};
  }}});
  await p.refresh();time=60000;empty=false;assert.equal((await p.refresh()).failed,1);
  historyFails=false;assert.equal((await p.refresh()).dispatched,1);
});

test('pump performs one fresh retry then cooldown stops 15s recapture; ordinary 30s poll resumes at due time',async t=>{
  const {createNativeVoicePump}=await import('./native-voice-pump.mjs');
  const f=fixture(t);let time=0,next=0,captures=0,asr=0;const timers=new Map();
  f.state.voices=[f.voice('1')];
  const p=f.make({now:()=>time,transcribe:async()=>{asr++;return {text:asr===1?' ':'fixture',backend:'fixture'};}});
  const pump=createNativeVoicePump({now:()=>time,setTimer:(cb,delay)=>{timers.set(++next,{cb,due:time+delay});return next;},clearTimer:id=>timers.delete(id),capture:async()=>{captures++;return{ok:true};},scan:()=>p.refresh()});
  t.after(()=>pump.close());
  await pump.refresh({capture:true});assert.equal(captures,1);assert.equal(asr,1);
  time=15000;const [id,timer]=[...timers][0];timers.delete(id);timer.cb();
  for(let i=0;i<80;i++)await Promise.resolve();
  assert.equal(captures,2);assert.equal(asr,1);assert.equal(timers.size,0);
  time=30000;await pump.refresh();assert.equal(asr,1);assert.equal(timers.size,0);
  time=60000;await pump.refresh();assert.equal(asr,2);assert.equal(f.state.events.length,1);assert.equal(captures,2);
});
