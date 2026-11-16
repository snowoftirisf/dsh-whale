"""Download a qualified incoming FileMessage through wxauto, never by UI scripts."""
import hashlib
import os
import uuid
from pathlib import Path

from attachment_policy import MAX_ATTACHMENT_BYTES, checked_file, local_path, safe_filename


def download_file(message, directory, ensure_chat=lambda: True, *, media_root=None,
                  max_bytes=MAX_ATTACHMENT_BYTES, download_timeout=30):
    """Return file metadata only; an attached file's content remains unknown.

    The caller qualifies sender/message and owns the single wxauto UI queue.
    directory must be this chat's inbound-files directory; media_root optionally
    supplies the migration-safe relativePath root. No process-global save path
    is changed, and the library must honor its explicit dir_path argument.
    """
    if not ensure_chat():
        raise RuntimeError('target-mismatch')
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
        raise RuntimeError('attachment-limit-invalid')
    root = local_path(directory)
    media = local_path(media_root) if media_root is not None else root
    if not root.is_relative_to(media):
        raise RuntimeError('attachment-path-denied')
    root.mkdir(parents=True, exist_ok=True)
    staging = local_path(root / 'downloads' / uuid.uuid4().hex)
    staging.mkdir(parents=True)
    # Download exceptions intentionally propagate for adapter diagnostics. No
    # broad retry: a recreated message row may refer to a different attachment.
    source = message.download(dir_path=str(staging), timeout=download_timeout)
    if not ensure_chat():
        raise RuntimeError('target-mismatch')
    if not isinstance(source, (str, Path)):
        raise RuntimeError('attachment-download-failed')
    source = checked_file(source, staging, max_bytes)
    before = source.stat()
    name = safe_filename(source.name)
    target = root / (uuid.uuid4().hex + '-' + name)
    digest = hashlib.sha256()
    size = 0
    try:
        # Exclusive target creation prevents clobbering an existing attachment.
        with source.open('rb') as incoming, target.open('xb') as output:
            while block := incoming.read(1024 * 1024):
                size += len(block)
                if size > max_bytes:
                    raise RuntimeError('attachment-size-limit')
                output.write(block)
                digest.update(block)
            output.flush()
            os.fsync(output.fileno())
        after_file = checked_file(source, staging, max_bytes)
        after = after_file.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino) or size != before.st_size:
            raise RuntimeError('attachment-changed-during-copy')
        if not ensure_chat():
            raise RuntimeError('target-mismatch')
        checked_file(target, root, max_bytes)
    except BaseException:
        # Only remove our unique, locally created copy; never remove external
        # paths returned by a failed or incompatible wxauto downloader.
        target.unlink(missing_ok=True)
        raise
    # A library sidecar or cleanup failure must not turn a verified retained
    # attachment into a failed message. Never recursively remove SDK output.
    cleanup_pending = False
    try:
        source.unlink()
        staging.rmdir()
    except OSError:
        cleanup_pending = True
    result = {'path': str(target), 'relativePath': target.relative_to(media).as_posix(),
            'name': name, 'size': size, 'sha256': digest.hexdigest(),
            'source': 'wechat-file-download', 'mediaType': 'file', 'contentStatus': 'unknown'}
    if cleanup_pending:
        result['stagingCleanupPending'] = True
    return result
