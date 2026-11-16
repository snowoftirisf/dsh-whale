import test from 'node:test';
import assert from 'node:assert/strict';
import {limitedHistoryResult,historyRoute} from './vault-client.mjs';
test('scope derives from actual session, group cannot choose owner or another group',()=>{
  const routes=new Map([['owner',{target:'owner',sessionId:'bili'}],['A',{target:'A',sessionId:'group-a'}],['B',{target:'B',sessionId:'group-b'}]]);
  const lookup=(sessionId,requestedChat)=>historyRoute({routes,sessionId,ownerSessionId:'bili',requestedChat});
  assert.equal(lookup('group-a').target,'A');assert.throws(()=>lookup('group-a','owner'));
  assert.throws(()=>lookup('group-a','B'));assert.throws(()=>lookup('fake','A'));
  assert.equal(lookup('bili','B').target,'B');assert.throws(()=>lookup('bili','unknown'));
});
test('whole envelope is bounded and remains valid JSON with source marker',()=>{
  const input={untrusted_history_data:true,messages:Array.from({length:50},()=>({text:'x'.repeat(500),metadata:{detail:'m'.repeat(200)}}))};
  const output=limitedHistoryResult(input,12000);assert.ok(JSON.stringify(output).length<=12000);
  assert.equal(output.untrusted_history_data,true);assert.equal(output.truncated,true);assert.equal(input.messages.length,50);
});

test('budgeted pagination traverses every row, including a cropped final page',()=>{
  const all=Array.from({length:27},(_,id)=>({id:String(id),text:'内容'.repeat(80),metadata:{info:'m'.repeat(180)}}));
  const seen=[];let offset=0;
  while(offset!==null){
    const rows=all.slice(offset,offset+20);
    const page=limitedHistoryResult({untrusted_history_data:true,messages:rows,order:'newest-first',
      next_offset:offset+rows.length<all.length?offset+rows.length:null},1200,{offset});
    assert.ok(JSON.stringify(page).length<=1200);assert.ok(page.messages.length);
    seen.push(...page.messages.map(m=>m.id));
    if(page.next_offset!==null)assert.ok(page.next_offset>offset);
    offset=page.next_offset;
    assert.ok(seen.length<=all.length);
  }
  assert.deepEqual(seen,all.map(m=>m.id));
});

test('context trimming keeps its anchor and closest contiguous neighbors',()=>{
  const rows=Array.from({length:11},(_,id)=>({id:String(id),text:'x'.repeat(300)}));
  for(const anchorId of ['0','5','10']){
    const page=limitedHistoryResult({untrusted_history_data:true,messages:rows,order:'oldest-first'},1000,{anchorId});
    assert.ok(page.messages.some(m=>m.id===anchorId));assert.ok(JSON.stringify(page).length<=1000);
    const ids=page.messages.map(m=>Number(m.id));
    assert.equal(ids.at(-1)-ids[0]+1,ids.length);
  }
});

test('one oversized record still yields its ID and usable bounded text',()=>{
  const input={untrusted_history_data:true,messages:[{id:'anchor',text:'abc'.repeat(2000),
    delivery_state:'unknown',metadata:{huge:'m'.repeat(10000)}}],order:'newest-first',next_offset:null};
  const page=limitedHistoryResult(input,700,{offset:9});
  assert.ok(JSON.stringify(page).length<=700);assert.equal(page.messages.length,1);
  assert.equal(page.messages[0].id,'anchor');assert.equal(page.messages[0].delivery_state,'unknown');
  assert.equal(page.messages[0].details_omitted,true);assert.ok(page.messages[0].text.length>0);
  assert.equal(page.next_offset,null);assert.equal(input.messages[0].text.length,6000);
});
