"""Trusted, persistent per-chat registration for received attachments."""
import hashlib
import json
import os
import uuid
from pathlib import Path

from attachment_policy import MAX_ATTACHMENT_BYTES, checked_file, local_path
from data_paths import absolute_data_path, relative_data_path


def chat_key(target):
    return hashlib.sha256(target.encode('utf8')).hexdigest()[:16]


class AttachmentRegistry:
    def __init__(self, file, data_root, attachment_root, routes):
        self.file = local_path(file)
        self.root = local_path(data_root)
        self.attachment_root = local_path(attachment_root)
        if not self.file.is_relative_to(self.root) or not self.attachment_root.is_relative_to(self.root):
            raise RuntimeError('attachment-path-denied')
        self.routes = set(routes)
        self.data = {'version': 1, 'chats': {}}
        if self.file.exists():
            value = json.loads(self.file.read_text('utf-8-sig'))
            if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('chats'), dict):
                raise RuntimeError('attachment-registry-invalid')
            for target, entries in value['chats'].items():
                if not isinstance(entries, dict):
                    raise RuntimeError('attachment-registry-invalid')
                for relative, metadata in entries.items():
                    if not isinstance(metadata, dict) or not isinstance(relative, str):
                        raise RuntimeError('attachment-registry-invalid')
                    path = absolute_data_path(relative, root=self.root)
                    if path == self.chat_root(target) or not path.is_relative_to(self.chat_root(target)):
                        raise RuntimeError('attachment-registry-invalid')
            self.data = value

    def chat_root(self, target):
        return self.attachment_root / chat_key(target)

    def _identity(self, file, target):
        """Read the actual bytes; a path alone is not a receipt's identity."""
        file = checked_file(file, self.chat_root(target))
        before = file.stat()
        digest = hashlib.sha256()
        size = 0
        with file.open('rb') as source:
            opened = os.fstat(source.fileno())
            for block in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(block)
                size += len(block)
                if size > MAX_ATTACHMENT_BYTES:
                    raise RuntimeError('attachment-size-limit')
        after = checked_file(file, self.chat_root(target)).stat()
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if identity(before) != identity(opened) or identity(before) != identity(after) or size != after.st_size:
            raise RuntimeError('attachment-file-changed')
        return size, digest.hexdigest()

    def register(self, target, metadata):
        if target not in self.routes:
            raise RuntimeError('attachment-target-denied')
        file = checked_file(metadata.get('path'), self.chat_root(target))
        relative = relative_data_path(file, root=self.root)
        size, digest = self._identity(file, target)
        if ('size' in metadata and metadata['size'] != size
                or 'sha256' in metadata and metadata['sha256'] != digest):
            raise RuntimeError('attachment-receipt-mismatch')
        result = {'size': size, 'sha256': digest}
        if 'name' in metadata:
            result['name'] = metadata['name']
        result['relativePath'] = relative
        previous = self.data['chats'].get(target, {}).get(relative)
        self.data['chats'].setdefault(target, {})[relative] = result
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            temporary = local_path(self.file.with_name(self.file.name + '.' + uuid.uuid4().hex + '.tmp'))
            with temporary.open('x', encoding='utf8') as output:
                json.dump(self.data, output, ensure_ascii=False, indent=2)
                output.write('\n')
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.file)
        except BaseException:
            if previous is None:
                self.data['chats'][target].pop(relative, None)
            else:
                self.data['chats'][target][relative] = previous
            raise
        return result

    def received_paths(self, target, filepath=None):
        if target not in self.routes:
            raise RuntimeError('attachment-target-denied')
        # Optional selection avoids rehashing unrelated large received files.
        # Legacy receipts without an actual hash cannot authorize a resend.
        selected = local_path(filepath) if filepath is not None else None
        paths = []
        for relative, receipt in self.data['chats'].get(target, {}).items():
            path = absolute_data_path(relative, root=self.root)
            if selected is not None and path != selected:
                continue
            try:
                size, digest = self._identity(path, target)
            except (OSError, RuntimeError):
                continue
            if receipt.get('size') == size and receipt.get('sha256') == digest:
                paths.append(path)
        return paths
