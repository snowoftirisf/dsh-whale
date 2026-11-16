"""Write a friend's local configuration for dsh-whale. Nothing here leaves the machine.

Run with the .venv-wxauto interpreter (setup.ps1 does this for you):

    python -X utf8 setup/configure.py --owner "主人在微信里的备注或昵称" [options]

Steps:
 1. create the data folder (default %USERPROFILE%\\WhaleData) and its manifest
 2. find the logged-in WeChat 4.x account (vault.capture discover; reads no keys)
 3. take one decrypted snapshot of that account's chat databases into the data folder
    (reads the running WeChat's database key in memory, like the plugin does at runtime)
 4. look the owner up by remark / nickname / WeChat ID in the snapshot's contact table
 5. write <data>/config/wxauto.local.json (+ voice, image, BreezeBlue, TikHub files)
    and ~/.dsh/dsh-whale.json pointing the plugin at the data folder

Re-running is safe: existing values are kept unless you pass a new one.
Secrets (API keys) are written only to <data>/config/*.local.json and never printed.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path.home()
DSH_HOME = Path(os.environ.get('DSH_HOME') or HOME / '.dsh')


def say(msg):
    print(msg, flush=True)


def fail(msg, code=1):
    print('✗ ' + msg, file=sys.stderr, flush=True)
    raise SystemExit(code)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return default
    except Exception as exc:  # corrupted file: refuse to overwrite blindly
        fail(f'{path} 不是有效 JSON（{type(exc).__name__}），请先手动修好或删掉再重跑')


def write_json(path, value, private=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)
    say(f'  写入 {path}' + ('（含密钥，不会显示内容）' if private else ''))


def find_plugin(explicit):
    """dsh-wxauto source: --plugin, the installed copy in the DSH profile, or this repo."""
    candidates = [Path(explicit)] if explicit else []
    profile = os.environ.get('DSH_PROFILE', 'desktop')
    candidates += [DSH_HOME / 'profiles' / profile / 'node_modules' / 'local-dsh-wxauto',
                   HERE.parent]
    for c in candidates:
        if (c / 'worker.py').is_file() and (c / 'vault' / 'capture.py').is_file():
            return c.resolve()
    fail('找不到 dsh-wxauto 插件目录；先在 DSH 里装好插件，或用 --plugin 指定')


def run_capture(plugin, data_root, *args):
    cmd = [sys.executable, '-X', 'utf8', '-m', 'vault.capture', '--root', str(data_root), *args]
    out = subprocess.run(cmd, cwd=plugin, capture_output=True, text=True, encoding='utf-8', errors='replace')
    line = (out.stdout.strip().splitlines() or [''])[-1]
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        fail('vault.capture 没有返回 JSON：' + (out.stderr.strip()[-400:] or line[-400:]))
    if not value.get('ok'):
        fail('vault.capture 失败：' + str(value.get('error')))
    return value['value']


def account_username(db_root):
    """xwechat_files/<wxid>_<4 chars>/db_storage → wxid (WeChat 4 folder convention)."""
    folder = Path(db_root).parent.name
    head, _, tail = folder.rpartition('_')
    return head if head and len(tail) == 4 else folder


def latest_snapshot(data_root):
    base = Path(data_root) / 'snapshots' / 'windows-live'
    latest = read_json(base / 'latest.json')
    if not latest or not latest.get('snapshot_id'):
        return None
    return base / 'snapshots' / latest['snapshot_id']


def contacts(snapshot):
    db = snapshot / 'contact' / 'contact.db'
    if not db.is_file():
        return []
    con = sqlite3.connect(f'file:{db.as_posix()}?mode=ro&immutable=1', uri=True)
    try:
        cols = {r[1] for r in con.execute('PRAGMA table_info(contact)')}
        pick = [c for c in ('username', 'remark', 'nick_name', 'alias') if c in cols]
        return [dict(zip(pick, row)) for row in con.execute(f'SELECT {",".join(pick)} FROM contact')]
    finally:
        con.close()


def find_owner(rows, name):
    name = name.strip()
    hits = [r for r in rows if not str(r.get('username', '')).endswith('@chatroom')
            and name in {str(r.get(k) or '').strip() for k in ('remark', 'nick_name', 'alias', 'username')}]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        fail(f'在微信联系人里没找到「{name}」。用小鲸这个微信号给主人发一条消息（让主人出现在最近聊天里），'
             '再重跑；名字要和小鲸微信里看到的备注或昵称完全一致。')
    fail(f'「{name}」匹配到 {len(hits)} 个联系人，请改用主人的微信号（wxid_ 开头或自定义微信号）')


def main():
    p = argparse.ArgumentParser(description='dsh-whale 本地配置')
    p.add_argument('--owner', help='主人：小鲸这个微信号里看到的主人备注/昵称/微信号')
    p.add_argument('--groups', help='允许小鲸说话的群名，逗号分隔（可留空，之后在群里@她也能自动登记）')
    p.add_argument('--data-root', default=os.environ.get('WHALE_DATA_ROOT') or str(HOME / 'WhaleData'))
    p.add_argument('--runtime-root', help='Python 环境和模型所在目录（默认 <data-root>/runtime）')
    p.add_argument('--plugin', help='dsh-wxauto 插件目录（默认自动找）')
    p.add_argument('--db-root', help='微信 db_storage 目录（默认自动发现）')
    p.add_argument('--dsh-profile', default=os.environ.get('DSH_PROFILE', 'desktop'))
    p.add_argument('--gemini-key', help='Google AI Studio API key，用来看图（写进 modlens 配置）')
    p.add_argument('--breeze-key', help='BreezeBlue API key（brz_ 开头），用来说话/发语音')
    p.add_argument('--breeze-voice', help='BreezeBlue 声音 ID（voc_ 开头）')
    p.add_argument('--tikhub-token', help='TikHub token（可选，抖音/小红书只读）')
    p.add_argument('--no-voice', action='store_true', help='不启用语音收发')
    p.add_argument('--skip-capture', action='store_true', help='跳过首次快照（已经有快照时）')
    a = p.parse_args()

    plugin = find_plugin(a.plugin)
    data_root = Path(a.data_root).resolve()
    runtime_root = Path(a.runtime_root).resolve() if a.runtime_root else data_root / 'runtime'
    sys.path.insert(0, str(plugin))
    from data_paths import initialize_data_root  # noqa: E402  (plugin module)

    say(f'① 数据目录 {data_root}')
    initialize_data_root(root=str(data_root))
    for sub in ('media/images', 'media/files', 'config'):
        (data_root / sub).mkdir(parents=True, exist_ok=True)

    cfg_path = data_root / 'config' / 'wxauto.local.json'
    cfg = read_json(cfg_path, {}) or {}
    owner_name = a.owner or cfg.get('target')
    if not owner_name:
        fail('第一次配置需要 --owner "主人在微信里的名字"')

    say('② 查找已登录的微信 4.x 账号')
    found = run_capture(plugin, data_root, 'discover', *(['--db-root', a.db_root] if a.db_root else []))
    accounts = found.get('accounts') or []
    if not found.get('weixin_processes'):
        fail('微信没在运行。请先登录小鲸要用的那个微信号，再重跑')
    if len(accounts) != 1:
        fail(f'发现 {len(accounts)} 个微信账号目录，请用 --db-root 指定小鲸那个号的 db_storage')
    acct = accounts[0]
    username = account_username(acct['db_root'])
    account_id = cfg.get('accountId') or 'account:' + hashlib.sha256(username.encode()).hexdigest()[:24]
    capture = {**(cfg.get('capture') or {}), 'enabled': True, 'dbRoot': acct['db_root'], 'sourceId': acct['source_id'],
               'accountUsername': username, 'scope': 'chat', 'intervalSeconds': 300, 'includeAudio': not a.no_voice,
               'reason': 'configured by dsh-whale setup/configure.py'}
    say(f'  账号目录已找到（{acct.get("chat_databases")} 个聊天库）')

    if not a.skip_capture or not latest_snapshot(data_root):
        say('③ 拍第一份聊天快照（读取正在运行的微信的数据库密钥，只存在本机数据目录）')
        run_capture(plugin, data_root, 'capture-then-import', '--db-root', acct['db_root'], '--source-id', acct['source_id'],
                    '--account-id', account_id, '--account-username', username,
                    *(['--include-audio'] if not a.no_voice else []))
    snap = latest_snapshot(data_root)
    if not snap:
        fail('快照没有生成，请看上面的错误')

    say(f'④ 在联系人里找主人「{owner_name}」')
    owner = find_owner(contacts(snap), owner_name)
    owner_key = 'wechat:' + owner['username']
    if not owner_key[7:].replace('_', '').replace('-', '').isalnum():
        fail('主人的微信号含有不支持的字符')
    say('  找到了')

    groups = [g.strip() for g in (a.groups.split(',') if a.groups else cfg.get('groups') or []) if g.strip()]
    python = str(Path(sys.executable).resolve())
    cfg.update({
        'enabled': True,
        'target': owner_name,
        'sessionId': 'bili-whale',  # owner chat shares the 养鱼 (dsh-bili-whale) main session
        'workspace': str(DSH_HOME / 'whale-work'),
        'dshProfile': a.dsh_profile,
        'python': python,
        'dataRoot': str(data_root),
        'runtimeRoot': str(runtime_root),
        'statePath': str(data_root / 'config' / 'receipts.local.json'),
        'accountId': account_id,
        'groups': groups,
        'chatKeys': {**(cfg.get('chatKeys') or {}), owner_name: owner_key},
        'receiveImages': True,
        'imageDirectory': str(data_root / 'media' / 'images'),
        'receiveFiles': True,
        'attachmentDirectory': str(data_root / 'media' / 'files'),
        'receiveVoice': not a.no_voice,
        'capture': capture,
        'autoGroups': cfg.get('autoGroups') or {'enabled': True, 'enabledAt': time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime())},
        'nativeVoiceSources': {**(cfg.get('nativeVoiceSources') or {}), 'enabled': not a.no_voice, 'ownerNativeChatKey': owner_key},
    })
    cfg.pop('codexBin', None) if not cfg.get('codexBin') else None
    say('⑤ 写配置')
    write_json(cfg_path, cfg)
    write_json(DSH_HOME / 'dsh-whale.json', {'dataRoot': str(data_root), 'enabled': True})

    images = data_root / 'config' / 'native-images.local.json'
    if not images.exists():
        write_json(images, {'enabled': True, 'since': int(time.time()), 'note': 'photos decrypted from local WeChat attachments'})

    if a.breeze_key:
        if not a.breeze_key.startswith('brz_'):
            fail('BreezeBlue key 应该以 brz_ 开头')
        write_json(data_root / 'config' / 'breezeblue.local.json', {'apiKey': a.breeze_key}, private=True)
    voice_path = data_root / 'voice' / 'voice.local.json'
    voice = read_json(voice_path, {}) or {}
    if a.breeze_voice or (not voice and a.breeze_key):
        voice.update({'approved': True, 'backend': 'breezeblue', 'breezeVoiceId': a.breeze_voice or voice.get('breezeVoiceId'),
                      'emotionDirectionsEnabled': True, 'videoAudioEnabled': True})
        if not voice.get('breezeVoiceId'):
            fail('用 BreezeBlue 说话需要 --breeze-voice voc_xxx（在 BreezeBlue 控制台选一个声音）')
        write_json(voice_path, voice)

    if a.tikhub_token:
        write_json(data_root / 'config' / 'tikhub.local.json', {'token': a.tikhub_token, 'enabled': True}, private=True)

    if a.gemini_key:
        ml_path = HOME / '.modlens' / 'config.json'
        ml = read_json(ml_path, {}) or {}
        ml['provider'] = 'gemini-api'
        gem = ml.setdefault('providers', {}).setdefault('gemini-api', {})
        gem.update({'apiKey': a.gemini_key, 'model': gem.get('model') or 'gemini-flash-latest'})
        write_json(ml_path, ml, private=True)

    say('\n✓ 配置完成。接下来：')
    say('  1. 完全退出并重新打开 DeepSeek Harness（插件代码和配置在启动时加载）')
    say('  2. 打开 设置 → 微信连接，点「切换为原生文字接收」；群聊在同一页逐个启用')
    say('  3. 用主人的微信给小鲸发一句话测试')
    say('  4. 有问题跑：node setup/doctor.mjs')


if __name__ == '__main__':
    main()
