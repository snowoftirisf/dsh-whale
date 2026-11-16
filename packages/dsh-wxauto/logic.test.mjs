import test from 'node:test';
import assert from 'node:assert/strict';
import { bubbles, createForwarder, chatPrompt, shouldSendReply, outgoingParts } from './logic.mjs';
test('autonomous mentions become metadata; meme markers become images in order', () => {
  const result = outgoingParts('[[wx-at:甲]][[wx-at:乙]]早呀\n\n[表情: 开心]\n\n今天不错', 'group');
  assert.deepEqual(result, [{kind:'text',text:'早呀',at:['甲','乙']},{kind:'meme',query:'开心'},{kind:'text',text:'今天不错'}]);
  assert.deepEqual(outgoingParts('[[wx-at:未在本批发言的群成员]]在吗', 'group')[0].at, ['未在本批发言的群成员']);
});
test('normal replies do not force mentions; private messages cannot use group mentions', () => {
  assert.deepEqual(outgoingParts('你好', 'group'), [{kind:'text',text:'你好'}]);
  assert.throws(() => outgoingParts('[[wx-at:甲]]你好', 'friend'), /mention-not-allowed/);
  assert.deepEqual(outgoingParts('[表情：比心]', 'friend'), [{kind:'meme',query:'比心'}]);
});
test('private and group inputs have explicit destination and speaker context', () => {
  const privatePrompt = chatPrompt({target: '主人', kind: 'friend'}, [{text: '私聊测试'}]);
  const groupPrompt = chatPrompt({target: '测试群', kind: 'group'}, [{sender: '甲', text: '群聊测试'}, {sender: '乙', text: '一起聊'}]);
  assert.match(privatePrompt, /当前渠道：微信私聊/);
  assert.match(privatePrompt, /当前不是群聊/);
  assert.doesNotMatch(privatePrompt, /群聊测试/);
  assert.match(groupPrompt, /当前渠道：微信群聊；群名：测试群/);
  assert.match(groupPrompt, /"发言人":"甲"/);
  assert.match(groupPrompt, /"发言人":"乙"/);
  assert.match(groupPrompt, /显示名不是身份认证/);
  assert.match(groupPrompt, /不主动公开主人私聊/);
  assert.doesNotMatch(groupPrompt, /私聊测试/);
});
test('group abstention stays silent while private text is preserved', () => {
  assert.equal(shouldSendReply('group', '[不回复]'), false);
  assert.equal(shouldSendReply('group', '  [不回复]\n'), false);
  assert.equal(shouldSendReply('group', '我在呢'), true);
  assert.equal(shouldSendReply('friend', '[不回复]'), true);
  assert.equal(shouldSendReply('group', ''), false);
});
test('preserves code blocks and bounds bubble count', () => {
  assert.deepEqual(bubbles('一\n\n二'), ['一', '二']);
  assert.equal(bubbles('```\na\n\nb\n```').length, 1);
  assert.equal(bubbles(Array(8).fill('段').join('\n\n')).length, 1);
});
test('only completed proactive turns; snapshots do not override origin', () => {
  const sent = [];
  const forward = createForwarder({ sessionId: 'bili-whale', send: text => sent.push(text) });
  const run = (source, reason, sid = 'bili-whale') => {
    const session = { id: sid };
    const event = (type, data, surfaceOp = 'append') => forward(session, { type, data: { turn: 1, ...data }, surfaceOp });
    event('turn/start', {});
    event('user/message', { source: { kind: source } });
    event('user/message', { source: { kind: 'runtime', form: 'snapshot' } });
    event('assistant/message', { step: 1, message: { content: [{ type: 'reasoning', text: '隐藏' }, { type: 'text', text: '旧' }] } });
    event('assistant/message', { step: 1, message: { content: [{ type: 'text', text: '你好' }] } });
    event('turn/end', { reason: { kind: reason } });
  };
  run('wxauto', 'completed'); run('weixin', 'completed'); run('speak-up', 'error'); run('speak-up', 'completed', 'other');
  assert.equal(sent.length, 0);
  run('speak-up', 'completed');
  assert.deepEqual(sent, ['你好']);
});

test('a compaction mid-turn (checkpoint, instructions, skill catalog) does not drop a proactive reply',()=>{
  const sent=[]
  const forward=createForwarder({sessionId:'bili-whale',send:text=>sent.push(text)})
  const ev=(type,data,surfaceOp='append')=>forward({id:'bili-whale'},{type,surfaceOp,data:{turn:7,...data}})
  ev('turn/start',{})
  ev('user/message',{source:{kind:'speak-up'}})
  ev('user/message',{source:{kind:'compact-checkpoint',compactionId:'x'}})
  ev('user/message',{source:{kind:'agent-instructions',form:'instructions'}})
  ev('user/message',{source:{kind:'skill-catalog',form:'catalog'}})
  ev('assistant/message',{step:6,message:{content:[{type:'text',text:'主人，测试'}]}})
  ev('turn/end',{reason:{kind:'completed'}})
  assert.deepEqual(sent,['主人，测试'])
})
test('one forwarder can serve several group sessions without mixing their turns',()=>{
  const sent=[]
  const forward=createForwarder({accepts:id=>id.startsWith('group-'),send:(text,id,sid)=>sent.push([sid,text,id])})
  const ev=(sid,type,data)=>forward({id:sid},{type,surfaceOp:'append',data:{turn:1,...data}})
  ev('group-a','turn/start',{});ev('group-b','turn/start',{})
  ev('group-a','user/message',{source:{kind:'speak-up'}});ev('group-b','user/message',{source:{kind:'wxauto'}})
  ev('group-a','assistant/message',{step:1,message:{content:[{type:'text',text:'大家好'}]}})
  ev('group-b','assistant/message',{step:1,message:{content:[{type:'text',text:'普通回复'}]}})
  ev('bili-whale','turn/start',{});ev('bili-whale','user/message',{source:{kind:'speak-up'}})
  ev('bili-whale','assistant/message',{step:1,message:{content:[{type:'text',text:'私聊'}]}});ev('bili-whale','turn/end',{reason:{kind:'completed'}})
  ev('group-b','turn/end',{reason:{kind:'completed'}});ev('group-a','turn/end',{reason:{kind:'completed'}})
  assert.deepEqual(sent,[['group-a','大家好','proactive:1']])
})
test('forwarding follows the original whale session rotation',()=>{
  let current='old';const sent=[]
  const forward=createForwarder({getSessionId:()=>current,send:text=>sent.push(text)})
  const run=id=>{
    const session={id};const event=(type,data)=>forward(session,{type,surfaceOp:'append',data:{turn:1,...data}})
    event('turn/start',{});event('user/message',{source:{kind:'speak-up'}})
    event('assistant/message',{step:1,message:{content:[{type:'text',text:id}]}})
    event('turn/end',{reason:{kind:'completed'}})
  }
  run('old');current='new';run('old');run('new')
  assert.deepEqual(sent,['old','new'])
})
