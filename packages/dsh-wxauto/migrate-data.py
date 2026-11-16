"""Copy-only, opt-in migration into WhaleData; dry-run is the default.

Never edits the active configuration, source data or WeChat credential stores.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

from attachment_policy import local_path
from data_paths import initialize_data_root, read_data_manifest, resolve_data_paths

EXCLUDED = ('credentials', 'credential', 'secrets', 'cookies', 'api-key', 'api_key', 'access-token', 'access_token')


def sha256(file):
    result = hashlib.sha256()
    with file.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def replace_paths(text, mapping):
    for old, new in sorted(mapping.items(), key=lambda item: len(item[0]), reverse=True):
        variants = {old, old.replace('\\', '/'), old.replace('/', '\\')}
        for variant in variants:
            text = text.replace(variant, new)
    return text


def clean_config(value):
    if isinstance(value, dict):
        return {key: clean_config(item) for key, item in value.items()
                if not any(word in key.lower() for word in ('token', 'secret', 'password', 'credential', 'cookie', 'apikey', 'api_key'))}
    if isinstance(value, list):
        return [clean_config(item) for item in value]
    return value


class Migration:
    def __init__(self, project_root, home, data_root):
        self.project = local_path(project_root)
        self.home = local_path(home)
        self.paths = resolve_data_paths(root=data_root)
        self.root = self.paths['root']
        self.entries = []
        self.mapping = {}
        self.warnings = []
        self._targets = set()

    def add(self, source, relative, content=None):
        source = local_path(source)
        if not source.is_file():
            raise RuntimeError('migration-source-missing: ' + str(source))
        target = local_path(self.root / relative)
        if not target.is_relative_to(self.root) or target == self.root:
            raise RuntimeError('migration-target-outside-root')
        if str(target).lower() in self._targets:
            raise RuntimeError('migration-duplicate-target')
        self._targets.add(str(target).lower())
        info = source.stat()
        if info.st_nlink > 1:
            raise RuntimeError('migration-source-hardlink-denied')
        digest = hashlib.sha256(content).hexdigest() if content is not None else sha256(source)
        size = len(content) if content is not None else info.st_size
        entry = {'source': str(source), 'target': str(target), 'relativePath': relative,
                 'sha256': digest, 'size': size, 'sourceSha256': sha256(source), '_content': content}
        self.entries.append(entry)
        self.mapping[str(source)] = str(target)

    def tree(self, source, destination):
        source = local_path(source)
        if not source.exists():
            self.warnings.append('optional source missing: ' + str(source))
            return
        # os.walk is only enumeration; every directory and file is separately
        # checked before it can become a copy source.
        for folder, directories, files in os.walk(source, followlinks=False):
            local_path(folder)
            directories[:] = sorted(name for name in directories if name not in ('__pycache__', '.git'))
            for name in directories:
                local_path(Path(folder) / name)
            for name in sorted(files):
                if any(word in name.lower() for word in EXCLUDED) or name.endswith(('-wal', '-shm', '.tmp', '.lock')):
                    self.warnings.append('excluded nonportable/credential file: ' + name)
                    continue
                file = Path(folder) / name
                self.add(file, (Path(destination) / file.relative_to(source)).as_posix())

    def build(self):
        read_data_manifest(root=self.root)
        memories = self.home / '.dsh' / 'memories'
        persona_root = self.home / '.bili-ai'
        workspace = self.home / '.dsh' / 'whale-work'
        self.mapping.update({str(memories): str(self.paths['memory']),
                             str(self.project / 'wx-media'): str(self.paths['media'] / 'legacy'),
                             str(persona_root / 'persona-history'): str(self.paths['persona'] / 'history'),
                             str(persona_root / 'persona.md'): str(self.paths['persona'] / 'persona.md')})
        self.tree(memories, 'memory')
        self.tree(persona_root / 'persona-history', 'persona/history')
        self.tree(self.project / 'wx-media', 'media/legacy')
        for filename in ('whale-diary.md', 'whale-diary-private.md'):
            file = self.home / '.dsh' / filename
            if file.is_file():
                self.add(file, 'memory/diaries/' + filename)
        persona = persona_root / 'persona.md'
        if persona.is_file():
            self.add(persona, 'persona/persona.md', replace_paths(persona.read_text('utf-8-sig'), self.mapping).encode('utf8'))
        for filename in ('AGENTS.md', 'MEMORY.md'):
            file = workspace / filename
            if file.is_file():
                self.add(file, 'config/workspace/' + filename,
                         replace_paths(file.read_text('utf-8-sig'), self.mapping).encode('utf8'))

        voice_file = self.project / 'voice-data' / 'voice.local.json'
        if voice_file.is_file():
            voice = clean_config(json.loads(voice_file.read_text('utf-8-sig')))
            reference = voice.get('referenceAudio')
            if reference:
                source = local_path(reference if Path(reference).is_absolute() else self.project / 'voice-data' / reference)
                # Only explicitly referenced waveforms are copied, not model weights.
                if source.suffix.lower() not in ('.wav', '.flac', '.mp3', '.ogg', '.m4a'):
                    raise RuntimeError('migration-reference-format-denied')
                relative = 'voice/references/' + sha256(source)[:16] + source.suffix.lower()
                self.add(source, relative)
                voice['referenceAudio'] = relative
                voice['referenceAudioBase'] = 'dataRoot'
            self.add(voice_file, 'voice/voice.local.json', json_bytes(voice))

        adapter_dir = self.project / 'plugins' / 'dsh-wxauto'
        config_file = adapter_dir / 'config.local.json'
        if config_file.is_file():
            config = clean_config(json.loads(config_file.read_text('utf-8-sig')))
            config.update(dataRoot=str(self.root), imageDirectory=str(self.paths['media'] / 'images'),
                          statePath=str(self.paths['config'] / 'receipts.local.json'),
                          workspaceMigrationTarget=str(self.paths['config'] / 'workspace'))
            self.add(config_file, 'config/wxauto.local.json', json_bytes(config))
            self.warnings.append('exported config is inactive; workspace and Python/Codex/meme runtime paths require deliberate runtime binding')
        receipt = adapter_dir / 'receipts.local.json'
        if receipt.is_file():
            self.add(receipt, 'config/receipts.local.json')
        self.warnings.append('model weights and WeChat accounts/login credentials are not migrated')
        return self

    def validate_targets(self):
        for entry in self.entries:
            target = local_path(entry['target'])
            if target.exists() and (not target.is_file() or sha256(target) != entry['sha256']):
                raise RuntimeError('migration-existing-file-conflict: ' + entry['relativePath'])
        receipt = local_path(self.paths['config'] / 'migration-receipt.json')
        if receipt.exists() and receipt.read_bytes() != json_bytes(self.report(executed=True)):
            raise RuntimeError('migration-existing-receipt-conflict')

    def report(self, *, executed=False):
        return {'format': 'whale-migration', 'version': 1, 'complete': executed,
                'dryRun': not executed, 'dataRoot': str(self.root),
                'files': [{key: value for key, value in entry.items() if not key.startswith('_')} for entry in self.entries],
                'pathMapping': self.mapping.copy(), 'warnings': list(self.warnings)}

    def execute(self):
        self.validate_targets()
        # Report conflicts before creating any destination. Source revalidation
        # prevents a plan produced from stale bytes from being published complete.
        for entry in self.entries:
            if sha256(local_path(entry['source'])) != entry['sourceSha256']:
                raise RuntimeError('migration-source-changed')
        initialize_data_root(root=self.root)
        for entry in self.entries:
            target = local_path(entry['target'])
            if target.exists():
                continue  # preflight already verified identical bytes
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as output:
                if entry['_content'] is not None:
                    output.write(entry['_content'])
                else:
                    with local_path(entry['source']).open('rb') as source:
                        for block in iter(lambda: source.read(1024 * 1024), b''):
                            output.write(block)
                output.flush()
                os.fsync(output.fileno())
            if sha256(target) != entry['sha256']:
                raise RuntimeError('migration-copy-verification-failed')
        value = self.report(executed=True)
        receipt = self.paths['config'] / 'migration-receipt.json'
        body = json_bytes(value)
        if receipt.exists():
            if receipt.read_bytes() != body:
                raise RuntimeError('migration-existing-receipt-conflict')
        else:
            with receipt.open('xb') as output:
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
        return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', default=str(Path(__import__('os').environ.get('WHALE_RUNTIME_ROOT') or Path(__file__).resolve().parents[2])))
    parser.add_argument('--home', default=str(Path.home()))
    parser.add_argument('--data-root', default=os.environ.get('WHALE_DATA_ROOT', os.path.join(os.path.expanduser('~'), 'WhaleData')))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--execute', action='store_true', help='copy and verify; sources remain unchanged')
    mode.add_argument('--dry-run', action='store_true', help='default: print plan without writing')
    args = parser.parse_args(argv)
    try:
        plan = Migration(args.project_root, args.home, args.data_root).build()
        plan.validate_targets()
        result = plan.execute() if args.execute else plan.report()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (RuntimeError, ValueError, OSError) as error:
        print(json.dumps({'complete': False, 'error': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
