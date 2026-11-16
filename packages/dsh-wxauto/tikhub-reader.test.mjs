import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createTikhubReader, compact, pickFields} from './tikhub-reader.mjs';

function root(t, config) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tikhub-'));
  t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  fs.mkdirSync(path.join(dir, 'config'));
  if (config) fs.writeFileSync(path.join(dir, 'config', 'tikhub.local.json'), JSON.stringify(config));
  return dir;
}

test('off by default: no token or enabled=false never calls the API', async t => {
  let calls = 0;
  const fetchImpl = async () => { calls++; return new Response('{}'); };
  assert.match((await createTikhubReader({dataRoot: root(t), fetchImpl})({platform: 'douyin', kind: 'feed'})).message, /未配置/);
  assert.match((await createTikhubReader({dataRoot: root(t, {token: 'x'.repeat(20)}), fetchImpl})({platform: 'douyin', kind: 'feed'})).message, /关闭/);
  assert.equal(calls, 0);
});

test('enabled: bearer token, guest feed request, compact items', async t => {
  const calls = [];
  const payload = {data: {aweme_list: [
    {aweme_id: '111', desc: '做饭视频', author: {nickname: '甲'}, statistics: {digg_count: 9, comment_count: 2}},
    {aweme_id: '222', desc: '猫', author: {nickname: '乙'}, statistics: {digg_count: 5}},
  ]}};
  const fetchImpl = async (url, init) => { calls.push({url: String(url), init}); return new Response(JSON.stringify(payload)); };
  const read = createTikhubReader({dataRoot: root(t, {token: 't'.repeat(20), enabled: true}), fetchImpl});
  const r = await read({platform: 'douyin', kind: 'feed'});
  assert.equal(calls[0].init.method, 'POST');
  assert.equal(calls[0].init.headers.Authorization, 'Bearer ' + 't'.repeat(20));
  assert.ok(!JSON.parse(calls[0].init.body).cookie); // guest mode: never a user cookie
  assert.deepEqual(r.items[0], {id: '111', title: '做饭视频', author: '甲', likes: 9, comments: 2});
  assert.equal(r.count, 2);
  const s = await read({platform: 'xiaohongshu', kind: 'search', query: '上海 黄鱼面'});
  assert.match(calls[1].url, /search_notes\?keyword=/);
  assert.equal(s.ok, true);
  assert.equal((await read({platform: 'douyin', kind: 'detail', id: 'bad id!'})).ok, false);
});

test('compact detail and pickFields depth', () => {
  assert.equal(compact({data: {aweme_detail: {aweme_id: '9', desc: 'x'}}}, 'detail').item.id, '9');
  assert.deepEqual(pickFields({a: {b: {c: {nickname: 'n'}}}}), {author: 'n'});
});
