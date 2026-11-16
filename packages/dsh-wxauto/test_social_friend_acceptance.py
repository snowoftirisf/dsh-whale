"""Isolated fixture plans/bindings: no actual wxauto import, UI or production."""
import copy
import sys
import hashlib
import json
from contextlib import nullcontext
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from social_actions import ActionContext, _digest
from social_ipc import SocialDispatcher
from social_wxauto_backend import ActionBinding, WxautoSocialBackend


class FakeCandidate:
    def __init__(self, wx, config, **kwargs):
        self.wx, self.options, self.events = wx, kwargs, wx.events
        self.closed = False
        self.attempted = False
        self.last_before = None
        self.last_observation = None
        self.events.append('constructed')

    def before(self, wx, parameters):
        plan = self.options['plan_provider']()
        assert plan['status'] == 'claimed' and plan['approvalMessageId']
        self.events.append('before')
        if getattr(wx, 'fail_before', False):
            raise RuntimeError('social-fixture-before-failed')
        self.last_before = {'source':'wxauto-uia','action':'add_friend','phase':'before',
            'observedAt':self.options['clock'](),'clientVersion':'4.2.0.12',
            'parametersHash':_digest('add_friend',parameters),
            'identityBasis':'owner-reviewed-query-and-name','publicWechatIdVerified':False,
            'identityEvidence':'id-unavailable','controls':[{'formRuntimeId':[42,1],
            'formHandle':1,'buttonRuntimeId':[42,2],'buttonBounds':[1,1,5,5],'pid':123,
            'querySha256':'a'*64,'expectedProfileNameSha256':'b'*64,
            'parametersHash':_digest('add_friend',parameters)}]}
        return {'source': 'wxauto-uia', 'action': 'add_friend', 'phase': 'before',
            'clientVersion': '4.2.0.12', 'parametersHash': _digest('add_friend', parameters),
            'observedAt': self.options['clock'](), 'controls': [{'class': 'fixture-only'}]}

    def perform(self, wx, parameters):
        self.attempted = True
        self.events.append('perform')
        if getattr(wx, 'fail_perform', False):
            raise RuntimeError('fixture-click-outcome-unknown')
        return {'clickAttempted': True}

    def after(self, wx, parameters, result, before):
        self.events.append('after')
        # Deliberately stronger than the real candidate: the entrance must
        # force unknown even if this future callback accidentally claims true.
        return {**before, 'phase': 'after', 'confirmed': True}

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.attempted:
            self.last_observation = {'schemaVersion':1,'kind':'unverified-add-friend-post-click-diagnostic',
                'capturedAt':self.options['clock'](),'attempted':True,'foregroundOwned':True,
                'foregroundMain':False,'foregroundWindowHandle':12,'ownershipStable':True,
                'truncated':False,'nodes':[{'index':0,'parentIndex':-1,'depth':0,'rootIndex':0,
                    'class':'mmui::Fixture','type':'WindowControl','pid':123,'windowHandle':12,
                    'runtimeIdSha256':'c'*64,'runtimeIdLength':2,'idSha256':'d'*64,'idLength':0,
                    'nameSha256':'e'*64,'nameLength':10,'valueAvailable':False,'valueSha256':None,
                    'valueLength':None,'geometryAvailable':True,'bounds':[1,2,3,4],'semanticRole':'unknown'}]}
            if getattr(self.wx, 'unsafe_observation',False):
                self.last_observation['nodes'][0]['rawName']='must-never-be-persisted'
        self.events.append('close')
        if self.wx.fail_close:
            raise RuntimeError('social-chat-restore-failed')

    def binding(self):
        return ActionBinding('fixture-only', self.perform, self.before, self.after)


class FriendAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.now = 1000
        self.wx = SimpleNamespace(events=[], fail_close=False,
            ChatInfo=lambda: {'chat_name': 'group-a', 'chat_type': 'group'})
        self.config = {'dataRoot': self.temp.name, 'target': 'owner', 'groups': ['group-a']}
        self.fail_restore = False
        def refresh(wx, target, kind):
            self.assertIs(wx, self.wx)
            self.wx.events.append(('restore', target, kind))
            if self.fail_restore:
                raise RuntimeError('fixture-only')
        self.backend = WxautoSocialBackend(self.wx, self.config, clock=lambda: self.now,
            client_probe=lambda wx: {'clientVersion': '4.2.0.12', 'executableName': 'Weixin.exe',
                'clientSha256': 'a' * 64}, refresh_chat=refresh)
        self.social = SocialDispatcher(self.config, self.backend, clock=lambda: self.now)
        self.owner = ActionContext('friend', 'owner', True)
        self.patcher = patch.dict(sys.modules, {'social_add_friend_candidate':
            SimpleNamespace(AddFriendCandidate=FakeCandidate)})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def plan(self, **updates):
        return self.social.queue.draft('add_friend', {'keywords': 'fixture-account',
            'expectedProfileName': 'fixture-name', 'addmsg': 'fixture-only', **updates}, self.owner)

    def approve(self, plan):
        event = {'event': 'message', 'id': 'fresh-fixture-owner-approval', 'target': 'owner',
            'kind': 'friend', 'mediaType': 'text', 'text': '批准操作 ' + plan['id'] + ' ' + plan['hash']}
        self.assertIsNotNone(self.social.observe_incoming(event))
        return self.social.dispatch({'op': 'social-approve', 'target': 'owner',
            'planId': plan['id'], 'hash': plan['hash'], 'approvalMessageId': event['id']})

    def command(self, plan, **updates):
        return {'op': 'social-acceptance', 'id': 'fixture-request', 'target': 'owner',
            'purpose': 'add-friend-initial-contact', 'planId': plan['id'], 'hash': plan['hash'], **updates}

    def test_pending_wrong_hash_expired_and_missing_owner_profile_never_construct(self):
        plan = self.plan()
        self.assertEqual(self.social.dispatch(self.command(plan))['executionStatus'], 'not-dispatched')
        self.approve(plan)
        with self.assertRaisesRegex(RuntimeError, 'plan-mismatch'):
            self.social.dispatch(self.command(plan, hash='b' * 64))
        self.now = plan['expiresAt']
        self.assertEqual(self.social.dispatch(self.command(plan))['executionStatus'], 'not-dispatched')
        self.now = 1000
        blank = self.social.queue.draft('add_friend', {'keywords': 'fixture-other', 'addmsg': 'fixture-only'}, self.owner)
        self.approve(blank)
        with self.assertRaisesRegex(RuntimeError, 'owner-profile-approval-required'):
            self.social.dispatch(self.command(blank))
        self.assertEqual(self.wx.events, [])

    def test_approved_record_without_actual_owner_message_grant_cannot_construct(self):
        plan = self.plan(); self.approve(plan)
        with self.social.queue._locked() as state:
            state['plans'][plan['id']].pop('approvalMessageId')
            self.social.queue._save(state)
        with self.assertRaisesRegex(RuntimeError, 'owner-profile-approval-required'):
            self.social.dispatch(self.command(plan))
        self.assertEqual(self.wx.events, [])

    def test_owner_only_exact_protocol_rejects_extra_binding_action_or_query(self):
        plan = self.plan()
        for updates in ({'target': 'group-a'}, {'purpose': 'other'}, {'fingerprint': 'a'*64},
                        {'binding': 'fake'}, {'action': 'add_friend'}, {'keywords': 'different'}):
            with self.subTest(updates=updates), self.assertRaises(RuntimeError):
                self.social.dispatch(self.command(plan, **updates))
        self.assertEqual(self.wx.events, [])

    def test_approved_claimed_once_unknown_after_cannot_promote_or_retry(self):
        plan = self.plan(); self.approve(plan)
        value = self.social.dispatch(self.command(plan))
        self.assertEqual(value['status'], 'unknown')
        self.assertEqual(value['executionStatus'], 'dispatched')
        self.assertTrue(value['chatRestored'])
        self.assertLess(self.wx.events.index('close'), self.wx.events.index(('restore', 'group-a', 'group')))
        self.assertFalse(self.backend.receipt_file.exists())
        self.assertEqual(self.backend.bindings, {})
        snapshot = copy.deepcopy(self.wx.events)
        again = self.social.dispatch(self.command(plan))
        self.assertEqual(again['executionStatus'], 'not-dispatched')
        self.assertEqual(self.wx.events, snapshot)
        self.assertTrue(set(value) <= {'id','hash','status','resultCode','executionStatus','chatRestored','diagnosticArtifact','diagnosticError',
            'addContactClickAttempted','preflightObserved','failurePhase','primaryFailureCode','cleanupFailureCode'})
        self.assertTrue(value['addContactClickAttempted']);self.assertTrue(value['preflightObserved'])
        artifact = self.backend.root / value['diagnosticArtifact']['path']
        self.assertEqual(hashlib.sha256(artifact.read_bytes()).hexdigest(),value['diagnosticArtifact']['sha256'])
        diagnostic=json.loads(artifact.read_text('utf8'))
        self.assertFalse(diagnostic['confirmed']);self.assertTrue(diagnostic['notAnAcceptanceReceipt'])
        self.assertEqual(diagnostic['before']['parametersHash'],plan['hash'])
        self.assertEqual(diagnostic['after']['nodes'][0]['semanticRole'],'unknown')
        self.assertNotIn('parameters',diagnostic);self.assertNotIn('fixture-account',artifact.read_text('utf8'))

    def test_close_failure_does_not_restore_or_retry_and_blocks_main_backend(self):
        plan = self.plan(); self.approve(plan); self.wx.fail_close = True
        result = self.social.reply(self.command(plan))
        self.assertTrue(result['ok'])
        self.assertEqual(result['value']['status'], 'unknown')
        self.assertTrue(result['value']['addContactClickAttempted'])
        self.assertEqual(result['value']['primaryFailureCode'],'social-post-action-outcome-unknown')
        self.assertEqual(result['value']['cleanupFailureCode'],'social-chat-restore-failed')
        self.assertFalse(result['value']['chatRestored'])
        self.assertTrue(self.backend.chat_binding_failed)
        self.assertEqual(self.wx.events.count('close'), 1)
        self.assertFalse(any(isinstance(x, tuple) and x[0]=='restore' for x in self.wx.events))
        self.assertTrue((self.backend.root / result['value']['diagnosticArtifact']['path']).is_file())

    def test_refresh_failure_retains_unknown_and_blocks_main_backend(self):
        plan = self.plan(); self.approve(plan); self.fail_restore = True
        value = self.social.dispatch(self.command(plan))
        self.assertEqual(value['status'], 'unknown')
        self.assertFalse(value['chatRestored'])
        self.assertTrue(self.backend.chat_binding_failed)
        self.assertEqual(self.wx.events.count('perform'), 1)

    def test_no_attempt_creates_no_observation_and_click_exception_still_preserves_snapshot(self):
        plan=self.plan();self.approve(plan);self.wx.fail_before=True
        value=self.social.dispatch(self.command(plan))
        self.assertEqual(value['status'],'failed')
        self.assertFalse(value['addContactClickAttempted']);self.assertFalse(value['preflightObserved'])
        self.assertEqual(value['failurePhase'],'preflight')
        self.assertNotIn('diagnosticArtifact',value);self.assertNotIn('diagnosticError',value)
        self.assertFalse((self.backend.paths['backups']/'social-initial-contact').exists())
        self.wx.fail_before=False;self.wx.fail_perform=True
        other=self.plan(keywords='fixture-second');self.approve(other)
        value=self.social.dispatch(self.command(other))
        self.assertEqual(value['status'],'unknown');self.assertIn('diagnosticArtifact',value)
        self.assertTrue(value['addContactClickAttempted']);self.assertTrue(value['preflightObserved'])
        self.assertEqual(value['failurePhase'],'perform')
        self.assertFalse(self.backend.receipt_file.exists())

    def test_preflight_and_cleanup_failures_preserve_primary_without_claiming_click(self):
        plan=self.plan();self.approve(plan);self.wx.fail_before=True;self.wx.fail_close=True
        value=self.social.dispatch(self.command(plan))
        self.assertEqual(value['status'],'failed')
        self.assertFalse(value['addContactClickAttempted'])
        self.assertEqual(value['primaryFailureCode'],'social-initial-failure-unclassified')
        self.assertEqual(value['cleanupFailureCode'],'social-chat-restore-failed')
        self.assertEqual(value['failurePhase'],'preflight')

    def test_missing_or_nonplain_attempt_attribute_is_not_false(self):
        from social_friend_acceptance import _execution_diagnostics
        class Missing:
            @property
            def attempted(self):raise AssertionError('descriptor-read')
        self.assertNotIn('addContactClickAttempted',_execution_diagnostics(Missing()))
        self.assertNotIn('preflightObserved',_execution_diagnostics(Missing()))
        self.assertNotIn('addContactClickAttempted',_execution_diagnostics(SimpleNamespace(attempted=1)))
        self.assertEqual(_execution_diagnostics(None),{})

    def test_unsafe_metadata_or_save_error_cannot_change_unknown_or_allow_retry(self):
        for unsafe in (True,False):
            self.wx.unsafe_observation=unsafe
            plan=self.plan(keywords='fixture-unsafe' if unsafe else 'fixture-save-failure');self.approve(plan)
            with patch('social_friend_acceptance._atomic_json', side_effect=OSError('private-path')) if not unsafe else nullcontext():
                value=self.social.dispatch(self.command(plan))
            self.assertEqual(value['status'],'unknown');self.assertNotIn('diagnosticArtifact',value)
            self.assertEqual(value['diagnosticError'],'social-initial-diagnostic-save-failed')
            snapshot=copy.deepcopy(self.wx.events)
            self.assertEqual(self.social.dispatch(self.command(plan))['executionStatus'],'not-dispatched')
            self.assertEqual(self.wx.events,snapshot)
        self.assertFalse(self.backend.receipt_file.exists())


if __name__ == '__main__':
    unittest.main()
