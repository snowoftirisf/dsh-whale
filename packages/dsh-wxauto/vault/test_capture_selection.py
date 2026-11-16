import json
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from .capture import CaptureService, SyncLock
from .capture_worker import CandidateWorker
from .capture_selection import select_capture_files
from .snapshot_import import import_snapshot
from .store import VaultError, digest_file
from .test_capture import FakeCollector


class AudioCollector(FakeCollector):
    def __init__(self, source):
        super().__init__(source)
        self.actual_files = []
        self.actual_scope = None
        self.all_called = False

    def selected_files(self, root, scope='chat'):
        self.all_called = True
        return super().selected_files(root, scope)

    def capture(self, root, files, pids, **kwargs):
        self.actual_files = list(files)
        self.actual_scope = kwargs['scope']
        receipt = super().capture(root, files, pids, **kwargs)
        snapshot = Path(receipt['snapshot'])
        for file in files:
            if file.name.startswith('media_'):
                shutil.copyfile(file, snapshot / 'message' / file.name)
        manifest_path = snapshot / 'snapshot-manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf8'))
        manifest['databases'] = [{'database': file.relative_to(snapshot).as_posix(), 'authenticated': True,
                                  'plaintext_sha256': digest_file(file)} for file in snapshot.rglob('*.db')]
        manifest_path.write_text(json.dumps(manifest), encoding='utf8')
        return receipt


class AudioCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.temp.name)
        self.source = self.root / 'db_storage'
        (self.source / 'message').mkdir(parents=True)
        (self.source / 'message.db').write_bytes(b'synthetic base source')
        self.audio = self.source / 'message/media_0.db'
        with closing(sqlite3.connect(self.audio)) as db:
            db.execute('CREATE TABLE audio_fixture(server_id INTEGER, data BLOB)')
            db.execute('INSERT INTO audio_fixture VALUES(1,?)', (b'fixture audio only',))
            db.commit()
        (self.source / 'message/message_resource.db').write_bytes(b'not selected')
        (self.source / 'favorite').mkdir()
        (self.source / 'favorite/favorite.db').write_bytes(b'not selected')
        self.fake = AudioCollector(self.source)
        self.args = {'db_root': str(self.source), 'source_id': 'fixture-source', 'account_id': 'trusted-account'}
        self.service = CaptureService(self.root / 'service', collector=self.fake)
        self.worker = CandidateWorker(self.root / 'candidate', collector=self.fake)

    def tearDown(self):
        self.worker.abort()
        self.temp.cleanup()

    def test_default_no_extension_and_opt_in_exact_media_only_not_all(self):
        result = self.service.capture_then_import(**self.args)
        self.assertNotIn('audio_capture', result)
        self.assertEqual(self.fake.actual_files, [self.source / 'message.db'])
        result = self.service.capture_then_import(**self.args, include_audio=True)
        self.assertEqual(self.fake.actual_files, [self.source / 'message.db', self.audio])
        self.assertEqual(self.fake.actual_scope, 'chat')
        self.assertFalse(self.fake.all_called)
        expected = {'schemaVersion': 1, 'enabled': True, 'scope': 'chat', 'databases': ['message/media_0.db']}
        self.assertEqual(result['audio_capture'], expected)
        manifest = json.loads((Path(result['snapshot']) / 'snapshot-manifest.json').read_text(encoding='utf8'))
        self.assertEqual(manifest['audio_capture'], expected)
        self.assertEqual(manifest['profile'], 'windows-chat-capture-v1')
        self.assertTrue(any(entry['database'] == 'message/media_0.db' and entry['authenticated'] for entry in manifest['databases']))

    def test_strict_flag_and_scope_rejected_before_capture_for_both_paths(self):
        for obj in (self.service.capture_then_import, self.worker.collect):
            for value in (None, 0, 1, 'true', {}, []):
                with self.subTest(value=value, method=obj.__name__):
                    with self.assertRaisesRegex(VaultError, 'must-be-bool'):
                        obj(**self.args, include_audio=value)
            with self.assertRaisesRegex(VaultError, 'requires-chat-scope'):
                obj(**self.args, include_audio=True, scope='all')
        self.assertEqual(self.fake.calls, 0)

    def test_audio_selection_missing_budget_and_link_are_explicit_failures(self):
        self.audio.unlink()
        with self.assertRaisesRegex(VaultError, 'databases-missing'):
            select_capture_files(self.fake, self.source, [], include_audio=True)
        for index in range(65):
            (self.audio.parent / f'media_{index}.db').write_bytes(b'fixture')
        with self.assertRaisesRegex(VaultError, 'database-budget'):
            select_capture_files(self.fake, self.source, [], include_audio=True)
        original = Path.lstat
        def reparse(path):
            return NS(st_mode=stat.S_IFREG, st_file_attributes=0x400) if path == self.audio else original(path)
        with patch.object(Path, 'lstat', reparse):
            with self.assertRaisesRegex(VaultError, 'link-denied'):
                select_capture_files(self.fake, self.source, [], include_audio=True)

    def test_path_escape_and_directory_named_media_rejected(self):
        with self.assertRaisesRegex(VaultError, 'path-denied'):
            select_capture_files(self.fake, self.source, [self.root / 'outside.db'], include_audio=True)
        self.audio.unlink()
        self.audio.mkdir()
        with self.assertRaisesRegex(VaultError, 'file-type-invalid'):
            select_capture_files(self.fake, self.source, [], include_audio=True)

    def test_candidate_metadata_audio_fingerprint_and_commit_tamper_without_writer(self):
        with patch('vault.capture.Vault', side_effect=AssertionError('no second writer')):
            candidate = self.worker.collect(**self.args, include_audio=True)
        target = Path(candidate['candidatePath']) / 'message/media_0.db'
        self.assertIn(str(target), self.worker.pending['fingerprints'])
        self.assertEqual(candidate['captureReceipt']['audio_capture']['databases'], ['message/media_0.db'])
        target.write_bytes(target.read_bytes() + b'changed')
        with self.assertRaisesRegex(VaultError, 'changed-before-commit'):
            self.worker.commit(jobId=candidate['jobId'], importResult={'complete': True,
                'snapshot_id': candidate['captureReceipt']['snapshot_id'], 'account_id': 'trusted-account'})
        self.assertFalse((self.worker.output / 'latest.json').exists())
        with SyncLock(self.worker.root):
            pass

    def test_service_audio_change_during_import_not_published_and_lock_released(self):
        def mutate(vault, snapshot, account):
            result = import_snapshot(vault, snapshot, account)
            target = Path(snapshot) / 'message/media_0.db'
            target.write_bytes(target.read_bytes() + b'changed')
            return result
        service = CaptureService(self.service.root, collector=self.fake, importer=mutate)
        with self.assertRaisesRegex(VaultError, 'changed-during-import'):
            service.capture_then_import(**self.args, include_audio=True)
        self.assertFalse((service.output / 'latest.json').exists())
        with SyncLock(service.root):
            pass

    def test_exact_source_and_selection_failure_release_candidate_lock(self):
        with self.assertRaisesRegex(VaultError, 'source-mismatch'):
            self.worker.collect(**{**self.args, 'source_id': 'wrong'}, include_audio=True)
        self.audio.unlink()
        with self.assertRaisesRegex(VaultError, 'databases-missing'):
            self.worker.collect(**self.args, include_audio=True)
        with SyncLock(self.worker.root):
            pass
        self.assertEqual(self.fake.calls, 0)

    def test_audio_unauthenticated_or_bad_hash_rejected_before_import(self):
        original = self.fake.capture
        for mode in ('authenticated', 'hash'):
            def bad(*args, **kwargs):
                receipt = original(*args, **kwargs)
                path = Path(receipt['snapshot']) / 'snapshot-manifest.json'
                value = json.loads(path.read_text(encoding='utf8'))
                audio = next(entry for entry in value['databases'] if entry['database'] == 'message/media_0.db')
                audio['authenticated' if mode == 'authenticated' else 'plaintext_sha256'] = False if mode == 'authenticated' else '0' * 64
                path.write_text(json.dumps(value), encoding='utf8')
                return receipt
            with self.subTest(mode=mode), patch.object(self.fake, 'capture', side_effect=bad):
                with self.assertRaisesRegex(VaultError, 'authentication-missing|checksum-mismatch'):
                    self.service.capture_then_import(**self.args, include_audio=True)
            with SyncLock(self.service.root):
                pass


if __name__ == '__main__':
    unittest.main()
