from contextlib import closing
import hashlib,json,shutil,sqlite3,tempfile,unittest,os
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
import native_group_text_catalog as m
from vault.test_snapshot_import import make_snapshot,TABLE,TALKER,OWNER,SENDER
from vault.store import digest_file


class GroupTextTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.tmp.cleanup)
        self.root=make_snapshot(Path(self.tmp.name)/'snapshot',rows=[
            (1,9001,1,1772323200,2,SENDER+':\nhello',0),
            (2,9002,1,1772323201,1,'outgoing',0),
            (3,0,1,1772323202,1,'zero outgoing',0),
            (4,9004,1,1772323203,9,'wxid_fake:\nnot source author',0),
            (5,0,1,1772323204,2,'missing stable ID',0)],hash_manifest=True)
        self.args=dict(expected_account_username=OWNER,expected_source_id='fixture',account_id='account',native_group_chat_key='wechat:'+TALKER)
        self.rehash()
    def rehash(self):
        file=self.root/'snapshot-manifest.json';v=json.loads(file.read_text());v['source_id']='fixture'
        v['databases']=[{'database':p.relative_to(self.root).as_posix(),'authenticated':True,'plaintext_sha256':digest_file(p)} for p in self.root.rglob('*.db')]
        file.write_text(json.dumps(v),encoding='utf8')
    def write(self,sql,args=(),file='message/message_0.db'):
        with closing(sqlite3.connect(self.root/file)) as c:c.execute(sql,args);c.commit()
        self.rehash()
    def read(self,**kw):return m.list_native_group_texts(self.root,**{**self.args,**kw})
    def page(self,**kw):return m.page_native_group_texts(self.root,scan_key='a'*64,**{**self.args,**kw})

    def test_actual_sqlite_raw_author_no_current_members_and_unresolved(self):
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result=self.read(page_size=1)
        self.assertEqual(len(result['messages']),1);a=result['messages'][0]
        self.assertEqual(a['senderId'],SENDER);self.assertEqual(a['senderNumericId'],2)
        self.assertEqual(a['rawText'],SENDER+':\nhello');self.assertEqual(a['renderedText'],a['rawText'])
        self.assertEqual(a['renderStatus'],'raw-preserved')
        self.assertEqual(set(result['observedNativeIds']),{'wechat-server:9001','wechat-server:9002','wechat-server:9004'})
        self.assertEqual([(r['nativeId'],r['reason']) for r in result['unresolved']],[( 'wechat-server:9004','sender-unresolved'),(None,'server-id-unresolved')])
        self.assertNotIn('senderId',result['unresolved'][0])
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_missing_display_and_departed_member_not_rejected(self):
        self.write('DELETE FROM contact WHERE username=?',(SENDER,),file='contact/contact.db')
        a=self.read()['messages'][0]
        self.assertEqual(a['senderId'],SENDER);self.assertEqual(a['senderDisplayName'],'成员（显示名未解析）')

    def test_positive_sender_prefix_only_with_member_proof(self):
        with closing(sqlite3.connect(self.root/'contact/contact.db')) as c:
            c.execute('CREATE TABLE chat_room(id INTEGER,username TEXT)');c.execute('INSERT INTO chat_room VALUES(1,?)',(TALKER,))
            c.execute('CREATE TABLE name2id(username TEXT)');c.execute('INSERT INTO name2id VALUES(?)',(SENDER,))
            c.execute('CREATE TABLE chatroom_member(room_id INTEGER,member_id INTEGER)');c.execute('INSERT INTO chatroom_member VALUES(1,1)');c.commit()
        self.rehash();a=self.read()['messages'][0]
        self.assertEqual(a['renderedText'],'hello');self.assertEqual(a['renderStatus'],'verified-sender-prefix-stripped')
        self.write('DELETE FROM chatroom_member',file='contact/contact.db')
        self.assertEqual(self.read()['messages'][0]['renderedText'],SENDER+':\nhello')

    def test_full_paging_outgoing_only_then_1001_incoming(self):
        self.write(f'DELETE FROM [{TABLE}]')
        with closing(sqlite3.connect(self.root/'message/message_0.db')) as c:
            c.executemany(f'INSERT INTO [{TABLE}] VALUES(?,?,?,?,?,?,?)',[(i,0,1,1772323200+i,1,'own',0) for i in range(1,1002)])
            c.executemany(f'INSERT INTO [{TABLE}] VALUES(?,?,?,?,?,?,?)',[(i,100000+i,1,1772323200+i,2,'peer',0) for i in range(1002,2003)]);c.commit()
        self.rehash();first=self.page();self.assertEqual(first['messages'],[]);self.assertFalse(first['complete']);self.assertEqual(first['scanned'],500)
        self.assertEqual(len(self.read()['messages']),1001)
        for kwargs,error in [({'max_pages':1},'page-limit'),({'max_bytes':1},'total-byte-limit'),({'max_identities':1},'identity-limit')]:
            with self.assertRaisesRegex(m.NativeGroupTextCatalogError,error):self.read(**kwargs)

    def test_cross_shard_duplicates_are_not_silently_deduplicated(self):
        shutil.copyfile(self.root/'message/message_0.db',self.root/'message/message_1.db');self.rehash()
        with self.assertRaisesRegex(m.NativeGroupTextCatalogError,'native-id-ambiguous'):self.read(page_size=1)

    def test_authenticated_identity_cursor_scope_and_mutation(self):
        for changes,error in [({'expected_account_username':'wrong'},'account-mismatch'),({'expected_source_id':'wrong'},'source-mismatch'),({'native_group_chat_key':'wechat:'+SENDER},'group-scope-invalid')]:
            with self.assertRaisesRegex(m.NativeGroupTextCatalogError,error):self.read(**changes)
        first=self.page(page_size=1)
        corrupted=first['nextCursor'][:-1]+('1'if first['nextCursor'][-1]=='0'else'0')
        with self.assertRaisesRegex(m.NativeGroupTextCatalogError,'cursor-invalid'):self.page(cursor=corrupted,page_size=1)
        with self.assertRaisesRegex(m.NativeGroupTextCatalogError,'cursor-scope-mismatch'):self.page(cursor=first['nextCursor'],page_size=2)
        file=self.root/'message/message_0.db';file.write_bytes(file.read_bytes()+b'changed')
        with self.assertRaises(m.NativeGroupTextCatalogError):self.read()

    def test_sender_mapping_repair_is_real_not_payload_guess(self):
        self.write('INSERT INTO Name2Id(rowid,user_name) VALUES(9,?)',('wxid_repaired',))
        messages=self.read()['messages'];a=next(x for x in messages if x['serverId']=='9004')
        self.assertEqual(a['senderId'],'wxid_repaired');self.assertEqual(a['rawText'],'wxid_fake:\nnot source author')
        self.assertEqual(a['renderStatus'],'raw-preserved')

    def test_snapshot_changed_after_scan_or_link_rejected(self):
        original=m._presentation
        def changed(*args):
            result=original(*args);file=self.root/'message/message_0.db';file.write_bytes(file.read_bytes()+b'x');return result
        with patch.object(m,'_presentation',side_effect=changed):
            with self.assertRaisesRegex(m.NativeGroupTextCatalogError,'snapshot-changed'):self.read()

    def test_frozen_reader_reparse_component_guard_is_preserved(self):
        # Simulate Windows lstat reparse metadata, not an actual OS link receipt.
        original=os.lstat;target=self.root/'message/message_0.db'
        def lstat(path,*args,**kw):
            value=original(path,*args,**kw)
            if Path(path)==target:
                return SimpleNamespace(st_mode=value.st_mode,st_file_attributes=0x400)
            return value
        with patch('os.lstat',side_effect=lstat):
            with self.assertRaises(m.NativeGroupTextCatalogError):self.read()


if __name__=='__main__':unittest.main()
