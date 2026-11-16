import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {MemoryStore,ArchiveStore} from '../lib/store.js';
import {memoryTool,resolveConfig} from '../lib/index.js';
import {createMemoryMetadataService,entryIdentity} from '../../dsh-wxauto/memory-view.mjs';

function fixture(t){
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'memory-journal-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const workspace=path.join(root,'workspace');fs.mkdirSync(workspace);
  const file=path.join(root,'metadata','provenance.json');let source={chatId:'chat-a',messageIds:['synthetic-first-source'],sourceKind:'wxauto-incoming'};
  const make=()=>{
    const policy={workspace,ownerSessionId:'owner',ownerChatId:'private',groups:[{sessionId:'a',chatId:'chat-a'},{sessionId:'b',chatId:'chat-b'}],metadataFile:file};
    const config=resolveConfig({memoryDir:path.join(root,'memory'),channelMemoryPolicy:policy,reviewMode:'auto',keyBranchFilter:false});
    const raw=new MemoryStore(config.memoryDir),archive=new ArchiveStore(config.memoryDir);
    const tool=memoryTool({get:()=>undefined},config,raw,{},()=>config,archive);
    const call=(id,args)=>tool.execute(args,{agent:{session:{id,header:{cwd:workspace}},ctx:{get:name=>name==='whaleHistory'?{currentSource:()=>source}:undefined}}});
    return {raw,call,tool,policy};
  };
  const read=()=>createMemoryMetadataService({file}).read();
  async function failRename(n,work){
    const original=fs.renameSync;let count=0;
    fs.renameSync=(from,to,...args)=>{if(path.resolve(to)===file&&++count===n)throw Error('synthetic-sidecar-rename-failure');return original(from,to,...args);};
    try{return await work();}finally{fs.renameSync=original;}
  }
  return {root,file,make,read,failRename,setSource:value=>source=value};
}

test('failed add prepare cannot write Markdown; retry never classifies an arbitrary legacy duplicate',async t=>{
  const f=fixture(t),a=f.make();
  assert.equal((await f.failRename(1,()=>a.call('a',{action:'add',target:'memory',content:'authorized fresh'}))).ok,false);
  assert.equal(a.raw.entriesOf('memory').length,0);assert.equal(Object.keys(f.read().pendingMutations||{}).length,0);
  a.raw.add('memory','legacy private duplicate');
  assert.equal((await a.call('a',{action:'add',target:'memory',content:'legacy private duplicate'})).ok,true);
  assert.equal(Object.keys(f.read().entries).length,0);
  assert.equal((await a.call('a',{action:'list',target:'memory'})).entries.length,0);
});

test('post-Markdown add failure recovers after restart without changing raw bytes or adopting new input sources',async t=>{
  const f=fixture(t),a=f.make();
  assert.equal((await f.failRename(2,()=>a.call('a',{action:'add',target:'memory',content:'recover this exact add'}))).ok,false);
  const raw=a.raw.readRaw('memory').text,pending=Object.values(f.read().pendingMutations);assert.equal(pending.length,1);
  assert.equal(Object.keys(f.read().entries).length,0);
  f.setSource({chatId:'chat-a',messageIds:['synthetic-second-source'],sourceKind:'wechat-native-unread'});
  const restarted=f.make(),result=await restarted.call('a',{action:'list',target:'memory'});
  assert.equal(result.entries.length,1);assert.equal(restarted.raw.readRaw('memory').text,raw);
  assert.equal(Object.keys(f.read().pendingMutations).length,0);
  const meta=Object.values(f.read().entries)[0];assert.deepEqual(meta.visibility,{kind:'chat',chatId:'chat-a'});
  assert.deepEqual(meta.sources,[{messageId:'synthetic-first-source',chatId:'chat-a',sourceKind:'wxauto-incoming'}]);
  assert.equal((await restarted.call('b',{action:'list',target:'memory'})).entries.length,0);
  await restarted.call('a',{action:'add',target:'memory',content:'recover this exact add'});
  assert.equal(restarted.raw.entriesOf('memory').length,1);assert.equal(Object.keys(f.read().entries).length,1);
});

test('pending other scope cannot be recovered, overwritten, removed or archived by another group',async t=>{
  const f=fixture(t),a=f.make();
  await f.failRename(2,()=>a.call('a',{action:'add',target:'memory',content:'pending group a'}));
  const bytes=fs.readFileSync(f.file),raw=a.raw.readRaw('memory').text;
  f.setSource({chatId:'chat-b',messageIds:['synthetic-b'],sourceKind:'wxauto-incoming'});
  assert.equal((await a.call('b',{action:'list',target:'memory'})).entries.length,0);
  for(const args of [{action:'add',content:'group b new'},{action:'replace',match:'pending group a',content:'stolen'},
      {action:'remove',match:'pending group a'},{action:'archive',match:'pending group a'}]){
    assert.equal((await a.call('b',{target:'memory',...args})).ok,false);
  }
  assert.equal(a.raw.readRaw('memory').text,raw);assert.deepEqual(fs.readFileSync(f.file),bytes);
});

test('replace keeps old archive and restores the exact new metadata/source after final rename failure',async t=>{
  const f=fixture(t),a=f.make();
  await a.call('a',{action:'add',target:'memory',content:'old approved content'});
  const old=a.raw.entriesOf('memory')[0],oldId=entryIdentity({target:'memory',content:old});
  f.setSource({chatId:'chat-a',messageIds:['synthetic-replacement-source'],sourceKind:'wechat-native-voice'});
  assert.equal((await f.failRename(2,()=>a.call('a',{action:'replace',target:'memory',match:'old approved',content:'new approved content'}))).ok,false);
  const newRaw=a.raw.readRaw('memory').text;assert.match(newRaw,/new approved/);
  assert.equal(f.read().archives.at(-1).entry.content,old);assert.equal(f.read().entries[oldId.ref].digest,oldId.digest);
  const restarted=f.make();assert.equal((await restarted.call('a',{action:'list',target:'memory'})).entries.length,1);
  assert.equal(restarted.raw.readRaw('memory').text,newRaw);assert.equal(f.read().archives.length,1);
  const next=Object.values(f.read().entries)[0];assert.equal(next.supersedes.digest,oldId.digest);
  assert.deepEqual(next.sources,[{messageId:'synthetic-replacement-source',chatId:'chat-a',sourceKind:'wechat-native-voice'}]);
  assert.equal((await restarted.call('a',{action:'archive',target:'memory',match:'new approved'})).ok,true);
  assert.equal((await restarted.call('a',{action:'list',target:'memory',archived:true})).entries.length,1);
});

test('raw writer failure is recognized as not-applied; recovery does not invent or retry the content',async t=>{
  const f=fixture(t),a=f.make(),write=a.raw.write;
  a.raw.write=()=>{throw Error('synthetic-raw-write-failure');};
  try{assert.equal((await a.call('a',{action:'add',target:'memory',content:'never applied'})).ok,false);}finally{a.raw.write=write;}
  assert.equal(Object.keys(f.read().pendingMutations).length,1);assert.equal(a.raw.entriesOf('memory').length,0);
  const restarted=f.make();assert.equal((await restarted.call('a',{action:'list',target:'memory'})).entries.length,0);
  assert.equal(Object.keys(f.read().pendingMutations).length,0);assert.equal(Object.keys(f.read().entries).length,0);
});

test('conflicting post-failure track stays pending and never blindly rewrites or promotes it',async t=>{
  const f=fixture(t),a=f.make();await f.failRename(2,()=>a.call('a',{action:'add',target:'memory',content:'authorized pending'}));
  a.raw.add('memory','synthetic manual external drift');const raw=a.raw.readRaw('memory').text,metadata=fs.readFileSync(f.file);
  const result=await f.make().call('a',{action:'list',target:'memory'});
  assert.equal(result.ok,false);assert.equal(a.raw.readRaw('memory').text,raw);assert.deepEqual(fs.readFileSync(f.file),metadata);
});

test('recovery rename failure leaves pending durable and can succeed on the next call',async t=>{
  const f=fixture(t),a=f.make();await f.failRename(2,()=>a.call('a',{action:'add',target:'memory',content:'recover twice'}));
  const raw=a.raw.readRaw('memory').text;
  assert.equal((await f.failRename(1,()=>f.make().call('a',{action:'list',target:'memory'}))).ok,false);
  assert.equal(Object.keys(f.read().pendingMutations).length,1);
  assert.equal((await f.make().call('a',{action:'list',target:'memory'})).entries.length,1);assert.equal(a.raw.readRaw('memory').text,raw);
});

test('concurrent new calls after a failed add yield one recovered entry and one original source binding',async t=>{
  const f=fixture(t),a=f.make();await f.failRename(2,()=>a.call('a',{action:'add',target:'memory',content:'concurrent recovery'}));
  const one=f.make(),two=f.make();const values=await Promise.all([one.call('a',{action:'add',target:'memory',content:'concurrent recovery'}),
    two.call('a',{action:'add',target:'memory',content:'concurrent recovery'})]);
  assert.ok(values.every(v=>v.ok));assert.equal(one.raw.entriesOf('memory').length,1);
  assert.equal(Object.keys(f.read().entries).length,1);assert.equal(Object.keys(f.read().pendingMutations).length,0);
});

test('owner can edit a group fact without recasting its old sources or shared visibility',async t=>{
  const f=fixture(t),a=f.make();await a.call('a',{action:'add',target:'memory',content:'group fact to edit'});f.setSource(null);
  assert.equal((await a.call('owner',{action:'replace',target:'memory',match:'group fact',content:'owner corrected group fact'})).ok,true);
  const meta=Object.values(f.read().entries)[0];assert.deepEqual(meta.visibility,{kind:'chat',chatId:'chat-a'});
  assert.equal(meta.sources[0].chatId,'chat-a');assert.equal((await a.call('b',{action:'list',target:'memory'})).entries.length,0);
});

test('stable entry ID replacement recovers the new digest without overwriting another version',async t=>{
  const f=fixture(t),a=f.make(),old='[id:abcd1234] [2026-11-02] stable identity old body';
  a.raw.entryIdMode='on';fs.mkdirSync(a.raw.dir,{recursive:true});a.raw.write('memory',[old]);
  await createMemoryMetadataService({file:f.file}).record({entry:{target:'memory',content:old},scope:{kind:'chat',chatId:'chat-a',sessionId:'a'},sources:[]});
  await f.failRename(2,()=>a.call('a',{action:'replace',target:'memory',match:'stable identity old',content:'stable identity new body'}));
  const pending=Object.values(f.read().pendingMutations)[0];
  assert.equal(pending.afterMetadata.ref,entryIdentity({target:'memory',content:old}).ref);
  assert.notEqual(pending.afterMetadata.digest,entryIdentity({target:'memory',content:old}).digest);
  const result=await f.make().call('a',{action:'list',target:'memory'});assert.match(result.entries[0],/new body/);
  assert.equal(Object.values(f.read().entries)[0].digest,pending.afterMetadata.digest);
});

test('failed replacement raw writer leaves the old content and metadata, then clears only its unapplied intent',async t=>{
  const f=fixture(t),a=f.make();await a.call('a',{action:'add',target:'memory',content:'old remains on disk'});
  const before=a.raw.readRaw('memory').text,meta=Object.values(f.read().entries)[0],write=a.raw.write;
  a.raw.write=()=>{throw Error('synthetic-replace-raw-failure');};
  try{assert.equal((await a.call('a',{action:'replace',target:'memory',match:'old remains',content:'must not appear'})).ok,false);}finally{a.raw.write=write;}
  assert.equal(a.raw.readRaw('memory').text,before);assert.equal(f.read().archives.at(-1).entry.content,a.raw.entriesOf('memory')[0]);
  const result=await f.make().call('a',{action:'list',target:'memory'});
  assert.match(result.entries[0],/old remains/);assert.deepEqual(Object.values(f.read().entries)[0],meta);
  assert.equal(Object.keys(f.read().pendingMutations).length,0);
});

test('stale metadata locks are not stolen; abrupt process death still requires verified lock recovery',async t=>{
  const f=fixture(t),a=f.make();await f.failRename(2,()=>a.call('a',{action:'add',target:'memory',content:'pending behind stale lock'}));
  fs.writeFileSync(f.file+'.lock','synthetic unverified stale owner');const before=fs.readFileSync(f.file);
  const service=createMemoryMetadataService({file:f.file,lockTimeoutMs:1});
  await assert.rejects(service.recoverMutations({scope:{kind:'chat',chatId:'chat-a',sessionId:'a'},readEntries:()=>a.raw.entriesOf('memory')}),/metadata-busy/);
  assert.ok(fs.existsSync(f.file+'.lock'));assert.deepEqual(fs.readFileSync(f.file),before);
});

test('revoked group session cannot complete a previously authorized intent until its original scope is trusted again',async t=>{
  const f=fixture(t),a=f.make();await f.failRename(2,()=>a.call('a',{action:'add',target:'memory',content:'await original trusted scope'}));
  const before=fs.readFileSync(f.file);a.policy.groups=a.policy.groups.filter(group=>group.sessionId!=='a');
  assert.equal((await a.call('a',{action:'list',target:'memory'})).entries.length,0);
  assert.equal((await a.call('a',{action:'add',target:'memory',content:'cannot grant myself access'})).ok,false);
  assert.deepEqual(fs.readFileSync(f.file),before);
  assert.equal((await f.make().call('a',{action:'list',target:'memory'})).entries.length,1);
});
