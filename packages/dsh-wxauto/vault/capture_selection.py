"""Explicit bounded audio DB extension; no acquisition, keys or history writer."""
import json
import os
from pathlib import Path
import re
import stat
from .snapshot_import import load_reader
from .store import VaultError, digest_file

MEDIA = re.compile(r'media_[0-9]+\.db')
MAX_AUDIO_DATABASES = 64


def validate_audio_flag(include_audio, scope):
    if type(include_audio) is not bool:
        raise VaultError('capture-include-audio-must-be-bool')
    if include_audio and scope != 'chat':
        raise VaultError('capture-audio-requires-chat-scope')


def _safe(path, root, *, directory=False):
    path, root = Path(os.path.abspath(path)), Path(os.path.abspath(root))
    if str(path).startswith(('\\\\', '//')) or not path.is_relative_to(root):
        raise VaultError('capture-audio-path-denied')
    for item in (path, *path.parents):
        info = item.lstat()
        if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
                or (hasattr(item, 'is_junction') and item.is_junction())):
            raise VaultError('capture-audio-link-denied')
    info = path.stat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise VaultError('capture-audio-file-type-invalid')
    if not directory and info.st_nlink != 1:
        raise VaultError('capture-audio-link-denied')
    return path


def select_capture_files(collector, root, base_files, *, scope='chat', include_audio=False):
    validate_audio_flag(include_audio, scope)
    if not include_audio:
        return (collector.selected_files(root, scope='all') if scope == 'all' else base_files), []
    root = _safe(root, root, directory=True)
    directory = _safe(root / 'message', root, directory=True)
    audio = []
    for path in sorted(directory.iterdir()):
        if MEDIA.fullmatch(path.name):
            audio.append(_safe(path, root))
            if len(audio) > MAX_AUDIO_DATABASES:
                raise VaultError('capture-audio-database-budget')
    if not audio:
        raise VaultError('capture-audio-databases-missing')
    files = [_safe(path, root) for path in base_files]
    files.extend(path for path in audio if path not in files)
    return files, [path.relative_to(root).as_posix() for path in audio]


def finalize_audio_capture(snapshot, receipt, audio_names, writer):
    """Authenticate actual detached extra files, annotate, return fingerprint paths.

    Vendor scope stays chat. All selected audio files need authenticated hash
    records and valid standalone SQLite; metadata never asserts media decoding.
    """
    if not audio_names:
        return []
    if len(audio_names) > MAX_AUDIO_DATABASES or len(set(audio_names)) != len(audio_names):
        raise VaultError('capture-audio-metadata-invalid')
    reader = load_reader()
    root = reader.secure_snapshot_root(Path(snapshot))
    manifest_path = _safe(root / 'snapshot-manifest.json', root)
    manifest = json.loads(manifest_path.read_text(encoding='utf8'))
    records = manifest.get('databases')
    if not isinstance(records, list):
        raise VaultError('capture-audio-authentication-missing')
    files = []
    for name in audio_names:
        if not isinstance(name, str) or not re.fullmatch(r'message/media_[0-9]+\.db', name):
            raise VaultError('capture-audio-metadata-invalid')
        file = _safe(root / name, root)
        for suffix in ('-wal', '-shm', '-journal'):
            if os.path.lexists(str(file) + suffix):
                raise VaultError('capture-audio-snapshot-sidecar-denied')
        matches = [entry for entry in records if isinstance(entry, dict) and entry.get('database') == name]
        if len(matches) != 1 or matches[0].get('authenticated') is not True:
            raise VaultError('capture-audio-authentication-missing')
        expected = matches[0].get('plaintext_sha256')
        if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected) or digest_file(file) != expected:
            raise VaultError('capture-audio-checksum-mismatch')
        with reader.connect_read_only(file) as db:
            if [row[0] for row in db.execute('PRAGMA integrity_check')] != ['ok']:
                raise VaultError('capture-audio-integrity-failed')
        files.append(file)
    metadata = {'schemaVersion': 1, 'enabled': True, 'scope': 'chat', 'databases': list(audio_names)}
    manifest['audio_capture'] = metadata
    writer(manifest_path, manifest)
    receipt['audio_capture'] = metadata
    return files
