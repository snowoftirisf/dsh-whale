from contextlib import contextmanager
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from social_moments_runtime import observe_feed
from social_friend_observe import OBSERVED_CLIENT_SHA,OBSERVED_VERSION
from social_wxauto_backend import WxautoSocialBackend
from social_ipc import SocialDispatcher
from test_social_moments_observe import Control


class MomentsRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.snapshot_id=str(uuid.uuid4());self.events=[];self.probes=0
        self.config={'dataRoot':str(self.root),'accountId':'fixture','target':'Owner','groups':['Group'],
            'capture':{'accountUsername':'self','sourceId':'fixture-source'}}
        (self.root/'config').mkdir();(self.root/'config/wxauto.local.json').write_text(json.dumps(self.config),encoding='utf8')
        folder=self.root/'snapshots/windows-live/snapshots'/self.snapshot_id;folder.mkdir(parents=True)
        (folder/'snapshot-manifest.json').write_text(json.dumps({'account_username':'self','source_id':'fixture-source'}),encoding='utf8')
        (self.root/'snapshots/windows-live/latest.json').write_text(json.dumps({'snapshot_id':self.snapshot_id}),encoding='utf8')
        exe=self.root/'Weixin.exe';exe.write_bytes(b'fixture-not-a-live-client')
        self.client={'executableName':'Weixin.exe','executable':str(exe),'pid':7,'clientVersion':OBSERVED_VERSION,'clientSha256':OBSERVED_CLIENT_SHA}
        self.main=Control('mmui::MainWindow');self.feed=Control('mmui::SNSContentView',children=[Control('mmui::TimelineCell')])
        self.wx=SimpleNamespace(_api=SimpleNamespace(control=self.main),ChatInfo=lambda:{'chat_name':'Group','chat_type':'group'})
        def probe(_):self.probes+=1;return copy.deepcopy(self.client)
        def refresh(wx,target,kind):self.events.append(('restore',target,kind))
        self.backend=WxautoSocialBackend(self.wx,self.config,client_probe=probe,refresh_chat=refresh)
        self.social=SocialDispatcher(self.config,self.backend)
        self.uia=SimpleNamespace(GetForegroundWindow=lambda:10)
        self.changed=False;self.navigation_calls=0;self.fail_collect=False

    def discovery(self,file,account,**kwargs):
        self.assertEqual(account,'fixture');self.assertEqual(kwargs['expected_account_username'],'self')
        return {'snapshotId':self.snapshot_id}

    def navigation(self,wx,**kwargs):
        self.navigation_calls+=1;self.assertIs(wx,self.wx)
        @contextmanager
        def context(bound):
            self.events.append('navigate')
            if self.fail_collect:raise RuntimeError('social-moments-fixture-collection-failed')
            def guard():
                self.assertTrue(kwargs['identity_check']());self.assertTrue(kwargs['client_unchanged']());return True
            yield {'clientVersion':OBSERVED_VERSION,'scope':'moments-feed','roots':[self.feed],'guard':guard}
        return context

    def observe(self,**kwargs):
        return observe_feed(self.backend,uia=self.uia,windows=lambda:[],discovery_fn=self.discovery,navigation_factory=self.navigation,
            process_probe=lambda c:(7,1.0,c['executable']),**kwargs)

    def test_real_collector_backend_recovery_and_metadata_only_no_registration(self):
        self.assertEqual(self.events,[]);self.assertEqual(self.navigation_calls,0);self.assertEqual(self.probes,0)
        result=self.observe()
        self.assertEqual(result['purpose'],'moments-feed-metadata');self.assertTrue(result['chatRestored']);self.assertTrue(result['readOnly'])
        self.assertFalse(result['sentMessages']);self.assertFalse(result['liveCapabilityGranted']);self.assertFalse(result['postIdentityVerified'])
        self.assertEqual(result['restoreTargetSha256'],hashlib.sha256(b'Group').hexdigest())
        self.assertEqual(self.events,['navigate',('restore','Group','group')]);self.assertEqual(self.probes,1)
        self.assertEqual(self.backend.bindings,{})

    def test_collect_exception_still_restores_original_route(self):
        self.fail_collect=True
        with self.assertRaisesRegex(RuntimeError,'fixture-collection-failed'):self.observe()
        self.assertEqual(self.events,['navigate',('restore','Group','group')]);self.assertFalse(self.backend.chat_binding_failed)

    def test_restore_failure_blocks_backend_and_never_reports_silent_true(self):
        self.backend.refresh_chat=lambda *args:(_ for _ in ()).throw(RuntimeError('fixture-restore-failure'))
        with self.assertRaisesRegex(RuntimeError,'social-chat-restore-failed'):self.observe()
        self.assertTrue(self.backend.chat_binding_failed);self.assertFalse(self.backend.last_chat_recovery['restored'])
        with self.assertRaisesRegex(RuntimeError,'navigation-unavailable'):self.backend.observe('moments-feed-metadata')

    def test_unsupported_client_and_account_source_mismatch_never_navigate(self):
        self.client['clientSha256']='b'*64
        with self.assertRaisesRegex(RuntimeError,'client-unverified'):self.observe()
        self.client['clientSha256']=OBSERVED_CLIENT_SHA
        manifest=self.root/'snapshots/windows-live/snapshots'/self.snapshot_id/'snapshot-manifest.json'
        for value in [{'account_username':'other','source_id':'fixture-source'},{'account_username':'self','source_id':'other'}]:
            manifest.write_text(json.dumps(value),encoding='utf8')
            with self.assertRaisesRegex(RuntimeError,'account-unverified'):self.observe()
        self.assertEqual(self.events,[]);self.assertEqual(self.navigation_calls,0)

    def test_owner_only_ipc_exact_args_and_branch_has_no_friend_observation_context(self):
        command={'op':'social-observe','target':'Owner','purpose':'moments-feed-metadata'}
        for changed in [{'target':'Group'},{'planId':'a'*32},{'account':'arbitrary'},{'path':'elsewhere'}]:
            with self.assertRaises(RuntimeError):self.social.dispatch({**command,**changed})
        def call(backend):
            self.assertIs(backend,self.backend);self.assertIsNone(backend._navigation_context);return {'fixture':True}
        with patch('social_moments_runtime.observe_feed',side_effect=call) as action:
            self.assertEqual(self.social.dispatch(command),{'fixture':True});self.assertEqual(action.call_count,1)
        with self.assertRaisesRegex(RuntimeError,'plan-invalid'):
            self.backend.observe('moments-feed-metadata',plan_id='a'*32)

    def test_actual_navigation_wait_failures_preserve_phase_class_count_and_final_recovery_in_reply(self):
        from social_moments_navigation import make_feed_navigation
        self.main.IsEnabled=1
        for index,(cls,phase) in enumerate([('mmui::XTabBarItem','locating-discover'),
            ('mmui::ExtensionDiscoverContentCell','locating-moments'),('mmui::SNSContentView','locating-feed')]):
            def wait(root,expected_class,name=None,**kwargs):
                if expected_class==cls:
                    error=RuntimeError('social-moments-control-missing-or-ambiguous');error.matches=index
                    raise error
                return SimpleNamespace(Click=lambda **_:None)
            def factory(wx,**kwargs):
                return make_feed_navigation(wx,**kwargs,helpers=SimpleNamespace(nodes=lambda root:[root],visible_owned_roots=lambda *args:[self.main]),waiter=wait)
            def run(backend):
                return observe_feed(backend,uia=self.uia,windows=lambda:[],discovery_fn=self.discovery,navigation_factory=factory,
                    process_probe=lambda c:(7,1.0,c['executable']))
            with patch('social_moments_runtime.observe_feed',side_effect=run):
                result=self.social.reply({'op':'social-observe','id':'fixture-'+str(index),'target':'Owner','purpose':'moments-feed-metadata'})
            self.assertFalse(result['ok']);diag=result['observation'];self.assertEqual(diag['phase'],phase)
            self.assertEqual(diag['expectedClass'],cls);self.assertEqual(diag['matches'],index)
            self.assertTrue(diag['chatRestored']);self.assertFalse(diag['completed']);self.assertGreaterEqual(diag['durationMs'],0)
            self.assertEqual(diag['code'],'social-moments-control-missing-or-ambiguous')
            self.assertEqual(self.events[-1],('restore','Group','group'))

    def test_preflight_and_restore_failure_diagnostics_use_actual_unknown_or_failed_recovery(self):
        self.backend.last_chat_recovery={'restored':True};self.client['clientSha256']='b'*64
        with self.assertRaises(RuntimeError):self.observe()
        diag=self.backend.last_moments_observation
        self.assertEqual(diag['phase'],'preflight');self.assertIsNone(diag['chatRestored']);self.assertFalse(diag['completed'])
        first=diag['attemptId'];self.client['clientSha256']=OBSERVED_CLIENT_SHA
        self.backend.refresh_chat=lambda *args:(_ for _ in ()).throw(RuntimeError('fixture-restore-failure'))
        with self.assertRaisesRegex(RuntimeError,'social-chat-restore-failed'):self.observe()
        diag=self.backend.last_moments_observation;self.assertNotEqual(first,diag['attemptId']);self.assertFalse(diag['chatRestored'])
        self.assertFalse(diag['completed']);self.assertEqual(diag['code'],'social-chat-restore-failed')

    def test_reply_never_echoes_stale_diagnostic_on_foreign_malformed_or_pre_dispatch_failure(self):
        self.observe();previous=copy.deepcopy(self.backend.last_moments_observation)
        base={'op':'social-observe','id':'fixture','target':'Owner','purpose':'moments-feed-metadata'}
        for command in [{**base,'target':'Group'},{**base,'account':'arbitrary'},None,{**base,'purpose':'friend-search-invalid'}]:
            self.assertNotIn('observation',self.social.reply(command))
        self.backend._navigation_context={'busy':True}
        self.assertNotIn('observation',self.social.reply(base));self.assertEqual(previous,self.backend.last_moments_observation)

    def test_success_reply_exposes_only_new_sanitized_diagnostic(self):
        def run(backend):
            result=self.observe();backend.last_moments_observation['private']='must not echo';return result
        with patch('social_moments_runtime.observe_feed',side_effect=run):
            reply=self.social.reply({'op':'social-observe','id':'fixture','target':'Owner','purpose':'moments-feed-metadata'})
        self.assertTrue(reply['ok']);self.assertTrue(reply['observation']['completed']);self.assertEqual(reply['observation']['phase'],'completed')
        self.assertNotIn('private',reply['observation']);self.assertTrue(reply['observation']['chatRestored'])


if __name__=='__main__':unittest.main()
