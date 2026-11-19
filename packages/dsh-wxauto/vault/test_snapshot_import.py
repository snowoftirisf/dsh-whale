import hashlib
import json
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import zstandard
from .store import Vault, VaultError, digest_file
from .snapshot_import import import_snapshot, load_reader

TALKER='group_fixture@chatroom'
TABLE='Msg_'+hashlib.md5(TALKER.encode()).hexdigest()
OWNER='wxid_owner_fixture'
SENDER='wxid_member_fixture'


def make_snapshot(root,rows=None,account=OWNER,hash_manifest=False):
    root.mkdir()
    for section in ('contact','session','message'):(root/section).mkdir()
    with closing(sqlite3.connect(root/'contact/contact.db')) as db:
        db.execute('CREATE TABLE contact(username TEXT,nick_name TEXT)')
        db.executemany('INSERT INTO contact VALUES(?,?)',[(TALKER,'测试群'),(OWNER,'小鲸'),(SENDER,'群友')]);db.commit()
    with closing(sqlite3.connect(root/'session/session.db')) as db:db.execute('CREATE TABLE session(username TEXT)');db.commit()
    with closing(sqlite3.connect(root/'message/message_0.db')) as db:
        db.execute('CREATE TABLE Name2Id(user_name TEXT)');db.executemany('INSERT INTO Name2Id VALUES(?)',[(OWNER,),(SENDER,)])
        db.execute(f'CREATE TABLE [{TABLE}](local_id INTEGER,server_id INTEGER,local_type INTEGER,create_time INTEGER,real_sender_id INTEGER,message_content BLOB,WCDB_CT_message_content INTEGER)')
        if rows is None:rows=[(1,9001,1,1772323200,2,'第一条',0),(2,9002,1,1772323201,1,'回复',0)]
        db.executemany(f'INSERT INTO [{TABLE}] VALUES(?,?,?,?,?,?,?)',rows);db.commit()
    manifest={'snapshot_id':str(uuid.uuid4()),'profile':'windows-chat-capture-v1'}
    if account is not None:manifest['account_username']=account
    if hash_manifest:
        manifest['databases']=[{'database':p,'authenticated':True,'plaintext_sha256':digest_file(root/p)} for p in ('contact/contact.db','session/session.db','message/message_0.db')]
    (root/'snapshot-manifest.json').write_text(json.dumps(manifest),encoding='utf8')
    return root


class SnapshotImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.root=Path(self.temp.name)
        self.v=Vault(self.root/'vault-root')

    def tearDown(self):self.v.close();self.temp.cleanup()

    def chat(self):return self.v.list_chats('trusted-owner')[0]['id']

    def test_source_bytes_unchanged_manifest_hmac_hash_and_message_directions(self):
        snapshot=make_snapshot(self.root/'snapshot',hash_manifest=True)
        before={str(p):p.read_bytes() for p in snapshot.rglob('*') if p.is_file()}
        result=import_snapshot(self.v,snapshot,'trusted-owner')
        self.assertTrue(result['complete']);self.assertEqual(result['scanned'],2)
        messages=self.v.history(self.chat())['messages']
        self.assertEqual({x['direction'] for x in messages},{'incoming','outgoing'})
        self.assertEqual(next(x['delivery_state'] for x in messages if x['direction']=='outgoing'),'unknown')
        self.assertEqual(before,{str(p):p.read_bytes() for p in snapshot.rglob('*') if p.is_file()})
        self.assertFalse(list(snapshot.rglob('*-journal')))

    def test_different_snapshot_uuid_same_messages_dedup_and_appended_row_imports(self):
        one=make_snapshot(self.root/'one');two=make_snapshot(self.root/'two')
        import_snapshot(self.v,one,'trusted-owner')
        result=import_snapshot(self.v,two,'trusted-owner')
        self.assertEqual(result['duplicates'],2);self.assertEqual(result['inserted'],0)
        with closing(sqlite3.connect(two/'message/message_0.db')) as db:
            db.execute(f'INSERT INTO [{TABLE}] VALUES(3,9003,1,1772323202,2,?,0)',('第二次新标记',));db.commit()
        result=import_snapshot(self.v,two,'trusted-owner')
        self.assertEqual(result['inserted'],1);self.assertEqual(self.v.status()['counts']['messages'],3)

    def test_no_server_id_source_key_keeps_repeated_identical_messages(self):
        rows=[(1,0,1,1772323200,2,'同一句',0),(2,None,1,1772323200,2,'同一句',0)]
        one=make_snapshot(self.root/'one',rows);two=make_snapshot(self.root/'two',rows)
        self.assertEqual(import_snapshot(self.v,one,'trusted-owner')['inserted'],2)
        self.assertEqual(import_snapshot(self.v,two,'trusted-owner')['duplicates'],2)
        self.assertTrue(all(x['native_id'] is None for x in self.v.history(self.chat())['messages']))

    def test_compressed_text_system_unknown_and_missing_original_media(self):
        rows=[(1,101,1,1772323200,2,zstandard.ZstdCompressor().compress('压缩后的中文'.encode()),4),
              (2,102,10000,1772323201,2,'成员加入了群聊',0),
              (3,103,9876,1772323202,2,'未知类型的正文',0),
              (4,104,3,1772323203,2,'<msg><img aeskey="secret" /></msg>',0)]
        result=import_snapshot(self.v,make_snapshot(self.root/'one',rows),'trusted-owner')
        self.assertEqual(result['types'],{'text':1,'system':1,'unknown':1,'image':1})
        values=self.v.history(self.chat())['messages']
        self.assertIn('压缩后的中文',[m['text'] for m in values])
        image=next(m for m in values if m['type']=='image')
        self.assertFalse(image['metadata']['media_available']);self.assertEqual(image['attachments'],[])
        self.assertNotIn('secret',str(values));self.assertEqual(result['media_restored'],0)

    def test_account_mapping_does_not_infer_direction_from_directory(self):
        snapshot=make_snapshot(self.root/'wxid_owner_fixture',account=None)
        import_snapshot(self.v,snapshot,'trusted-owner')
        self.assertTrue(all(m['direction']=='unknown' for m in self.v.history(self.chat())['messages']))

    def test_verified_account_promotes_only_stable_unknown_direction_preserving_original_evidence(self):
        one=make_snapshot(self.root/'unknown',account=None)
        two=make_snapshot(self.root/'verified',account=OWNER)
        import_snapshot(self.v,one,'trusted-owner')
        before={m['id']:m for m in self.v.history(self.chat())['messages']}
        result=import_snapshot(self.v,two,'trusted-owner')
        self.assertEqual(result['directions_promoted'],2);self.assertEqual(result['inserted'],0)
        after={m['id']:m for m in self.v.history(self.chat())['messages']}
        self.assertEqual(set(before),set(after))
        for ident,old in before.items():
            new=after[ident]
            self.assertEqual(new['delivery_state'],old['delivery_state'])
            evidence=new['metadata'].pop('direction_resolution')
            self.assertEqual(new['metadata'],old['metadata'])
            self.assertEqual(evidence['snapshot_id'],json.loads((two/'snapshot-manifest.json').read_text())['snapshot_id'])
            self.assertEqual(evidence['previous_direction'],'unknown')
            self.assertEqual(evidence['sender_id'],old['sender_id'])
        self.assertEqual({m['direction'] for m in after.values()},{'incoming','outgoing'})
        self.assertEqual(import_snapshot(self.v,two,'trusted-owner')['directions_promoted'],0)
        self.assertEqual(self.v.status()['counts']['messages'],2)

    def test_no_native_id_or_previously_missing_sender_cannot_promote(self):
        rows=[(1,0,1,1772323200,2,'无server ID',0),(2,9002,1,1772323201,1,'原sender缺失',0)]
        import_snapshot(self.v,make_snapshot(self.root/'unknown',rows,account=None),'trusted-owner')
        with self.v.db:self.v.db.execute('UPDATE messages SET sender_id=NULL WHERE native_id=?',('wechat-server:9002',))
        result=import_snapshot(self.v,make_snapshot(self.root/'verified',rows),'trusted-owner')
        self.assertEqual(result['directions_promoted'],0)
        self.assertTrue(all(m['direction']=='unknown' for m in self.v.history(self.chat())['messages']))

    def test_cross_source_native_link_is_not_a_direction_promotion(self):
        import_snapshot(self.v,make_snapshot(self.root/'unknown',account=None),'trusted-owner')
        other=make_snapshot(self.root/'verified')
        (other/'message/message_0.db').rename(other/'message/message_1.db')
        result=import_snapshot(self.v,other,'trusted-owner')
        self.assertEqual(result['linked'],2);self.assertEqual(result['directions_promoted'],0)
        self.assertTrue(all(m['direction']=='unknown' for m in self.v.history(self.chat())['messages']))

    def test_conflicting_known_direction_is_never_reversed(self):
        import_snapshot(self.v,make_snapshot(self.root/'known'),'trusted-owner')
        cursor=self.v.status()['cursors'].copy()
        with self.assertRaisesRegex(VaultError,'direction-conflict'):
            import_snapshot(self.v,make_snapshot(self.root/'wrong-account',account=SENDER),'trusted-owner')
        self.assertEqual(self.v.status()['cursors'],cursor)
        self.assertEqual({m['direction'] for m in self.v.history(self.chat())['messages']},{'incoming','outgoing'})

    def test_sender_conflict_or_invalid_snapshot_never_promotes_directions(self):
        import_snapshot(self.v,make_snapshot(self.root/'unknown',account=None),'trusted-owner')
        cursor=self.v.status()['cursors'].copy()
        conflict=make_snapshot(self.root/'conflict',[(1,9001,1,1772323200,1,'第一条',0),(2,9002,1,1772323201,1,'回复',0)])
        with self.assertRaisesRegex(VaultError,'sender-conflict'):import_snapshot(self.v,conflict,'trusted-owner')
        self.assertTrue(all(m['direction']=='unknown' for m in self.v.history(self.chat())['messages']))
        self.assertEqual(self.v.status()['cursors'],cursor)
        verified=make_snapshot(self.root/'verified');original=self.v.record_message
        def changed(*args,**kwargs):
            result=original(*args,**kwargs)
            file=verified/'snapshot-manifest.json';value=json.loads(file.read_text());value['changed']=True;file.write_text(json.dumps(value))
            return result
        with patch.object(self.v,'record_message',side_effect=changed):
            with self.assertRaisesRegex(VaultError,'changed-during-import'):import_snapshot(self.v,verified,'trusted-owner')
        self.assertTrue(all(m['direction']=='unknown' for m in self.v.history(self.chat())['messages']))
        self.assertEqual(self.v.status()['cursors'],cursor)

    def test_same_display_name_chats_and_unmapped_tables_stay_distinct(self):
        snapshot=make_snapshot(self.root/'one')
        unknown='Msg_'+'a'*32
        with closing(sqlite3.connect(snapshot/'message/message_0.db')) as db:
            db.execute(f'CREATE TABLE [{unknown}](create_time INTEGER,content TEXT)')
            db.execute(f'INSERT INTO [{unknown}] VALUES(1772323200,?)',('未解析对话',));db.commit()
        result=import_snapshot(self.v,snapshot,'trusted-owner')
        self.assertEqual(result['unresolved_chats'],1)
        self.assertEqual({c['kind'] for c in self.v.list_chats('trusted-owner')},{'group','unknown'})

    def test_reject_sidecar_invalid_manifest_and_hash_mismatch_without_cursor(self):
        one=make_snapshot(self.root/'one');(one/'message/message_0.db-wal').write_bytes(b'not detached')
        with self.assertRaisesRegex(VaultError,'validation-failed'):import_snapshot(self.v,one,'trusted-owner')
        two=make_snapshot(self.root/'two',hash_manifest=True)
        manifest=json.loads((two/'snapshot-manifest.json').read_text());manifest['databases'][0]['plaintext_sha256']='0'*64
        (two/'snapshot-manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(VaultError,'checksum-mismatch'):import_snapshot(self.v,two,'trusted-owner')
        self.assertEqual(self.v.status()['cursors'],{})

    def test_failed_decode_does_not_advance_cursor_and_retry_is_idempotent(self):
        good=make_snapshot(self.root/'good');import_snapshot(self.v,good,'trusted-owner')
        cursor=self.v.status()['cursors'].copy()
        rows=[(1,9001,1,1772323200,2,'第一条',0),(2,9002,1,1772323201,1,'回复',0),
              (3,9003,1,1772323202,2,'第三条',0),(4,9004,1,1772323203,2,b'\x28\xb5\x2f\xfdBAD',4)]
        bad=make_snapshot(self.root/'bad',rows)
        with self.assertRaises(Exception):import_snapshot(self.v,bad,'trusted-owner')
        self.assertEqual(self.v.status()['cursors'],cursor)
        with closing(sqlite3.connect(bad/'message/message_0.db')) as db:
            db.execute(f'UPDATE [{TABLE}] SET message_content=?,WCDB_CT_message_content=0 WHERE local_id=4',('第四条',));db.commit()
        result=import_snapshot(self.v,bad,'trusted-owner')
        self.assertEqual(result['inserted'],1);self.assertEqual(self.v.status()['counts']['messages'],4)

    def test_identity_conflict_is_reported_instead_of_dropping_new_payload(self):
        one=make_snapshot(self.root/'one');import_snapshot(self.v,one,'trusted-owner')
        cursor=self.v.status()['cursors'].copy()
        with closing(sqlite3.connect(one/'message/message_0.db')) as db:
            db.execute(f'UPDATE [{TABLE}] SET message_content=? WHERE local_id=1',('不同内容',));db.commit()
        with self.assertRaisesRegex(VaultError,'^snapshot-source-identity-conflict:text:text-to-text$'):import_snapshot(self.v,one,'trusted-owner')
        self.assertEqual(self.v.status()['cursors'],cursor)

    def test_identity_conflict_names_what_changed_without_content(self):
        one=make_snapshot(self.root/'one');import_snapshot(self.v,one,'trusted-owner')
        with closing(sqlite3.connect(one/'message/message_0.db')) as db:
            db.execute(f'UPDATE [{TABLE}] SET message_content=?,local_type=10002,server_id=server_id+70000 WHERE local_id=1',('甲撤回了一条消息',));db.commit()
        with self.assertRaises(VaultError) as caught:import_snapshot(self.v,one,'trusted-owner')
        code=str(caught.exception)
        self.assertEqual(code,'snapshot-source-identity-conflict:text-and-native-id:text-to-recalled')
        # Must survive the plugin's code-only filters after the "vault-" prefix.
        self.assertRegex('vault-'+code,r'^[a-z][a-z0-9:_-]{0,100}$')

    def test_source_change_during_scan_does_not_advance_cursor(self):
        snapshot=make_snapshot(self.root/'one');original=self.v.record_message
        def modified(*args,**kwargs):
            result=original(*args,**kwargs)
            file=snapshot/'snapshot-manifest.json'
            value=json.loads(file.read_text());value['changed_during_test']=True;file.write_text(json.dumps(value))
            return result
        with patch.object(self.v,'record_message',side_effect=modified):
            with self.assertRaisesRegex(VaultError,'changed-during-import'):import_snapshot(self.v,snapshot,'trusted-owner')
        self.assertEqual(self.v.status()['cursors'],{})


if __name__=='__main__':unittest.main()
