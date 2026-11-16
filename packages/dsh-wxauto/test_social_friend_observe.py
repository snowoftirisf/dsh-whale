"""Inert workflow fixtures: never connect to or operate a real window."""
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from social_friend_observe import lookup_observed_ui, observe_owner, OBSERVED_VERSION, OBSERVED_CLIENT_SHA
from social_wxauto_backend import WxautoSocialBackend
from social_ipc import SocialDispatcher


class ObserveTests(unittest.TestCase):
    def setUp(self):
        self.stage='main';self.ops=[];self.report={};self.fail_read=False;self.fail_close=False
        self.root=NS(ClassName='mmui::MainWindow',ProcessId=7,NativeWindowHandle=10,IsOffscreen=0,GetRuntimeId=lambda:[10])
        self.form=NS(ClassName='mmui::AddFriendWindow',ControlTypeName='WindowControl',Name='添加朋友',
            AutomationId='AddFriendWindow',ProcessId=7,NativeWindowHandle=12,IsOffscreen=0,GetRuntimeId=lambda:[12])
        self.popup=NS(NativeWindowHandle=11)
        def click(stage):
            self.ops.append(('click',stage))
            if stage=='main' and self.fail_close:raise RuntimeError('fixture-close-error')
            self.stage=stage
        quick=NS(Click=lambda **kw:click('quick'))
        entry=NS(Click=lambda **kw:click('form'))
        close=NS(ProcessId=7,ControlTypeName='ButtonControl',IsOffscreen=0,IsEnabled=True,
                 Click=lambda **kw:click('main'))
        def unique(roots,cls,name):return quick if name=='快捷操作' else close
        def wait(check):
            value=check()
            if not value:raise RuntimeError('fixture-observation-timeout')
            return value
        def refresh(wx,target,kind):self.ops.append(('restore',target,kind))
        self.refresh=refresh
        self.wx=NS(_api=NS(control=self.root),ChatInfo=lambda:{'chat_name':'group-a','chat_type':'group'})
        self.client={'clientVersion':OBSERVED_VERSION,'clientSha256':OBSERVED_CLIENT_SHA,'executableName':'Weixin.exe'}
        self.uia=NS(GetForegroundWindow=lambda:{'main':10,'quick':11,'form':12}[self.stage])
        self.helpers=NS(unique=unique,nodes=lambda root:[],wait_for=wait,
            find_entry=lambda roots,pid:entry if self.stage=='quick' else None,
            find_form=lambda roots,pid:self.form if self.stage=='form' else None,
            verified_quick_menu=lambda *args:(self.popup,entry,('fixture-menu',)),
            close_quick_menu=lambda *args:click('main') or True,
            redact_observation=lambda value:value,
            sha=lambda value:hashlib.sha256(value.encode()).hexdigest(),
            visible_owned_roots=lambda *args:[self.root]+([self.form] if self.stage=='form' else [self.popup] if self.stage=='quick' else []))

    def lookup(self,activate=False,candidate=False,sequence=None,identity=None):
        outer=self
        class Search:
            def __init__(self,*args,**kwargs):pass
            def dispatch(self,account):outer.ops.append(('search',account));return {'attempted':True}
        def read(*args,**kwargs):
            if self.fail_read:raise RuntimeError('fixture-read-error')
            if sequence is not None:
                value=next(sequence)
                if callable(value):return value()
                if isinstance(value,Exception):raise value
                return value
            return {'state':'no-match-observed','controls':[],'queryValueMatched':True}
        with patch('social_friend_search.FriendSearch',Search), patch('social_contact_observation.read_friend_search_result',read), \
                patch('social_contact_observation.read_candidate_profile',side_effect=identity or (lambda *a: {'identityMatched':True,'identityEvidence':'matched'})):
            return lookup_observed_ui(self.wx,'owner_public_id','owner',routes={'group-a':'group'},
                client=self.client,uia=self.uia,windows=lambda:[],client_probe=lambda wx:self.client,
                refresh_chat=self.refresh,helpers=self.helpers,report=self.report,strict_restore=True,
                activate_bound_main=activate,candidate_profile=candidate)

    def test_existing_instance_only_search_once_close_and_restore_same_group(self):
        result=self.lookup()
        self.assertTrue(result['originalChatRestored'])
        self.assertEqual(self.ops,[('click','quick'),('click','form'),('search','owner_public_id'),
                                  ('click','main'),('restore','group-a','group')])

    def test_bound_show_once_before_search_when_harness_has_foreground(self):
        self.stage='external'
        self.uia.GetForegroundWindow=lambda:{'external':99,'main':10,'quick':11,'form':12}[self.stage]
        self.helpers.visible_owned_roots=lambda *args:[self.root]+([self.form] if self.stage=='form' else [self.popup] if self.stage=='quick' else [])
        def show():self.ops.append(('show',));self.stage='main'
        self.wx.Show=show
        self.lookup(activate=True)
        self.assertEqual(self.ops[0],('show',))
        self.assertEqual(self.ops.count(('show',)),1)
        self.assertTrue(self.report['originalChatRestored'])

    def test_reader_error_still_closes_owned_form_and_restores_without_retry(self):
        self.fail_read=True
        with self.assertRaisesRegex(RuntimeError,'fixture-read-error'):self.lookup()
        self.assertEqual(sum(op[0]=='search' for op in self.ops),1)
        self.assertTrue(self.report['originalChatRestored'])

    def test_loading_content_change_then_stable_profile_dispatches_search_once(self):
        import itertools
        profile=NS(ClassName='mmui::ProfileHeaderUi',ControlTypeName='GroupControl')
        self.helpers.nodes=lambda root:[profile]
        sequence=itertools.chain([RuntimeError('contact-search-controls-changed')],
            itertools.repeat({'state':'profile-observed','controls':[]}))
        identities=iter([RuntimeError('contact-observation-controls-changed'),{'identityMatched':True,'identityEvidence':'matched'}])
        def identity(*args):
            value=next(identities)
            if isinstance(value,Exception):raise value
            return value
        result=self.lookup(candidate=True,sequence=sequence,identity=identity)
        self.assertTrue(result['profileIdentityMatched'])
        self.assertEqual(sum(op[0]=='search' for op in self.ops),1)
        self.assertTrue(result['originalChatRestored'])

    def test_continuous_content_changes_hit_original_deadline_without_redispatch(self):
        import itertools
        clock=[0]
        def sleep(n):clock[0]+=n
        with patch('social_friend_observe.time.monotonic',side_effect=lambda:clock[0]), \
                patch('social_friend_observe.time.sleep',side_effect=sleep):
            with self.assertRaisesRegex(RuntimeError,'contact-search-observation-timeout'):
                self.lookup(candidate=True,sequence=itertools.repeat(RuntimeError('contact-search-controls-changed')))
        self.assertGreaterEqual(clock[0],3)
        self.assertLess(clock[0],3.2)
        self.assertEqual(sum(op[0]=='search' for op in self.ops),1)
        self.assertTrue(self.report['originalChatRestored'])

    def test_foreground_change_during_content_race_does_not_retry_read(self):
        reads=[]
        def change():
            reads.append(1);self.uia.GetForegroundWindow=lambda:99
            raise RuntimeError('contact-search-controls-changed')
        with self.assertRaisesRegex(RuntimeError,'social-chat-restore-failed'):
            self.lookup(candidate=True,sequence=iter([change]))
        self.assertEqual(len(reads),1)
        self.assertEqual(sum(op[0]=='search' for op in self.ops),1)

    def test_old_mismatch_then_new_matching_profile_is_passively_reread(self):
        import itertools
        self.helpers.nodes=lambda root:[NS(ClassName='mmui::ProfileHeaderUi',ControlTypeName='GroupControl')]
        identities=iter([{'identityMatched':False,'identityEvidence':'id-mismatch'},
                         {'identityMatched':True,'identityEvidence':'matched'}])
        result=self.lookup(candidate=True,sequence=itertools.repeat({'state':'profile-observed','controls':[]}),
            identity=lambda *a:next(identities))
        self.assertTrue(result['profileIdentityMatched'])
        self.assertEqual(sum(op[0]=='search' for op in self.ops),1)

    def test_unavailable_candidate_id_stays_unmatched_but_buttons_are_read_without_click(self):
        import itertools
        clock=[0]
        self.helpers.nodes=lambda root:[NS(ClassName='mmui::ProfileHeaderUi',ControlTypeName='GroupControl'),
            NS(ClassName='mmui::XButton',ControlTypeName='ButtonControl',Name='添加到通讯录',ProcessId=7,IsOffscreen=0)]
        with patch('social_friend_observe.time.monotonic',side_effect=lambda:clock[0]), \
                patch('social_friend_observe.time.sleep',side_effect=lambda n:clock.__setitem__(0,clock[0]+n)):
            result=self.lookup(candidate=True,sequence=itertools.repeat({'state':'profile-observed','controls':[]}),
                identity=lambda *a:{'identityMatched':False,'identityEvidence':'id-unavailable'})
        self.assertFalse(result['profileIdentityMatched'])
        self.assertEqual(result['identityEvidence'],'id-unavailable')
        self.assertEqual(result['profileButtons'][0]['label'],'add-contact')
        self.assertEqual(self.ops,[('click','quick'),('click','form'),('search','owner_public_id'),
                                  ('click','main'),('restore','group-a','group')])

    def test_owned_close_unknown_is_fatal_and_does_not_repeat_input(self):
        self.fail_close=True
        with self.assertRaisesRegex(RuntimeError,'social-chat-restore-failed'):self.lookup()
        self.assertEqual(sum(op==('click','main') for op in self.ops),1)
        self.assertNotIn(('restore','group-a','group'),self.ops)

    def test_backend_requires_same_ui_thread_and_marks_restore_fatal(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as root:
            backend=WxautoSocialBackend(self.wx,{'dataRoot':root,'target':'owner','groups':['group-a']},
                client_probe=lambda wx:self.client,refresh_chat=self.refresh)
            backend.ui_thread_id=threading.get_ident()+1
            with patch('social_friend_observe.observe_owner') as call:
                with self.assertRaisesRegex(RuntimeError,'ui-thread-required'):backend.observe('friend-search-owner')
                call.assert_not_called()
            backend.ui_thread_id=threading.get_ident()
            with patch('social_friend_observe.observe_owner',side_effect=RuntimeError('social-chat-restore-failed')):
                with self.assertRaisesRegex(RuntimeError,'restore-failed'):backend.observe('friend-search-owner')
            self.assertTrue(backend.chat_binding_failed)
            self.assertIsNone(backend._navigation_context)

    def test_dispatcher_only_fixed_owner_purpose_and_rejects_arbitrary_arguments(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as root:
            calls=[]
            backend=NS(observe=lambda purpose:calls.append(purpose) or {'readOnly':True})
            d=SocialDispatcher({'dataRoot':root,'target':'owner','groups':['group-a']},backend)
            command={'op':'social-observe','target':'owner','purpose':'friend-search-owner'}
            self.assertTrue(d.dispatch(command)['readOnly'])
            for bad in ({**command,'target':'group-a'},{**command,'purpose':'arbitrary'},
                        {**command,'account':'arbitrary'}):
                with self.assertRaises(RuntimeError):d.dispatch(bad)
            self.assertEqual(calls,['friend-search-owner'])

    def test_backend_only_maps_fixed_probe_codes_and_never_exception_body(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as root:
            backend=WxautoSocialBackend(self.wx,{'dataRoot':root,'target':'owner'},
                client_probe=lambda wx:self.client,refresh_chat=self.refresh)
            for error,code in ((RuntimeError('probe-menu-foreground-changed'),'social-observation-menu-foreground-changed'),
                               (RuntimeError('private account/path body'),'social-observation-unavailable'),
                               (ValueError('private account/path body'),'social-observation-unavailable')):
                with self.subTest(error=type(error).__name__),patch('social_friend_observe.observe_owner',side_effect=error):
                    with self.assertRaises(RuntimeError) as caught:backend.observe('friend-search-owner')
                    self.assertEqual(str(caught.exception),code)
                    self.assertFalse(backend.chat_binding_failed)

    def test_client_and_snapshot_identity_fail_before_any_ui(self):
        with self.assertRaisesRegex(RuntimeError,'client-unverified'):
            observe_owner(self.wx,{},frozenset(),client={'clientVersion':'unknown'},
                client_probe=lambda wx:self.client,refresh_chat=self.refresh,helpers=self.helpers)
        self.assertEqual(self.ops,[])
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as root:
            folder=Path(root)/'snapshots/windows-live/snapshots/fixture';folder.mkdir(parents=True)
            (folder/'snapshot-manifest.json').write_text(json.dumps({'source_id':'wrong','account_username':'self'}))
            with patch('group_discovery.discover_groups',return_value={'snapshotId':'fixture'}):
                with self.assertRaisesRegex(RuntimeError,'source-mismatch'):
                    observe_owner(self.wx,{'dataRoot':root,'accountId':'account','capture':{'sourceId':'expected'}},
                        frozenset(),client=self.client,client_probe=lambda wx:self.client,
                        refresh_chat=self.refresh,helpers=self.helpers)
        self.assertEqual(self.ops,[])


if __name__=='__main__':unittest.main()
