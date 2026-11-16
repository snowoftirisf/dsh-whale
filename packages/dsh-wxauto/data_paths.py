"""Portable, versioned data-root layout shared by migration and runtime code."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from attachment_policy import local_path

DEFAULT_DATA_ROOT = os.environ.get('WHALE_DATA_ROOT') or os.path.join(os.path.expanduser('~'), 'WhaleData').replace('\\', '/')
SCHEMA_VERSION = 2
LAYOUT = {name: name for name in ('config', 'vault', 'media', 'memory', 'persona', 'voice',
                                  'outbox', 'snapshots', 'backups')}
LAYOUT['database'] = 'vault/whale.sqlite'


def resolve_data_paths(config=None, *, root=None):
    config = config or {}
    if not isinstance(config, dict):
        raise RuntimeError('invalid-data-config')
    selected = root if root is not None else config.get('dataRoot', DEFAULT_DATA_ROOT)
    if not isinstance(selected, (str, Path)) or not str(selected).strip():
        raise RuntimeError('invalid-data-root')
    base = local_path(selected)
    return {'root': base, 'manifest': base / 'manifest.json',
            **{name: base / relative for name, relative in LAYOUT.items()}}


def relative_data_path(filepath, config=None, *, root=None):
    base = resolve_data_paths(config, root=root)['root']
    file = local_path(filepath)
    if file == base or not file.is_relative_to(base):
        raise RuntimeError('data-path-outside-root')
    return file.relative_to(base).as_posix()


def absolute_data_path(relative, config=None, *, root=None):
    if not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative:
        raise RuntimeError('invalid-relative-data-path')
    value = PurePosixPath(relative)
    if value.is_absolute() or '..' in value.parts or relative in ('.', ''):
        raise RuntimeError('invalid-relative-data-path')
    base = resolve_data_paths(config, root=root)['root']
    result = local_path(base.joinpath(*value.parts))
    relative_data_path(result, root=base)
    return result


def validate_manifest(value):
    if not isinstance(value, dict) or value.get('format') != 'whale-data':
        raise RuntimeError('unsupported-data-manifest')
    version = value.get('schemaVersion')
    if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= SCHEMA_VERSION:
        raise RuntimeError('unsupported-data-manifest')
    paths = value.get('paths', {})
    if not isinstance(paths, dict):
        raise RuntimeError('invalid-data-manifest-paths')
    for name, path in paths.items():
        if name not in LAYOUT or path != LAYOUT[name]:
            raise RuntimeError('invalid-data-manifest-paths')
    return {**value, 'paths': {**LAYOUT, **paths}}


def read_data_manifest(config=None, *, root=None, required=False):
    paths = resolve_data_paths(config, root=root)
    local_path(paths['manifest'])
    if not paths['manifest'].exists():
        if required:
            raise RuntimeError('data-manifest-missing')
        return None
    return validate_manifest(json.loads(paths['manifest'].read_text(encoding='utf-8-sig')))


def initialize_data_root(config=None, *, root=None):
    paths = resolve_data_paths(config, root=root)
    existing = read_data_manifest(root=paths['root'])
    value = existing or {'format': 'whale-data', 'schemaVersion': SCHEMA_VERSION,
                         'createdAt': datetime.now(timezone.utc).isoformat(), 'paths': LAYOUT.copy()}
    for name in ('root', *[k for k in LAYOUT if k != 'database']):
        local_path(paths[name]).mkdir(parents=True, exist_ok=True)
    if existing is None:
        # A preexisting root is allowed, but an existing manifest is never replaced.
        with paths['manifest'].open('x', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
    return paths
