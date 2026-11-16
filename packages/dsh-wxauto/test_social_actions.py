import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from social_actions import ACTIONS, ActionContext, SocialActions


class SocialTests(unittest.TestCase):
    def test_full_friend_notice_scope_is_durable_and_cannot_upgrade_old_notice(self):
        p = self.queue.draft('add_friend', {'keywords':'test-id', 'expectedProfileName':'Test', 'addmsg':'Hello'}, self.owner)
        notice = self.queue.claim_approval_notice(p['id'],p['hash'],self.owner,purpose='add-friend-request')
        self.assertEqual(notice['purpose'],'add-friend-request')
        self.assertIn('尝试提交一次',notice['text'])
        self.assertIn('不表示已经成为好友',notice['text'])
        reopened = SocialActions(self.root, owner_target='owner', allowed_groups=['group-a','group-b'],clock=lambda:self.now)
        self.assertEqual(reopened.inspect(p['id'],self.owner)['approvalNotice']['purpose'],'add-friend-request')
        old = self.queue.draft('add_friend', {'keywords':'other-id','expectedProfileName':'Other'}, self.owner)
        self.queue.claim_approval_notice(old['id'],old['hash'],self.owner)
        refused = self.queue.claim_approval_notice(old['id'],old['hash'],self.owner,purpose='add-friend-request')
        self.assertEqual(refused['state'],'already-claimed')
        self.assertNotIn('purpose',self.queue.inspect(old['id'],self.owner)['approvalNotice'])

    def test_full_notice_rejects_other_actions_missing_name_and_nonowner(self):
        for action,parameters in [('create_group',{'contacts':['a','b']}),('add_friend',{'keywords':'test-id'})]:
            p = self.queue.draft(action,parameters,self.owner)
            with self.assertRaisesRegex(RuntimeError,'purpose-denied'):
                self.queue.claim_approval_notice(p['id'],p['hash'],self.owner,purpose='add-friend-request')
            self.assertNotIn('approvalNotice',self.queue.inspect(p['id'],self.owner))
        p=self.queue.draft('add_friend',{'keywords':'test-id','expectedProfileName':'Test'},self.owner)
        with self.assertRaisesRegex(RuntimeError,'purpose-denied'):
            self.queue.claim_approval_notice(p['id'],p['hash'],ActionContext('friend','other'),purpose='add-friend-request')
        with self.assertRaisesRegex(RuntimeError,'purpose-denied'):
            self.queue.claim_approval_notice(p['id'],p['hash'],self.owner,purpose='arbitrary')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 1000.0
        self.owner = ActionContext('friend', 'owner', True)
        self.group = ActionContext('group', 'group-a')
        self.queue = SocialActions(self.root, owner_target='owner', allowed_groups=['group-a', 'group-b'], clock=lambda: self.now)

    def approval(self, plan):
        return ActionContext('friend', 'owner', True, 'owner-message-1', plan['id'], plan['hash'])

    def backend(self, result=None):
        calls = []
        evidence = {'clientVersion': '4.2.0.1000', 'actions': {
            action: {'clientVersion': '4.2.0.1000', 'liveVerified': True} for action in ACTIONS}}
        backend = SimpleNamespace(capabilities=lambda: copy.deepcopy(evidence), calls=calls, evidence=evidence,
            get_group_announcement=lambda group: {'text': 'old announcement', 'verified': True, 'clientVersion': '4.2.0.1000'})
        def execute(action, parameters, **kwargs):
            calls.append((action, parameters, kwargs))
            # Claim must be durable before any external backend execution.
            stored = self.queue.inspect(kwargs['idempotency_key'], self.owner)
            self.assertEqual(stored['status'], 'claimed')
            return {'status': 'confirmed', 'receipt': {'operation': 'real-backend-receipt'}} if result is None else result
        backend.execute = execute
        return backend

    def test_publish_is_autonomous_owner_channel_only_and_keeps_default_privacy(self):
        plan = self.queue.draft('publish_moment', {'text': 'hello'}, self.owner)
        self.assertEqual(plan['status'], 'ready')
        backend = self.backend()
        result = self.queue.execute(plan['id'], plan['hash'], self.owner, backend)
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual(backend.calls[0][1], {'text': 'hello', 'mediaFiles': []})
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(self.queue.execute(plan['id'], plan['hash'], self.owner, backend)['executionStatus'], 'not-dispatched')
        self.assertEqual(len(backend.calls), 1)
        with self.assertRaisesRegex(RuntimeError, 'owner-context-required'):
            self.queue.draft('publish_moment', {'text': 'group cannot publish'}, self.group)
        with self.assertRaisesRegex(RuntimeError, 'parameters-invalid'):
            self.queue.draft('publish_moment', {'text': 'hello', 'privacy': 'public'}, self.owner)

    def test_moment_media_is_root_relative_and_limited_to_media_or_outbox(self):
        plan = self.queue.draft('publish_moment', {'mediaFiles': [str(self.root / 'outbox/photo.png')]}, self.owner)
        self.assertEqual(plan['parameters']['mediaFiles'], ['outbox/photo.png'])
        with self.assertRaisesRegex(RuntimeError, 'media-path-denied'):
            self.queue.draft('publish_moment', {'mediaFiles': [str(self.root / 'config/credentials.json')]}, self.owner)

    def test_announcement_only_current_allowed_group_or_owner_and_old_text_saved_first(self):
        with self.assertRaisesRegex(RuntimeError, 'group-denied'):
            self.queue.draft('set_group_announcement', {'group': 'group-b', 'text': 'new'}, self.group)
        with self.assertRaisesRegex(RuntimeError, 'group-denied'):
            self.queue.draft('set_group_announcement', {'group': 'other', 'text': 'new'}, self.owner)
        plan = self.queue.draft('set_group_announcement', {'group': 'group-a', 'text': 'new'}, self.group)
        backend = self.backend()
        original = backend.execute
        def execute(action, parameters, **kwargs):
            stored = self.queue.inspect(plan['id'], self.owner)
            self.assertEqual(stored['previousAnnouncement']['text'], 'old announcement')
            self.assertEqual(kwargs['expected_previous_announcement'], 'old announcement')
            return original(action, parameters, **kwargs)
        backend.execute = execute
        result = self.queue.execute(plan['id'], plan['hash'], self.group, backend)
        self.assertEqual(result['status'], 'confirmed')

    def test_unverified_old_announcement_never_changes_group(self):
        plan = self.queue.draft('set_group_announcement', {'group': 'group-a', 'text': 'new'}, self.owner)
        backend = self.backend()
        backend.get_group_announcement = lambda group: {'text': 'uncertain', 'verified': False}
        result = self.queue.execute(plan['id'], plan['hash'], self.owner, backend)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(backend.calls, [])

    def test_each_friend_or_group_management_action_requires_explicit_owner_approval(self):
        for action, parameters in [('create_group', {'contacts': ['a', 'b']}),
                                   ('add_friend', {'keywords': 'friend-id', 'addmsg': 'hello'}),
                                   ('accept_friend', {'requestId': 'request-1', 'applicant': 'person', 'verificationMessage': 'hello'})]:
            with self.subTest(action=action):
                plan = self.queue.draft(action, parameters, self.owner)
                backend = self.backend()
                self.assertEqual(plan['status'], 'pending')
                self.queue.execute(plan['id'], plan['hash'], self.owner, backend)
                self.assertEqual(backend.calls, [])
                with self.assertRaisesRegex(RuntimeError, 'explicit-owner-approval-required'):
                    self.queue.approve(plan['id'], plan['hash'], self.owner)
                approved = self.queue.approve(plan['id'], plan['hash'], self.approval(plan))
                self.assertEqual(approved['approvalMessageId'], 'owner-message-1')
                self.assertEqual(self.queue.execute(plan['id'], plan['hash'], self.owner, backend)['status'], 'confirmed')
                self.assertEqual(backend.calls[0][1], approved['parameters'])

    def test_group_even_with_owner_name_flag_cannot_approve_or_execute_management(self):
        plan = self.queue.draft('create_group', {'contacts': ['a', 'b']}, self.group)
        forged = ActionContext('group', 'group-a', True, 'message', plan['id'], plan['hash'])
        with self.assertRaisesRegex(RuntimeError, 'owner-private-required'):
            self.queue.approve(plan['id'], plan['hash'], forged)
        self.queue.approve(plan['id'], plan['hash'], self.approval(plan))
        with self.assertRaisesRegex(RuntimeError, 'owner-private-required'):
            self.queue.execute(plan['id'], plan['hash'], forged, self.backend())

    def test_approval_grant_must_bind_same_plan_id_and_hash(self):
        plan = self.queue.draft('add_friend', {'keywords': 'a'}, self.owner)
        wrong = ActionContext('friend', 'owner', True, 'owner-message', 'other-plan', plan['hash'])
        with self.assertRaisesRegex(RuntimeError, 'explicit-owner-approval-required'):
            self.queue.approve(plan['id'], plan['hash'], wrong)
        with self.assertRaisesRegex(RuntimeError, 'hash-required'):
            self.queue.approve(plan['id'], None, self.approval(plan))

    def test_parameters_are_copied_and_tampering_or_hash_changes_fail_closed(self):
        parameters = {'contacts': ['a', 'b']}
        plan = self.queue.draft('create_group', parameters, self.owner)
        parameters['contacts'][0] = 'evil'
        plan['parameters']['contacts'][0] = 'also evil'
        actual = self.queue.inspect(plan['id'], self.owner)
        self.assertEqual(actual['parameters']['contacts'], ['a', 'b'])
        with self.assertRaisesRegex(RuntimeError, 'hash-mismatch'):
            self.queue.approve(plan['id'], '0' * 64, self.approval(plan))
        state = json.loads(self.queue.file.read_text('utf8'))
        state['plans'][plan['id']]['parameters']['contacts'][0] = 'changed on disk'
        self.queue.file.write_text(json.dumps(state), encoding='utf8')
        with self.assertRaisesRegex(RuntimeError, 'hash-mismatch'):
            self.queue.execute(plan['id'], actual['hash'], self.owner, self.backend())

    def test_expected_friend_profile_name_is_bound_and_rendered_without_approval(self):
        parameters={'keywords':'_Test50K','addmsg':'fixture greeting','expectedProfileName':'🛌'}
        plan=self.queue.draft('add_friend',parameters,self.owner)
        old=self.queue.draft('add_friend',{'keywords':'_Test50K','addmsg':'fixture greeting'},self.owner)
        self.assertNotEqual(plan['hash'],old['hash'])
        self.assertNotIn('expectedProfileName',old['parameters'])
        notice=self.queue.claim_approval_notice(plan['id'],plan['hash'],self.owner)
        self.assertIn('预期昵称：'+json.dumps('🛌',ensure_ascii=False),notice['text'])
        self.assertIn('尚未执行',notice['text'])
        self.assertEqual(self.queue.inspect(plan['id'],self.owner)['status'],'pending')
        state=json.loads(self.queue.file.read_text('utf8'))
        state['plans'][plan['id']]['parameters']['expectedProfileName']='different nickname'
        self.queue.file.write_text(json.dumps(state),encoding='utf8')
        with self.assertRaisesRegex(RuntimeError,'hash-mismatch'):
            self.queue.approve(plan['id'],plan['hash'],self.approval(plan))

    def test_expected_friend_profile_name_rejects_empty_nonstring_or_over_limit(self):
        for value in ('','   ',None,7,'x'*129,'nul\x00name'):
            with self.subTest(valueType=type(value).__name__),self.assertRaisesRegex(RuntimeError,'parameters-invalid'):
                self.queue.draft('add_friend',{'keywords':'_Test50K','expectedProfileName':value},self.owner)
        boundary=self.queue.draft('add_friend',{'keywords':'_Test50K','expectedProfileName':'x'*128},self.owner)
        self.assertEqual(boundary['parameters']['expectedProfileName'],'x'*128)

    def test_expired_plans_cannot_be_approved_or_executed(self):
        pending = self.queue.draft('add_friend', {'keywords': 'a'}, self.owner)
        ready = self.queue.draft('publish_moment', {'text': 'hello'}, self.owner)
        self.now += 86400
        self.assertEqual(self.queue.approve(pending['id'], pending['hash'], self.approval(pending))['status'], 'expired')
        backend = self.backend()
        self.assertEqual(self.queue.execute(ready['id'], ready['hash'], self.owner, backend)['status'], 'expired')
        self.assertEqual(backend.calls, [])

    def test_capability_requires_exact_live_verified_version_and_does_not_claim(self):
        plan = self.queue.draft('publish_moment', {'text': 'hello'}, self.owner)
        backend = self.backend()
        for feature in ({'liveVerified': False, 'clientVersion': '4.2.0.1000'},
                        {'liveVerified': True, 'clientVersion': '4.1.8.107'}, {}):
            backend.evidence['actions']['publish_moment'] = feature
            result = self.queue.execute(plan['id'], plan['hash'], self.owner, backend)
            self.assertEqual(result['executionStatus'], 'unverified')
            self.assertEqual(self.queue.inspect(plan['id'], self.owner)['status'], 'ready')
        self.assertEqual(backend.calls, [])

    def test_backend_exception_or_unconfirmed_result_is_unknown_and_never_retried(self):
        for outcome in ('exception', 'truthy', 'no-receipt'):
            plan = self.queue.draft('publish_moment', {'text': outcome}, self.owner)
            backend = self.backend(result=True if outcome == 'truthy' else {'status': 'confirmed'})
            if outcome == 'exception':
                def fail(*args, **kwargs):
                    backend.calls.append('attempt')
                    raise RuntimeError('do not persist this private detail')
                backend.execute = fail
            self.assertEqual(self.queue.execute(plan['id'], plan['hash'], self.owner, backend)['status'], 'unknown')
            self.queue.execute(plan['id'], plan['hash'], self.owner, backend)
            self.assertEqual(len(backend.calls), 1)
        self.assertNotIn('do not persist this private detail', self.queue.file.read_text('utf8'))

    def test_explicit_backend_failure_and_rejection_are_not_success(self):
        plan = self.queue.draft('publish_moment', {'text': 'hello'}, self.owner)
        backend = self.backend({'status': 'failed', 'code': 'permission-denied'})
        self.assertEqual(self.queue.execute(plan['id'], plan['hash'], self.owner, backend)['status'], 'failed')
        pending = self.queue.draft('create_group', {'contacts': ['a', 'b']}, self.owner)
        self.queue.reject(pending['id'], pending['hash'], self.owner)
        self.assertEqual(self.queue.execute(pending['id'], pending['hash'], self.owner, backend)['status'], 'rejected')

    def test_crash_after_claim_remains_nonretryable_on_new_instance(self):
        plan = self.queue.draft('publish_moment', {'text': 'hello'}, self.owner)
        backend = self.backend()
        backend.execute = lambda *args, **kwargs: (_ for _ in ()).throw(SystemExit())
        with self.assertRaises(SystemExit):
            self.queue.execute(plan['id'], plan['hash'], self.owner, backend)
        restarted = SocialActions(self.root, owner_target='owner', allowed_groups=['group-a'])
        self.assertEqual(restarted.inspect(plan['id'], self.owner)['status'], 'claimed')
        backend = self.backend()
        restarted.execute(plan['id'], plan['hash'], self.owner, backend)
        self.assertEqual(backend.calls, [])

    def test_corrupt_state_and_unknown_parameters_do_not_dispatch(self):
        with self.assertRaisesRegex(RuntimeError, 'parameters-invalid'):
            self.queue.draft('add_friend', {'keywords': 'a', 'executeCommand': 'bad'}, self.owner)
        self.queue.file.parent.mkdir(parents=True, exist_ok=True)
        self.queue.file.write_text('{bad json', encoding='utf8')
        with self.assertRaises(ValueError):
            self.queue.draft('publish_moment', {'text': 'hello'}, self.owner)

    def test_two_queue_instances_cannot_dispatch_same_plan_concurrently(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        plan = self.queue.draft('publish_moment', {'text': 'one post'}, self.owner)
        entered, release = threading.Event(), threading.Event()
        backend = self.backend()
        original = backend.execute
        def execute(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(10))
            return original(*args, **kwargs)
        backend.execute = execute
        second = SocialActions(self.root, owner_target='owner', clock=lambda: self.now)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.queue.execute, plan['id'], plan['hash'], self.owner, backend)
            self.assertTrue(entered.wait(10))
            repeated = second.execute(plan['id'], plan['hash'], self.owner, backend)
            self.assertEqual(repeated['executionStatus'], 'not-dispatched')
            release.set()
            self.assertEqual(first.result(timeout=10)['status'], 'confirmed')
        self.assertEqual(len(backend.calls), 1)


if __name__ == '__main__':
    unittest.main()
