from contextlib import closing
import base64
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import native_owner_text_catalog as module
from native_owner_text_catalog import page_native_owner_texts, list_native_owner_texts, NativeOwnerTextCatalogError
from vault.test_snapshot_import import make_snapshot, TABLE, OWNER, SENDER
from vault.snapshot_import import load_reader
from vault.store import digest_file


class OwnerTextCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = make_snapshot(Path(self.tmp.name) / 'snapshot', rows=[
            (1, 9001, 1, 1772323200, 2, 'fixture incoming', 0),
            (2, 9002, 1, 1772323201, 1, 'fixture outgoing', 0),
            (3, 0, 1, 1772323202, 1, 'zero outgoing', 0),
            (4, 9004, 34, 1772323203, 2, 'not text', 0)], hash_manifest=True)
        self.table = load_reader().message_table(SENDER)
        with closing(sqlite3.connect(self.root / 'message/message_0.db')) as db:
            db.execute(f'ALTER TABLE [{TABLE}] RENAME TO [{self.table}]')
            db.commit()
        self.rehash()
        self.args = dict(expected_account_username=OWNER, expected_source_id='fixture', account_id='trusted-account',
                         owner_native_chat_key='wechat:' + SENDER, expected_peer_username=SENDER)

    def rehash(self):
        file = self.root / 'snapshot-manifest.json'
        value = json.loads(file.read_text('utf8'))
        value['source_id'] = 'fixture'
        value['databases'] = [{'database': p.relative_to(self.root).as_posix(), 'authenticated': True,
                               'plaintext_sha256': digest_file(p)} for p in self.root.rglob('*.db')]
        file.write_text(json.dumps(value), encoding='utf8')

    def write(self, sql, args=()):
        with closing(sqlite3.connect(self.root / 'message/message_0.db')) as db:
            db.execute(sql, args)
            db.commit()
        self.rehash()

    def page(self, **kw):
        return page_native_owner_texts(self.root, scan_key='a' * 64, **{**self.args, **kw})

    def read(self, **kw):
        return list_native_owner_texts(self.root, **{**self.args, **kw})

    def test_exact_private_text_scope_no_audio_and_readonly(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        value = self.read()
        self.assertTrue(value['complete'])
        self.assertEqual(set(value), {'source', 'accountId', 'snapshotId', 'scopeDigest', 'nativeChatKey', 'complete', 'messages'})
        self.assertEqual([m['serverId'] for m in value['messages']], ['9001'])
        self.assertEqual(value['messages'][0]['senderId'], SENDER)
        expected = hashlib.sha256(json.dumps(['trusted-account', 'wechat:' + SENDER, SENDER], separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(value['scopeDigest'], expected)
        page = self.page()
        self.assertEqual(page['scanned'], 3)
        self.assertEqual(page['observedNativeIds'], ['wechat-server:9001', 'wechat-server:9002'])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_1001_incoming_and_outgoing_only_pages_including_zero_server_ids(self):
        with closing(sqlite3.connect(self.root / 'message/message_0.db')) as db:
            db.execute(f'DELETE FROM [{self.table}]')
            db.executemany(f'INSERT INTO [{self.table}] VALUES(?,?,?,?,?,?,?)', [
                (i, 0, 1, 1772323200 + i, 1, 'outgoing', 0) for i in range(1, 1002)])
            db.executemany(f'INSERT INTO [{self.table}] VALUES(?,?,?,?,?,?,?)', [
                (i, 200000 + i, 1, 1772323200 + i, 2, 'incoming', 0) for i in range(1002, 2003)])
            db.commit()
        self.rehash()
        first = self.page()
        self.assertEqual(first['messages'], [])
        self.assertEqual(first['observedNativeIds'], [])
        self.assertEqual(first['scanned'], 500)
        self.assertFalse(first['complete'])
        second = self.page(cursor=first['nextCursor'])
        self.assertEqual(second['messages'], [])
        self.assertNotEqual(second['nextCursor'], first['nextCursor'])
        self.assertEqual(len(self.read()['messages']), 1001)

    def test_cross_shard_duplicate_physical_outgoing_id_rejected(self):
        self.write(f'DELETE FROM [{self.table}] WHERE real_sender_id=2')
        shutil.copyfile(self.root / 'message/message_0.db', self.root / 'message/message_1.db')
        self.rehash()
        with self.assertRaisesRegex(NativeOwnerTextCatalogError, 'native-id-ambiguous'):
            self.read(page_size=1)

    def test_all_outgoing_catalog_is_complete_empty_not_an_input(self):
        self.write(f'DELETE FROM [{self.table}] WHERE real_sender_id=2')
        result = self.read(page_size=1)
        self.assertTrue(result['complete'])
        self.assertEqual(result['messages'], [])

    def test_actual_third_party_sender_is_not_inferred_as_owner_peer(self):
        with closing(sqlite3.connect(self.root / 'message/message_0.db')) as db:
            db.execute('INSERT INTO Name2Id VALUES(?)', ('wxid_unrelated_fixture',))
            db.execute(f'UPDATE [{self.table}] SET real_sender_id=3 WHERE server_id=9001')
            db.commit()
        self.rehash()
        with self.assertRaisesRegex(NativeOwnerTextCatalogError, 'sender-unresolved'):
            self.read()

    def test_duplicate_incoming_across_pages_rejected(self):
        self.write(f'INSERT INTO [{self.table}] SELECT * FROM [{self.table}] WHERE server_id=9001')
        with self.assertRaisesRegex(NativeOwnerTextCatalogError, 'native-id-ambiguous'):
            self.read(page_size=1)

    def test_identity_source_snapshot_and_cursor_scope_are_strict(self):
        for kw in [dict(expected_account_username='wrong'), dict(expected_source_id='wrong'),
                   dict(expected_snapshot_id='wrong'), dict(owner_native_chat_key='wechat:some@chatroom'),
                   dict(expected_peer_username=OWNER), dict(account_id=''), dict(page_size=True)]:
            with self.subTest(kw=kw), self.assertRaises(NativeOwnerTextCatalogError):
                self.read(**kw)
        first = self.page(page_size=1)
        for kw in [dict(page_size=2), dict(account_id='other-account')]:
            with self.subTest(kw=kw), self.assertRaisesRegex(NativeOwnerTextCatalogError, 'cursor-scope'):
                self.page(cursor=first['nextCursor'], **kw)
        payload, signature = first['nextCursor'].split('.')
        parsed = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        parsed['rowid'] = 1000
        changed = base64.urlsafe_b64encode(json.dumps(parsed).encode()).decode().rstrip('=') + '.' + signature
        with self.assertRaisesRegex(NativeOwnerTextCatalogError, 'cursor-invalid'):
            self.page(page_size=1, cursor=changed)

    def test_changed_authenticated_file_and_changed_manifest_cannot_resume(self):
        first = self.page(page_size=1)
        self.write(f'UPDATE [{self.table}] SET message_content=? WHERE server_id=9001', ('changed fixture',))
        with self.assertRaisesRegex(NativeOwnerTextCatalogError, 'cursor-scope'):
            self.page(page_size=1, cursor=first['nextCursor'])
        self.write(f'UPDATE [{self.table}] SET message_content=? WHERE server_id=9001', ('again',))
        # No manifest rehash for this last edit: authentication must fail even first page.
        with closing(sqlite3.connect(self.root / 'message/message_0.db')) as db:
            db.execute(f'UPDATE [{self.table}] SET message_content=? WHERE server_id=9001', ('tamper',)); db.commit()
        with self.assertRaises(NativeOwnerTextCatalogError):
            self.read()

    def test_missing_auth_and_sender_payload_fallback_refused(self):
        manifest = self.root / 'snapshot-manifest.json'
        value = json.loads(manifest.read_text('utf8')); value.pop('databases')
        manifest.write_text(json.dumps(value), encoding='utf8')
        with self.assertRaisesRegex(NativeOwnerTextCatalogError, 'authentication-required'):
            self.read()
        self.write(f'UPDATE [{self.table}] SET real_sender_id=99,message_content=? WHERE server_id=9001', (SENDER + ':\nspoofed',))
        with self.assertRaisesRegex(NativeOwnerTextCatalogError, 'sender-unresolved'):
            self.read()

    def test_invalid_envelopes_do_not_truncate_or_coerce(self):
        for column, value in [('create_time', 1.5), ('create_time', 0), ('local_id', 0),
                              ('server_id', 0), ('message_content', '  ')]:
            with self.subTest(column=column, value=value):
                with closing(sqlite3.connect(self.root / 'message/message_0.db')) as db:
                    db.execute(f'UPDATE [{self.table}] SET create_time=1772323200,local_id=1,server_id=9001,message_content=? WHERE rowid=1', ('fixture',))
                    db.execute(f'UPDATE [{self.table}] SET [{column}]=? WHERE rowid=1', (value,)); db.commit()
                self.rehash()
                with self.assertRaises(NativeOwnerTextCatalogError):
                    self.read()

    def test_page_bytes_identity_and_total_limits_no_partial_catalog(self):
        for kw, error in [(dict(max_pages=1, page_size=1), 'page-limit'),
                          (dict(max_bytes=1), 'total-byte-limit'), (dict(max_identities=1), 'identity-limit')]:
            with self.subTest(kw=kw), self.assertRaisesRegex(NativeOwnerTextCatalogError, error):
                self.read(**kw)
        original = module.page_native_owner_texts
        calls = []
        def broken(*args, **kwargs):
            calls.append(True)
            if len(calls) == 2:
                raise NativeOwnerTextCatalogError('fixture-later-page-failed')
            return original(*args, **kwargs)
        with patch.object(module, 'page_native_owner_texts', side_effect=broken), self.assertRaisesRegex(NativeOwnerTextCatalogError, 'later-page-failed'):
            self.read(page_size=1)

    def test_collector_rejects_page_scope_swap_and_cursor_loop(self):
        first = self.page(page_size=1)
        changed = {**first, 'accountId': 'other-account'}
        with patch.object(module, 'page_native_owner_texts', return_value=changed), self.assertRaisesRegex(NativeOwnerTextCatalogError, 'page-scope-invalid'):
            self.read(page_size=1)
        # Empty input pages are valid progress, but repeating a cursor is not.
        empty = {**first, 'messages': [], 'observedNativeIds': []}
        with patch.object(module, 'page_native_owner_texts', return_value=empty), self.assertRaisesRegex(NativeOwnerTextCatalogError, 'cursor-not-advancing'):
            self.read(page_size=1)

    def test_snapshot_race_and_symlink_are_refused(self):
        actual = module._fingerprint
        with patch.object(module, '_fingerprint', side_effect=lambda paths: {**actual(paths), 'changed': (0, 0, 'x')}), self.assertRaisesRegex(NativeOwnerTextCatalogError, 'snapshot-changed'):
            self.read()
        link = Path(self.tmp.name) / 'linked-snapshot'
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except OSError:
            # Independent lexical root check exercises refusal without requiring OS privilege.
            with patch.object(module, '_validate', side_effect=RuntimeError('link-denied')), self.assertRaises(NativeOwnerTextCatalogError):
                self.read()
            return
        with self.assertRaises(NativeOwnerTextCatalogError):
            list_native_owner_texts(link, **self.args)


if __name__ == '__main__':
    unittest.main()
