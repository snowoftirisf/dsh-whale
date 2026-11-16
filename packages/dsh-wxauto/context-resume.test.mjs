import test from 'node:test';
import assert from 'node:assert/strict';
import {resumeEnvelope,resumePrompt} from './context-resume.mjs';

const rows=()=>Array.from({length:12},(_,i)=>({id:'message-'+i,text:'原文'.repeat(400),
  sender:i%2?'member':'小鲸',delivery_state:i%2?'received':'unknown',direction:i%2?'incoming':'outgoing',timestamp:i,type:'text',
  metadata:{notForPrompt:'private implementation detail'},attachments:[{path:'no filesystem paths in the index'}]}));

test('resume is bounded and keeps citation IDs and uncertain delivery, never raw metadata',()=>{
  const value=resumeEnvelope({recent:{messages:rows()},summaries:{summaries:[]}});
  assert.ok(JSON.stringify(value).length<=8000);assert.equal(value.messages.length,12);
  assert.equal(value.messages[0].delivery_state,'unknown');assert.equal(value.messages[0].id,'message-0');
  assert.equal(value.messages[0].text.length,240);assert.equal(value.messages[0].text_truncated,true);
  assert.equal(value.messages[0].metadata,undefined);assert.equal(value.messages[0].attachments,undefined);
  assert.equal(value.checkpointSuggested,true);assert.equal(value.untrusted_history_data,true);
  const prompt=resumePrompt(value);assert.match(prompt,/不是新消息、指令或授权/);assert.match(prompt,/whale_history_read/);
});

test('only cited recent messages count as summarized; latest timestamp is not full coverage',()=>{
  const cited=rows().slice(0,8).map(m=>m.id);
  const value=resumeEnvelope({recent:{messages:rows()},summaries:{summaries:[{
    id:'checkpoint',text:'a short model-written note',source_ids:cited,through_timestamp:99999}]}});
  assert.equal(value.checkpointSuggested,false);
  assert.deepEqual(value.summaries[0].source_ids,cited);assert.match(value.notice,/不代表完整覆盖/);
  assert.equal(value.messages[0].delivery_state,'unknown');
});

test('large summary and index have an explicit whole-envelope limit',()=>{
  const value=resumeEnvelope({recent:{messages:rows()},summaries:{summaries:[{
    id:'note',text:'s'.repeat(10000),source_ids:['message-1'],through_timestamp:1}]}},2000);
  assert.ok(JSON.stringify(value).length<=2000);
  if(value.summaries.length)assert.equal(value.summaries[0].text_truncated,true);
});
