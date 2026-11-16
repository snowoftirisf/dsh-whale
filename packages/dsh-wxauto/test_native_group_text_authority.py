import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid

from native_group_text_authority import NativeGroupTextAuthority, trusted_group_route, normalized_catalog
from native_owner_text_claim import _hash

ROOT=Path(__file__).resolve().parent
KEY='wechat:123@chatroom'
EPOCH='a'*32


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name);(self.folder/'config').mkdir()
        self.config={'dataRoot':str(self.folder),'accountId':'account-test','target':'Owner',
            'capture':{'enabled':True,'accountUsername':'wxid_self','sourceId':'source-test'}}
        self.route={'accountId':'account-test','nativeChatKey':KEY,'target':'Group A','sessionId':'original-group-session',
            'chatId':'original-group-chat','status':'active'}
        self.registry()
        row={'nativeId':'wechat-server:10','serverId':'10','nativeChatKey':KEY,'senderId':'wxid_member',
            'senderDisplayName':'Member','senderNumericId':7,'rawText':'old text','renderedText':'old text',
            'renderStatus':'raw-preserved','timestampSeconds':1900,'localId':1,
            'snapshotSourceId':'message_0/table/1','direction':'incoming'}
        self.catalog={'source':'authenticated-native-group-text-catalog','accountId':'account-test','nativeChatKey':KEY,
            'scopeDigest':_hash(['account-test',KEY]),'snapshotId':str(uuid.uuid4()),'complete':True,
            'messages':[row],'unresolved':[],'observedNativeIds':['wechat-server:10']}
        self.reader_calls=[]

    def registry(self):
        (self.folder/'config/group-routes.local.json').write_text(json.dumps({'version':1,'accountId':'account-test','groups':[self.route]}),encoding='utf8')

    def reader(self,*args,**kwargs):
        self.reader_calls.append((args,kwargs));return copy.deepcopy(self.catalog)

    def make(self):
        a=NativeGroupTextAuthority(self.config,KEY,catalog_reader=self.reader,clock=lambda:2000.123)
        self.addCleanup(a.close)
        return a

    def start(self):
        a=self.make();self.assertEqual(a.restore()['mode'],'ui');a.prepare();return a

    def baseline(self,a,*,delegated=False):
        # Exercise the actual Node writer rather than mirroring its JSON schema.
        module=(ROOT/'native-group-text-inbox.mjs').as_uri()
        script="import {createNativeGroupTextInbox} from "+json.dumps(module)+";let s='';for await(const c of process.stdin)s+=c;const p=JSON.parse(s);createNativeGroupTextInbox({...p.config,now:()=>2000123}).baseline(p.args);"
        payload={'config':{'dataRoot':str(self.folder),'accountId':'account-test','nativeChatKey':KEY},
            'args':{'catalog':self.catalog,'authorityEpoch':EPOCH,'cutoverAtMs':a.prepared_at_ms,
                'delegatedUnreadNativeIds':['wechat-server:10']if delegated else []}}
        result=subprocess.run(['node','--input-type=module','-e',script],input=json.dumps(payload),text=True,
            encoding='utf8',stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=ROOT,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)

    def event(self):
        return {'id':'actual-ui-id','target':'Group A','kind':'group','mediaType':'text',
            'sender':'Owner','text':'批准操作 a1234567','images':[],'files':[]}

    def test_absent_marker_is_inert_ui_without_files_created(self):
        a=self.make();self.assertFalse(a.paths()[0].exists());self.assertEqual(a.restore()['mode'],'ui')
        self.assertFalse(a.paths()[0].exists());self.assertEqual(a.route_event(self.event())['action'],'pass')

    def test_no_marker_keeps_ui_group_working_without_capture_configuration(self):
        self.config.pop('capture');a=self.make();self.assertEqual(a.restore()['mode'],'ui')
        self.assertEqual(a.route_event(self.event()),{'action':'pass','event':self.event()})

    def test_node_baseline_commit_and_restart_exact_original_route(self):
        a=self.start();self.baseline(a);ticket=a.ticket
        self.assertEqual(a.commit(ticket,EPOCH)['mode'],'native')
        self.assertEqual(self.reader_calls[0][1]['native_group_chat_key'],KEY)
        self.assertEqual(self.make().restore()['authorityEpoch'],EPOCH)
        self.assertEqual(a.route_event(self.event()),{'action':'hint','authorityEpoch':EPOCH})

    def test_preparing_real_envelope_blocks_commit_and_abort_drains_once(self):
        a=self.start();event=self.event();a.route_event(event);event['text']='mutated after admission'
        with self.assertRaisesRegex(RuntimeError,'not-quiet'):a.commit(a.ticket,EPOCH)
        ticket=a.ticket;pending=a.abort(ticket)
        self.assertEqual(pending['events'],[self.event()]);self.assertEqual(a.status()['mode'],'preparing')
        with self.assertRaisesRegex(RuntimeError,'restore-unverified'):self.make().restore()
        a.finish_abort(ticket,1);self.assertEqual(self.make().restore()['mode'],'ui')
        with self.assertRaisesRegex(RuntimeError,'abort-unverified'):a.finish_abort(ticket,1)

    def test_prepare_crash_never_falls_back_to_ui(self):
        a=self.start();self.assertTrue(a.paths()[3].exists());b=self.make()
        with self.assertRaisesRegex(RuntimeError,'restore-unverified'):b.restore()
        self.assertEqual(b.status()['mode'],'blocked')

    def test_delegated_unread_cannot_enter_generic_existing_group_commit(self):
        a=self.start();self.baseline(a,delegated=True)
        with self.assertRaisesRegex(RuntimeError,'baseline-required'):a.commit(a.ticket,EPOCH)
        self.assertFalse(a.paths()[1].exists())

    def test_baseline_held_unresolved_and_server_zero_match_real_node_writer(self):
        u={'nativeId':'wechat-server:11','serverId':'11','nativeChatKey':KEY,'rawText':'unmapped',
            'timestampSeconds':1901,'localId':2,'snapshotSourceId':'message_0/table/2',
            'senderNumericId':8,'reason':'sender-unresolved','direction':'unknown'}
        z={**u,'nativeId':None,'serverId':None,'localId':3,'snapshotSourceId':'message_0/table/3','reason':'server-id-unresolved'}
        self.catalog['unresolved']=[u,z];self.catalog['observedNativeIds'].append('wechat-server:11')
        a=self.start();self.baseline(a);self.assertEqual(a.commit(a.ticket,EPOCH)['mode'],'native')

    def test_validly_digested_baseline_tamper_still_requires_actual_catalog(self):
        a=self.start();self.baseline(a);file=a.paths()[2];value=json.loads(file.read_text('utf8'))
        value['claims'][0]['rawText']='forged';value['claims'][0]['renderedText']='forged'
        value.pop('digest');value['digest']=_hash(value);file.write_text(json.dumps(value,ensure_ascii=False,separators=(',',':')),encoding='utf8')
        with self.assertRaisesRegex(RuntimeError,'baseline-claims-mismatch'):a.commit(a.ticket,EPOCH)
        self.assertFalse(a.paths()[1].exists())

    def test_capture_scope_change_between_prepare_commit_refuses(self):
        a=self.start();self.baseline(a);self.catalog['accountId']='other'
        with self.assertRaisesRegex(RuntimeError,'catalog-unverified'):a.commit(a.ticket,EPOCH)

    def test_partial_exclusive_marker_is_unknown_and_never_deleted(self):
        a=self.start();self.baseline(a);a.paths()[1].write_text('{',encoding='utf8')
        with self.assertRaisesRegex(RuntimeError,'commit-unknown'):a.commit(a.ticket,EPOCH)
        self.assertEqual(a.paths()[1].read_text('utf8'),'{');self.assertEqual(a.mode,'blocked')

    def test_current_route_rename_keeps_native_session_but_rotation_refuses(self):
        a=self.start();self.baseline(a);a.commit(a.ticket,EPOCH)
        self.route['target']='Renamed Group';self.registry();self.assertEqual(self.make().restore()['target'],'Renamed Group')
        self.route['sessionId']='other-session';self.registry()
        with self.assertRaisesRegex(RuntimeError,'restore-unverified'):self.make().restore()

    def test_group_owner_and_media_never_gain_owner_approval_semantics(self):
        a=self.start();self.assertEqual(a.route_event(self.event())['action'],'deferred')
        private={**self.event(),'kind':'friend','target':'Owner'}
        self.assertEqual(a.route_event(private)['action'],'pass')
        self.assertEqual(a.route_event({**self.event(),'mediaType':'voice'})['action'],'pass')

    def test_registry_ambiguity_inactive_and_foreign_account_are_rejected(self):
        self.route['status']='pending';self.registry()
        with self.assertRaisesRegex(RuntimeError,'route-inactive'):trusted_group_route(self.config,KEY)
        self.route['status']='active';self.route['accountId']='foreign';self.registry()
        with self.assertRaisesRegex(RuntimeError,'route-inactive'):trusted_group_route(self.config,KEY)

    def test_stale_second_controller_cannot_replace_preparation(self):
        a=self.make();b=self.make();a.restore();b.restore();a.prepare();ticket=a.ticket
        with self.assertRaises(FileExistsError):b.prepare()
        self.assertEqual(json.loads(a.paths()[3].read_text('utf8'))['ticket'],ticket)

    def test_route_rotation_after_prepare_or_live_commit_blocks(self):
        a=self.start();self.baseline(a);self.route['sessionId']='changed';self.registry()
        with self.assertRaisesRegex(RuntimeError,'route-changed'):a.commit(a.ticket,EPOCH)
        a.abort(a.ticket);a.finish_abort(a.ticket,0)
        self.route['sessionId']='original-group-session';self.registry()
        # Reusing an uncommitted inbox is intentionally not an initialization.
        b=self.start();b.prepared_at_ms=2000123;b.commit(b.ticket,EPOCH)
        self.route['chatId']='foreign-chat';self.registry()
        self.assertEqual(b.status()['mode'],'blocked')


if __name__=='__main__':unittest.main()
