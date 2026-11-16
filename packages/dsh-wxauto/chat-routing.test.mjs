import test from 'node:test';
import assert from 'node:assert/strict';
import {groupSessionId, createChatQueue,claimRecoveryInbox} from './chat-routing.mjs';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {MemoryStore} from '../dsh-memory-evolve/lib/store.js';

test('a verified pending inbox is claimed once and cannot target another chat',()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'whale-inbox-'));
  try{
    const file=path.join(dir,'inbox.json'),routes=new Map([['A',{kind:'group'}]]);
    const data={version:1,state:'pending',messages:[{id:'recovery-one',target:'A',kind:'group',sender:'member',text:'English please'}]};
    fs.writeFileSync(file,JSON.stringify(data));
    assert.equal(claimRecoveryInbox(file,routes).length,1);
    assert.deepEqual(claimRecoveryInbox(file,routes),[]);
    fs.writeFileSync(file,JSON.stringify({...data,messages:[{...data.messages[0],target:'B'}]}));
    assert.throws(()=>claimRecoveryInbox(file,routes),/invalid-recovery-message/);
  }finally{fs.rmSync(dir,{recursive:true,force:true});}
});

test('native owner authority holds old UI recovery without consuming or replaying it',t=>{
  const dir=fs.mkdtempSync(path.resolve('artifacts/native-owner-recovery-'));
  t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
  const file=path.join(dir,'inbox.json'),routes=new Map([['owner',{kind:'friend'}]]);
  const data={version:1,state:'pending',messages:[{id:'recovery-old',target:'owner',kind:'friend',sender:'Peer',text:'synthetic old UI recovery'}]};
  fs.writeFileSync(file,JSON.stringify(data));const before=fs.readFileSync(file);
  assert.throws(()=>claimRecoveryInbox(file,routes,{denyOwnerTextTarget:'owner'}),/native-owner-text-recovery-held/);
  assert.equal(fs.readFileSync(file).equals(before),true);
  assert.equal(claimRecoveryInbox(file,routes).length,1); // Legacy UI mode remains unchanged.
});

test('the existing Memory Evolve shares persisted memory between group session IDs in one workspace', () => {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'whale-memory-'));
  try {
    const store=new MemoryStore(dir);
    const a={session:{id:groupSessionId('A'),header:{cwd:dir}}};
    const b={session:{id:groupSessionId('B'),header:{cwd:dir}}};
    for(const track of ['memory','user','key','project']) {
      assert.equal(store.add(track,'shared fact '+track,a).ok,true);
      assert.match(store.entriesOf(track,b).join('\n'),/shared fact/);
    }
  } finally { fs.rmSync(dir,{recursive:true,force:true}); }
});

test('a slow group does not block another group, while its own messages stay ordered', async () => {
  const enqueue = createChatQueue(), events = [];
  let finish;
  const slow = enqueue('A', async () => { events.push('A1'); await new Promise(r=>finish=r); });
  const second = enqueue('A', async () => events.push('A2'));
  await enqueue('B', async () => events.push('B1'));
  assert.deepEqual(events, ['A1','B1']);
  finish(); await Promise.all([slow,second]);
  assert.deepEqual(events, ['A1','B1','A2']);
  await assert.rejects(enqueue('A', ()=>{throw Error('failed')}));
  await enqueue('A', async()=>events.push('A3'));
  assert.equal(events.at(-1),'A3');
});

test('group identities survive restart and cannot collide with the owner session', () => {
  assert.equal(groupSessionId('甲'),groupSessionId('甲'));
  assert.notEqual(groupSessionId('甲'),groupSessionId('乙'));
  assert.match(groupSessionId('甲'),/^whale-wx-group-[a-f0-9]{24}$/);
});

test('pause holds queued/new owner work without blocking other groups or cancelling running work',async()=>{
  const queue=createChatQueue(),events=[];let finish;
  const running=queue('Owner',async()=>{events.push('running');await new Promise(r=>finish=r);});
  await Promise.resolve();assert.deepEqual(queue.status('Owner'),{pending:1,active:true,paused:false});
  const release=queue.pause('Owner'),pending=queue('Owner',async()=>events.push('queued'));
  assert.deepEqual(queue.status('Owner'),{pending:2,active:true,paused:true});
  await queue('Group',async()=>events.push('group'));finish();await running;
  assert.deepEqual(queue.status('Owner'),{pending:1,active:false,paused:true});
  assert.deepEqual(events,['running','group']);release();release();await pending;
  assert.deepEqual(events,['running','group','queued']);assert.deepEqual(queue.status('Owner'),{pending:0,active:false,paused:false});
});

test('quiet pause detects new background work; duplicate pause releases only its own hold',async()=>{
  const queue=createChatQueue(),events=[];
  assert.deepEqual(queue.status('Owner'),{pending:0,active:false,paused:false});
  const first=queue.pause('Owner'),second=queue.pause('Owner');
  const one=queue('Owner',()=>events.push(1)),two=queue('Owner',()=>events.push(2));
  first();first();await Promise.resolve();assert.deepEqual(events,[]);assert.equal(queue.status('Owner').pending,2);
  second();await Promise.all([one,two]);assert.deepEqual(events,[1,2]);
  await queue.idle('Owner');assert.equal(queue.status('Owner').paused,false);
});

test('re-pause before queued continuation resumes preserves barrier and release is scoped',async()=>{
  const queue=createChatQueue(),events=[],a=queue.pause('A'),b=queue.pause('B');
  const work=queue('A',()=>events.push('A'));await Promise.resolve();a();const next=queue.pause('A');b();
  await Promise.resolve();assert.deepEqual(events,[]);assert.equal(queue.status('A').paused,true);
  next();await work;assert.deepEqual(events,['A']);
});

