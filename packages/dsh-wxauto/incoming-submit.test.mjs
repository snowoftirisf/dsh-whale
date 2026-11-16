import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {createNativeVoiceInbox} from './native-voice-inbox.mjs';
import {createNativeVoiceProcessor} from './native-voice-processor.mjs';
import {createNativeOwnerTextInbox,ownerTextScopeDigest} from './native-owner-text-inbox.mjs';
import {createNativeOwnerTextProcessor} from './native-owner-text-processor.mjs';
import {createIncomingSubmissionGate,isIncomingSubmissionSafelyUnissued} from './incoming-submit.mjs';

async function fixture(t){
  const dataRoot=fs.mkdtempSync(path.resolve('artifacts/incoming-submit-'));t.after(()=>fs.rmSync(dataRoot,{recursive:true,force:true}));
  const accountId='fixture',identity={accountId,ownerNativeChatKey:'wechat:peer',expectedPeerUsername:'peer',ownerTarget:'Owner'};
  const row=(id,rawText)=>({nativeId:'wechat-server:'+id,serverId:String(id),nativeChatKey:'wechat:peer',senderId:'peer',senderDisplayName:'Peer',
    rawText,timestampSeconds:100,localId:id,snapshotSourceId:'synthetic/'+id,direction:'incoming'});
  const vc=voices=>({source:'authenticated-native-voice-catalog',accountId,snapshotId:'synthetic',voices});
  const tc=messages=>({source:'authenticated-native-owner-text-catalog',accountId,snapshotId:'synthetic',complete:true,
    nativeChatKey:'wechat:peer',scopeDigest:ownerTextScopeDigest(accountId,'wechat:peer','peer'),messages});
  const voiceInbox=createNativeVoiceInbox({dataRoot,accountId});voiceInbox.initialize(vc([]),{nativeChatKeys:['wechat:peer']});
  const textInbox=createNativeOwnerTextInbox({dataRoot,...identity,now:()=>200000});textInbox.initialize(tc([]),{cutoverAtMs:100000});
  const route={target:'Owner',kind:'friend',sessionId:'original-session'},events=[];
  const voiceProcessor=createNativeVoiceProcessor({inbox:voiceInbox,readCatalog:async()=>vc([row(1,'[voice]')]),routeForNativeKey:()=>route,
    extract:async c=>({file:path.join(dataRoot,'source.silk'),metadata:{serverId:c.serverId,nativeChatKey:c.nativeChatKey,senderId:c.senderId,
      timestampSeconds:c.timestampSeconds,localId:c.localId,sha256:'a'.repeat(64),durationMilliseconds:2000}}),
    decode:async s=>({file:s.file+'.wav',sha256:'b'.repeat(64),sourceSha256:s.metadata.sha256}),
    transcribe:async()=>({text:'synthetic ASR',backend:'fixture-cpu'}),history:{incoming:async()=>({messageIds:['source','transcript']})},enqueue:async e=>events.push(e)});
  const textProcessor=createNativeOwnerTextProcessor({...identity,inbox:textInbox,readCatalog:async()=>tc([row(2,'synthetic ordinary text')]),
    routeForOwner:()=>route,activeAuthority:()=>({active:true,...identity,authorityEpoch:textInbox.status().authorityEpoch}),
    history:{incoming:async()=>({id:'synthetic-text-history'})},enqueue:async e=>events.push(e)});
  await voiceProcessor.refresh();await textProcessor.refresh();
  const config={dataRoot,accountId,voiceProcessor,textProcessor};
  const args={sessionId:route.sessionId,target:route.target,batch:events};
  return {dataRoot,voiceInbox,textInbox,voiceProcessor,textProcessor,config,args,events,route,gate:createIncomingSubmissionGate(config),
    markers:()=>fs.existsSync(path.join(dataRoot,'config/incoming-submissions'))?fs.readdirSync(path.join(dataRoot,'config/incoming-submissions')):[]};
}

test('mixed real inbox/processors validate without mutation and issue one durable permit only',async t=>{
  const f=await fixture(t);
  for(const p of [f.voiceProcessor,f.textProcessor])assert.equal(p.validateSubmitting(f.events,{sessionId:f.args.sessionId,target:'Owner'}).length,1);
  assert.equal(f.voiceInbox.status().pending,1);assert.equal(f.textInbox.status().pending,1);
  const ui={id:'ui-ordinary',kind:'friend',target:'Owner',text:'synthetic UI input'};
  const value=await f.gate.submit({...f.args,batch:[...f.events,ui]});assert.equal(value.issued,true);assert.match(value.reservationId,/^[a-f0-9]{64}$/);
  assert.equal(f.voiceInbox.pending().length,0);assert.equal(f.textInbox.pending().length,0);assert.equal(f.markers().length,2);
  const contents=f.markers().map(name=>fs.readFileSync(path.join(f.dataRoot,'config/incoming-submissions',name),'utf8')).join('');
  assert.equal(contents.includes('synthetic ASR'),false);assert.equal(contents.includes('synthetic ordinary text'),false);
  await assert.rejects(()=>createIncomingSubmissionGate(f.config).submit({...f.args,batch:[...f.events].reverse()}),/incoming-submit-not-issued/);
});

test('invalid second preflight and wrong session/recipient/UI scope consume neither inbox',async t=>{
  const f=await fixture(t);
  for(const args of [
    {...f.args,batch:f.events.map(e=>e.sourceKind==='wechat-native-text'?{...e,text:'tampered'}:e)},
    {...f.args,sessionId:'wrong-session'}, {...f.args,target:'Other'},
    {...f.args,batch:[...f.events,{id:'spoof',target:'Other',kind:'friend',text:'UI'}]},
    {...f.args,batch:[...f.events,{id:'spoof',target:'Owner',kind:'group',text:'UI'}]},
  ])await assert.rejects(()=>f.gate.submit(args),error=>{
    assert.match(error.message,/not-issued/);assert.equal(isIncomingSubmissionSafelyUnissued(error),true);return true;
  });
  assert.equal(f.voiceInbox.pending().length,1);assert.equal(f.textInbox.pending().length,1);assert.deepEqual(f.markers(),[]);
  await assert.rejects(()=>createIncomingSubmissionGate({...f.config,accountId:'other'}).submit(f.args),/not-issued/);
  assert.equal(f.textInbox.pending().length,1);
});

test('text-only gate issues without consuming unrelated voice; voice-only remains a legacy caller path',async t=>{
  const f=await fixture(t),text=f.events.filter(e=>e.sourceKind==='wechat-native-text'),voice=f.events.filter(e=>e.sourceKind==='wechat-native-voice');
  await assert.rejects(()=>f.gate.submit({...f.args,batch:voice}),/not-issued/);
  assert.deepEqual(f.markers(),[]);assert.equal(f.voiceInbox.pending().length,1);
  assert.equal((await f.gate.submit({...f.args,batch:text})).issued,true);
  assert.equal(f.voiceInbox.pending().length,1);assert.equal(f.textInbox.pending().length,0);
  assert.equal((await f.voiceProcessor.markSubmitting(voice)).submitted,1);
});

test('competing consumer after preflight leaves terminal reservation, no permit or resets after partial submit',async t=>{
  const f=await fixture(t);
  const voice={...f.voiceProcessor,async markSubmitting(...args){
    const value=await f.voiceProcessor.markSubmitting(...args);
    f.textInbox.submit(f.textInbox.pending().map(c=>c.claimId),{requireAllQueued:true});return value;
  }};
  const gate=createIncomingSubmissionGate({...f.config,voiceProcessor:voice});
  await assert.rejects(()=>gate.submit(f.args),error=>{assert.equal(isIncomingSubmissionSafelyUnissued(error),false);return true;});assert.equal(f.markers().length,1);
  assert.equal(f.voiceInbox.pending().length,0);assert.equal(f.textInbox.status().submitted,1);
  await assert.rejects(()=>gate.submit(f.args),/not-issued/);assert.equal(f.markers().length,1);
});

test('reservation fsync/readback and issued-write failures retain terminal markers without second permit',async t=>{
  for(const mode of ['reservation-fsync','reservation-readback','issued-write']){
    const f=await fixture(t);const open=fs.openSync,sync=fs.fsyncSync,write=fs.writeFileSync,read=fs.readFileSync;const descriptors=new Map();
    try{
      fs.openSync=(file,...args)=>{const fd=open(file,...args);descriptors.set(fd,String(file));return fd;};
      fs.fsyncSync=fd=>{if(mode==='reservation-fsync'&&descriptors.get(fd)?.endsWith('.reservation.json'))throw Error('fixture');return sync(fd);};
      fs.readFileSync=(file,...args)=>{if(mode==='reservation-readback'&&descriptors.get(file)?.endsWith('.reservation.json'))throw Error('fixture');return read(file,...args);};
      fs.writeFileSync=(file,...args)=>{if(mode==='issued-write'&&descriptors.get(file)?.endsWith('.issued.json'))throw Error('fixture');return write(file,...args);};
      await assert.rejects(()=>f.gate.submit(f.args),error=>{assert.equal(isIncomingSubmissionSafelyUnissued(error),false);return true;});
    }finally{fs.openSync=open;fs.fsyncSync=sync;fs.writeFileSync=write;fs.readFileSync=read;}
    assert.equal(f.markers().filter(n=>n.endsWith('.reservation.json')).length,1);
    if(mode==='issued-write'){assert.equal(f.voiceInbox.pending().length,0);assert.equal(f.textInbox.pending().length,0);assert.equal(f.markers().length,2);}
    else{assert.equal(f.voiceInbox.pending().length,1);assert.equal(f.textInbox.pending().length,1);}
    await assert.rejects(()=>f.gate.submit(f.args),error=>{
      assert.equal(isIncomingSubmissionSafelyUnissued(error),false);return true;
    });
  }
});

test('only genuine preflight error identity is safely unissued, never a forged/serialized error',async t=>{
  const f=await fixture(t);let genuine;
  try{await f.gate.submit({...f.args,sessionId:'rotated-session'});}catch(error){genuine=error;}
  assert.equal(isIncomingSubmissionSafelyUnissued(genuine),true);
  for(const forged of [Error(genuine.message),{message:genuine.message,code:'incoming-submit-not-issued',safelyUnissued:true},
    {...genuine},JSON.parse(JSON.stringify(genuine)),{cause:genuine},null,undefined,'incoming-submit-not-issued'])
    assert.equal(isIncomingSubmissionSafelyUnissued(forged),false);
  assert.equal(f.voiceInbox.status().pending,1);assert.equal(f.textInbox.status().pending,1);assert.deepEqual(f.markers(),[]);
});

test('path failure after pure preflight remains unbranded and does not release terminal boundary',async t=>{
  const f=await fixture(t);fs.writeFileSync(path.join(f.dataRoot,'config/incoming-submissions'),'not a directory');
  await assert.rejects(()=>f.gate.submit(f.args),error=>{
    assert.match(error.message,/not-issued/);assert.equal(isIncomingSubmissionSafelyUnissued(error),false);return true;
  });
  assert.equal(f.voiceInbox.status().pending,1);assert.equal(f.textInbox.status().pending,1);
});

test('partial mixed reservation remains unbranded across another gate/session/subset and text stays prepared',async t=>{
  const f=await fixture(t),failingText={...f.textProcessor,markSubmitting:()=>{throw Error('fixture before text submit');}};
  const first=createIncomingSubmissionGate({...f.config,textProcessor:failingText});
  await assert.rejects(first.submit(f.args),error=>!isIncomingSubmissionSafelyUnissued(error));
  assert.equal(f.voiceInbox.pending().length,0);assert.equal(f.textInbox.pending().length,1);
  assert.equal(f.textProcessor.status().prepared,1);
  const another=createIncomingSubmissionGate(f.config);
  for(const args of [f.args,{...f.args,sessionId:'another-session'},
    {...f.args,batch:f.events.filter(e=>e.sourceKind==='wechat-native-text')}])
    await assert.rejects(another.submit(args),error=>!isIncomingSubmissionSafelyUnissued(error));
  assert.equal(f.textProcessor.status().prepared,1);await f.textProcessor.refresh();assert.equal(f.events.length,2);
  assert.equal(f.markers().length,1);
  const child=spawnSync(process.execPath,['--input-type=module','-e',`
    import fs from 'node:fs';import {createIncomingSubmissionGate,isIncomingSubmissionSafelyUnissued} from './plugins/dsh-wxauto/incoming-submit.mjs';
    const x=JSON.parse(fs.readFileSync(0,'utf8'));let mutated=false;
    const voiceProcessor={validateSubmitting:()=>[],markSubmitting:()=>{mutated=true;return {submitted:0};}};
    const textProcessor={validateSubmitting:()=>[x.args.batch[0].nativeOwnerTextClaimId],markSubmitting:()=>{mutated=true;return {submitted:1};}};
    try{await createIncomingSubmissionGate({...x.config,voiceProcessor,textProcessor}).submit(x.args);console.log(JSON.stringify({issued:true}));}
    catch(e){console.log(JSON.stringify({safe:isIncomingSubmissionSafelyUnissued(e),mutated}));}
  `],{input:JSON.stringify({config:{dataRoot:f.dataRoot,accountId:'fixture'},args:{...f.args,batch:f.events.filter(e=>e.sourceKind==='wechat-native-text')}}),encoding:'utf8',windowsHide:true});
  assert.equal(child.status,0);assert.deepEqual(JSON.parse(child.stdout),{safe:false,mutated:false});
});

test('unknown, malformed, linked, changing or disappearing reservation inventory cannot brand a rejection',async t=>{
  for(const mode of ['unknown','malformed','linked','changed','disappeared']){
    const f=await fixture(t),dir=path.join(f.dataRoot,'config/incoming-submissions');fs.mkdirSync(dir);
    if(mode==='unknown')fs.writeFileSync(path.join(dir,'unexpected'),'fixture');
    if(mode==='malformed')fs.writeFileSync(path.join(dir,'a'.repeat(64)+'.reservation.json'),'{');
    const stat=fs.lstatSync,list=fs.readdirSync;let once=false;
    try{
      if(mode==='linked')fs.lstatSync=(p,...args)=>{const s=stat(p,...args);if(p!==dir)return s;const v=Object.create(s);v.isSymbolicLink=()=>true;return v;};
      if(['changed','disappeared'].includes(mode))fs.readdirSync=(p,...args)=>{
        const names=list(p,...args);if(p===dir&&!once){once=true;if(mode==='changed')fs.writeFileSync(path.join(dir,'new-unverified'),'fixture');else fs.rmdirSync(dir);}return names;
      };
      await assert.rejects(f.gate.submit({...f.args,sessionId:'rotated'}),error=>!isIncomingSubmissionSafelyUnissued(error));
    }finally{fs.lstatSync=stat;fs.readdirSync=list;}
    assert.equal(f.textInbox.pending().length,1);assert.equal(f.voiceInbox.pending().length,1);
  }
});

test('first-ever reservation-free session rotation permits exact release then one requeue under current session',async t=>{
  const f=await fixture(t),old=[...f.events];f.route.sessionId='rotated-session';
  let error;try{await f.gate.submit({...f.args,sessionId:'rotated-session'});}catch(value){error=value;}
  assert.equal(isIncomingSubmissionSafelyUnissued(error),true);assert.deepEqual(f.markers(),[]);
  assert.deepEqual(f.voiceProcessor.release(old),{released:1});assert.deepEqual(f.textProcessor.release(old),{released:1});
  await f.voiceProcessor.refresh();await f.textProcessor.refresh();assert.equal(f.events.length,4);
  const current=f.events.slice(2),args={sessionId:'rotated-session',target:'Owner',batch:current};let asks=0;
  if((await f.gate.submit(args)).issued)asks++;
  await assert.rejects(f.gate.submit(args),error=>!isIncomingSubmissionSafelyUnissued(error));
  assert.equal(asks,1);assert.equal(f.voiceInbox.pending().length,0);assert.equal(f.textInbox.pending().length,0);
});

test('concurrent gates obtain at most one permit; reordered and modified envelopes cannot reissue',async t=>{
  const f=await fixture(t),other=createIncomingSubmissionGate(f.config);
  const results=await Promise.allSettled([f.gate.submit(f.args),other.submit({...f.args,batch:[...f.events].reverse()})]);
  assert.equal(results.filter(r=>r.status==='fulfilled').length,1);
  await assert.rejects(()=>other.submit({...f.args,batch:f.events.map(e=>({...e,text:'changed'}))}),/not-issued/);
  assert.equal(f.markers().length,2);
});
