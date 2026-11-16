import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createSpeakUpDelivery } from '../lib/im-delivery.mjs'

function setup(send = async () => ({ sent: true })) {
  const sent = [], logs = []
  const settings = { 目标会话: ['fish'], IM投递: { sessionId: 'fish', botId: 'wx-test', targetId: 'owner' } }
  const delivery = createSpeakUpDelivery({ config: () => settings,
    service: () => ({ send: async (...args) => { sent.push(args); return send(...args) } }), log: value => logs.push(value) })
  const emit = (type, data, surfaceOp = 'append', sid = 'fish') => delivery.event({ id: sid }, { type, data, surfaceOp })
  const begin = (kind = 'speak-up') => {
    emit('turn/start', { turn: 1 })
    emit('user/message', { source: { kind }, content: [{ type: 'text', text: 'private trigger' }] })
  }
  const answer = (text, step = 0, extra = {}) => emit('assistant/message', { turn: 1, step, message: { content: [{ type: 'thinking', text: 'secret' }, { type: 'text', text }] }, ...extra })
  const end = (kind = 'completed') => emit('turn/end', { turn: 1, reason: { kind } })
  return { sent, logs, settings, delivery, emit, begin, answer, end }
}

test('speak-up sends completed answer once; excludes thought/trigger and deduplicates canonical step', async () => {
  const f = setup(); f.begin(); f.answer('old'); f.answer('主人，醒了吗？');
  assert.equal(f.sent.length, 0)
  f.end(); f.end(); await f.delivery.idle()
  assert.equal(f.sent.length, 1)
  assert.deepEqual(f.sent[0].slice(0, 3), ['wx-test', 'owner', '主人，醒了吗？'])
})
test('normal GUI, IM and schedule replies are not duplicated', async () => {
  for (const source of ['user', 'weixin', 'schedule']) {
    const f = setup(); f.begin(source); f.answer('normal'); f.end(); await f.delivery.idle(); assert.equal(f.sent.length, 0)
  }
})
test('native runtime/time/memory snapshots do not mask the speak-up origin', async () => {
  const f = setup(); f.begin()
  for (const kind of ['runtime-context', 'time-context', 'memory']) {
    f.emit('user/message', { source: { kind, form: 'snapshot' }, content: [{ type: 'text', text: 'internal context' }] })
  }
  f.answer('主人，早。'); f.end(); await f.delivery.idle()
  assert.equal(f.sent.length, 1); assert.equal(f.sent[0][2], '主人，早。')
})
test('failed, interrupted and wrong-session output is never delivered', async () => {
  for (const reason of ['error', 'interrupted']) {
    const f = setup(); f.begin(); f.answer('partial'); f.end(reason); await f.delivery.idle(); assert.equal(f.sent.length, 0)
  }
  const f = setup(); f.begin(); f.answer('partial', 0, { interrupted: true }); f.end(); await f.delivery.idle(); assert.equal(f.sent.length, 0)
  const g = setup(); g.emit('turn/start', { turn: 1 }, 'append', 'other'); g.answer('other'); g.end(); await g.delivery.idle(); assert.equal(g.sent.length, 0)
})
test('changed target cancels pending delivery; failure is logged without retry or false success', async () => {
  const f = setup(); f.begin(); f.answer('hi'); f.end(); f.settings.IM投递 = null
  await f.delivery.idle(); assert.equal(f.sent.length, 0)
  const g = setup(async () => { throw Object.assign(new Error('private detail'), { code: 'delivery-failed' }) })
  g.begin(); g.answer('hi'); g.end(); await g.delivery.idle()
  assert.equal(g.sent.length, 1); assert.equal(g.logs.length, 1)
  assert.match(g.logs[0], /失败/); assert.doesNotMatch(g.logs[0], /private detail/)
})
