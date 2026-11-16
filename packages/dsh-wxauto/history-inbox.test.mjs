import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHistoryInbox} from './history-inbox.mjs';

function fixture(write,overrides={}) {
  const dataRoot=fs.mkdtempSync(path.join(os.tmpdir(),'whale-inbox-'));
  const config={dataRoot,accountId:'test-account'};
  const routes=new Map([['owner',{kind:'friend'}],['group',{kind:'group'}]]);
  const opts={config,routes,write,...overrides};
  return {config,routes,opts,inbox:createHistoryInbox(opts),folder:path.join(dataRoot,'snapshots/realtime-inbox')};
}
const message={id:'source-1',kind:'friend',target:'owner',sender:'owner',text:'a received message'};

test('failed ingestion survives restart; replay writes history only, with original observed time',async()=>{
  const f=fixture(async()=>{throw Error('writer offline');});
  await assert.rejects(f.inbox.enqueue(message),/writer offline/);
  const files=fs.readdirSync(f.folder);assert.equal(files.length,1);
  const saved=JSON.parse(fs.readFileSync(path.join(f.folder,files[0]),'utf8'));
  f.inbox.close();const received=[];
  const reopened=createHistoryInbox({...f.opts,write:async event=>{received.push(event);return {id:'stored-1'};}});
  await reopened.replay();
  assert.equal(received.length,1);assert.equal(received[0].id,message.id);
  assert.equal(received[0].observedTimestamp,saved.observedAt);
  assert.equal(received[0].timestamp,undefined);assert.deepEqual(fs.readdirSync(f.folder),[]);
  reopened.close();
});

test('uncertain commit retry preserves source ID for sole-writer idempotency',async()=>{
  const records=new Map();let calls=0;
  const f=fixture(async event=>{
    records.set(event.id,event.text);calls++;
    if(calls===1)throw Error('commit acknowledgement lost');
    return {id:event.id,inserted:false};
  });
  await assert.rejects(f.inbox.enqueue(message));await f.inbox.replay();
  assert.equal(records.size,1);assert.equal(calls,2);assert.deepEqual(fs.readdirSync(f.folder),[]);f.inbox.close();
});

test('media paths rebase on restored root instead of reading old machine paths',async()=>{
  const f=fixture(async()=>{throw Error('offline');});
  const media=path.join(f.config.dataRoot,'media','image.png');
  fs.mkdirSync(path.dirname(media));fs.writeFileSync(media,'fixture');
  await assert.rejects(f.inbox.enqueue({...message,images:[{path:media,status:'downloaded'}]}));f.inbox.close();
  const restored=fs.mkdtempSync(path.join(os.tmpdir(),'whale-inbox-restored-'));
  fs.cpSync(f.config.dataRoot,restored,{recursive:true});let actual;
  const inbox=createHistoryInbox({...f.opts,config:{...f.config,dataRoot:restored},write:async event=>{actual=event;return {id:'restored'};}});
  await inbox.replay();assert.equal(actual.images[0].path,path.join(restored,'media','image.png'));
  assert.equal(actual.images[0].journalRelativePath,undefined);inbox.close();
});

test('native voice audio descriptors rebase with their roles and hashes without turning into image or file input',async()=>{
  const f=fixture(async()=>{throw Error('offline');});
  const audio=path.join(f.config.dataRoot,'voice','incoming-native','source.silk');
  fs.mkdirSync(path.dirname(audio),{recursive:true});fs.writeFileSync(audio,'synthetic silk fixture');
  await assert.rejects(f.inbox.enqueue({...message,sourceKind:'wechat-native-voice',audio:[{path:audio,role:'source',sha256:'a'.repeat(64)}]}));
  f.inbox.close();const restored=fs.mkdtempSync(path.join(os.tmpdir(),'whale-voice-inbox-restored-'));
  fs.cpSync(f.config.dataRoot,restored,{recursive:true});let actual;
  const inbox=createHistoryInbox({...f.opts,config:{...f.config,dataRoot:restored},write:async event=>{actual=event;return {id:'raw',messageIds:['raw','asr']};}});
  await inbox.replay();assert.equal(actual.audio[0].path,path.join(restored,'voice','incoming-native','source.silk'));
  assert.equal(actual.audio[0].role,'source');assert.equal(actual.audio[0].sha256,'a'.repeat(64));
  assert.deepEqual(actual.images,[]);assert.deepEqual(actual.files,[]);inbox.close();
});

test('source conflicts and foreign account/route records remain unconsumed',async()=>{
  const f=fixture(async()=>{throw Error('offline');});
  await assert.rejects(f.inbox.enqueue(message));
  assert.throws(()=>f.inbox.enqueue({...message,text:'changed text'}),/source-conflict/);
  f.inbox.close();let calls=0;
  const foreign=createHistoryInbox({...f.opts,config:{...f.config,accountId:'other-account'},write:async()=>{calls++;return {id:'bad'};}});
  await foreign.replay();assert.equal(calls,0);assert.equal(fs.readdirSync(f.folder).length,1);foreign.close();
  const removed=createHistoryInbox({...f.opts,routes:new Map(),write:async()=>{calls++;return {id:'bad'};}});
  await removed.replay();assert.equal(calls,0);assert.equal(fs.readdirSync(f.folder).length,1);removed.close();
});

test('concurrent same-event calls share one write and keep unrelated channels separate',async()=>{
  const calls=[];let release;
  const gate=new Promise(resolve=>{release=resolve;});
  const f=fixture(async event=>{calls.push(event.target);await gate;return {id:event.target};});
  const a=f.inbox.enqueue(message),b=f.inbox.enqueue(message);
  const c=f.inbox.enqueue({...message,target:'group',kind:'group'});
  release();await Promise.all([a,b,c]);assert.deepEqual(calls,['owner','group']);f.inbox.close();
});

test('attachment failure retains whole event until all required indexing succeeds',async()=>{
  let ready=false,calls=0;
  const f=fixture(async()=>{calls++;if(!ready)throw Error('attachment-index-failed');return {id:'stored'};});
  await assert.rejects(f.inbox.enqueue(message));assert.equal(fs.readdirSync(f.folder).length,1);
  ready=true;await f.inbox.replay();assert.equal(calls,2);assert.deepEqual(fs.readdirSync(f.folder),[]);f.inbox.close();
});
