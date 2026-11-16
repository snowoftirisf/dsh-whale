import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.DSH_HOME = mkdtempSync(join(tmpdir(), 'speak-up-per-session-'))
const { 归一化, 挑触发文案 } = await import(new URL('../lib/host.js', import.meta.url).href)

test('per-session trigger text survives normalization and is picked for that session only', () => {
  const 配置 = 归一化({
    触发文案: '私聊那句',
    分会话触发文案: { 'whale-wx-group-x': '群聊那句', 空的: '  ', 坏的: 3 },
  })
  assert.deepEqual(配置.分会话触发文案, { 'whale-wx-group-x': '群聊那句' })
  assert.equal(挑触发文案(配置, 'whale-wx-group-x'), '群聊那句')
  assert.equal(挑触发文案(配置, 'session-whale-wx-group-x'), '群聊那句') // session- prefix is the same session
  assert.equal(挑触发文案(配置, 'bili-whale'), '私聊那句')
})

test('missing or malformed per-session map falls back to the global text', () => {
  for (const 值 of [undefined, null, [], 'x']) {
    const 配置 = 归一化({ 触发文案: '全局', 分会话触发文案: 值 })
    assert.deepEqual(配置.分会话触发文案, {})
    assert.equal(挑触发文案(配置, 'any'), '全局')
  }
})
