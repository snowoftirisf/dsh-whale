"""Native monitor unread fixtures: authenticated DBs and real Node inbox validators."""
import copy,json,subprocess,unittest
from pathlib import Path
from unittest.mock import patch
import native_group_monitor_unread as m
from native_group_text_catalog import list_native_group_texts
from native_group_text_authority import NativeGroupTextAuthority
from native_group_ui_binding import verify_native_monitor_binding
import test_native_group_handoff as fixtures
from vault.test_snapshot_import import TABLE

class MonitorUnreadTests(unittest.TestCase):
 def setUp(self):
  self.fixture=fixtures.NativeHandoffTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
  self.config=self.fixture.config;self.config['target']='owner';self.config['capture']['enabled']=True
  self.config['nativeVoiceSources']={'enabled':True}
  self.key=self.fixture.claim['nativeChatKey'];self.root=self.fixture.data;self.epoch='a'*32;self.clock=1772323210.123
  self.route={'accountId':self.config['accountId'],'nativeChatKey':self.key,'target':'new group','sessionId':self.fixture.claim['sessionId'],'chatId':'canonical','status':'active'}
  (self.root/'config/group-routes.local.json').write_text(json.dumps({'version':1,'accountId':self.config['accountId'],'groups':[self.route]}),encoding='utf8')
  self.latest=self.root/'snapshots/windows-live/latest.json'
 def node(self,script,payload):
  r=subprocess.run(['node','--input-type=module','-e',script],input=json.dumps(payload),text=True,encoding='utf8',capture_output=True,cwd=Path(__file__).parent,timeout=15)
  self.assertEqual(r.returncode,0,r.stderr)
 def bind(self):return verify_native_monitor_binding(self.config,self.key,self.epoch,'new group')
 def initialize(self,voice=False):
  if voice:self.fixture.voice_payload()
  source=self.fixture.snapshot
  catalog=list_native_group_texts(source,expected_account_username=self.config['capture']['accountUsername'],expected_source_id=self.config['capture']['sourceId'],
       account_id=self.config['accountId'],native_group_chat_key=self.key)
  a=NativeGroupTextAuthority(self.config,self.key,clock=lambda:self.clock);self.addCleanup(a.close);a.restore();a.prepare()
  self.node("import fs from 'node:fs';import {createNativeGroupTextInbox}from './native-group-text-inbox.mjs';const v=JSON.parse(fs.readFileSync(0,'utf8'));createNativeGroupTextInbox(v.config).baseline(v.options);",
       {'config':{'dataRoot':str(self.root),'accountId':self.config['accountId'],'nativeChatKey':self.key},
        'options':{'catalog':catalog,'authorityEpoch':self.epoch,'cutoverAtMs':a.prepared_at_ms}})
  a.commit(a.ticket,self.epoch)
  self.latest.write_text(json.dumps({'status':'complete','scope':'chat','source_id':self.config['capture']['sourceId'],'snapshot':str(source),
      'snapshot_id':source.name,'import':{'complete':True,'account_id':self.config['accountId']}}),encoding='utf8')
  self.before={'isnew':True,'new_count':2,'time':'12:01','content':'[语音] 4"'if voice else self.fixture.claim['messages'][-1]['text']}
  return self.bind()
 def voice_baseline(self):
  self.node("import fs from 'node:fs';import {createNativeVoiceInbox}from './native-voice-inbox.mjs';const v=JSON.parse(fs.readFileSync(0,'utf8'));createNativeVoiceInbox(v.config).initialize(v.catalog,{nativeChatKeys:[v.key]});",
      {'config':{'dataRoot':str(self.root),'accountId':self.config['accountId']},'key':self.key,
       'catalog':{'source':'authenticated-native-voice-catalog','accountId':self.config['accountId'],'snapshotId':self.fixture.snapshot.name,'voices':[]}})
 def test_text_uses_authenticated_count_tail_and_revalidates_pinned_after_latest_badge_changes(self):
  binding=self.initialize();before={str(p):p.read_bytes()for p in self.root.rglob('*')if p.is_file()}
  proof=m.qualify_native_monitor_unread(binding,self.before)
  self.assertEqual(proof.unread_count,2);self.assertEqual(proof.server_ids,('9001','9002'));self.assertEqual(proof.last_kind,'text')
  self.assertNotIn(self.before['content'],repr(proof));self.assertEqual(proof.rendered_tail,self.before['content'])
  self.assertEqual(before,{str(p):p.read_bytes()for p in self.root.rglob('*')if p.is_file()})
  self.latest.write_text(json.dumps({'new_latest':'badge cleared or moved'}),encoding='utf8')
  self.assertIs(m.revalidate_native_monitor_unread(proof,binding),proof)
 def test_earlier_voice_with_text_tail_requires_ownership_but_no_blob_read(self):
  self.fixture.voice_payload(text_tail=True);binding=self.initialize();self.voice_baseline()
  with patch.object(m,'read_native_voice_source',side_effect=AssertionError('not tail voice')) as read:
   proof=m.qualify_native_monitor_unread(binding,self.before)
   self.assertEqual(proof.last_kind,'text');self.assertIsNone(proof.last_voice_duration_ms)
   self.assertIs(m.revalidate_native_monitor_unread(proof,binding),proof);read.assert_not_called()
 def test_counts_preview_binding_and_fake_proof_cannot_qualify(self):
  binding=self.initialize()
  for change in ({'new_count':1},{'content':'different latest'},{'isnew':False}):
   with self.assertRaises(RuntimeError):m.qualify_native_monitor_unread(binding,{**self.before,**change})
  with self.assertRaises(RuntimeError):m.qualify_native_monitor_unread(True,self.before)
  with self.assertRaises(RuntimeError):m.revalidate_native_monitor_unread(object(),binding)
 def test_system_and_media_not_native_owned_stay_pending_without_any_ui(self):
  binding=self.initialize()
  for kind in (10000,3):
   self.fixture.source.sql(self.fixture.snapshot,f'UPDATE [{TABLE}] SET local_type=? WHERE local_id=2',(kind,),db='message/message_0.db')
   with self.assertRaisesRegex(RuntimeError,'unsupported-type'):m.qualify_native_monitor_unread(binding,self.before)
 def test_voice_requires_real_existing_chat_baseline_enabled_and_duration(self):
  binding=self.initialize(voice=True)
  with self.assertRaises(RuntimeError):m.qualify_native_monitor_unread(binding,self.before)
  self.voice_baseline();proof=m.qualify_native_monitor_unread(binding,self.before)
  self.assertEqual(proof.last_kind,'voice');self.assertEqual(proof.last_voice_duration_ms,4880)
  self.assertIs(m.revalidate_native_monitor_unread(proof,binding),proof)
  with self.assertRaisesRegex(RuntimeError,'voice-preview-mismatch'):m.qualify_native_monitor_unread(binding,{**self.before,'content':'[语音] 9"'})
  with self.assertRaisesRegex(RuntimeError,'voice-preview-mismatch'):m.qualify_native_monitor_unread(binding,{**self.before,'content':'[语音]'})
  self.config['nativeVoiceSources']['enabled']=False
  with self.assertRaisesRegex(RuntimeError,'voice-disabled'):m.qualify_native_monitor_unread(self.bind(),self.before)
 def test_corrupt_voice_baseline_marker_and_snapshot_change_are_rejected(self):
  binding=self.initialize(voice=True);self.voice_baseline()
  file=self.root/'config/native-voice-inbox.json';value=json.loads(file.read_text(encoding='utf8'));value['chatBaselines']=[]
  file.write_text(json.dumps(value),encoding='utf8')
  with self.assertRaises(RuntimeError):m.qualify_native_monitor_unread(binding,self.before)
  source=self.fixture.snapshot/'message/message_0.db';source.write_bytes(source.read_bytes()+b'changed')
  with self.assertRaises(RuntimeError):m.qualify_native_monitor_unread(binding,self.before)
 def test_marker_changed_after_qualification_never_revalidates(self):
  binding=self.initialize();proof=m.qualify_native_monitor_unread(binding,self.before)
  a=NativeGroupTextAuthority(self.config,self.key);file=a.paths()[1];value=json.loads(file.read_text(encoding='utf8'));value['createdAtMs']+=1
  value.pop('digest');value['digest']=m._hash(value);file.write_text(json.dumps(value),encoding='utf8')
  with self.assertRaises(RuntimeError):m.revalidate_native_monitor_unread(proof,binding)

if __name__=='__main__':unittest.main()
