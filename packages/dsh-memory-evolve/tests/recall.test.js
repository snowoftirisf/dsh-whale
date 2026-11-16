import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { recallSearch, queryTerms, scoreText, validateRecallSources } from '../lib/recall.js'

const store = (tracks) => ({
  entriesOf: (target) => tracks[target] ?? [],
  query: (target) => tracks[target] ?? [],
})

test('search spans every track, ranks by matched terms, newest first on ties', () => {
  const s = store({
    memory: ['[2026-11-10] 群里：小明 去吃黄鱼面', '[2026-11-15] 群里：小明 说黄鱼面偏咸'],
    user: ['[2026-11-01] 主人喜欢猫'],
    project: ['[2026-11-12 10:00] 看了 BV1xx 微缩木桌'],
    daily: ['[21:00] 和主人聊上海天气'],
  })
  const r = recallSearch({ rawStore: s, channel: null, agent: {}, query: '小明 黄鱼面' })
  assert.equal(r.ok, true)
  assert.deepEqual(r.results.map((x) => x.text), ['[2026-11-15] 群里：小明 说黄鱼面偏咸', '[2026-11-10] 群里：小明 去吃黄鱼面'])
  assert.match(r.entries[0], /^\[memory 2026-11-15\] /)
  assert.equal(recallSearch({ rawStore: s, channel: null, agent: {}, query: '微缩' }).results[0].source, 'project')
})

test('long Chinese phrase falls back to bigrams; nothing found says how to retry', () => {
  assert.ok(scoreText('小明 吃黄鱼面', ['上次吃的黄鱼面']) > 0)
  const empty = recallSearch({ rawStore: store({}), channel: null, agent: {}, query: '不存在的东西' })
  assert.equal(empty.results.length, 0)
  assert.match(empty.message, /whale_history_search/)
  assert.equal(recallSearch({ rawStore: store({}), channel: null, agent: {}, query: '  ' }).ok, false)
  assert.deepEqual(queryTerms('A，b  a'), ['a', 'b'])
})

test('group scope sees only what channel filter allows, and no privateOnly sources', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'recall-'))
  const reports = path.join(dir, 'reports'); fs.mkdirSync(reports)
  fs.writeFileSync(path.join(reports, 'r1.md'), '# 2026-11-14 阅读报告\n\n《手搓内存条》磁芯存储器 BV1abc\n')
  const diary = path.join(dir, 'diary.md'); fs.writeFileSync(diary, '主人今天说内存条很酷')
  const sources = [{ label: '视频', path: reports }, { label: '日记', path: diary, privateOnly: true }]
  const s = store({ memory: ['[2026-11-15] 私聊：内存条话题', '[2026-11-15] 群里：内存条'] })
  const channel = { scope: { kind: 'chat', chatId: 'g' }, filter: (t, rows) => rows.filter((x) => x.includes('群里')), filterArchived: () => [] }
  const group = recallSearch({ rawStore: s, channel, agent: {}, query: '内存条', sources })
  assert.deepEqual(group.results.map((x) => x.source).sort(), ['memory', '视频'])
  assert.ok(!group.results.some((x) => x.text.includes('私聊')))
  const owner = recallSearch({ rawStore: s, channel: { ...channel, scope: { kind: 'private' }, filter: (t, rows) => rows }, agent: {}, query: '内存条', sources })
  assert.ok(owner.results.some((x) => x.source === '日记'))
  assert.equal(recallSearch({ rawStore: s, channel: { scope: null }, agent: {}, query: 'x' }).ok, false)
})

test('tool payload only uses fields the memory output schema declares', async () => {
  const { memoryTool } = await import('../lib/index.js')
  const tool = memoryTool({ get: () => undefined }, { toolName: 'memory' }, store({}), null, () => ({}), null)
  const declared = Object.keys(tool.output.schema.properties)
  const { recallPayload } = await import('../lib/recall.js')
  for (const query of ['小明', '不存在']) {
    const payload = recallPayload(recallSearch({ rawStore: store({ memory: ['[2026-11-15] 小明 黄鱼面'] }), channel: null, agent: {}, query }))
    assert.deepEqual(Object.keys(payload).filter((k) => !declared.includes(k)), [])
    assert.ok(payload.entries.every((e) => typeof e === 'string'))
  }
})

test('recallSources must be labelled absolute paths', () => {
  assert.doesNotThrow(() => validateRecallSources([{ label: 'a', path: path.resolve('x') }]))
  assert.throws(() => validateRecallSources([{ label: 'a', path: 'relative' }]))
  assert.throws(() => validateRecallSources({}))
})
