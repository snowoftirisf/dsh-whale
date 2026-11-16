"""Create an additive, private migration backup; never overwrite source data."""
import argparse
import datetime
import hashlib
import json
import os
import shutil
from pathlib import Path


def copy_tree(source, destination):
    if not source.exists():
        return
    if destination.exists():
        raise RuntimeError('backup-destination-exists')
    if source.is_dir():
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns('__pycache__', '.git', 'node_modules'))
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default=os.environ.get('WHALE_DATA_ROOT', os.path.join(os.path.expanduser('~'), 'WhaleData')))
    parser.add_argument('--phase', choices=['configuration', 'wechat-stopped'], required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if root == Path(root.anchor):
        raise ValueError('data-root-cannot-be-drive-root')
    home = Path.home()
    project = Path(__import__('os').environ.get('WHALE_RUNTIME_ROOT') or Path(__file__).resolve().parents[2])
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target = root / 'backups' / (args.phase + '-' + stamp)
    target.mkdir(parents=True, exist_ok=False)
    if args.phase == 'configuration':
        sources = {
            'wxauto': project / 'plugins/dsh-wxauto',
            'profile-manifest.json': home / '.dsh/profiles/desktop/package.json',
            'profile-patch.yml': home / '.dsh/profiles/desktop/cordis.patch.yml',
            'memory': home / '.dsh/memories',
            'persona': home / '.bili-ai/persona.md',
            'persona-history': home / '.bili-ai/persona-history',
            'workspace': home / '.dsh/whale-work',
            'bili-config.json': home / '.dsh/bili-whale.config.json',
            'speak-up.json': home / '.dsh/speak-up-config.json',
            'voice-selection.json': project / 'voice-data/voice.local.json',
        }
    else:
        # Caller must have verified all Weixin processes exited normally first.
        sources = {
            'xwechat_files': home / 'Documents/xwechat_files',
            'wechat-roaming': home / 'AppData/Roaming/Tencent/xwechat',
        }
    for label, source in sources.items():
        copy_tree(source, target / label)
    entries = []
    for file in target.rglob('*'):
        if file.is_file():
            h = hashlib.sha256()
            with file.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    h.update(chunk)
            entries.append({'path': file.relative_to(target).as_posix(), 'bytes': file.stat().st_size, 'sha256': h.hexdigest()})
    (target / 'backup-manifest.json').write_text(json.dumps({'version': 1, 'phase': args.phase,
        'createdAt': stamp, 'files': entries}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'backup': str(target), 'files': len(entries), 'bytes': sum(e['bytes'] for e in entries)}))


if __name__ == '__main__':
    main()
