"""Synthetic acceptance fixtures only; no instance of real wxauto is created."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from types import FunctionType
import inspect
import unittest

from social_actions import ActionContext
from social_wxauto_backend import ActionBinding, WxautoSocialBackend


class SocialBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = {'dataRoot': str(self.root), 'target': 'owner', 'groups': ['group-a']}
        self.owner = ActionContext('friend', 'owner', True)
        self.now = 1000.0
        self.client = {'pid': 123, 'executableName': 'Weixin.exe', 'clientVersion': '4.2.0.1000', 'clientSha256': 'a' * 64}
        self.calls = []
        self.parameters = None
        self.action = None
        self.bad_before = False
        self.bad_after = False
        self.info = {'chat_type': 'group', 'chat_name': 'group-a'}
        self.announcement = 'old announcement'
        self.wx = SimpleNamespace(ChatWith=lambda target, **kwargs: self.calls.append(('select', target, kwargs)),
                                  ChatInfo=lambda: copy.deepcopy(self.info))
        self.recoveries = []
        self.backend = WxautoSocialBackend(self.wx, self.config, client_probe=lambda wx: copy.deepcopy(self.client), clock=lambda: self.now,
            refresh_chat=lambda wx,target,kind:self.recoveries.append((wx,target,kind)))

    def observe(self, phase, parameters):
        from social_actions import _digest
        return {'source': 'wxauto-uia', 'action': self.action, 'phase': phase,
                'clientVersion': self.client['clientVersion'], 'parametersHash': _digest(self.action, parameters),
                'target': parameters.get('group'), 'observedAt': self.now,
                'controls': [{'className': 'SyntheticTestControl', 'name': 'fixture, not live UI'}],
                'confirmed': phase == 'after' and not self.bad_after}

    def test_private_group_sync_preserves_bindings_and_recovers_actual_new_group(self):
        bindings=dict(self.backend.bindings)
        self.backend.sync_group_routes(frozenset({'group-a','new-group'}))
        self.assertEqual(self.backend.groups,self.backend.plans.groups)
        self.assertEqual(self.backend.bindings,bindings)
        self.info={'chat_type':'group','chat_name':'new-group'}
        self.backend._navigation_context={}
        self.backend._start_navigation()
        self.assertEqual(self.backend._navigation_context['target'],'new-group')
        self.assertFalse(self.backend._navigation_context['fallback'])
        with self.assertRaisesRegex(RuntimeError,'during-navigation'):
            self.backend.sync_group_routes(frozenset())
        self.assertIn('new-group',self.backend.groups)
        self.backend._navigation_context=None
        self.backend._with_chat_recovery(lambda:self.backend._start_navigation())
        self.assertEqual(self.recoveries[-1][1:],('new-group','group'))
        self.assertTrue(self.backend.last_chat_recovery['restored'])
        self.assertFalse(self.backend.last_chat_recovery['usedOwnerFallback'])
        self.backend.sync_group_routes(frozenset({'group-a'}))
        with self.assertRaisesRegex(RuntimeError,'social-group-denied'):
            self.backend._group('new-group')

    def bind(self, action):
        self.action = action
        def perform(wx, parameters):
            self.calls.append(('perform', copy.deepcopy(parameters)))
            return True
        def before(wx, parameters):
            result = self.observe('before', parameters)
            if self.bad_before:
                result['parametersHash'] = '0' * 64
            return result
        def after(wx, parameters, result, old):
            return self.observe('after', parameters)
        def read(wx, group):
            return {'text': self.announcement, 'target': group, 'verified': True,
                    'source': 'wxauto-uia', 'clientVersion': self.client['clientVersion'],
                    'observedAt': self.now, 'controls': [{'name': 'synthetic announcement'}]}
        binding = ActionBinding('synthetic-test-binding', perform, before, after,
                                read if action == 'set_group_announcement' else None)
        self.backend.bindings[action] = binding
        return binding

    def receipt(self, action):
        binding = self.backend.bindings[action]
        entry = {'status': 'passed', 'bindingFingerprint': binding.fingerprint()}
        for phase in ('before', 'after'):
            file = self.root / 'backups' / ('synthetic-' + phase + '.json')
            file.parent.mkdir(exist_ok=True)
            body = json.dumps({'source': 'wxauto-uia', 'action': action, 'phase': phase,
                'clientVersion': self.client['clientVersion'], 'controls': [{'name': 'synthetic-only'}],
                'confirmed': phase == 'after'}).encode()
            file.write_bytes(body)
            entry[phase] = {'path': file.relative_to(self.root).as_posix(), 'sha256': hashlib.sha256(body).hexdigest()}
        value = {'format': 'whale-social-live-acceptance', 'version': 1,
            'clientVersion': self.client['clientVersion'], 'clientSha256': self.client['clientSha256'], 'actions': {action: entry}}
        self.backend.receipt_file.parent.mkdir(exist_ok=True)
        self.backend.receipt_file.write_text(json.dumps(value), encoding='utf8')
        return value

    def plan(self, action='publish_moment', parameters=None):
        self.bind(action)
        self.receipt(action)
        self.parameters = parameters or {'text': 'hello'}
        return self.backend.plans.draft(action, self.parameters, self.owner)

    def execute(self, plan):
        return self.backend.plans.execute(plan['id'], plan['hash'], self.owner, self.backend)

    def test_missing_bindings_or_receipts_never_enable_actions_or_touch_ui(self):
        self.assertFalse(any(value['liveVerified'] for value in self.backend.capabilities()['actions'].values()))
        plan = self.backend.plans.draft('publish_moment', {'text': 'hello'}, self.owner)
        self.assertEqual(self.execute(plan)['executionStatus'], 'unverified')
        self.bind('publish_moment')
        self.assertEqual(self.execute(plan)['executionStatus'], 'unverified')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.recoveries, [])

    def test_invalid_plan_does_not_recover_or_navigate(self):
        plan = self.plan()
        result = self.backend.execute('publish_moment', plan['parameters'], idempotency_key=plan['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.recoveries, [])
        self.assertEqual(self.calls, [])

    def test_success_restores_original_allowlisted_chat_without_reseeding(self):
        plan = self.plan()
        self.assertEqual(self.execute(plan)['status'], 'confirmed')
        self.assertEqual(self.recoveries, [(self.wx,'group-a','group')])
        self.assertEqual(self.backend.last_chat_recovery,
            {'restored':True,'targetKind':'group','usedOwnerFallback':False})
        self.assertFalse(self.backend.chat_binding_failed)

    def test_nonallowlisted_or_stale_current_chat_falls_back_only_to_owner(self):
        for mode in ('other','stale'):
            plan = self.plan(parameters={'text':mode})
            self.info={'chat_name':'not-allowed','chat_type':'friend'}
            if mode == 'stale':
                def stale():raise LookupError('stale')
                self.wx.ChatInfo=stale
            self.assertEqual(self.execute(plan)['status'],'confirmed')
            self.assertEqual(self.recoveries[-1],(self.wx,'owner','friend'))
            self.assertTrue(self.backend.last_chat_recovery['usedOwnerFallback'])

    def test_preflight_exception_recovers_once_without_performing(self):
        plan=self.plan()
        binding=self.backend.bindings['publish_moment']
        def fail(wx,parameters):raise LookupError('unresolved-modal')
        self.backend.bindings['publish_moment']=ActionBinding(binding.implementation_id,
            binding.perform,fail,binding.observe_after)
        self.receipt('publish_moment')
        self.assertEqual(self.execute(plan)['status'],'failed')
        self.assertEqual(len(self.recoveries),1)
        self.assertFalse(any(c[0]=='perform' for c in self.calls))

    def test_restore_failure_never_overwrites_confirmed_or_retries_external_action(self):
        plan=self.plan()
        def fail(*args):raise LookupError('modal-still-open')
        self.backend.refresh_chat=fail
        self.assertEqual(self.execute(plan)['status'],'confirmed')
        self.assertTrue(self.backend.chat_binding_failed)
        self.assertEqual(self.backend.last_chat_recovery['code'],'social-chat-restore-failed')
        self.assertFalse(self.backend.last_chat_recovery['restored'])
        self.execute(plan)
        self.assertEqual(sum(c[0]=='perform' for c in self.calls),1)
        self.assertEqual(self.backend.capabilities()['actions'],{})

    def test_unknown_external_result_stays_unknown_when_restore_also_fails(self):
        plan=self.plan()
        self.bad_after=True
        def fail(*args):raise RuntimeError('unresolved-user-draft')
        self.backend.refresh_chat=fail
        self.assertEqual(self.execute(plan)['status'],'unknown')
        self.assertTrue(self.backend.chat_binding_failed)
        self.assertFalse(self.backend.last_chat_recovery['restored'])
        self.execute(plan)
        self.assertEqual(sum(c[0]=='perform' for c in self.calls),1)

    def test_announcement_read_recovers_on_success_and_target_failure(self):
        self.bind('set_group_announcement'); self.receipt('set_group_announcement')
        self.assertTrue(self.backend.get_group_announcement('group-a')['verified'])
        self.assertEqual(len(self.recoveries),1)
        self.info={'chat_name':'wrong','chat_type':'group'}
        with self.assertRaisesRegex(RuntimeError,'target-mismatch'):
            self.backend.get_group_announcement('group-a')
        self.assertEqual(len(self.recoveries),2)
        with self.assertRaisesRegex(RuntimeError,'group-denied'):
            self.backend.get_group_announcement('not-allowed')
        self.assertEqual(len(self.recoveries),2)

    def test_receipt_cannot_validate_different_loaded_code_with_identical_disk_source(self):
        plan = self.plan()
        binding = self.backend.bindings['publish_moment']
        original = binding.perform
        constants = tuple(False if value is True else value for value in original.__code__.co_consts)
        self.assertNotEqual(constants, original.__code__.co_consts)
        changed = FunctionType(original.__code__.replace(co_consts=constants),
            original.__globals__, original.__name__, original.__defaults__, original.__closure__)
        self.assertEqual(inspect.getsource(original), inspect.getsource(changed))
        replacement = ActionBinding(binding.implementation_id, changed,
            binding.observe_before, binding.observe_after, binding.read_announcement)
        self.assertNotEqual(binding.fingerprint(), replacement.fingerprint())
        self.backend.bindings['publish_moment'] = replacement
        self.assertFalse(self.backend.capabilities()['actions']['publish_moment']['liveVerified'])
        self.assertEqual(self.execute(plan)['executionStatus'], 'unverified')
        self.assertEqual(self.calls, [])

    def test_version_hash_binding_and_acceptance_artifacts_must_all_match(self):
        plan = self.plan()
        self.assertTrue(self.backend.capabilities()['actions']['publish_moment']['liveVerified'])
        original = copy.deepcopy(self.client)
        self.client['clientVersion'] = '4.2.0.1001'
        self.assertEqual(self.execute(plan)['executionStatus'], 'unverified')
        self.client = copy.deepcopy(original)
        self.client['clientSha256'] = 'b' * 64
        self.assertEqual(self.execute(plan)['executionStatus'], 'unverified')
        self.client = copy.deepcopy(original)
        value = json.loads(self.backend.receipt_file.read_text())
        value['actions']['publish_moment']['bindingFingerprint'] = 'wrong'
        self.backend.receipt_file.write_text(json.dumps(value))
        self.assertEqual(self.execute(plan)['executionStatus'], 'unverified')
        self.receipt('publish_moment')
        (self.root / 'backups/synthetic-after.json').write_bytes(b'changed')
        self.assertEqual(self.execute(plan)['executionStatus'], 'unverified')
        self.assertEqual(self.calls, [])

    def test_claim_and_actual_before_after_contract_are_required(self):
        plan = self.plan()
        direct = self.backend.execute('publish_moment', plan['parameters'], idempotency_key=plan['id'])
        self.assertEqual(direct['status'], 'failed')
        self.assertEqual(self.calls, [])
        result = self.execute(plan)
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual(sum(call[0] == 'perform' for call in self.calls), 1)
        self.execute(plan)
        self.assertEqual(sum(call[0] == 'perform' for call in self.calls), 1)

    def test_wrong_preflight_never_performs_and_truthy_result_without_after_evidence_is_unknown(self):
        plan = self.plan()
        self.bad_before = True
        self.assertEqual(self.execute(plan)['status'], 'failed')
        self.assertEqual(self.calls, [])
        plan = self.plan()
        self.bad_before = False
        self.bad_after = True
        self.assertEqual(self.execute(plan)['status'], 'unknown')
        self.assertEqual(sum(call[0] == 'perform' for call in self.calls), 1)

    def test_announcement_checks_exact_chat_type_and_preserves_old_text(self):
        plan = self.plan('set_group_announcement', {'group': 'group-a', 'text': 'new announcement'})
        result = self.execute(plan)
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual(result['previousAnnouncement']['text'], 'old announcement')
        self.assertTrue(all(call[2] == {'exact': True} for call in self.calls if call[0] == 'select'))

    def test_wrong_group_and_changed_announcement_never_perform(self):
        plan = self.plan('set_group_announcement', {'group': 'group-a', 'text': 'new announcement'})
        self.info['chat_type'] = 'friend'
        result = self.execute(plan)
        self.assertEqual(result['status'], 'unknown')  # old-body acquisition threw; no external write
        self.assertFalse(any(call[0] == 'perform' for call in self.calls))
        self.info['chat_type'] = 'group'
        plan = self.plan('set_group_announcement', {'group': 'group-a', 'text': 'new announcement'})
        original = self.backend.get_group_announcement
        reads = {'value': 0}
        def changing(group):
            reads['value'] += 1
            if reads['value'] > 1:
                self.announcement = 'someone changed it'
            return original(group)
        self.backend.get_group_announcement = changing
        self.assertEqual(self.execute(plan)['status'], 'failed')
        self.assertFalse(any(call[0] == 'perform' for call in self.calls))

    def test_manual_action_still_needs_plan_specific_owner_approval(self):
        plan = self.plan('create_group', {'contacts': ['a', 'b']})
        self.assertEqual(self.execute(plan)['status'], 'pending')
        self.assertEqual(self.calls, [])
        approved = ActionContext('friend', 'owner', True, 'explicit-owner-message', plan['id'], plan['hash'])
        self.backend.plans.approve(plan['id'], plan['hash'], approved)
        self.assertEqual(self.execute(plan)['status'], 'confirmed')


    def unverified_plan(self, action='publish_moment', parameters=None):
        self.bind(action)
        return self.backend.plans.draft(action, parameters or {'text': 'local acceptance fixture'}, self.owner)

    def accept(self, plan, **overrides):
        from social_wxauto_backend import run_local_acceptance
        return run_local_acceptance(self.backend, **{'plan_id': plan['id'], 'expected_hash': plan['hash'],
            'action': plan['action'], 'fingerprint': self.backend.bindings[plan['action']].fingerprint(), **overrides})

    def test_local_first_acceptance_does_not_need_or_forge_prior_receipt(self):
        plan = self.unverified_plan()
        self.assertFalse(self.backend.receipt_file.exists())
        self.assertEqual(self.execute(plan)['executionStatus'], 'unverified')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.accept(plan)['status'], 'confirmed')
        self.assertTrue(self.backend.capabilities()['actions']['publish_moment']['liveVerified'])
        self.assertEqual(sum(c[0] == 'perform' for c in self.calls), 1)
        self.assertEqual(self.accept(plan)['executionStatus'], 'not-dispatched')
        self.assertEqual(sum(c[0] == 'perform' for c in self.calls), 1)
        entry = json.loads(self.backend.receipt_file.read_text())['actions']['publish_moment']
        self.assertEqual(entry['planId'], plan['id'])
        self.assertEqual(entry['parametersHash'], plan['hash'])

    def test_local_acceptance_rejects_wrong_fingerprint_action_and_hash_before_ui(self):
        plan = self.unverified_plan()
        for kwargs in ({'fingerprint': 'b' * 64}, {'action': 'add_friend'}, {'expected_hash': '0' * 64}):
            with self.assertRaises(RuntimeError): self.accept(plan, **kwargs)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.backend.plans.inspect(plan['id'], self.owner)['status'], 'ready')
        self.assertFalse(self.backend.receipt_file.exists())

    def test_local_acceptance_cannot_bypass_owner_plan_approval(self):
        plan = self.unverified_plan('create_group', {'contacts': ['a', 'b']})
        self.assertEqual(self.accept(plan)['executionStatus'], 'not-dispatched')
        self.assertEqual(self.calls, [])
        approval = ActionContext('friend', 'owner', True, 'owner-plan-approval', plan['id'], plan['hash'])
        self.backend.plans.approve(plan['id'], plan['hash'], approval)
        self.assertEqual(self.accept(plan)['status'], 'confirmed')

    def test_local_acceptance_without_after_is_unknown_and_never_retries_or_promotes(self):
        plan = self.unverified_plan(); self.bad_after = True
        self.assertEqual(self.accept(plan)['status'], 'unknown')
        self.assertFalse(self.backend.receipt_file.exists())
        self.assertEqual(self.accept(plan)['executionStatus'], 'not-dispatched')
        self.assertEqual(sum(c[0] == 'perform' for c in self.calls), 1)
        self.assertFalse(self.backend.capabilities()['actions']['publish_moment']['liveVerified'])

    def test_local_acceptance_before_mismatch_never_performs_or_promotes(self):
        plan = self.unverified_plan(); self.bad_before = True
        self.assertEqual(self.accept(plan)['status'], 'failed')
        self.assertEqual(self.calls, [])
        self.assertFalse(self.backend.receipt_file.exists())

    def test_first_announcement_acceptance_saves_original_without_old_receipt(self):
        plan = self.unverified_plan('set_group_announcement', {'group': 'group-a', 'text': 'new announcement'})
        self.assertEqual(self.accept(plan)['status'], 'confirmed')
        self.assertEqual(self.backend.plans.inspect(plan['id'], self.owner)['previousAnnouncement']['text'], 'old announcement')
        self.assertTrue(self.backend.capabilities()['actions']['set_group_announcement']['liveVerified'])

    def test_missing_observed_bindings_load_gracefully_without_client(self):
        from unittest.mock import patch
        from social_wxauto_backend import load_observed_bindings
        error = ModuleNotFoundError('missing', name='social_observed_bindings')
        with patch('social_wxauto_backend.importlib.import_module', side_effect=error):
            self.assertEqual(load_observed_bindings(None, self.config), {})


if __name__ == '__main__':
    unittest.main()
