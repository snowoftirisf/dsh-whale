import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {resolveScope,entryIdentity,canReadMemory,visibleEntries,boundedIndex,readMemoryRef,validateSourceRefs,createMemoryMetadataService} from './memory-view.mjs';

const sessions=new Map([['owner',{kind:'private',chatId:'owner-chat'}],['group-a',{kind:'chat',chatId:'stable-a'}],['group-b',{kind:'chat',chatId:'stable-b'}]]);
const owner=resolveScope('owner',sessions), a=resolveScope('group-a',sessions), b=resolveScope('group-b',sessions);
const entry=(content,target='memory')=>({target,content,...(['key','project'].includes(target)?{projectKey:'whale-project'}:{})});
function fixture(t,options={}) {
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'whale-memory-view-'));
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const file=path.join(directory,'provenance.json');
  return {file,service:createMemoryMetadataService({file,...options})};
}

test('only trusted exact session mappings establish scope; renaming display name cannot change it',()=>{
  assert.equal(resolveScope('unknown',sessions),null);
  assert.equal(resolveScope('owner',{owner:{kind:'private',chatId:'owner-chat'}}),null);
  const renamed=new Map(sessions);renamed.set('group-a',{kind:'chat',chatId:'stable-a',name:'renamed'});
  assert.deepEqual(resolveScope('group-a',renamed),a);
});

test('legacy entries and edited content default to private; A and B do not see each other',async t=>{
  const {service}=fixture(t);const legacy=entry('old private fact'),ea=entry('A fact'),eb=entry('B fact');
  await service.record({entry:ea,scope:a});await service.record({entry:eb,scope:b});
  const state=service.read();
  assert.deepEqual(visibleEntries([legacy,ea,eb],state,a),[ea]);
  assert.deepEqual(visibleEntries([legacy,ea,eb],state,b),[eb]);
  assert.deepEqual(visibleEntries([legacy,ea,eb],state,owner),[legacy,ea,eb]);
  assert.equal(canReadMemory({...ea,content:'external edit'},state,a),false);
  assert.equal(canReadMemory(ea,state,null),false);
});

test('same text with different tracks and project identities does not collide',()=>{
  assert.notEqual(entryIdentity(entry('same','memory')).ref,entryIdentity(entry('same','user')).ref);
  assert.notEqual(entryIdentity(entry('same','key')).ref,entryIdentity({...entry('same','key'),projectKey:'another'}).ref);
  assert.throws(()=>entryIdentity({target:'project',content:'text'}),/project-key/);
});

test('stable entry IDs cannot reuse metadata after an external body edit',async t=>{
  const {service}=fixture(t);const e=entry('[id:12345678] old');await service.record({entry:e,scope:a});
  const changed=entry('[id:12345678] new');
  assert.equal(entryIdentity(e).ref,entryIdentity(changed).ref);
  assert.equal(canReadMemory(changed,service.read(),a),false);
});

test('only an explicit owner action promotes shared scope; groups cannot overwrite it',async t=>{
  const {service}=fixture(t);const e=entry('public preference');await service.record({entry:e,scope:owner});
  await assert.rejects(service.promoteShared({entry:e,scope:a,explicitOwnerRequest:true}),/promotion-denied/);
  await assert.rejects(service.promoteShared({entry:e,scope:owner}),/promotion-denied/);
  await service.promoteShared({entry:e,scope:owner,explicitOwnerRequest:true});
  assert.equal(canReadMemory(e,service.read(),a),true);assert.equal(canReadMemory(e,service.read(),b),true);
  await assert.rejects(service.record({entry:e,scope:a}),/record-denied/);
});

test('bounded index provides usable refs and omits daily/project prose',async t=>{
  const {service}=fixture(t);const es=[entry('short preference'),entry('FULL DAILY PRIVATE','daily'),entry('FULL PROJECT PRIVATE','project')];
  const view=boundedIndex(es,service.read(),owner,{maxChars:250});
  assert.ok(view.text.length<=250);assert.equal(view.refs.length,1);assert.equal(view.omitted,2);
  assert.ok(!view.text.includes('FULL DAILY'));assert.ok(!view.text.includes('FULL PROJECT'));
  assert.equal(readMemoryRef(view.refs[0],es,service.read(),owner).content,'short preference');
  assert.throws(()=>readMemoryRef(view.refs[0],es,service.read(),a),/unavailable/);
  assert.equal(boundedIndex(es,service.read(),owner,{maxChars:0}).text,'');
  assert.throws(()=>boundedIndex(es,service.read(),owner,{maxChars:-1}),/budget/);
});

test('bounded index keeps the profile and the newest memories when the budget is tight',async t=>{
  const {service}=fixture(t);
  const es=[entry('OLDEST memory'),entry('middle memory'),entry('NEWEST memory'),entry('owner profile','user')];
  const view=boundedIndex(es,service.read(),owner,{maxChars:10000});
  const at=text=>view.text.indexOf(text);
  assert.ok(at('owner profile')<at('NEWEST memory')&&at('NEWEST memory')<at('middle memory')&&at('middle memory')<at('OLDEST memory'));
  const tight=boundedIndex(es,service.read(),owner,{maxChars:view.text.length-1});
  assert.ok(tight.text.includes('owner profile')&&tight.text.includes('NEWEST memory')&&!tight.text.includes('OLDEST memory'));
});

test('source references must come from the current authorized chat, not model-supplied paths',()=>{
  const messages=new Map([['a1',{chatId:'stable-a'}],['b1',{chatId:'stable-b'}],['gone',{chatId:'stable-a',deleted:true}]]);
  assert.deepEqual(validateSourceRefs(['a1','a1'],a,messages),[{messageId:'a1',chatId:'stable-a'}]);
  for(const id of ['b1','gone','C:/private.txt'])assert.throws(()=>validateSourceRefs([id],a,messages),/source/);
});

test('replacement archives before applying, preserves visibility/source refs and verifies readback',async t=>{
  const {service}=fixture(t);const before=entry('[id:12345678] before'),after=entry('[id:12345678] after');
  await service.record({entry:before,scope:a,sources:[{messageId:'a1',chatId:'stable-a'}]});
  const result=await service.archiveAndReplace({before,after,scope:a,apply:()=>{
    const state=service.read();assert.equal(state.archives.at(-1).entry.content,before.content);return after.content;
  }});
  assert.equal(result.visibility.chatId,'stable-a');assert.equal(result.sources[0].messageId,'a1');
  assert.equal(result.supersedes.digest,entryIdentity(before).digest);
  assert.equal(canReadMemory(after,service.read(),a),true);
  assert.equal(canReadMemory(before,service.read(),a),false);
  await assert.rejects(service.archiveAndReplace({before:after,after:entry('next'),scope:b,apply:()=>{throw Error('should not run')}}),/replace-denied/);
});

test('failed archive never calls writer; failed writer retains durable old content',async t=>{
  const f=fixture(t);const before=entry('before'),after=entry('after');let called=false;
  const broken=createMemoryMetadataService({file:f.file,writeAtomic:()=>{throw Error('disk-full')}});
  await assert.rejects(broken.archiveAndReplace({before,after,scope:owner,apply:()=>{called=true;return after.content;}}),/disk-full/);
  assert.equal(called,false);
  await f.service.record({entry:before,scope:a});
  await assert.rejects(f.service.archiveAndReplace({before,after,scope:a,apply:()=>{throw Error('writer-failed')}}),/writer-failed/);
  assert.equal(f.service.read().archives.at(-1).entry.content,'before');
  assert.equal(canReadMemory(before,f.service.read(),a),true);
});

test('unexpected write result does not publish metadata for changed content',async t=>{
  const {service}=fixture(t);const before=entry('before'),after=entry('after');await service.record({entry:before,scope:a});
  await assert.rejects(service.archiveAndReplace({before,after,scope:a,apply:()=>undefined}),/readback-mismatch/);
  assert.equal(canReadMemory(after,service.read(),a),false);
});

test('separate service instances serialize updates and leave no temporary/lock files',async t=>{
  const {service,file}=fixture(t),other=createMemoryMetadataService({file});
  await Promise.all([service.record({entry:entry('first'),scope:a}),other.record({entry:entry('second'),scope:b})]);
  assert.equal(Object.keys(service.read().entries).length,2);
  assert.deepEqual(fs.readdirSync(path.dirname(file)),['provenance.json']);
});

test('moving the data root preserves portable project references and classification',async t=>{
  const {service,file}=fixture(t);const e=entry('project convention','key');
  await service.record({entry:e,scope:a});const ref=entryIdentity(e).ref;
  const moved=path.join(path.dirname(file),'moved','provenance.json');fs.mkdirSync(path.dirname(moved));fs.copyFileSync(file,moved);
  const restored=createMemoryMetadataService({file:moved});
  assert.equal(readMemoryRef(ref,[e],restored.read(),a).content,e.content);
  assert.equal(canReadMemory(e,restored.read(),b),false);
});

test('metadata corruption fails loudly, and a stale lock is not deleted to force access',async t=>{
  const {service,file}=fixture(t,{lockTimeoutMs:30});fs.writeFileSync(file,'not-json');assert.throws(()=>service.read());
  fs.unlinkSync(file);fs.writeFileSync(file+'.lock','existing owner');
  await assert.rejects(service.record({entry:entry('new'),scope:a}),/busy/);
  assert.equal(fs.readFileSync(file+'.lock','utf8'),'existing owner');
});
