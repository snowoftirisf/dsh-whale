"""Filesystem policy for ordinary attachments; independent of WeChat UI."""
import os
import re
import stat
import unicodedata
from pathlib import Path

MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024
_RESERVED = re.compile(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', re.I)


def safe_filename(value):
    """Keep a displayable basename, without Windows paths, ADS or device names."""
    value = unicodedata.normalize('NFKC', str(value)).replace('\\', '/').split('/')[-1]
    value = ''.join('_' if ord(c) < 32 or c in '<>:"/\\|?*' else c for c in value)
    value = value.strip().strip('. ') or 'attachment'
    if _RESERVED.match(value):
        value = '_' + value
    # Leave room for the unique prefix and for the directory part on Windows.
    return value[:140].rstrip('. ') or 'attachment'


def local_path(value):
    """Absolute lexical path. Reject traversal, UNC and all existing links."""
    try:
        path = Path(value).expanduser()
        raw = os.fspath(path)
        if raw.startswith(('\\\\', '//')) or '..' in path.parts or '\x00' in raw:
            raise ValueError('unsafe path')
        path = Path(os.path.abspath(path))
        for component in (*reversed(path.parents), path):
            try:
                info = component.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError('linked path')
        return path
    except (TypeError, ValueError, OSError) as error:
        raise RuntimeError('attachment-path-denied') from error


def checked_file(filepath, root, max_bytes=MAX_ATTACHMENT_BYTES):
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
        raise RuntimeError('attachment-limit-invalid')
    root = local_path(root)
    file = local_path(filepath)
    if file == root or not file.is_relative_to(root):
        raise RuntimeError('attachment-path-denied')
    try:
        info = file.stat()
    except OSError as error:
        raise RuntimeError('attachment-file-unavailable') from error
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError('attachment-file-unavailable')
    if info.st_nlink > 1:
        raise RuntimeError('attachment-path-denied')
    if not 0 < info.st_size <= max_bytes:
        raise RuntimeError('attachment-size-limit')
    return file


def attachment_file(filepath, *, received_root, outbox_root, received_paths=(), max_bytes=MAX_ATTACHMENT_BYTES):
    """Send only a registered received file or a file from this chat's outbox.

    All roots and received_paths must come from trusted adapter state, never
    from model-supplied tool arguments. Call immediately before wxauto SendFiles.
    """
    file = local_path(filepath)
    outbox = local_path(outbox_root)
    if file != outbox and file.is_relative_to(outbox):
        return checked_file(file, outbox, max_bytes)
    received = local_path(received_root)
    file = checked_file(file, received, max_bytes)
    registered = {local_path(p) for p in received_paths}
    if file not in registered:
        raise RuntimeError('attachment-not-registered')
    return file
