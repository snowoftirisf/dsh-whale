import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {createOutgoingHistory} from './outgoing-history.mjs';
import {createVaultProcess} from './vault-process.mjs';

test('rendered file evidence survives offline journal replay as sanitized delivery detail',async()=>{
  const dataRoot=fs.mkdtempSync(path.join(os.tmpdir(),'whale-file-proof-replay-'));
  const config={dataRoot,accountId:'fixture'},routes=new Map([['owner',{kind:'friend'}]]);
  const offline=createOutgoingHistory({config,routes,chat:async()=>({id:'owner-chat'}),
    client:{request:async()=>{throw Error('offline');}},report:()=>{}});
  const reference=offline.record('owner',{kind:'attachment',text:'fixture'},'file-operation');
  const evidence={kind:'rendered-file-row',direction:'self',rowClass:'mmui::ChatBubbleItemView',
    rowRuntimeIdSha256:'a'.repeat(64),filenameSha256:'b'.repeat(64),filenameLength:20,
    stableObservations:2,recipientBytesVerified:false,path:'must-not-leak',name:'must-not-leak'};
  offline.delivery(reference,'file-operation','sent',{renderedFileEvidence:evidence,extra:'must-not-leak'});
  await offline.idle();offline.close();
  const folder=path.join(dataRoot,'snapshots/realtime-outgoing');
  assert.equal(fs.readdirSync(folder).length,2);
  assert.equal(fs.readdirSync(folder).map(f=>fs.readFileSync(path.join(folder,f),'utf8')).join('').includes('must-not-leak'),false);
  const calls=[];
  const online=createOutgoingHistory({config,routes,chat:async()=>({id:'owner-chat'}),
    client:{request:async(op,args)=>{calls.push({op,args});return {id:'message'};}},report:()=>{}});
  try {
    await online.replay();
    const details=calls.filter(c=>c.op==='record_delivery_observation');
    assert.equal(details.length,1);
    const {path:unusedPath,name:unusedName,...expected}=evidence;
    assert.deepEqual(details[0].args.detail.renderedFileEvidence,expected);
    assert.equal(details[0].args.state,'sent');
    assert.deepEqual(fs.readdirSync(folder),[]);
  } finally {online.close();fs.rmSync(dataRoot,{recursive:true,force:true});}
});

test('offline outgoing facts return durable references without waiting for the database',async()=>{
  const dataRoot=fs.mkdtempSync(path.join(os.tmpdir(),'whale-outgoing-offline-'));
  let resolveWriter,calls=0;
  const recorder=createOutgoingHistory({config:{dataRoot,accountId:'fixture'},routes:new Map([['owner',{kind:'friend'}]]),
    chat:async()=>({id:'chat'}),client:{request:()=>{calls++;return new Promise(resolve=>{resolveWriter=resolve;});}},report:()=>{}});
  const reference=recorder.record('owner',{kind:'text',text:'message body'},'op');
  const receipt=recorder.delivery(reference,'op','sent');
  assert.equal(reference.journaled,true);assert.equal(receipt.journaled,true);
  const files=fs.readdirSync(path.join(dataRoot,'snapshots/realtime-outgoing'));
  assert.equal(files.length,2);
  // Closing cannot dispatch a queued UI action; only this injected DB promise exists.
  await new Promise(resolve=>setImmediate(resolve));assert.equal(calls,1);
  recorder.close();resolveWriter({id:'stored'});await recorder.idle();
  assert.equal(calls,1);
});

test('offline generated/attempted/sent facts replay into a real writer as one sent message',async t=>{
  const cwd=path.dirname(fileURLToPath(import.meta.url));
  const dataRoot=fs.mkdtempSync(path.join(os.tmpdir(),'whale-outgoing-recovery-'));
  const config={dataRoot,accountId:'fixture'},routes=new Map([['owner',{kind:'friend'}],['group',{kind:'group'}]]);
  const offline=createOutgoingHistory({config,routes,chat:async()=>({id:'offline'}),
    client:{request:async()=>{throw Error('writer unavailable fixture');}},report:()=>{}});
  const ref=offline.record('owner',{kind:'voice',text:'source speech text',durationSeconds:12,at:[]},'one-operation');
  offline.delivery(ref,'one-operation','attempted');offline.delivery(ref,'one-operation','sent');
  await offline.idle();offline.close();
  const folder=path.join(dataRoot,'snapshots/realtime-outgoing');assert.equal(fs.readdirSync(folder).length,3);
  const children=[];
  const client=createVaultProcess({python:(process.env.WHALE_TEST_PYTHON||path.resolve(cwd,'../../.venv-wxauto/Scripts/python.exe')),cwd,dataRoot,
    spawnWorker:(...args)=>{const child=spawn(...args);children.push(child);return child;}});
  t.after(async()=>{client.close();await Promise.all(children.filter(c=>c.exitCode===null&&c.signalCode===null).map(c=>once(c,'exit')));});
  const chat=target=>client.request('ensure_chat',{account_id:'fixture',chat_key:target,kind:routes.get(target).kind,display_name:target});
  const recorder=createOutgoingHistory({config,routes,chat,client,report:()=>{}});t.after(()=>recorder.close());
  await recorder.replay();assert.deepEqual(fs.readdirSync(folder),[]);
  const owner=await chat('owner'),group=await chat('group');
  const found=await client.request('search',{chat_id:owner.id,allowed_chat_ids:[owner.id]});
  assert.equal(found.messages.length,1);assert.equal(found.messages[0].delivery_state,'sent');
  assert.equal(found.messages[0].source_id,'one-operation');assert.equal(found.messages[0].timestamp,ref.historyEvent.timestamp);
  assert.equal(found.messages[0].metadata.textStatus,'synthesis-input-not-segment-transcript');
  const isolated=await client.request('search',{chat_id:group.id,allowed_chat_ids:[group.id]});assert.equal(isolated.messages.length,0);
  const counts=await client.request('status');assert.equal(counts.counts.deliveries,1);
  await recorder.replay();assert.equal((await client.request('status')).counts.messages,1);
});

test('invalid route or delivery reference is rejected before journaling',()=>{
  const dataRoot=fs.mkdtempSync(path.join(os.tmpdir(),'whale-outgoing-scope-'));
  const recorder=createOutgoingHistory({config:{dataRoot,accountId:'fixture'},routes:new Map([['owner',{kind:'friend'}]]),
    chat:async()=>({id:'unused'}),client:{request:async()=>{throw Error('unused');}},report:()=>{}});
  assert.throws(()=>recorder.record('foreign',{kind:'text',text:'no'},'op'),/route-invalid/);
  assert.throws(()=>recorder.record('owner',{kind:'text',text:'no'},''),/source-invalid/);
  assert.throws(()=>recorder.delivery({historyEvent:{sourceId:'one'}},'other','sent'),/delivery-invalid/);
  assert.equal(fs.existsSync(path.join(dataRoot,'snapshots/realtime-outgoing')),false);recorder.close();
});

test('shutdown seals attempted send before delayed native callback; history replay never invokes transport',async t=>{
  const cwd=path.dirname(fileURLToPath(import.meta.url)),dataRoot=fs.mkdtempSync(path.join(os.tmpdir(),'outgoing-delayed-close-'));
  const config={dataRoot,accountId:'fixture'},routes=new Map([['group',{kind:'group'}]]);
  const recorder=createOutgoingHistory({config,routes,chat:async()=>({id:'offline-fixture'}),client:{request:async()=>({id:'offline-fixture'})},report:()=>{}});
  const reference=recorder.record('group',{kind:'text',text:'synthetic delayed text'},'delayed-op');recorder.delivery(reference,'delayed-op','attempted');await recorder.idle();
  let finishNative,transportCalls=1;
  const delayedNative=new Promise(resolve=>{finishNative=resolve;});
  const sendCallback=delayedNative.then(()=>recorder.delivery(reference,'delayed-op','sent'));
  assert.deepEqual(recorder.close(),{unknownJournaled:1,unknownFailed:0});
  const folder=path.join(dataRoot,'snapshots/realtime-outgoing');let files=fs.readdirSync(folder);
  assert.equal(files.length,1);assert.equal(JSON.parse(fs.readFileSync(path.join(folder,files[0]))).event.deliveryState,'unknown');
  finishNative();assert.deepEqual(await sendCallback,{journaled:false,closed:true});
  assert.deepEqual(recorder.close(),{unknownJournaled:1,unknownFailed:0});assert.equal(fs.readdirSync(folder).length,1);
  const children=[],client=createVaultProcess({python:(process.env.WHALE_TEST_PYTHON||path.resolve(cwd,'../../.venv-wxauto/Scripts/python.exe')),cwd,dataRoot,spawnWorker:(...args)=>{const c=spawn(...args);children.push(c);return c;}});
  t.after(async()=>{client.close();await Promise.all(children.filter(c=>c.exitCode===null&&c.signalCode===null).map(c=>once(c,'exit')));});
  const chat=()=>client.request('ensure_chat',{account_id:'fixture',chat_key:'group',kind:'group',display_name:'Synthetic group'});
  const restored=createOutgoingHistory({config,routes,chat,client,report:()=>{}});t.after(()=>restored.close());
  await restored.replay();const c=await chat(),found=await client.request('search',{chat_id:c.id,allowed_chat_ids:[c.id]});
  assert.equal(found.messages.length,1);assert.equal(found.messages[0].delivery_state,'unknown');assert.equal(found.messages[0].source_id,'delayed-op');
  assert.equal(transportCalls,1);assert.deepEqual(fs.readdirSync(folder),[]);
});

test('close preserves journaled terminal states and never marks unattempted generated output unknown',async()=>{
  const dataRoot=fs.mkdtempSync(path.join(os.tmpdir(),'outgoing-terminal-close-')),events=[];
  const recorder=createOutgoingHistory({config:{dataRoot,accountId:'fixture'},routes:new Map([['owner',{kind:'friend'}]]),chat:async()=>({id:'stored'}),client:{request:async(op,args)=>{events.push({op,...args});return {id:'stored'};}},report:()=>{}});
  for(const [id,state] of [['success','sent'],['failure','failed'],['uncertain','unknown']]){
    const r=recorder.record('owner',{kind:'text',text:'synthetic'},id);recorder.delivery(r,id,'attempted');recorder.delivery(r,id,state);
  }
  recorder.record('owner',{kind:'text',text:'never attempted'},'generated-only');await recorder.idle();
  assert.deepEqual(recorder.close(),{unknownJournaled:0,unknownFailed:0});
  assert.deepEqual(events.filter(e=>e.op==='record_delivery_observation'&&e.state==='unknown').map(e=>e.operation_id),['uncertain']);
});

test('unknown journal save failure is reported without claiming durable closure or retrying on late callback',async()=>{
  const dataRoot=fs.mkdtempSync(path.join(os.tmpdir(),'outgoing-close-fail-')),reports=[];
  const recorder=createOutgoingHistory({config:{dataRoot,accountId:'fixture'},routes:new Map([['owner',{kind:'friend'}]]),chat:async()=>({id:'stored'}),client:{request:async()=>({id:'stored'})},report:(...args)=>reports.push(args)});
  const ref=recorder.record('owner',{kind:'text',text:'fixture'},'attempt');recorder.delivery(ref,'attempt','attempted');await recorder.idle();
  const rename=fs.renameSync;fs.renameSync=()=>{throw Error('synthetic disk failure');};
  let result;try{result=recorder.close();}finally{fs.renameSync=rename;}
  assert.deepEqual(result,{unknownJournaled:0,unknownFailed:1});assert.deepEqual(recorder.close(),result);
  assert.deepEqual(recorder.delivery(ref,'attempt','unknown'),{journaled:false,closed:true});
  assert.ok(reports.some(([name])=>name==='vault-outgoing-journal-failed'));assert.ok(reports.some(([name,facts])=>name==='vault-outgoing-shutdown'&&facts.unknownFailed===1));
});

test('delayed worker-exit rejection after journal close is handled and cannot lose the shutdown unknown',async()=>{
  const dataRoot=fs.mkdtempSync(path.join(os.tmpdir(),'outgoing-exit-close-'));
  const recorder=createOutgoingHistory({config:{dataRoot,accountId:'fixture'},routes:new Map([['owner',{kind:'friend'}]]),chat:async()=>({id:'stored'}),client:{request:async()=>({id:'stored'})},report:()=>{}});
  const ref=recorder.record('owner',{kind:'text',text:'synthetic'},'worker-exit');recorder.delivery(ref,'worker-exit','attempted');await recorder.idle();
  let rejectTransport;const transport=new Promise((_resolve,reject)=>{rejectTransport=reject;});
  const handled=transport.catch(()=>recorder.delivery(ref,'worker-exit','unknown'));
  assert.deepEqual(recorder.close(),{unknownJournaled:1,unknownFailed:0});rejectTransport(Error('worker-exited'));
  assert.deepEqual(await handled,{journaled:false,closed:true});
  const folder=path.join(dataRoot,'snapshots/realtime-outgoing'),files=fs.readdirSync(folder);
  assert.equal(files.length,1);assert.equal(JSON.parse(fs.readFileSync(path.join(folder,files[0]))).event.deliveryState,'unknown');
});
