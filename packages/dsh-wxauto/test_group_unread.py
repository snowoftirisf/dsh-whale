import hashlib,json,re,sqlite3,tempfile,unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import group_unread
from vault.test_snapshot_import import make_snapshot,TALKER,TABLE,OWNER,SENDER
from vault.store import digest_file


class GroupUnreadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.root=Path(self.temp.name)
        self.args={'native_chat_key':'wechat:'+TALKER,'account_id':'trusted-account',
                   'expected_account_username':OWNER,'expected_source_id':'test-source'}
    def tearDown(self):self.temp.cleanup()
    def refresh(self,snapshot):
        file=snapshot/'snapshot-manifest.json';manifest=json.loads(file.read_text(encoding='utf8'))
        manifest['source_id']='test-source'
        for item in manifest['databases']:item['plaintext_sha256']=digest_file(snapshot/item['database'])
        file.write_text(json.dumps(manifest),encoding='utf8')
    def snapshot(self,name='one',rows=None):
        rows=rows or [(1,9001,1,1772323200,2,'first new native text',0),(2,9002,1,1772323201,2,'second new native text',0)]
        snap=make_snapshot(self.root/name,rows,hash_manifest=True)
        with closing(sqlite3.connect(snap/'session/session.db')) as db:
            db.execute('CREATE TABLE Name2Id(user_name TEXT)');db.execute('INSERT INTO Name2Id VALUES(?)',(TALKER,))
            db.execute('CREATE TABLE SessionTable(username TEXT,unread_count INTEGER,unread_first_msg_srv_id INTEGER,last_msg_locald_id INTEGER,last_timestamp INTEGER)')
            db.execute('INSERT INTO SessionTable VALUES(?,?,?,?,?)',(TALKER,len(rows),rows[0][1],rows[-1][0],rows[-1][3]))
            db.execute('CREATE TABLE SessionUnreadListTable_1(username_id INTEGER,server_id INTEGER,create_time INTEGER,local_id INTEGER)')
            db.executemany('INSERT INTO SessionUnreadListTable_1 VALUES(1,?,?,?)',[(r[1],r[3],r[0]) for r in rows]);db.commit()
        self.refresh(snap);return snap
    def sql(self,snapshot,sql,args=(),db='session/session.db'):
        with closing(sqlite3.connect(snapshot/db)) as con:con.execute(sql,args);con.commit()
        self.refresh(snapshot)
    def test_exact_native_unread_ready_with_original_ids_sender_and_no_source_changes(self):
        snap=self.snapshot();before={str(p):p.read_bytes() for p in snap.rglob('*') if p.is_file()}
        value=group_unread.read_snapshot_group_unread(snap,**self.args)
        self.assertEqual(value['state'],'ready',value);self.assertEqual(value['unreadCount'],2)
        self.assertEqual(value['serverIds'],['9001','9002']);self.assertTrue(value['triggerReply'])
        self.assertTrue(all(m['senderId']==SENDER and m['senderDisplayName']=='群友' and m['direction']=='incoming' for m in value['messages']))
        self.assertEqual(value['messages'][0]['nativeId'],'wechat-server:9001')
        self.assertEqual(value['messages'][0]['text'],'first new native text')
        self.assertEqual(before,{str(p):p.read_bytes() for p in snap.rglob('*') if p.is_file()})
    def test_only_target_unread_ids_are_decoded_not_old_target_or_other_chat_payload(self):
        snap=self.snapshot()
        self.sql(snap,f'INSERT INTO [{TABLE}] VALUES(3,9999,1,1772323202,2,?,4)',(b'\x28\xb5\x2f\xfdBAD',),db='message/message_0.db')
        other='Msg_'+hashlib.md5(OWNER.encode()).hexdigest()
        with closing(sqlite3.connect(snap/'message/message_0.db')) as con:
            con.execute(f'CREATE TABLE [{other}] AS SELECT * FROM [{TABLE}] WHERE 0')
            con.execute(f'INSERT INTO [{other}] VALUES(4,9001,1,1772323203,2,?,4)',(b'\x28\xb5\x2f\xfdBAD',));con.commit()
        self.refresh(snap)
        value=group_unread.read_snapshot_group_unread(snap,**self.args)
        self.assertEqual(value['state'],'ready',value);self.assertEqual(value['messageCount'],2)
    def test_incomplete_conflicting_or_excessive_markers_stay_pending_without_partial_messages(self):
        changes=[('UPDATE SessionTable SET unread_count=0',(), 'marker-not-present'),
            ('UPDATE SessionTable SET unread_count=21',(),'budget-exceeded'),
            ('UPDATE SessionTable SET unread_first_msg_srv_id=0',(),'first-server-missing'),
            ('DELETE FROM SessionUnreadListTable_1 WHERE server_id=9002',(),'marker-count-conflict'),
            ('UPDATE SessionUnreadListTable_1 SET server_id=9001',(),'server-set-conflict'),
            ('UPDATE SessionUnreadListTable_1 SET local_id=99 WHERE server_id=9001',(),'envelope-conflict'),
            ('UPDATE SessionTable SET unread_first_msg_srv_id=9002',(),'first-marker-conflict'),
            ('UPDATE SessionTable SET last_msg_locald_id=1',(),'last-message-bound-conflict')]
        for index,(sql,args,code) in enumerate(changes):
            snap=self.snapshot(str(index));self.sql(snap,sql,args)
            value=group_unread.read_snapshot_group_unread(snap,**self.args)
            self.assertEqual(value['state'],'pending',value);self.assertIn(code,value['code']);self.assertEqual(value['messages'],[])
    def test_unknown_self_or_unsupported_media_does_not_silently_drop_rows(self):
        for index,rows in enumerate([
            [(1,9001,1,1772323200,1,'self is not a new user',0)],
            [(1,9001,1,1772323200,99,'unknown sender',0)],
            [(1,9001,1,1772323200,2,'text',0),(2,9002,49,1772323201,2,'file unavailable',0)],
            [(1,9001,3,1772323200,2,'image unavailable',0)],
        ]):
            value=group_unread.read_snapshot_group_unread(self.snapshot(str(index),rows),**self.args)
            self.assertEqual(value['state'],'pending',value);self.assertEqual(value['messages'],[]);self.assertFalse(value['triggerReply'])
            if index>=2:self.assertIn('media-not-supported',value['code'])
    def test_system_is_context_only_and_never_triggers_a_reply_alone(self):
        value=group_unread.read_snapshot_group_unread(self.snapshot(rows=[(1,9001,10000,1772323200,2,'system context',0)]),**self.args)
        self.assertEqual(value['state'],'ready',value);self.assertFalse(value['triggerReply'])
        self.assertTrue(value['messages'][0]['contextOnly']);self.assertEqual(value['messages'][0]['type'],'system')
    def test_voice_admission_keeps_original_message_shape_without_asr_or_duration(self):
        value=group_unread.read_snapshot_group_unread(self.snapshot(rows=[(1,9001,34,1772323200,2,'<msg><voicemsg/></msg>',0)]),**self.args)
        self.assertEqual(value['state'],'ready',value)
        message=value['messages'][0]
        self.assertEqual(message['type'],'voice')
        self.assertEqual(set(message),{'nativeId','serverId','snapshotSourceId','senderId','senderDisplayName','type','text','timestampSeconds','contextOnly','direction'})
        self.assertNotIn('duration',message)
    def test_text_budget_or_empty_text_does_not_become_partial_input(self):
        for index,text in enumerate(['','x'*32001]):
            value=group_unread.read_snapshot_group_unread(self.snapshot(str(index),[(1,9001,1,1772323200,2,text,0)]),**self.args)
            self.assertEqual(value['state'],'pending',value);self.assertEqual(value['messages'],[])
    def test_source_account_authentication_and_changed_snapshot_fail_closed(self):
        snap=self.snapshot()
        for key,value in [('expected_source_id','other'),('expected_account_username','other')]:
            result=group_unread.read_snapshot_group_unread(snap,**{**self.args,key:value})
            self.assertEqual(result['state'],'pending');self.assertEqual(result['messages'],[])
        original=group_unread._rows
        def changed(*args):
            yield from original(*args)
            file=snap/'snapshot-manifest.json';data=json.loads(file.read_text());data['changed']=True;file.write_text(json.dumps(data))
        with patch.object(group_unread,'_rows',side_effect=changed):
            self.assertIn('snapshot-changed',group_unread.read_snapshot_group_unread(snap,**self.args)['code'])
        manifest=json.loads((snap/'snapshot-manifest.json').read_text());manifest['databases']=[]
        (snap/'snapshot-manifest.json').write_text(json.dumps(manifest))
        result=group_unread.read_snapshot_group_unread(snap,**self.args)
        self.assertEqual(result['state'],'pending');self.assertEqual(result['messages'],[])
    def latest(self):
        data=self.root/'data';folder=data/'snapshots/windows-live/snapshots';folder.mkdir(parents=True)
        snapshot=self.snapshot();identifier=json.loads((snapshot/'snapshot-manifest.json').read_text())['snapshot_id']
        dest=folder/identifier;snapshot.rename(dest)
        latest={'status':'complete','scope':'chat','source_id':'test-source','snapshot':str(dest),'snapshot_id':identifier,
                'import':{'complete':True,'account_id':'trusted-account'}}
        pointer=data/'snapshots/windows-live/latest.json';pointer.write_text(json.dumps(latest))
        return data,pointer
    def test_latest_wrapper_uses_only_unchanged_committed_same_account_snapshot(self):
        data,pointer=self.latest();value=group_unread.read_latest_group_unread(data,**self.args)
        self.assertEqual(value['state'],'ready',value)
        original=group_unread.read_snapshot_group_unread
        def moved(*args,**kwargs):
            result=original(*args,**kwargs);pointer.write_text(pointer.read_text()+' ');return result
        with patch.object(group_unread,'read_snapshot_group_unread',side_effect=moved):
            result=group_unread.read_latest_group_unread(data,**self.args)
        self.assertEqual(result['state'],'pending');self.assertIn('latest-changed',result['code']);self.assertEqual(result['messages'],[])
        latest=json.loads(pointer.read_text());latest['import']['account_id']='other';pointer.write_text(json.dumps(latest))
        self.assertIn('account-unverified',group_unread.read_latest_group_unread(data,**self.args)['code'])


if __name__=='__main__':unittest.main()
