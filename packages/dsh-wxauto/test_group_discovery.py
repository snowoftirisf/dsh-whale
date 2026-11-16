import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from group_discovery import discover_groups
from vault.store import VaultError, digest_file
from vault.test_snapshot_import import make_snapshot, OWNER, TALKER, SENDER


class GroupDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.base = Path(self.temp.name)
        self.now = datetime.now(timezone.utc)
        self.root = make_snapshot(self.base / 'initial', hash_manifest=True)
        self.manifest = json.loads((self.root / 'snapshot-manifest.json').read_text())
        self.manifest['captured_at'] = self.now.isoformat()
        self.root.rename(self.base / self.manifest['snapshot_id'])
        (self.base / 'snapshots').mkdir()
        (self.base / self.manifest['snapshot_id']).rename(self.base / 'snapshots' / self.manifest['snapshot_id'])
        self.root = self.base / 'snapshots' / self.manifest['snapshot_id']
        with closing(sqlite3.connect(self.root / 'contact/contact.db')) as db:
            for column, kind in (('remark', 'TEXT'), ('alias', 'TEXT'), ('delete_flag', 'INTEGER'), ('is_in_chat_room', 'INTEGER')):
                db.execute(f'ALTER TABLE contact ADD COLUMN {column} {kind}')
            db.execute('UPDATE contact SET delete_flag=0,is_in_chat_room=1')
            db.execute('CREATE TABLE name2id(username TEXT)')
            db.execute('INSERT INTO name2id VALUES(?)', (OWNER,))
            db.execute('CREATE TABLE chat_room(id INTEGER, username TEXT)')
            db.execute('INSERT INTO chat_room VALUES(9,?)', (TALKER,))
            db.execute('CREATE TABLE chatroom_member(room_id INTEGER,member_id INTEGER)')
            db.execute('INSERT INTO chatroom_member VALUES(9,1)')
            db.commit()
        with closing(sqlite3.connect(self.root / 'session/session.db')) as db:
            db.execute('CREATE TABLE SessionTable(username TEXT,is_hidden INTEGER,status INTEGER,last_timestamp INTEGER)')
            db.execute('INSERT INTO SessionTable VALUES(?,0,0,1234)', (TALKER,))
            db.execute('CREATE TABLE SessionDeleteTable(username TEXT,delete_time INTEGER)')
            db.commit()
        self.latest = self.base / 'latest.json'
        self.pointer = {'status': 'complete', 'validation': 'passed', 'read_only_source': True,
                        'keys_persisted': False, 'snapshot_id': self.manifest['snapshot_id'], 'snapshot': str(self.root)}
        self.rehash()

    def tearDown(self):
        self.temp.cleanup()

    def rehash(self):
        for entry in self.manifest['databases']:
            entry['plaintext_sha256'] = digest_file(self.root / entry['database'])
        (self.root / 'snapshot-manifest.json').write_text(json.dumps(self.manifest), encoding='utf8')
        self.latest.write_text(json.dumps(self.pointer), encoding='utf8')

    def change(self, dbfile, sql, args=()):
        with closing(sqlite3.connect(self.root / dbfile)) as db:
            db.execute(sql, args)
            db.commit()
        self.rehash()

    def discover(self, **kwargs):
        return discover_groups(self.latest, 'trusted-account', now=self.now, **kwargs)

    def default_title_fixture(self):
        def message(value):
            body = b'\x0a' + bytes([len(value.encode())]) + value.encode()
            return b'\x0a' + bytes([len(body)]) + body
        second = 'wxid_second_fixture'
        blob = b''.join(message(value) for value in (second, SENDER, OWNER))
        with closing(sqlite3.connect(self.root / 'contact/contact.db')) as db:
            db.execute('ALTER TABLE chat_room ADD COLUMN ext_buffer BLOB')
            db.execute('UPDATE chat_room SET ext_buffer=?', (blob,))
            db.execute('UPDATE contact SET nick_name=NULL WHERE username=?', (TALKER,))
            db.execute('INSERT INTO contact(username,nick_name,delete_flag,is_in_chat_room) VALUES(?,?,0,0)', (second, '第二位'))
            db.execute('INSERT INTO name2id VALUES(?)', (SENDER,))
            db.execute('INSERT INTO name2id VALUES(?)', (second,))
            db.executemany('INSERT INTO chatroom_member VALUES(9,?)', [(2,), (3,)])
            db.commit()
        self.rehash()
        return blob

    def test_positive_membership_and_native_identity_no_source_writes(self):
        before = {str(p): p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
        group = self.discover()['groups'][0]
        self.assertTrue(group['eligible'])
        self.assertEqual(group['chatKey'], 'wechat:' + TALKER)
        self.assertEqual(group['membership']['selfMembershipRows'], 1)
        self.assertEqual(group['displayNameSource'], 'nick_name')
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.base.rglob('*') if p.is_file()})

    def test_unnamed_is_unresolved_never_generic_placeholder(self):
        self.change('contact/contact.db', 'UPDATE contact SET nick_name=NULL WHERE username=?', (TALKER,))
        group = self.discover()['groups'][0]
        self.assertIsNone(group['displayName'])
        self.assertFalse(group['eligible'])
        self.assertIn('explicit-display-name-missing', group['unresolvedReasons'])

    def test_duplicate_title_including_private_chat_blocks_routing(self):
        self.change('contact/contact.db', 'UPDATE contact SET nick_name=? WHERE username=?', ('测试群', OWNER))
        group = self.discover()['groups'][0]
        self.assertEqual(group['displayCollisionCount'], 2)
        self.assertIn('display-name-ambiguous', group['unresolvedReasons'])

    def test_membership_absent_or_negative_never_eligible(self):
        for sql in ('DELETE FROM chatroom_member', 'UPDATE contact SET is_in_chat_room=0', 'UPDATE contact SET delete_flag=1'):
            with self.subTest(sql=sql):
                self.change('contact/contact.db', sql)
                self.assertIn('current-membership-unconfirmed', self.discover()['groups'][0]['unresolvedReasons'])

    def test_session_absence_hidden_or_delete_record_is_not_live_membership(self):
        self.change('session/session.db', 'INSERT INTO SessionDeleteTable VALUES(?,123)', (TALKER,))
        group = self.discover()['groups'][0]
        self.assertEqual(group['membership']['selfMembershipRows'], 1)
        self.assertFalse(group['eligible'])
        self.assertIn('active-local-session-unconfirmed', group['unresolvedReasons'])

    def test_exact_suffix_no_contains_match_and_rename_keeps_id(self):
        original = self.discover()['groups'][0]['chatId']
        self.change('contact/contact.db', 'UPDATE contact SET nick_name=? WHERE username=?', ('新名字', TALKER))
        self.assertEqual(self.discover()['groups'][0]['chatId'], original)
        self.assertNotEqual(discover_groups(self.latest, 'different-account', now=self.now)['groups'][0]['chatId'], original)
        self.change('contact/contact.db', 'UPDATE contact SET username=? WHERE username=?', ('fake@chatroom.suffix', TALKER))
        self.assertEqual(self.discover()['groups'], [])

    def test_snapshot_uuid_change_does_not_change_chat_identity(self):
        old = self.discover()['groups'][0]['chatId']
        import uuid
        identifier = str(uuid.uuid4())
        dest = self.root.parent / identifier
        self.root.rename(dest)
        self.root = dest
        self.manifest['snapshot_id'] = identifier
        self.pointer.update(snapshot_id=identifier, snapshot=str(dest))
        self.rehash()
        self.assertEqual(self.discover()['groups'][0]['chatId'], old)

    def test_authenticated_hash_required_and_tamper_rejected(self):
        self.manifest.pop('databases')
        (self.root / 'snapshot-manifest.json').write_text(json.dumps(self.manifest), encoding='utf8')
        with self.assertRaisesRegex(VaultError, 'capture-manifest-required'):
            self.discover()

    def test_database_hash_tamper_rejected(self):
        with closing(sqlite3.connect(self.root / 'contact/contact.db')) as db:
            db.execute('UPDATE contact SET nick_name=?', ('changed',))
            db.commit()
        with self.assertRaisesRegex(VaultError, 'checksum-mismatch'):
            self.discover()

    def test_stale_future_and_account_mismatch(self):
        with self.assertRaisesRegex(VaultError, 'account-mismatch'):
            self.discover(expected_account_username='wrong-account')
        for delta in (-901, 61):
            self.manifest['captured_at'] = (self.now + timedelta(seconds=delta)).isoformat()
            self.rehash()
            with self.assertRaisesRegex(VaultError, 'snapshot-stale'):
                self.discover()

    def test_pointer_escape_and_unverified_state(self):
        self.pointer['snapshot'] = str(self.root.parent.parent / 'outside')
        self.rehash()
        with self.assertRaisesRegex(VaultError, 'location-invalid'):
            self.discover()
        self.pointer['status'] = 'failed'
        self.rehash()
        with self.assertRaisesRegex(VaultError, 'latest-unverified'):
            self.discover()

    def test_unknown_schema_rejected_not_guessed(self):
        self.change('contact/contact.db', 'DROP TABLE chatroom_member')
        with self.assertRaisesRegex(VaultError, 'schema-unsupported'):
            self.discover()

    def test_race_fingerprint_rejected(self):
        with patch('group_discovery._fingerprint', return_value={}):
            with self.assertRaisesRegex(VaultError, 'snapshot-changed'):
                self.discover()

    def test_latest_symlink_rejected(self):
        link = self.base / 'link.json'
        try:
            link.symlink_to(self.latest)
        except OSError:
            self.skipTest('symlinks unavailable')
        with self.assertRaises(Exception):
            discover_groups(link, 'trusted-account', now=self.now)

    def test_default_title_uses_observed_wire_order_not_numeric_member_order(self):
        self.default_title_fixture()
        group = self.discover()['groups'][0]
        self.assertEqual(group['displayName'], '第二位、群友')
        self.assertEqual(group['displayNameSource'], 'derived-member-title')
        self.assertTrue(group['eligible'])
        self.assertTrue(group['requiresUiConfirmation'])
        self.assertEqual(group['uiConfirmation'], {'exactName': True, 'chatType': 'group'})

    def test_derived_title_collision_with_private_name_blocks_route(self):
        self.default_title_fixture()
        self.change('contact/contact.db', 'UPDATE contact SET nick_name=? WHERE username=?', ('第二位、群友', OWNER))
        self.assertIn('display-name-ambiguous', self.discover()['groups'][0]['unresolvedReasons'])

    def test_derived_titles_collide_across_distinct_native_groups(self):
        blob = self.default_title_fixture()
        self.change('contact/contact.db', 'INSERT INTO contact(username,delete_flag,is_in_chat_room) VALUES(?,0,1)', ('other@chatroom',))
        self.change('contact/contact.db', 'INSERT INTO chat_room VALUES(10,?,?)', ('other@chatroom', blob))
        for member_id in (1, 2, 3):
            self.change('contact/contact.db', 'INSERT INTO chatroom_member VALUES(10,?)', (member_id,))
        groups = self.discover()['groups']
        self.assertEqual(len(groups), 2)
        self.assertTrue(all('display-name-ambiguous' in group['unresolvedReasons'] for group in groups))

    def test_default_title_malformed_wire_or_membership_mismatch_rejected(self):
        self.default_title_fixture()
        self.change('contact/contact.db', 'UPDATE chat_room SET ext_buffer=?', (b'\x0a\xff',))
        self.assertIsNone(self.discover()['groups'][0]['displayName'])

    def test_default_title_does_not_guess_remark_precedence(self):
        self.default_title_fixture()
        self.change('contact/contact.db', 'UPDATE contact SET remark=? WHERE username=?', ('备注不同', SENDER))
        self.assertIsNone(self.discover()['groups'][0]['displayName'])

    def test_default_title_requires_exact_member_set(self):
        self.default_title_fixture()
        self.change('contact/contact.db', 'DELETE FROM chatroom_member WHERE member_id=2')
        self.assertIsNone(self.discover()['groups'][0]['displayName'])


if __name__ == '__main__':
    unittest.main()
