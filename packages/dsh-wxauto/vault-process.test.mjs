import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter,once} from 'node:events';
import {PassThrough} from 'node:stream';
import {spawn} from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createVaultProcess} from './vault-process.mjs';
import {createHistoryInbox} from './history-inbox.mjs';

function fixture(options={}) {
  const children=[];let time=0;
  const client=createVaultProcess({python:'fixture-python',cwd:'fixture',dataRoot:'fixture',now:()=>time,
    spawnWorker:()=>{
      const child=new EventEmitter();child.pid=100+children.length;
      child.stdin=new PassThrough();child.stdout=new PassThrough();child.stderr=new PassThrough();child.requests=[];
      child.stdin.on('data',line=>child.requests.push(JSON.parse(line)));
      child.answer=(index,value)=>child.stdout.write(JSON.stringify({id:child.requests[index].id,ok:true,value})+'\n');
      children.push(child);return child;
    },...options});
  return {client,children,advance:()=>{time+=5001;}};
}

test('confirmed exit rejects uncertain work and only later calls start one replacement',async()=>{
  const f=fixture(),first=f.children[0];
  const pending=f.client.request('record_message',{source_id:'original'});
  const rejected=assert.rejects(pending,/outcome-unknown/);first.emit('exit',1);await rejected;
  await assert.rejects(f.client.request('status'),/backoff/);assert.equal(f.children.length,1);
  f.advance();const a=f.client.request('status'),b=f.client.request('search');
  assert.equal(f.children.length,2);assert.deepEqual(f.children[1].requests.map(r=>r.op),['status','search']);
  f.children[1].answer(1,'b');f.children[1].answer(0,'a');assert.deepEqual(await Promise.all([a,b]),['a','b']);
  f.client.close();
});

test('timeout does not terminate or replace the live writer',async()=>{
  const f=fixture({timeoutMs:15});
  await assert.rejects(f.client.request('record_message'),/timeout-outcome-unknown/);
  f.advance();const next=f.client.request('status');assert.equal(f.children.length,1);
  f.children[0].answer(0,'late ignored');f.children[0].answer(1,'current');
  assert.equal(await next,'current');f.client.close();
});

test('live process or pipe error cannot authorize replacement before exit',async()=>{
  for(const pipe of [false,true]){
    const f=fixture();
    if(pipe)f.children[0].stdin.emit('error',Error('pipe'));else f.children[0].emit('error',Error('process error'));
    f.advance();await assert.rejects(f.client.request('status'),/unavailable/);assert.equal(f.children.length,1);
    f.children[0].emit('exit',1);f.advance();const next=f.client.request('status');
    assert.equal(f.children.length,2);f.children[1].answer(0,'restored');assert.equal(await next,'restored');f.client.close();
  }
});

test('failed spawn without PID can recover after bounded backoff',async()=>{
  const f=fixture();f.children[0].pid=undefined;f.children[0].emit('error',Error('ENOENT'));
  await assert.rejects(f.client.request('status'),/backoff/);
  f.advance();const next=f.client.request('status');assert.equal(f.children.length,2);
  f.children[1].answer(0,true);assert.equal(await next,true);f.client.close();
});

test('explicit close rejects pending calls, stops once and never restarts',async()=>{
  const f=fixture(),pending=f.client.request('record_message');
  const rejected=assert.rejects(pending,/closed-outcome-unknown/);f.client.close();f.client.close();await rejected;
  f.children[0].emit('exit',0);f.advance();await assert.rejects(f.client.request('status'),/client-closed/);
  assert.equal(f.children.length,1);assert.equal(f.children[0].requests.filter(r=>r.op==='stop').length,1);
});

test('real isolated Python writer restarts after its confirmed exit and retains history',async t=>{
  const cwd=path.dirname(fileURLToPath(import.meta.url));
  const dataRoot=fs.mkdtempSync(path.join(os.tmpdir(),'whale-writer-recovery-'));
  const children=[];
  const client=createVaultProcess({python:(process.env.WHALE_TEST_PYTHON||path.resolve(cwd,'../../.venv-wxauto/Scripts/python.exe')),cwd,dataRoot,restartDelayMs:0,
    spawnWorker:(...args)=>{const child=spawn(...args);children.push(child);return child;}});
  t.after(async()=>{client.close();await Promise.all(children.filter(c=>c.exitCode===null&&c.signalCode===null).map(c=>once(c,'exit')));});
  const chat=await client.request('ensure_chat',{account_id:'fixture',chat_key:'owner',kind:'friend',display_name:'owner'});
  const saved=await client.request('record_message',{chat_id:chat.id,source:'fixture',source_id:'before-exit',text:'restart continuity'});
  const exited=once(children[0],'exit');children[0].kill();await exited;
  const result=await client.request('search',{chat_id:chat.id,allowed_chat_ids:[chat.id],query:'restart continuity'});
  assert.equal(children.length,2);assert.notEqual(children[0].pid,children[1].pid);
  assert.equal(result.messages.length,1);assert.equal(result.messages[0].id,saved.id);
  assert.equal(client.status().generation,2);
});

test('durable inbox recovers through a real writer replacement without duplicate history',async t=>{
  const cwd=path.dirname(fileURLToPath(import.meta.url));
  const dataRoot=fs.mkdtempSync(path.join(os.tmpdir(),'whale-inbox-writer-recovery-')),children=[];
  const client=createVaultProcess({python:(process.env.WHALE_TEST_PYTHON||path.resolve(cwd,'../../.venv-wxauto/Scripts/python.exe')),cwd,dataRoot,restartDelayMs:0,
    spawnWorker:(...args)=>{const child=spawn(...args);children.push(child);return child;}});
  t.after(async()=>{client.close();await Promise.all(children.filter(c=>c.exitCode===null&&c.signalCode===null).map(c=>once(c,'exit')));});
  const chat=await client.request('ensure_chat',{account_id:'fixture',chat_key:'owner',kind:'friend',display_name:'owner'});
  let first=true;
  const inbox=createHistoryInbox({config:{dataRoot,accountId:'fixture'},routes:new Map([['owner',{kind:'friend'}]]),
    write:async event=>{
      const saved=await client.request('record_message',{chat_id:chat.id,source:'wxauto-incoming',source_id:event.id,
        text:event.text,timestamp:event.observedTimestamp,timestamp_quality:'observed'});
      if(first){first=false;const exited=once(children[0],'exit');children[0].kill();await exited;throw Error('lost commit receipt fixture');}
      return saved;
    }});
  t.after(()=>inbox.close());
  await assert.rejects(inbox.enqueue({id:'one-input',target:'owner',kind:'friend',text:'history only, never a fresh request'}));
  const folder=path.join(dataRoot,'snapshots/realtime-inbox');assert.equal(fs.readdirSync(folder).length,1);
  await inbox.replay();assert.deepEqual(fs.readdirSync(folder),[]);assert.equal(children.length,2);
  const found=await client.request('search',{chat_id:chat.id,allowed_chat_ids:[chat.id]});
  assert.equal(found.messages.length,1);assert.equal(found.messages[0].source_id,'one-input');
});
