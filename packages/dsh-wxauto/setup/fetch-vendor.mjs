#!/usr/bin/env node
// Downloads the pinned yichen-skills WeChat reader modules into
// packages/dsh-wxauto/vault/vendor/ and verifies every file against vendor-lock.json.
//
// Why a download instead of shipping the files: upstream (mcncarl/yichen-skills) uses a
// "Personal Learning and Non-Commercial Use License" that does not allow public
// redistribution. Each user fetches it themselves from the original repository, for
// personal non-commercial use, and must keep its LICENSE and notices.
//
// Usage: node setup/fetch-vendor.mjs [--plugin <dsh-wxauto dir>] [--mirror <base url>]
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const args = process.argv.slice(2);
const opt = name => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : undefined; };
const here = path.dirname(fileURLToPath(import.meta.url));
const plugin = path.resolve(opt('--plugin') ?? path.join(here, '..'));
const vendor = path.join(plugin, 'vault', 'vendor');
const lock = JSON.parse(fs.readFileSync(path.join(vendor, 'vendor-lock.json'), 'utf8'));
const m = /^https:\/\/github\.com\/([^/]+)\/([^/]+?)(?:\.git)?$/.exec(lock.repository);
if (!m || !/^[0-9a-f]{40}$/.test(lock.commit)) throw Error('vendor-lock.json: unexpected repository/commit');
const base = (opt('--mirror') ?? `https://raw.githubusercontent.com/${m[1]}/${m[2]}`).replace(/\/$/, '');

const sha = buf => crypto.createHash('sha256').update(buf).digest('hex');
let fetched = 0, kept = 0;
for (const entry of lock.files) {
  if (entry.path.includes('..') || path.isAbsolute(entry.path)) throw Error('bad path in lock: ' + entry.path);
  const target = path.join(vendor, ...entry.path.split('/'));
  if (fs.existsSync(target) && sha(fs.readFileSync(target)) === entry.sha256) { kept++; continue; }
  const url = `${base}/${lock.commit}/${entry.source.split('/').map(encodeURIComponent).join('/')}`;
  let body;
  for (let attempt = 1; ; attempt++) {
    try {
      const res = await fetch(url);
      if (!res.ok) throw Error(`HTTP ${res.status}`);
      body = Buffer.from(await res.arrayBuffer());
      break;
    } catch (error) {
      if (attempt >= 3) { console.error(`✗ 下载失败 ${entry.source}: ${error.message}`); process.exit(2); }
    }
  }
  if (sha(body) !== entry.sha256) {
    console.error(`✗ 校验失败 ${entry.path}（上游内容和锁定版本不一致，已拒绝写入）`);
    process.exit(3);
  }
  fs.mkdirSync(path.dirname(target), {recursive: true});
  fs.writeFileSync(target, body);
  fetched++;
}
console.log(`✓ yichen-skills@${lock.commit.slice(0, 8)}：下载 ${fetched} 个，已存在 ${kept} 个，全部 SHA256 校验通过`);
console.log('  这些文件遵循上游的个人学习与非商业使用许可（vault/vendor/LICENSE.yichen.txt），请勿再公开分发。');
