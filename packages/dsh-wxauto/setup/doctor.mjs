#!/usr/bin/env node
// dsh-whale 体检：只读检查，不改任何文件，不打印任何密钥或聊天内容。
// Usage: node setup/doctor.mjs [--profile desktop]
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const args = process.argv.slice(2);
const profile = args.includes('--profile') ? args[args.indexOf('--profile') + 1] : (process.env.DSH_PROFILE || 'desktop');
const here = path.dirname(fileURLToPath(import.meta.url));
const plugin = path.resolve(args.includes('--plugin') ? args[args.indexOf('--plugin') + 1] : path.join(here, '..'));
const dshHome = process.env.DSH_HOME || path.join(os.homedir(), '.dsh');
let bad = 0, warn = 0;
const ok = m => console.log('  ✓ ' + m);
const no = (m, fix) => { bad++; console.log('  ✗ ' + m + (fix ? `\n      → ${fix}` : '')); };
const maybe = (m, fix) => { warn++; console.log('  ! ' + m + (fix ? `\n      → ${fix}` : '')); };
const json = file => { try { return JSON.parse(fs.readFileSync(file, 'utf8').replace(/^﻿/, '')); } catch { return null; } };
const section = t => console.log('\n' + t);

section('DeepSeek Harness 插件');
const pkg = json(path.join(dshHome, 'profiles', profile, 'package.json'));
if (!pkg) no(`找不到 profile「${profile}」`, '先安装并打开一次 DeepSeek Harness');
else {
  const bundles = new Set(pkg.dsh?.profile?.bundles ?? []);
  const need = [
    ['local-dsh-wxauto', '微信通道（本仓库 packages/dsh-wxauto）'],
    ['dsh-bili-whale', '养鱼：人设、作息、B站（本仓库 packages/dsh-bili-whale）'],
    ['dsh-memory-evolve', '记忆（本仓库 packages/dsh-memory-evolve）'],
    ['dsh-speak-up', '主动搭话（本仓库 packages/dsh-speak-up）'],
    ['@xmanrui/dsh-im', 'IM 基础库（npm）'],
    ['@liustack/modlens', '看图（npm）'],
    ['dsh-meme', '表情包（npm）'],
  ];
  for (const [name, what] of need) bundles.has(name) ? ok(`${name} — ${what}`) : no(`${name} 没装或没启用 — ${what}`, '见 INSTALL.md 第 1 步');
}

section('数据目录与配置');
const boot = json(path.join(plugin, 'config.local.json')) ?? json(path.join(dshHome, 'dsh-whale.json'));
const dataRoot = boot?.dataRoot;
if (!dataRoot) no('还没配置', '运行 setup/setup.ps1 -Owner "主人微信名"');
const cfg = dataRoot ? json(path.join(dataRoot, 'config', 'wxauto.local.json')) : null;
if (dataRoot && !cfg) no(`${dataRoot}\\config\\wxauto.local.json 不存在或损坏`, '重跑 setup/configure.py');
if (cfg) {
  ok(`数据目录 ${dataRoot}`);
  cfg.enabled === true ? ok('通道已启用') : no('wxauto.local.json 里 enabled 不是 true');
  cfg.target ? ok('主人已设置') : no('没有主人（target）', 'configure.py --owner "主人微信名"');
  /^wechat:[A-Za-z0-9_-]+$/.test(cfg.nativeVoiceSources?.ownerNativeChatKey ?? '') ? ok('主人微信号已绑定') : no('主人微信号没绑定', '重跑 configure.py');
  cfg.capture?.enabled && cfg.capture?.sourceId && cfg.capture?.accountUsername ? ok('本地快照采集已配置') : no('快照采集没配置', '重跑 configure.py');
  fs.existsSync(cfg.python || '') ? ok('Python 环境存在') : no('Python 环境不存在：' + cfg.python, '重跑 setup.ps1');
  cfg.sessionId === 'bili-whale' ? ok('主会话 = 养鱼主会话') : no('sessionId 必须是 bili-whale');
  const latest = json(path.join(dataRoot, 'snapshots', 'windows-live', 'latest.json'));
  latest?.snapshot_id ? ok('已有聊天快照') : maybe('还没有成功的快照', '微信登录后重跑 configure.py');
}

section('微信读取模块（yichen-skills，按需下载）');
const lock = json(path.join(plugin, 'vault', 'vendor', 'vendor-lock.json'));
if (!lock) no('vendor-lock.json 丢失', '重新安装插件');
else {
  const broken = lock.files.filter(f => {
    const file = path.join(plugin, 'vault', 'vendor', ...f.path.split('/'));
    return !fs.existsSync(file) || crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex') !== f.sha256;
  });
  broken.length ? no(`${broken.length} 个文件缺失或校验不对`, `node "${path.join(here, 'fetch-vendor.mjs')}"（插件更新后需要重跑）`) : ok(`${lock.files.length} 个文件校验通过`);
}

section('看图 / 说话 / 视频');
const modlens = json(path.join(os.homedir(), '.modlens', 'config.json'));
const provider = modlens?.provider;
provider && (modlens.providers?.[provider]?.apiKey || provider.endsWith('-cli')) ? ok(`看图 provider: ${provider}`) : maybe('看图没配置', 'configure.py --gemini-key <Google AI Studio key>');
if (dataRoot) {
  const voice = json(path.join(dataRoot, 'voice', 'voice.local.json'));
  const breeze = json(path.join(dataRoot, 'config', 'breezeblue.local.json'));
  if (voice?.backend === 'breezeblue') {
    /^brz_/.test(breeze?.apiKey ?? '') ? ok('BreezeBlue key 已配置') : no('选了 BreezeBlue 但没 key', 'configure.py --breeze-key brz_…');
    voice.breezeVoiceId ? ok('声音 ID 已配置') : no('没有声音 ID', 'configure.py --breeze-voice voc_…');
  } else maybe('没配置语音（只发文字）', 'configure.py --breeze-key brz_… --breeze-voice voc_…');
  const tik = json(path.join(dataRoot, 'config', 'tikhub.local.json'));
  tik?.enabled ? ok('TikHub 已启用') : console.log('  · TikHub 未启用（可选）');
}
let ffmpeg = 'ffmpeg';
try { ffmpeg = (await import(new URL('../dsh-bili-whale/tools/lib/ffmpeg.mjs', 'file:///' + plugin.replaceAll('\\', '/') + '/').href)).ffmpegPath() || 'ffmpeg'; } catch {}
try { execFileSync(ffmpeg, ['-version'], {stdio: 'ignore'}); ok('ffmpeg'); }
catch { maybe('没找到 ffmpeg', 'winget install Gyan.FFmpeg，然后重开 DSH'); }
const bili = json(path.join(dshHome, 'bili-whale.config.json'));
bili ? ok('养鱼设置存在') : maybe('还没打开过「设置 → 养鱼」', '在 DSH 设置里填 B 站 cookie 和人设');

section('运行状态（DSH 正在运行时才有）');
const status = json(path.join(plugin, 'status.local.json'));
if (!status) maybe('还没有运行状态', '重启 DeepSeek Harness');
else {
  console.log(`  · 最近事件：${status.event ?? '未知'}${status.updatedAt ? '（' + status.updatedAt + '）' : ''}`);
  status.ready === true ? ok('微信连接 ready') : maybe('微信连接还没 ready', '确认微信已登录、窗口没被最小化到托盘，再重启 DSH');
  const mode = status.ownerTextCutover?.authorityMode ?? status.ownerTextAuthority?.mode;
  mode === 'native' ? ok('主人私聊：原生文字接收已启用') : maybe('主人私聊还在 UI 模式', 'DSH 设置 → 微信连接 → 「切换为原生文字接收」');
  if (status.code || status.message) console.log(`  · 最近错误：${status.code ?? ''} ${String(status.message ?? '').slice(0, 160)}`);
}

console.log(`\n${bad ? '✗' : '✓'} ${bad} 个问题，${warn} 个提醒`);
process.exit(bad ? 1 : 0);
