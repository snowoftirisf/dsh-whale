"""Pinned source-available reference modules; no live capture runs on import."""
import hashlib
import importlib.util
import json
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

ROOT=Path(__file__).resolve().parent
COMMIT='d176934a3b201a6ae0bfe94daafdf67a1d8860e3'
_LOCK=threading.RLock()
_MODULES={}
PURE=('wechat_schema','message_display','snapshot_reader')
CAPTURE=PURE+('windows_crypto','windows_wal','windows_native','windows_keyscan','windows_live')


def verify(names=CAPTURE):
    manifest=json.loads((ROOT/'vendor-lock.json').read_text(encoding='utf8'))
    if manifest.get('commit')!=COMMIT:raise RuntimeError('vendor-commit-mismatch')
    entries={r['path']:r for r in manifest['files']}
    for name in names:
        relative=name+'.py';file=ROOT/relative
        if file.is_symlink() or not file.is_file() or relative not in entries:
            raise RuntimeError('vendor-module-unavailable')
        if hashlib.sha256(file.read_bytes()).hexdigest()!=entries[relative]['sha256']:
            raise RuntimeError('vendor-source-checksum-mismatch')
    return True


@contextmanager
def module_context(capture=False):
    """Supply upstream absolute imports only for this serialized operation."""
    names=CAPTURE if capture else PURE
    with _LOCK:
        verify(names)
        previous={name:sys.modules.get(name) for name in names}
        try:
            for name in names:
                if name not in _MODULES:
                    spec=importlib.util.spec_from_file_location('_whale_vendored_'+name,ROOT/(name+'.py'))
                    module=importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    _MODULES[name]=module
                sys.modules[name]=_MODULES[name]
            yield _MODULES
        finally:
            for name,value in previous.items():
                if value is None:sys.modules.pop(name,None)
                else:sys.modules[name]=value


def reader():
    with module_context() as modules:return modules['snapshot_reader']
