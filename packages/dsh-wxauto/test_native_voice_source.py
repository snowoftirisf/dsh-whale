from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from native_voice_source import read_native_voice_source, NativeVoiceSourceError
from vault.test_snapshot_import import make_snapshot, TALKER, TABLE, OWNER
from vault.store import digest_file


class NativeVoiceSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.base = Path(self.temp.name)
        self.blob = b'\x02#!SILK_V3' + b'\x04\x00abcd' * 8
        self.xml = f'<msg><voicemsg voiceformat="4" voicelength="4800" length="{len(self.blob)}" aeskey="never-export" voiceurl="never-export" /></msg>'
        self.root = make_snapshot(self.base / 'snapshot', rows=[(1, 9001, 34, 1772323200, 2, self.xml, 0)], hash_manifest=True)
        self.audio = self.root / 'message/media_0.db'
        with closing(sqlite3.connect(self.audio)) as db:
            db.execute('CREATE TABLE Name2Id(user_name TEXT)')
            db.execute('INSERT INTO Name2Id VALUES(?)', (TALKER,))
            db.execute('CREATE TABLE VoiceInfo(chat_name_id INTEGER,create_time INTEGER,local_id INTEGER,svr_id INTEGER,voice_data BLOB,data_index TEXT)')
            db.execute('INSERT INTO VoiceInfo VALUES(1,1772323200,1,9001,?,?)', (self.blob, 'must not be returned'))
            db.commit()
        self.args = {'native_chat_key': 'wechat:' + TALKER, 'server_id': '9001',
                     'expected_account_username': OWNER, 'expected_source_id': 'fixture-source'}
        self.rehash()

    def tearDown(self):
        self.temp.cleanup()

    def rehash(self):
        path = self.root / 'snapshot-manifest.json'
        manifest = json.loads(path.read_text(encoding='utf8'))
        manifest['source_id'] = 'fixture-source'
        manifest['audio_capture'] = {'schemaVersion': 1, 'enabled': True, 'scope': 'chat', 'databases': ['message/media_0.db']}
        manifest['databases'] = [{'database': file.relative_to(self.root).as_posix(), 'authenticated': True,
                                  'plaintext_sha256': digest_file(file)} for file in self.root.rglob('*.db')]
        path.write_text(json.dumps(manifest), encoding='utf8')

    def change(self, sql, args=(), file=None, rehash=True):
        with closing(sqlite3.connect(file or self.audio)) as db:
            db.execute(sql, args)
            db.commit()
        if rehash:
            self.rehash()

    def read(self, **overrides):
        return read_native_voice_source(self.root, **{**self.args, **overrides})

    def test_exact_incoming_blob_and_metadata_without_writes_or_secrets(self):
        before = {str(path): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        result = self.read()
        self.assertEqual(result['blob'], self.blob)
        self.assertEqual(result['metadata']['codec'], 'silk-v3')
        self.assertEqual(result['metadata']['durationMilliseconds'], 4800)
        self.assertEqual(result['metadata']['identityMatch'], 'exact-server-chat-local-time')
        self.assertNotIn('never-export', json.dumps(result['metadata']))
        self.assertNotIn('data_index', result['metadata'])
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.root.rglob('*') if path.is_file()})

    def test_account_source_snapshot_and_unknown_chat_are_denied(self):
        for override in ({'expected_account_username': 'wrong'}, {'expected_source_id': 'wrong'},
                         {'expected_snapshot_id': 'wrong'}, {'native_chat_key': 'wechat:unknown@chatroom'}):
            with self.subTest(override=override), self.assertRaises(NativeVoiceSourceError):
                self.read(**override)

    def test_zero_negative_bool_and_missing_server_never_fallback(self):
        for value in (0, -1, True, None, '0', '9002'):
            with self.subTest(value=value), self.assertRaises(NativeVoiceSourceError):
                self.read(server_id=value)
        self.change('UPDATE VoiceInfo SET svr_id=0')
        with self.assertRaisesRegex(NativeVoiceSourceError, 'missing-or-ambiguous'):
            self.read()

    def test_same_server_but_wrong_chat_time_or_local_id_are_denied(self):
        for sql in ('UPDATE Name2Id SET user_name="wrong@chatroom"',
                    'UPDATE VoiceInfo SET create_time=1772323201', 'UPDATE VoiceInfo SET local_id=2'):
            with self.subTest(sql=sql):
                self.change(sql)
                with self.assertRaisesRegex(NativeVoiceSourceError, 'mismatch'):
                    self.read()
                self.change('UPDATE Name2Id SET user_name=?', (TALKER,))
                self.change('UPDATE VoiceInfo SET create_time=1772323200,local_id=1')

    def test_duplicate_media_and_message_ids_are_not_chosen_arbitrarily(self):
        self.change('INSERT INTO VoiceInfo SELECT * FROM VoiceInfo')
        with self.assertRaisesRegex(NativeVoiceSourceError, 'ambiguous'):
            self.read()
        self.change('DELETE FROM VoiceInfo WHERE rowid=2')
        self.change(f'INSERT INTO [{TABLE}] SELECT * FROM [{TABLE}]', file=self.root / 'message/message_0.db')
        with self.assertRaisesRegex(NativeVoiceSourceError, 'ambiguous'):
            self.read()

    def test_outgoing_or_non_voice_native_message_denied(self):
        self.change(f'UPDATE [{TABLE}] SET real_sender_id=1', file=self.root / 'message/message_0.db')
        with self.assertRaisesRegex(NativeVoiceSourceError, 'incoming-required'):
            self.read()
        self.change(f'UPDATE [{TABLE}] SET real_sender_id=2,local_type=1', file=self.root / 'message/message_0.db')
        with self.assertRaisesRegex(NativeVoiceSourceError, 'type-mismatch'):
            self.read()

    def test_blob_limit_codec_header_and_declared_length_verified(self):
        with self.assertRaisesRegex(NativeVoiceSourceError, 'size-invalid'):
            self.read(max_bytes=4)
        self.change('UPDATE VoiceInfo SET voice_data=?', (b'not-silk',))
        with self.assertRaisesRegex(NativeVoiceSourceError, 'silk-header-invalid'):
            self.read()
        self.change('UPDATE VoiceInfo SET voice_data=?', (self.blob + b'changed',))
        with self.assertRaisesRegex(NativeVoiceSourceError, 'length-mismatch'):
            self.read()

    def test_unknown_voice_codec_is_not_inferred_from_header(self):
        self.change(f'UPDATE [{TABLE}] SET message_content=?', (self.xml.replace('voiceformat="4"', 'voiceformat="1"'),), file=self.root / 'message/message_0.db')
        with self.assertRaisesRegex(NativeVoiceSourceError, 'codec-unsupported'):
            self.read()

    def test_media_checksum_and_authentication_required(self):
        self.change('UPDATE VoiceInfo SET data_index="modified"', rehash=False)
        with self.assertRaisesRegex(NativeVoiceSourceError, 'checksum-mismatch'):
            self.read()
        self.rehash()
        path = self.root / 'snapshot-manifest.json'
        value = json.loads(path.read_text(encoding='utf8'))
        next(record for record in value['databases'] if record['database'] == 'message/media_0.db')['authenticated'] = False
        path.write_text(json.dumps(value), encoding='utf8')
        with self.assertRaisesRegex(NativeVoiceSourceError, 'authentication-required'):
            self.read()

    def test_audio_capture_and_sidecars_required(self):
        (self.audio.parent / 'media_0.db-wal').write_bytes(b'not a checkpointed file')
        with self.assertRaisesRegex(NativeVoiceSourceError, 'sidecar-denied'):
            self.read()
        (self.audio.parent / 'media_0.db-wal').unlink()
        path = self.root / 'snapshot-manifest.json'
        value = json.loads(path.read_text(encoding='utf8'))
        value.pop('audio_capture')
        path.write_text(json.dumps(value), encoding='utf8')
        with self.assertRaisesRegex(NativeVoiceSourceError, 'audio-capture-required'):
            self.read()

    def test_stable_fingerprint_checks_after_read(self):
        import native_voice_source
        original = native_voice_source._fingerprint
        calls = 0
        def changed(paths):
            nonlocal calls
            calls += 1
            return original(paths) if calls == 1 else {}
        with patch.object(native_voice_source, '_fingerprint', side_effect=changed):
            with self.assertRaisesRegex(NativeVoiceSourceError, 'snapshot-changed'):
                self.read()


if __name__ == '__main__':
    unittest.main()
