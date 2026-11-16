"""Actual authenticated-source and Node-inbox fixtures; no production/UI."""
import copy
import json
import unittest
from unittest.mock import patch

import native_group_ui_binding as binding
from dynamic_group_watch_candidate import register_group
from test_dynamic_group_watch import fixture, Row
import test_native_group_handoff as handoff_fixtures
import test_native_group_text_authority as authority_fixtures
from test_native_group_text_authority import KEY, EPOCH


class RegisteredHandoffTests(unittest.TestCase):
    def setUp(self):
        self.f = handoff_fixtures.NativeHandoffTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def confirmed(self):
        f = self.f; h = f.verify(); monitor = f.monitor()
        result = f.register(monitor, h)
        self.assertTrue(result['ok'], result)
        return binding.confirm_registered_handoff(f.config, h, result,
            routes=monitor.routes, registry=monitor.registry), h, monitor, result

    def test_actual_registered_handoff_revalidates_exact_source(self):
        proof, h, _, _ = self.confirmed()
        self.assertIs(binding.revalidate_registered_handoff(proof, config=self.f.config,
            native_chat_key=h.native_chat_key, claim_id=h.claim_id, claim_hash=h.claim_hash), proof)
        self.f.claim['state'] = 'submitted'; self.f.save()
        with self.assertRaises(RuntimeError):
            binding.revalidate_registered_handoff(proof, config=self.f.config,
                native_chat_key=h.native_chat_key, claim_id=h.claim_id, claim_hash=h.claim_hash)

    def test_no_bool_wrong_claim_or_reused_result_can_mint_proof(self):
        proof, h, monitor, result = self.confirmed()
        for changed in ({'checkedThisCall':False}, {'originalChatRestored':False},
                        {'nativeQueuedHandoff':False}, {'confirmationReused':True}):
            with self.assertRaises(RuntimeError):
                binding.confirm_registered_handoff(self.f.config, h, {**result, **changed},
                    routes=monitor.routes, registry=monitor.registry)
        for candidate, claim in ((True, h.claim_id), (proof, 'b'*32)):
            with self.assertRaises(RuntimeError):
                binding.revalidate_registered_handoff(candidate, config=self.f.config,
                    native_chat_key=h.native_chat_key, claim_id=claim, claim_hash=h.claim_hash)
        self.assertIs(binding.confirm_registered_handoff(self.f.config, h,
            {**result, 'confirmationReused':True}, routes=monitor.routes,
            registry=monitor.registry, previous=proof), proof)

    def test_first_admission_retry_reconfirms_raw_handoff_after_prior_registration(self):
        _,h,monitor,_=self.confirmed()
        monitor.state['calls'].clear()
        result=register_group(monitor.wx,monitor.monitor,monitor.routes,monitor.seen,monitor.registry,
            target=h.target,native_chat_key=h.native_chat_key,account_id=h.account_id,
            expected_account_id=h.account_id,native_handoff=h)
        self.assertTrue(result['ok'],result)
        self.assertTrue(result['checkedThisCall'])
        self.assertFalse(result['confirmationReused'])
        self.assertTrue(result['originalChatRestored'])
        self.assertEqual(monitor.state['calls'],['new group','owner'])
        proof=binding.confirm_registered_handoff(self.f.config,h,result,
            routes=monitor.routes,registry=monitor.registry)
        self.assertEqual(proof.claim_id,h.claim_id)


class MonitorRestoreTests(unittest.TestCase):
    def setUp(self):
        self.a = authority_fixtures.AuthorityTests(); self.a.setUp(); self.addCleanup(self.a.doCleanups)
        self.a.route['target'] = 'new group'; self.a.registry()
        controller = self.a.start(); self.a.baseline(controller)
        controller.commit(controller.ticket, EPOCH)
        self.proof = binding.verify_native_monitor_binding(self.a.config, KEY, EPOCH, 'new group')

    def register(self, f, **extra):
        return register_group(f.wx, f.monitor, f.routes, f.seen, f.registry,
            target='new group', native_chat_key=KEY, account_id=self.a.config['accountId'],
            expected_account_id=self.a.config['accountId'], native_monitor_binding=self.proof, **extra)

    def native_state(self, target):
        return {'version':1, 'mode':'native', 'target':target, 'nativeChatKey':KEY,
                'authorityEpoch':EPOCH, 'accountId':self.a.config['accountId']}

    def test_committed_native_restore_keeps_future_text_without_sender_lookup(self):
        f = fixture()
        f.monitor.native_group_text_authority = self.native_state
        f.monitor.message_direction = lambda _: self.fail('native text must not look up avatars')
        result = self.register(f)
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['nativeMonitorRestored'])
        self.assertEqual(f.state['current'], 'owner')
        self.assertFalse(f.monitor.recover_startup_unread)
        self.assertEqual(f.monitor.startup_pending, {})
        f.state['rows']['new group'].append(Row(3,'fresh native text'))
        f.state['info']['new group'].update(isnew=True,new_count=1,content='fresh native text',time='12:02')
        observations = f.monitor.read('new group')
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]['observation'], 'native-group-text')
        self.assertNotIn('text', observations[0])
        self.assertEqual(f.monitor.read('new group'), [])
        self.assertEqual(f.monitor.startup_pending, {})

    def test_unproven_unread_media_keeps_badge_before_navigation(self):
        f = fixture(unread=1); f.state['rows']['new group'] = [Row(2, '[图片]')]
        f.state['rows']['new group'][0].ClassName = 'mmui::ChatImageItemView'
        f.state['info']['new group']['content'] = '[图片]'
        f.monitor.native_group_text_authority = self.native_state
        from types import SimpleNamespace
        f.monitor.message_direction = lambda _: 'friend'
        f.monitor.parse_incoming = lambda _: SimpleNamespace(attr='friend', type='image',content='[图片]',sender='member')
        result = self.register(f)
        self.assertFalse(result['ok'], result)
        self.assertTrue(result['pending'])
        self.assertEqual(f.state['calls'], [])
        self.assertTrue(f.state['info']['new group']['isnew'])
        self.assertNotIn('new group', f.monitor.cursor.rows)

    def test_forged_marker_changed_route_and_ambiguous_name_do_not_navigate(self):
        f = fixture()
        with patch.object(binding, 'verify_native_monitor_binding', wraps=binding.verify_native_monitor_binding):
            self.a.route['sessionId'] = 'changed'; self.a.registry()
            result = self.register(f)
        self.assertEqual(result['code'], 'dynamic-group-native-monitor-binding-unverified')
        self.assertEqual(f.state['calls'], [])
        self.a.route['sessionId'] = 'original-group-session'; self.a.registry()
        original = f.wx.GetSession; f.wx.GetSession = lambda: original()+[original()[-1]]
        result = self.register(f)
        self.assertEqual(result['code'], 'dynamic-group-session-missing-or-ambiguous')
        self.assertEqual(f.state['calls'], [])
        with self.assertRaises(RuntimeError):
            binding.revalidate_native_monitor_binding(True, target='new group',native_chat_key=KEY,
                account_id=self.a.config['accountId'])

    def test_arrival_or_unproven_media_bottom_never_seeds_unread(self):
        for cause in ('arrival', 'bottom'):
            f = fixture(unread=0 if cause=='arrival' else 1); f.monitor.native_group_text_authority=self.native_state
            if cause == 'arrival':
                select = f.monitor.select
                def changed(name):
                    select(name)
                    if name == 'new group': f.state['info'][name]['content'] = 'new arrival'
                f.monitor.select = changed
            else:
                f.wx._api._chat_api.msgbox.GetScrollPattern = lambda: None
            result = self.register(f)
            self.assertFalse(result['ok'], result)
            self.assertEqual(f.state['current'], 'owner')
            self.assertNotIn('new group', f.monitor.cursor.rows)
            self.assertEqual(f.registry, {})


class AuthenticatedUnreadRestoreTests(unittest.TestCase):
    def test_native_text_and_voice_restore_without_scroll_pattern_or_avatar_parse(self):
        import test_native_group_monitor_unread as fixtures
        for voice in (False, True):
            case=fixtures.MonitorUnreadTests(); case.setUp(); self.addCleanup(case.doCleanups)
            proof=case.initialize(voice=voice)
            if voice:case.voice_baseline()
            f=case.fixture.monitor()
            f.wx._api._chat_api.msgbox.GetScrollPattern=lambda:None
            f.monitor.native_voice_sources=True
            f.monitor.message_direction=lambda _:self.fail('native unread cannot parse avatars')
            f.monitor.parse_incoming=lambda _:self.fail('native unread cannot parse SDK envelopes')
            if voice:
                f.state['info']['new group']['content']='[语音] 4"'
                f.state['rows']['new group'][-1].Name='语音4"秒未播放'
                f.state['rows']['new group'][-1].ClassName='mmui::ChatVoiceItemView'
            result=register_group(f.wx,f.monitor,f.routes,f.seen,f.registry,target='new group',
                native_chat_key=case.key,account_id=case.config['accountId'],
                expected_account_id=case.config['accountId'],native_monitor_binding=proof)
            self.assertTrue(result['ok'],result)
            self.assertTrue(result['nativeMonitorRestored'])
            self.assertTrue(result['historicalBaselineOnly'])
            self.assertEqual(f.state['current'],'owner')
            self.assertEqual(f.monitor.startup_pending,{})
            self.assertFalse(f.monitor.recover_startup_unread)

    def test_native_count_mismatch_preserves_actual_badge_before_navigation(self):
        import test_native_group_monitor_unread as fixtures
        case=fixtures.MonitorUnreadTests();case.setUp();self.addCleanup(case.doCleanups)
        proof=case.initialize();f=case.fixture.monitor(unread=1)
        result=register_group(f.wx,f.monitor,f.routes,f.seen,f.registry,target='new group',
            native_chat_key=case.key,account_id=case.config['accountId'],
            expected_account_id=case.config['accountId'],native_monitor_binding=proof)
        self.assertEqual(result['code'],'dynamic-group-native-unread-proof-required')
        self.assertTrue(result['pending']);self.assertEqual(f.state['calls'],[])
        self.assertEqual(f.state['info']['new group']['new_count'],1)


if __name__ == '__main__': unittest.main()
