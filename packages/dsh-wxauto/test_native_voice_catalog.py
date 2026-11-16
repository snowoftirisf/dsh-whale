from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from native_voice_catalog import list_native_voices,page_native_voices,NativeVoiceCatalogError
from vault.test_snapshot_import import make_snapshot,TALKER,TABLE,OWNER,SENDER
from vault.snapshot_import import load_reader
from vault.store import digest_file


class VoiceCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.tmp.cleanup)
        self.root=make_snapshot(Path(self.tmp.name)/'snapshot',rows=[
            (1,9001,34,1772323200,2,'<msg><voicemsg aeskey="never-export-key" voiceurl="https://never-export.invalid"/></msg>',0),
            (2,9002,34,1772323201,1,'outgoing',0),(3,0,34,1772323202,1,'zero outgoing',0),
            (4,9004,1,1772323203,2,'not voice',0)],hash_manifest=True)
        self.reader=load_reader();self.others=['second@chatroom','wxid_private']
        with closing(sqlite3.connect(self.root/'contact/contact.db')) as db:
            db.executemany('INSERT INTO contact VALUES(?,?)',[(t,'Other chat') for t in self.others]);db.commit()
        with closing(sqlite3.connect(self.root/'message/message_0.db')) as db:
            for i,t in enumerate(self.others):
                table=self.reader.message_table(t)
                db.execute(f'CREATE TABLE [{table}] AS SELECT * FROM [{TABLE}] WHERE 0')
                db.execute(f'INSERT INTO [{table}] VALUES(1,?,34,1772323200,2,?,0)',(9100+i,'other envelope'))
            db.commit()
        self.rehash()
        self.args=dict(expected_account_username=OWNER,expected_source_id='fixture',allowed_chat_keys=['wechat:'+TALKER])
    def rehash(self):
        file=self.root/'snapshot-manifest.json';v=json.loads(file.read_text())
        v['source_id']='fixture';v['audio_capture']={'enabled':True,'scope':'chat','schemaVersion':1,'databases':['message/media_0.db']}
        v['databases']=[{'database':p.relative_to(self.root).as_posix(),'authenticated':True,'plaintext_sha256':digest_file(p)} for p in self.root.rglob('*.db')]
        v['databases'].append({'database':'message/media_0.db','authenticated':True,'plaintext_sha256':'a'*64})
        file.write_text(json.dumps(v),encoding='utf8')
    def read(self,**kw):return list_native_voices(self.root,**{**self.args,**kw})
    def page(self,**kw):return page_native_voices(self.root,account_id='trusted-account',scan_key='a'*64,**{**self.args,**kw})
    def test_exact_allowlist_incoming_and_no_media_blob_read_or_writes(self):
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result=self.read();self.assertEqual(len(result['voices']),1)
        row=result['voices'][0];self.assertEqual(row['nativeId'],'wechat-server:9001')
        self.assertEqual(row['senderId'],SENDER);self.assertEqual(row['rawText'],'[语音]')
        self.assertNotIn('never-export',json.dumps(result));self.assertNotIn('voicemsg',row['rawText'])
        self.assertNotIn('other envelope',json.dumps(result));self.assertFalse((self.root/'message/media_0.db').exists())
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertEqual(self.read(allowed_chat_keys=[])['voices'],[])
    def test_two_groups_and_private_are_exact_scopes(self):
        for t in self.others:
            rows=self.read(allowed_chat_keys=['wechat:'+t])['voices']
            self.assertEqual(len(rows),1);self.assertEqual(rows[0]['nativeChatKey'],'wechat:'+t)
        self.assertEqual(len(self.read(allowed_chat_keys=['wechat:'+TALKER,*['wechat:'+t for t in self.others]])['voices']),3)
    def test_account_source_snapshot_and_duplicate_allowlist_denied(self):
        for kw in [dict(expected_account_username='wrong'),dict(expected_source_id='wrong'),dict(expected_snapshot_id='wrong'),
                   dict(allowed_chat_keys=['wechat:'+TALKER]*2)]:
            with self.subTest(kw=kw),self.assertRaises(NativeVoiceCatalogError):self.read(**kw)
    def test_duplicate_native_and_budget_are_not_truncated(self):
        with closing(sqlite3.connect(self.root/'message/message_0.db')) as db:
            db.execute(f'INSERT INTO [{TABLE}] SELECT * FROM [{TABLE}] WHERE server_id=9001');db.commit()
        self.rehash()
        with self.assertRaisesRegex(NativeVoiceCatalogError,'native-id-ambiguous'):self.read()
        with self.assertRaisesRegex(NativeVoiceCatalogError,'native-id-ambiguous'):self.read(max_messages=1)
    def test_missing_audio_auth_and_corrupt_source_refuse(self):
        file=self.root/'snapshot-manifest.json';v=json.loads(file.read_text());v.pop('audio_capture');file.write_text(json.dumps(v))
        with self.assertRaisesRegex(NativeVoiceCatalogError,'audio-capture-required'):self.read()
        self.rehash()
        with closing(sqlite3.connect(self.root/'message/message_0.db')) as db:
            db.execute(f'UPDATE [{TABLE}] SET message_content=? WHERE server_id=9001',('tampered',));db.commit()
        with self.assertRaises(NativeVoiceCatalogError):self.read()
    def test_snapshot_race_fails_without_exporting_exception_text(self):
        import native_voice_catalog as module
        actual=module._fingerprint
        with patch.object(module,'_fingerprint',side_effect=lambda paths:{**actual(paths),'changed':(0,0,'x')}):
            with self.assertRaisesRegex(NativeVoiceCatalogError,'snapshot-changed'):self.read()
    def test_1000_outgoing_then_one_fresh_incoming_no_longer_exceeds_global_budget(self):
        # Reproducer for the historical accumulation bug: positive server IDs
        # on outgoing voices consume all budget before filtering by direction.
        with closing(sqlite3.connect(self.root/'message/message_0.db')) as db:
            db.execute(f'DELETE FROM [{TABLE}]')
            db.executemany(f'INSERT INTO [{TABLE}] VALUES(?,?,?,?,?,?,?)',[
                (i,100000+i,34,1772323200+i,1,'synthetic outgoing voice',0) for i in range(1,1001)])
            db.execute(f'INSERT INTO [{TABLE}] VALUES(1001,999999,34,1772324201,2,?,0)',('fresh synthetic incoming',));db.commit()
        self.rehash()
        self.assertEqual([v['serverId'] for v in self.read()['voices']],['999999'])
        self.assertEqual([v['serverId'] for v in self.read()['voices']],['999999'])
    def test_1001_incoming_voices_returns_a_complete_catalog(self):
        with closing(sqlite3.connect(self.root/'message/message_0.db')) as db:
            db.execute(f'DELETE FROM [{TABLE}]')
            db.executemany(f'INSERT INTO [{TABLE}] VALUES(?,?,?,?,?,?,?)',[
                (i,200000+i,34,1772323200+i,2,'synthetic incoming voice',0) for i in range(1,1002)]);db.commit()
        self.rehash()
        self.assertEqual(len(self.read()['voices']),1001)
    def test_paging_1000_outgoing_and_fresh_incoming_advances_empty_input_pages(self):
        with closing(sqlite3.connect(self.root/'message/message_0.db')) as db:
            db.execute(f'DELETE FROM [{TABLE}]')
            db.executemany(f'INSERT INTO [{TABLE}] VALUES(?,?,?,?,?,?,?)',[(i,100000+i,34,1772323200+i,1,'outgoing',0) for i in range(1,1001)])
            db.execute(f'INSERT INTO [{TABLE}] VALUES(1001,999999,34,1772324201,2,?,0)',('incoming',));db.commit()
        self.rehash();first=self.page();self.assertEqual(first['voices'],[]);self.assertEqual(first['scanned'],500);self.assertFalse(first['complete'])
        second=self.page(cursor=first['nextCursor']);self.assertEqual(second['voices'],[]);self.assertEqual(second['scanned'],500)
        third=self.page(cursor=second['nextCursor']);self.assertTrue(third['complete']);self.assertIsNone(third['nextCursor']);self.assertEqual(third['voices'][0]['serverId'],'999999')
        self.assertGreater(second['position']['rowid'],first['position']['rowid'])
    def test_paging_1001_incoming_and_multiple_tables_keeps_every_source_once(self):
        with closing(sqlite3.connect(self.root/'message/message_0.db')) as db:
            db.execute(f'DELETE FROM [{TABLE}]')
            db.executemany(f'INSERT INTO [{TABLE}] VALUES(?,?,?,?,?,?,?)',[(i,200000+i,34,1772323200+i,2,'incoming',0) for i in range(1,1002)]);db.commit()
        self.rehash();pages=[];cursor=None
        while True:
            p=self.page(cursor=cursor,allowed_chat_keys=['wechat:'+TALKER,*['wechat:'+t for t in self.others]],page_size=127);pages.append(p)
            if p['complete']:break
            cursor=p['nextCursor'];self.assertLess(len(pages),30)
        all_ids=[v['nativeId'] for p in pages for v in p['voices']]
        self.assertEqual(len(all_ids),1003);self.assertEqual(len(set(all_ids)),1003)
        self.assertEqual({v['nativeChatKey'] for p in pages for v in p['voices']},{'wechat:'+TALKER,*['wechat:'+t for t in self.others]})
    def test_cursor_tamper_wrong_snapshot_allowlist_or_scan_key_rejected(self):
        first=self.page(page_size=1);token=first['nextCursor'];self.assertIsNotNone(token)
        for options in ({'cursor':token[:-1]+('0' if token[-1]!='0' else '1')},{'cursor':token,'scan_key':'b'*64},{'cursor':token,'allowed_chat_keys':['wechat:'+self.others[0]]}):
            args={**self.args,'account_id':'trusted-account','scan_key':'a'*64,'page_size':1,**options}
            with self.subTest(options=options),self.assertRaisesRegex(NativeVoiceCatalogError,'cursor-'):page_native_voices(self.root,**args)
        file=self.root/'snapshot-manifest.json';v=json.loads(file.read_text());import uuid;v['snapshot_id']=str(uuid.uuid4());file.write_text(json.dumps(v))
        with self.assertRaisesRegex(NativeVoiceCatalogError,'cursor-scope-mismatch'):self.page(cursor=token,page_size=1)
    def test_keyset_boundary_across_shards_preserves_same_local_rowid_different_sources(self):
        file=self.root/'message/message_1.db'
        with closing(sqlite3.connect(file)) as db:
            db.execute('CREATE TABLE Name2Id(user_name TEXT)');db.executemany('INSERT INTO Name2Id VALUES(?)',[(OWNER,),(SENDER,)])
            db.execute(f'CREATE TABLE [{TABLE}](local_id INTEGER,server_id INTEGER,local_type INTEGER,create_time INTEGER,real_sender_id INTEGER,message_content BLOB,WCDB_CT_message_content INTEGER)')
            db.execute(f'INSERT INTO [{TABLE}] VALUES(1,999001,34,1772323200,2,?,0)',('second shard voice',));db.commit()
        self.rehash();cursor=None;found=[]
        while True:
            p=self.page(cursor=cursor,page_size=1);found.extend(p['voices'])
            if p['complete']:break
            cursor=p['nextCursor']
        self.assertEqual({r['serverId'] for r in found},{'9001','999001'})
        self.assertEqual(len({r['snapshotSourceId'] for r in found}),2)


if __name__=='__main__':unittest.main()
