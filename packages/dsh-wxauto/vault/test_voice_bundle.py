import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from .store import Vault, VaultError


class VoiceBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root=Path(self.tmp.name);self.v=Vault(self.root)
        self.chat=self.v.ensure_chat('a','wechat:room@chatroom','group','group')['id']
        (self.root/'media').mkdir();self.file=self.root/'media/source.silk';self.file.write_bytes(b'#!SILK_V3fixture')
        self.args=dict(chat_id=self.chat,account_id='a',source_id='voice-event-1',native_id='wechat-server:12345',sender='member',sender_id='wxid_member',timestamp=1793597379000,raw_text='[voice raw payload]',transcript='今天一起去吃午饭',backend={'name':'sensevoice','model':'int8-fixed','language':'zh'},audio=[{'path':'media/source.silk','sha256':hashlib.sha256(self.file.read_bytes()).hexdigest(),'role':'source'}])
    def tearDown(self):self.v.close();self.tmp.cleanup()
    def run_bundle(self,**changes):return self.v.request('record_native_voice_bundle',{**self.args,**changes})
    def test_atomic_searchable_parent_and_idempotent(self):
        first=self.run_bundle();second=self.run_bundle()
        self.assertTrue(first['inserted']);self.assertFalse(second['inserted']);self.assertEqual(first['messageIds'],second['messageIds'])
        self.assertEqual(self.v.db.execute('select count(*) from messages').fetchone()[0],2)
        self.assertEqual(self.v.db.execute('select count(*) from attachments').fetchone()[0],1)
        found=self.v.search(self.chat,query='吃午饭')['messages'];self.assertEqual(len(found),1)
        self.assertEqual(found[0]['parent_message_id'],first['id']);self.assertIsNone(found[0]['native_id'])
        raw=self.v.db.execute('select * from messages where id=?',(first['id'],)).fetchone()
        self.assertEqual(raw['text'],self.args['raw_text']);self.assertEqual(raw['delivery_state'],'received')
    def test_existing_snapshot_keeps_all_raw_fields(self):
        old=self.v.record_message(self.chat,'wechat-snapshot','row1',native_id=self.args['native_id'],type='voice',direction='incoming',sender_id=self.args['sender_id'],timestamp=self.args['timestamp'],text=self.args['raw_text'],metadata={'original':True})
        before=dict(self.v.db.execute('select * from messages where id=?',(old['id'],)).fetchone())
        got=self.run_bundle();self.assertEqual(got['id'],old['id'])
        self.assertEqual(dict(self.v.db.execute('select * from messages where id=?',(old['id'],)).fetchone()),before)
    def test_event_changed_transcript_conflicts_but_new_backend_allowed(self):
        self.run_bundle()
        with self.assertRaisesRegex(VaultError,'voice-event-source-conflict'):self.run_bundle(transcript='不同文本')
        new=self.run_bundle(backend={'name':'sensevoice','model':'new-revision'},transcript='修订文本')
        self.assertEqual(self.v.db.execute('select count(*) from messages').fetchone()[0],3)
        self.assertEqual(new['id'],self.v.db.execute("select id from messages where type='voice'").fetchone()[0])
    def test_sender_raw_time_direction_conflicts(self):
        self.run_bundle()
        for changes in ({'sender_id':'other'},{'raw_text':'changed'},{'timestamp':1793597379999}):
            with self.subTest(changes=changes),self.assertRaisesRegex(VaultError,'voice-native-message-conflict'):self.run_bundle(**changes)
    def test_wrong_account_and_cross_group_do_not_attach_existing_parent(self):
        first=self.run_bundle()
        with self.assertRaisesRegex(VaultError,'voice-account-conflict'):self.run_bundle(account_id='other')
        group=self.v.ensure_chat('a','wechat:other@chatroom','group','other')['id']
        got=self.run_bundle(chat_id=group,source_id='event-other')
        self.assertNotEqual(first['id'],got['id'])
        evidence=self.v._format(self.v.db.execute('select * from messages where id=?',(got['evidenceId'],)).fetchone())
        self.assertEqual(evidence['parent_message_id'],got['id'])
        self.assertFalse(self.v.search(group,query='missing')['messages'])
    def test_hash_and_relative_path_required_before_writes(self):
        for audio in ([{**self.args['audio'][0],'sha256':'0'*64}],[{**self.args['audio'][0],'path':str(self.file.resolve())}],[{**self.args['audio'][0],'path':'../outside'}]):
            with self.subTest(audio=audio),self.assertRaises(VaultError):self.run_bundle(audio=audio)
            self.assertEqual(self.v.db.execute('select count(*) from messages').fetchone()[0],0)
    def test_transaction_rolls_back_after_message_and_attachment_inserts(self):
        # A failure at the final file revalidation must roll back raw+FTS+aliases+attachments.
        from . import store
        original=store.digest_file;calls=0
        def changed(path):
            nonlocal calls
            calls+=1
            return original(path) if calls==1 else 'f'*64
        with patch.object(store,'digest_file',side_effect=changed),self.assertRaisesRegex(VaultError,'voice-audio-changed'):self.run_bundle()
        for table in ('messages','attachments','sources'):
            self.assertEqual(self.v.db.execute('select count(*) from '+table).fetchone()[0],0)
        if self.v.fts:self.assertEqual(self.v.db.execute('select count(*) from message_fts').fetchone()[0],0)
    def test_decoded_attachment_linked_and_duplicate_roles_rejected(self):
        wav=self.root/'media/decoded.wav';wav.write_bytes(b'RIFFfixture')
        decoded={'path':'media/decoded.wav','sha256':hashlib.sha256(wav.read_bytes()).hexdigest(),'role':'decoded'}
        got=self.run_bundle(audio=self.args['audio']+[decoded]);self.assertEqual(len(got['attachmentIds']),2)
        with self.assertRaisesRegex(VaultError,'voice-audio-role-invalid'):self.run_bundle(audio=self.args['audio']*2)
    def test_private_jsonl_dispatch_and_parent_readback(self):
        from .cli import response
        answer=response(self.v,{'id':'fixture','op':'record_native_voice_bundle','args':self.args})
        self.assertTrue(answer['ok']);self.assertEqual(answer['id'],'fixture')
        got=answer['value']
        read=self.v.get_message_text(self.chat,got['evidenceId'],allowed_chat_ids=[self.chat])
        self.assertEqual(read['message']['parent_message_id'],got['id'])
        conflict=response(self.v,{'id':'conflict','op':'record_native_voice_bundle','args':{**self.args,'transcript':'changed'}})
        self.assertFalse(conflict['ok']);self.assertEqual(conflict['error'],'voice-event-source-conflict')
    def test_outgoing_native_cannot_be_relabelled_incoming(self):
        self.v.record_message(self.chat,'wechat-snapshot','outgoing',native_id=self.args['native_id'],type='voice',direction='outgoing',sender_id=self.args['sender_id'],timestamp=self.args['timestamp'],text=self.args['raw_text'])
        with self.assertRaisesRegex(VaultError,'voice-native-message-conflict'):self.run_bundle()
        self.assertEqual(self.v.db.execute('select count(*) from attachments').fetchone()[0],0)
    def test_source_audio_hardlink_rejected(self):
        import os
        linked=self.root/'media/link.silk'
        try:os.link(self.file,linked)
        except OSError:self.skipTest('Host does not permit hardlinks')
        with self.assertRaisesRegex(VaultError,'voice-audio-link-invalid'):self.run_bundle()

if __name__=='__main__':unittest.main()
