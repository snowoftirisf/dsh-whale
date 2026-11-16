import hashlib,json,os,subprocess,unittest
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import native_group_handoff as handoff
from group_unread import read_snapshot_group_unread
import test_group_unread as unread_fixtures
from test_dynamic_group_watch import fixture,Row
from vault.test_snapshot_import import TABLE,TALKER,OWNER,SENDER
from vault.store import digest_file


class NativeHandoffTests(unittest.TestCase):
    def setUp(self):
        self.source=unread_fixtures.GroupUnreadTests();self.source.setUp();self.addCleanup(self.source.tearDown)
        root=self.source.root;self.data=root/'data';folder=self.data/'snapshots/windows-live/snapshots';folder.mkdir(parents=True)
        snapshot=self.source.snapshot();ident=json.loads((snapshot/'snapshot-manifest.json').read_text())['snapshot_id']
        self.snapshot=folder/ident;snapshot.rename(self.snapshot)
        value=read_snapshot_group_unread(self.snapshot,**self.source.args);assert value['state']=='ready'
        self.config={'dataRoot':str(self.data),'accountId':'trusted-account','sessionId':'owner',
            'capture':{'accountUsername':self.source.args['expected_account_username'],'sourceId':'test-source'}}
        self.claim={'format':'whale-native-group-inbox','version':1,'accountId':'trusted-account',
            'nativeChatKey':self.source.args['native_chat_key'],'target':'new group','sessionId':'group-session-fixture',
            'claimId':'a'*32,'state':'queued','snapshotId':ident,'unreadCount':value['unreadCount'],
            'serverIds':value['serverIds'],'messages':value['messages']}
        name=hashlib.sha256(('trusted-account\0'+self.claim['nativeChatKey']).encode()).hexdigest()+'.json'
        self.file=self.data/'config/auto-group-inbox'/name;self.file.parent.mkdir(parents=True);self.save()
    def save(self):
        self.claim['claimHash']=handoff.claim_hash(self.claim)
        self.file.write_text(json.dumps(self.claim,ensure_ascii=False),encoding='utf8')
    def verify(self):return handoff.verify_queued_handoff(self.config,target='new group',native_chat_key=self.claim['nativeChatKey'],claim_id='a'*32)
    def monitor(self,unread=2):
        f=fixture(unread=unread);text=self.claim['messages'][-1]['text']
        f.state['info']['new group']['content']=text
        f.state['rows']['new group']=[Row(2,self.claim['messages'][0]['text']),Row(3,text)]
        return f
    def register(self,f,proof):
        return f.register(native_chat_key=self.claim['nativeChatKey'],account_id='trusted-account',expected_account_id='trusted-account',native_handoff=proof)
    def raw_payloads(self,first,last):
        with closing(sqlite3.connect(self.snapshot/'contact/contact.db')) as con:
            con.execute('ALTER TABLE contact ADD COLUMN alias TEXT')
            con.execute('UPDATE contact SET alias=? WHERE username=?',('member_alias',SENDER))
            con.execute('CREATE TABLE chat_room(id INTEGER,username TEXT)');con.execute('INSERT INTO chat_room VALUES(1,?)',(TALKER,))
            con.execute('CREATE TABLE name2id(username TEXT)');con.executemany('INSERT INTO name2id VALUES(?)',[(OWNER,),(SENDER,)])
            con.execute('CREATE TABLE chatroom_member(room_id INTEGER,member_id INTEGER)')
            con.executemany('INSERT INTO chatroom_member VALUES(1,?)',[(1,),(2,)]);con.commit()
        with closing(sqlite3.connect(self.snapshot/'message/message_0.db')) as con:
            for local,text in [(1,first),(2,last)]:con.execute(f'UPDATE [{TABLE}] SET message_content=? WHERE local_id=?',(text,local))
            con.commit()
        self.source.refresh(self.snapshot)
        value=read_snapshot_group_unread(self.snapshot,**self.source.args);self.assertEqual(value['state'],'ready',value)
        self.claim['messages']=value['messages'];self.save()
    def voice_payload(self,*,only=False,text_tail=False):
        voice_local=1 if text_tail else 2
        blob=b'\x02#!SILK_V3'+b'fixture native voice bytes'
        xml=f'<msg><voicemsg voiceformat="4" voicelength="4880" length="{len(blob)}" /></msg>'
        with closing(sqlite3.connect(self.snapshot/'message/message_0.db')) as con:
            con.execute(f'UPDATE [{TABLE}] SET local_type=34,message_content=? WHERE local_id=?',(xml,voice_local))
            if only:con.execute(f'DELETE FROM [{TABLE}] WHERE local_id=1')
            con.commit()
        if only:
            with closing(sqlite3.connect(self.snapshot/'session/session.db')) as con:
                con.execute('DELETE FROM SessionUnreadListTable_1 WHERE local_id=1')
                con.execute('UPDATE SessionTable SET unread_count=1,unread_first_msg_srv_id=9002');con.commit()
        audio=self.snapshot/'message/media_0.db'
        with closing(sqlite3.connect(audio)) as con:
            con.execute('CREATE TABLE Name2Id(user_name TEXT)');con.execute('INSERT INTO Name2Id VALUES(?)',(TALKER,))
            con.execute('CREATE TABLE VoiceInfo(chat_name_id INTEGER,create_time INTEGER,local_id INTEGER,svr_id INTEGER,voice_data BLOB)')
            con.execute('INSERT INTO VoiceInfo VALUES(1,?,?,?,?)',(1772323199+voice_local,voice_local,9000+voice_local,blob));con.commit()
        path=self.snapshot/'snapshot-manifest.json';manifest=json.loads(path.read_text(encoding='utf8'))
        manifest['audio_capture']={'schemaVersion':1,'enabled':True,'scope':'chat','databases':['message/media_0.db']}
        manifest['databases'].append({'database':'message/media_0.db','authenticated':True,'plaintext_sha256':digest_file(audio)})
        path.write_text(json.dumps(manifest),encoding='utf8');self.source.refresh(self.snapshot)
        value=read_snapshot_group_unread(self.snapshot,**self.source.args);self.assertEqual(value['state'],'ready',value)
        self.claim.update(messages=value['messages'],serverIds=value['serverIds'],unreadCount=value['unreadCount']);self.save()
    def voice_monitor(self,*,only=False):
        f=fixture(unread=self.claim['unreadCount'])
        row=Row(3,'语音4"秒未播放');row.ClassName='mmui::ChatVoiceItemView'
        f.state['rows']['new group']=[row] if only else [Row(2,self.claim['messages'][0]['text']),row]
        f.state['info']['new group']['content']='[语音] 4"'
        return f
    def test_original_sender_prefix_is_stripped_once_despite_presentation_identifier_redaction(self):
        self.raw_payloads(SENDER+':\nIntroduction:\nkeep user heading',SENDER+':\r\nactual clean body')
        before=self.file.read_bytes();raw=self.claim['messages'][-1]['text']
        self.assertTrue(raw.startswith('[内部标识已隐藏]:'))
        proof=self.verify()
        self.assertEqual(proof.last_text,'actual clean body')
        self.assertEqual(proof.rendered_messages[0][1],'Introduction:\nkeep user heading')
        self.assertEqual([x[0] for x in proof.rendered_messages],[m['nativeId'] for m in self.claim['messages']])
        self.assertEqual(self.file.read_bytes(),before)
    def test_same_contact_member_alias_qualifies_but_unrecognized_user_colon_text_is_unchanged(self):
        self.raw_payloads('member_alias:\nverified alias body','Introduction:\nordinary heading')
        proof=self.verify()
        self.assertEqual(proof.rendered_messages[0][1],'verified alias body')
        self.assertEqual(proof.last_text,'Introduction:\nordinary heading')
    def test_real_other_member_prefix_is_conflict_and_placeholder_is_not_sender_identity(self):
        self.raw_payloads(OWNER+':\ncontradictory sender','[内部标识已隐藏]:\nordinary literal placeholder')
        with self.assertRaisesRegex(RuntimeError,'payload-sender-conflict'):self.verify()
        # Remove only the contradictory row; a literal placeholder must not
        # become a trusted sender simply because presentation also uses it.
        with closing(sqlite3.connect(self.snapshot/'message/message_0.db')) as con:
            con.execute(f'UPDATE [{TABLE}] SET message_content=? WHERE local_id=1',('ordinary text',));con.commit()
        self.source.refresh(self.snapshot)
        value=read_snapshot_group_unread(self.snapshot,**self.source.args);self.claim['messages']=value['messages'];self.save()
        self.assertEqual(self.verify().last_text,'[内部标识已隐藏]:\nordinary literal placeholder')
    def test_hash_matches_js_compact_utf8_and_original_snapshot_survives_latest_change(self):
        script="let s='';for await(const b of process.stdin)s+=b;const c=JSON.parse(s),crypto=await import('node:crypto');const m=c.messages.map(x=>[x.nativeId,x.serverId,x.senderId,x.senderDisplayName,x.type,x.text,x.timestampSeconds,x.contextOnly]);console.log(crypto.createHash('sha256').update(JSON.stringify([c.accountId,c.nativeChatKey,c.target,c.sessionId,c.claimId,c.snapshotId,c.serverIds,m])).digest('hex'));"
        result=subprocess.run(['node','--input-type=module','-e',script],input=json.dumps(self.claim,ensure_ascii=False),encoding='utf8',stdout=subprocess.PIPE,check=True)
        self.assertEqual(result.stdout.strip(),self.claim['claimHash'])
        before=self.file.read_bytes();proof=self.verify()
        (self.data/'snapshots/windows-live/latest.json').write_text('{}')
        self.assertEqual(handoff.revalidate_handoff(proof,target='new group',native_chat_key=proof.native_chat_key),proof)
        self.assertEqual(self.file.read_bytes(),before)
    def test_submitted_tampered_content_source_identity_and_claim_ids_are_refused(self):
        for mode in ['submitted','bad-hash','changed-text','changed-server-set','wrong-own','wrong-source','wrong-target','wrong-claim','boolean-count']:
            with self.subTest(mode=mode):
                original=json.loads(json.dumps(self.claim));config=json.loads(json.dumps(self.config))
                if mode=='submitted':self.claim['state']='submitted'
                elif mode=='changed-text':self.claim['messages'][0]['text']='forged text';self.save()
                elif mode=='changed-server-set':self.claim['serverIds'].reverse();self.save()
                elif mode=='wrong-own':self.config['capture']['accountUsername']='other'
                elif mode=='wrong-source':self.config['capture']['sourceId']='other'
                elif mode=='wrong-target':self.claim['target']='another';self.save()
                elif mode=='wrong-claim':self.claim['claimId']='b'*32;self.save()
                elif mode=='boolean-count':self.claim['unreadCount']=True
                else:self.claim['claimHash']='0'*64
                if mode in ['submitted','bad-hash','boolean-count']:self.file.write_text(json.dumps(self.claim))
                with self.assertRaisesRegex(RuntimeError,'handoff-unverified'):self.verify()
                self.claim=original;self.config=config;self.save()
    def test_hardlink_claim_is_rejected_before_trusting_its_contents(self):
        duplicate=self.file.with_suffix('.copy')
        try:os.link(self.file,duplicate)
        except OSError:self.skipTest('host denies hardlink creation')
        with self.assertRaisesRegex(RuntimeError,'handoff-unverified'):self.verify()
    def test_native_queued_handoff_seeds_baseline_without_global_recovery_or_claim_write(self):
        f=self.monitor();proof=self.verify();before=self.file.read_bytes();gate=dict(f.monitor.gate.handled)
        result=self.register(f,proof)
        self.assertTrue(result['ok'],result);self.assertTrue(result['nativeQueuedHandoff'])
        self.assertFalse(f.monitor.recover_startup_unread);self.assertEqual(f.monitor.startup_pending,{})
        self.assertEqual(f.monitor.gate.handled,gate);self.assertEqual(f.state['current'],'owner')
        self.assertIn('new group',f.monitor.cursor.rows);self.assertEqual(self.file.read_bytes(),before)
        self.assertEqual(self.claim['state'],'queued')
    def test_already_read_ui_zero_can_handoff_only_with_real_still_queued_source(self):
        f=self.monitor(unread=0);result=self.register(f,self.verify())
        self.assertTrue(result['ok'],result);self.assertTrue(result['nativeQueuedHandoff'])
        f=self.monitor();result=self.register(f,{'claimId':'a'*32})
        self.assertEqual(result['code'],'dynamic-group-native-handoff-unverified');self.assertEqual(f.state['calls'],[])
    def test_count_preview_duplicate_or_viewport_changes_cannot_be_accepted(self):
        for mode in ['count','preview','duplicate','viewport','arrival']:
            f=self.monitor();proof=self.verify()
            if mode=='count':f.state['info']['new group']['new_count']=1
            elif mode=='preview':f.state['info']['new group']['content']='different latest text'
            elif mode=='duplicate':f.state['rows']['new group'].insert(0,Row(4,self.claim['messages'][-1]['text']))
            else:
                original=f.monitor.controls;calls=[0]
                def controls():
                    calls[0]+=1
                    if mode=='viewport' and calls[0]==2:f.state['rows']['new group'].append(Row(5,'new arrival'))
                    if mode=='arrival' and calls[0]==1:f.state['info']['new group']['content']='new arrival'
                    return original()
                f.monitor.controls=controls
            result=self.register(f,proof)
            self.assertFalse(result['ok'],(mode,result));self.assertTrue(result['pending'])
            self.assertEqual(f.routes,{'owner':'friend'});self.assertEqual(f.registry,{})
            self.assertNotIn('new group',f.monitor.cursor.rows)
    def test_submitted_during_navigation_rolls_back_and_does_not_replay(self):
        f=self.monitor();proof=self.verify();original=f.monitor.select
        def select(name):
            original(name)
            if name=='new group':self.claim['state']='submitted';self.save()
        f.monitor.select=select
        result=self.register(f,proof)
        self.assertFalse(result['ok']);self.assertEqual(f.state['current'],'owner')
        self.assertEqual(f.registry,{});self.assertNotIn('new group',f.monitor.cursor.rows)
    def test_voice_only_tail_exact_audio_proof_has_no_fabricated_text(self):
        self.voice_payload(only=True);before=self.file.read_bytes();proof=self.verify()
        self.assertEqual(proof.last_kind,'voice');self.assertEqual(proof.last_voice_duration_ms,4880)
        self.assertEqual(proof.last_text,'')
        self.assertEqual(proof.rendered_messages[-1][1],self.claim['messages'][-1]['text'])
        result=self.register(self.voice_monitor(only=True),proof)
        self.assertTrue(result['ok'],result);self.assertTrue(result['nativeQueuedHandoff'])
        self.assertEqual(self.file.read_bytes(),before)
    def test_mixed_text_then_voice_tail_preserves_both_raw_messages(self):
        self.voice_payload();proof=self.verify();f=self.voice_monitor()
        self.assertEqual([message['type'] for message in self.claim['messages']],['text','voice'])
        self.assertEqual(len(proof.rendered_messages),2)
        self.assertTrue(self.register(f,proof)['ok'])
        self.assertEqual(f.state['current'],'owner')
    def test_mixed_voice_then_text_keeps_original_strict_text_tail(self):
        self.voice_payload(text_tail=True);proof=self.verify()
        self.assertEqual(proof.last_kind,'text');self.assertIsNone(proof.last_voice_duration_ms)
        self.assertEqual(proof.last_text,self.claim['messages'][-1]['text'])
        self.assertTrue(self.register(self.monitor(),proof)['ok'])
    def test_voice_duration_duplicate_nonvoice_and_changed_preview_stay_pending(self):
        self.voice_payload();proof=self.verify()
        for mode in ('seconds','malformed-seconds','duplicate','nonvoice','preview'):
            with self.subTest(mode=mode):
                f=self.voice_monitor()
                if mode=='seconds':f.state['rows']['new group'][-1].Name='语音9"秒未播放'
                elif mode=='malformed-seconds':f.state['rows']['new group'][-1].Name='语音未知时长'
                elif mode=='duplicate':
                    duplicate=Row(4,'语音4"秒未播放');duplicate.ClassName='mmui::ChatVoiceItemView'
                    f.state['rows']['new group'].insert(0,duplicate)
                elif mode=='nonvoice':f.state['rows']['new group'][-1].ClassName='mmui::ChatTextItemView'
                else:f.state['info']['new group']['content']='[语音] 9"'
                result=self.register(f,proof)
                self.assertFalse(result['ok'],result);self.assertTrue(result['pending'])
                self.assertNotIn('new group',f.monitor.cursor.rows)
                self.assertEqual(f.routes,{'owner':'friend'})
    def test_voice_tail_missing_or_malformed_audio_never_becomes_handoff(self):
        self.voice_payload()
        with closing(sqlite3.connect(self.snapshot/'message/media_0.db')) as con:
            con.execute('UPDATE VoiceInfo SET voice_data=?',(b'not SILK',));con.commit()
        self.source.refresh(self.snapshot)
        with self.assertRaisesRegex(RuntimeError,'handoff-unverified'):self.verify()
    def test_voice_handoff_keeps_count_viewport_and_queued_claim_guards(self):
        self.voice_payload();proof=self.verify()
        for mode in ('count','viewport','submitted'):
            with self.subTest(mode=mode):
                f=self.voice_monitor()
                if mode=='count':f.state['info']['new group']['new_count']=1
                elif mode=='viewport':
                    original=f.monitor.controls;calls=[0]
                    def controls():
                        calls[0]+=1
                        if calls[0]==2:f.state['rows']['new group'].append(Row(5,'new arrival'))
                        return original()
                    f.monitor.controls=controls
                else:
                    original=f.monitor.select
                    def select(name):
                        original(name)
                        if name=='new group':self.claim['state']='submitted';self.save()
                    f.monitor.select=select
                result=self.register(f,proof)
                self.assertFalse(result['ok'],result);self.assertTrue(result['pending'])
                self.assertNotIn('new group',f.monitor.cursor.rows)
                self.assertEqual(f.routes,{'owner':'friend'})
                self.assertEqual(f.state['current'],'owner')
                self.claim['state']='queued';self.save()


if __name__=='__main__':unittest.main()
