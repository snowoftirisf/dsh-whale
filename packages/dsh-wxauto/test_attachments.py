import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from attachment_policy import attachment_file, checked_file, local_path, safe_filename
from incoming_files import download_file


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.received = self.base / 'media' / 'chat-a' / 'files'
        self.outbox = self.base / 'media' / 'chat-a' / 'outbox'
        self.received.mkdir(parents=True)
        self.outbox.mkdir()

    def downloader(self, content=b'opaque document', name='普通文件.pdf'):
        def perform(*, dir_path, timeout):
            self.assertEqual(timeout, 30)
            destination = Path(dir_path) / name
            destination.write_bytes(content)
            return destination
        return SimpleNamespace(download=perform)

    def test_download_keeps_unique_names_hash_and_relative_path(self):
        first = download_file(self.downloader(), self.received, media_root=self.base / 'media')
        second = download_file(self.downloader(), self.received, media_root=self.base / 'media')
        self.assertNotEqual(first['path'], second['path'])
        self.assertEqual(first['sha256'], hashlib.sha256(b'opaque document').hexdigest())
        self.assertEqual(first['size'], len(b'opaque document'))
        self.assertEqual(first['name'], '普通文件.pdf')
        self.assertEqual(first['contentStatus'], 'unknown')
        self.assertEqual(first['mediaType'], 'file')
        self.assertTrue(first['relativePath'].startswith('chat-a/files/'))
        self.assertTrue(Path(first['path']).is_file())
        self.assertEqual(list((self.received / 'downloads').iterdir()), [])

    def test_download_cannot_read_outside_staging_or_unknown_library_result(self):
        outside = self.base / 'private.txt'
        outside.write_text('secret')
        for result in (outside, None, False, {'ok': True}):
            with self.subTest(result=result):
                message = SimpleNamespace(download=lambda **kwargs: result)
                with self.assertRaisesRegex(RuntimeError, 'attachment-(path-denied|download-failed)'):
                    download_file(message, self.received)
        self.assertEqual(outside.read_text(), 'secret')

    def test_empty_and_oversized_files_are_rejected(self):
        for content in (b'', b'abcd'):
            with self.assertRaisesRegex(RuntimeError, 'attachment-size-limit'):
                download_file(self.downloader(content), self.received, max_bytes=3)
        self.assertEqual(list(self.received.glob('*.pdf')), [])

    def test_ordinary_attachment_can_exceed_image_limit(self):
        result = download_file(self.downloader(b'a' * (21 * 1024 * 1024)), self.received)
        self.assertGreater(result['size'], 20 * 1024 * 1024)

    def test_chat_change_before_or_after_download_never_retains_file(self):
        calls = []
        message = SimpleNamespace(download=lambda **kwargs: calls.append(kwargs))
        with self.assertRaisesRegex(RuntimeError, 'target-mismatch'):
            download_file(message, self.received, ensure_chat=lambda: False)
        self.assertEqual(calls, [])
        checks = iter([True, False])
        with self.assertRaisesRegex(RuntimeError, 'target-mismatch'):
            download_file(self.downloader(), self.received, ensure_chat=lambda: next(checks))
        self.assertEqual(list(self.received.glob('*.pdf')), [])

    def test_download_exception_is_not_retried(self):
        def fail(**kwargs):
            raise AttributeError('preview unavailable')
        with self.assertRaisesRegex(AttributeError, 'preview unavailable'):
            download_file(SimpleNamespace(download=fail), self.received)

    def test_sdk_sidecar_cleanup_does_not_discard_a_verified_download(self):
        def perform(**kwargs):
            staging = Path(kwargs['dir_path'])
            (staging / 'sdk-sidecar').write_bytes(b'diagnostic')
            destination = staging / 'document.pdf'
            destination.write_bytes(b'received document')
            return destination
        result = download_file(SimpleNamespace(download=perform), self.received)
        self.assertTrue(result['stagingCleanupPending'])
        self.assertEqual(Path(result['path']).read_bytes(), b'received document')
        self.assertEqual(result['sha256'], hashlib.sha256(b'received document').hexdigest())
        self.assertEqual(len(list((self.received / 'downloads').glob('*/sdk-sidecar'))), 1)

    def test_receipt_registration_and_chat_scope_are_required_for_resending(self):
        file = self.received / 'a.txt'
        file.write_text('received')
        kwargs = dict(received_root=self.received, outbox_root=self.outbox)
        with self.assertRaisesRegex(RuntimeError, 'attachment-not-registered'):
            attachment_file(file, **kwargs)
        self.assertEqual(attachment_file(file, received_paths=[str(file)], **kwargs), file)
        other = self.base / 'media' / 'chat-b' / 'private.txt'
        other.parent.mkdir()
        other.write_text('other chat')
        with self.assertRaisesRegex(RuntimeError, 'attachment-path-denied'):
            attachment_file(other, received_paths=[str(other)], **kwargs)

    def test_current_chat_outbox_is_allowed_but_arbitrary_disk_files_are_not(self):
        generated = self.outbox / 'report.zip'
        generated.write_bytes(b'archive')
        kwargs = dict(received_root=self.received, outbox_root=self.outbox)
        self.assertEqual(attachment_file(generated, **kwargs), generated)
        private = self.base / 'credential.json'
        private.write_bytes(b'private')
        with self.assertRaisesRegex(RuntimeError, 'attachment-path-denied'):
            attachment_file(private, **kwargs)

    def test_filename_normalization_prevents_windows_paths_ads_and_devices(self):
        self.assertEqual(safe_filename(r'..\..\private.txt'), 'private.txt')
        self.assertEqual(safe_filename('CON.txt'), '_CON.txt')
        self.assertEqual(safe_filename('a.txt:secret'), 'a.txt_secret')
        self.assertEqual(safe_filename(' . '), 'attachment')
        self.assertLessEqual(len(safe_filename('字' * 200)), 140)

    def test_path_traversal_and_unc_are_rejected(self):
        for path in (self.received / '..' / 'private.txt', r'\\server\share\file.txt'):
            with self.assertRaisesRegex(RuntimeError, 'attachment-path-denied'):
                local_path(path)

    def test_symlink_root_or_file_cannot_read_private_data(self):
        private = self.base / 'private.txt'
        private.write_text('secret')
        link = self.outbox / 'link.txt'
        try:
            link.symlink_to(private)
        except OSError:
            self.skipTest('OS does not grant symlink creation')
        with self.assertRaisesRegex(RuntimeError, 'attachment-path-denied'):
            attachment_file(link, received_root=self.received, outbox_root=self.outbox)

    def test_reparse_attribute_is_rejected_even_without_symlink_bit(self):
        ordinary = Path.lstat
        file = self.outbox / 'junction-child.txt'
        file.write_text('data')
        def lstat(path, **kwargs):
            value = ordinary(path, **kwargs)
            if path == self.outbox:
                return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=1024)
            return value
        with patch.object(Path, 'lstat', lstat):
            with self.assertRaisesRegex(RuntimeError, 'attachment-path-denied'):
                checked_file(file, self.outbox)

    def test_hardlinked_private_file_cannot_be_sent_from_outbox(self):
        private = self.base / 'private.txt'
        private.write_text('secret')
        link = self.outbox / 'hardlink.txt'
        try:
            link.hardlink_to(private)
        except OSError:
            self.skipTest('OS does not grant hardlink creation')
        with self.assertRaisesRegex(RuntimeError, 'attachment-path-denied'):
            attachment_file(link, received_root=self.received, outbox_root=self.outbox)


if __name__ == '__main__':
    unittest.main()
