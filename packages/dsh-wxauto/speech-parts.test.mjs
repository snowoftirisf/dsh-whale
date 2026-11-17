import test from 'node:test';
import assert from 'node:assert/strict';
import { speechParts } from './speech-parts.mjs';
import { chatPrompt } from './logic.mjs';

test('model chooses voice, text and memes in explicit order without trigger heuristics',()=>{
  assert.deepEqual(speechParts('文字\n\n[[wx-voice]]我来啦！[[/wx-voice]]\n\n[表情: 开心]','friend',true),
    [{kind:'text',text:'文字'},{kind:'voice',text:'我来啦！'},{kind:'meme',query:'开心'}]);
  assert.deepEqual(speechParts('想听你的声音','friend',true),[{kind:'text',text:'想听你的声音'}]);
});
test('not ready preserves words without leaking voice markers or pretending delivery',()=>{
  assert.deepEqual(speechParts('[[wx-voice]]你好[[/wx-voice]]','friend',false),[{kind:'text',text:'你好'}]);
  assert.deepEqual(speechParts('前文\n\n[[wx-voice]]没闭合','friend',true),[{kind:'text',text:'前文'},{kind:'voice',text:'没闭合'}]);
  assert.throws(()=>speechParts('[[wx-voice]][[wx-at:甲]][[/wx-voice]]','group',true),/invalid/);
});
test('original mentions, code blocks and total meme limit remain',()=>{
  assert.deepEqual(speechParts('[[wx-at:甲]]早\n\n[[wx-voice]]你好[[/wx-voice]]','group',true)[0],
    {kind:'text',text:'早',at:['甲']});
  assert.equal(speechParts('```\n[[wx-voice]]示例[[/wx-voice]]\n```','friend',true)[0].kind,'text');
  assert.throws(()=>speechParts('[表情:1][表情:2][[wx-voice]]a[[/wx-voice]][表情:3][表情:4]','friend',true),/too-many/);
});
test('available channel prompts leave modality to model; unavailable channels do not advertise voice',()=>{
  assert.match(chatPrompt({kind:'friend',target:'主人'},[{text:'你好'}],true),/你自主决定/);
  assert.match(chatPrompt({kind:'friend',target:'主人'},[{text:'你好'}],false),/尚未就绪/);
});

test('model-selected emotional speech preserves mixed reply order and never speaks its direction',()=>{
  assert.deepEqual(speechParts('先说一句\n\n[[wx-voice:轻快、带一点惊喜]]你回来啦！[[/wx-voice]]\n\n[[wx-voice:平静]]慢慢说。[[/wx-voice]]','friend',true),[
    {kind:'text',text:'先说一句'},
    {kind:'voice',text:'你回来啦！',emotion:'轻快、带一点惊喜'},
    {kind:'voice',text:'慢慢说。',emotion:'平静'}]);
  assert.deepEqual(speechParts('[[wx-voice:开心]]你好[[/wx-voice]]','friend',false),[{kind:'text',text:'你好'}]);
  assert.match(chatPrompt({kind:'friend',target:'主人'},[{text:'你好'}],{voice:true,emotion:true}),/自主决定语气和情绪/);
  assert.doesNotMatch(chatPrompt({kind:'friend',target:'主人'},[{text:'你好'}],{voice:false,emotion:true}),/wx-voice:开心/);
});

test('malformed directions cannot leak as plain control text or nest actions',()=>{
  for(const text of ['[[wx-voice:]]你好[[/wx-voice]]','[[wx-voice:开心\n惊喜]]你好[[/wx-voice]]',
    '[[wx-voice:开心]]a[[wx-voice]]b[[/wx-voice]]','[[wx-voice:'+ 'x'.repeat(161)+']]a[[/wx-voice]]']){
    assert.throws(()=>speechParts(text,'friend',true),/voice/);
  }
});
