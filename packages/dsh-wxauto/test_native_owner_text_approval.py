from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import unittest
from unittest.mock import Mock, patch

import native_owner_text_approval as module
from native_owner_text_approval import apply_native_owner_decision
from native_owner_text_catalog import list_native_owner_texts
from social_actions import ActionContext
from social_ipc import SocialDispatcher
import test_native_owner_text_claim as claim_fixture
from vault.store import digest_file
from vault.test_snapshot_import import OWNER, SENDER

HERE = Path(__file__).parent


class NativeOwnerDecisionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = claim_fixture.OwnerTextClaimTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.config = {**self.fixture.config, 'target': 'owner-fixture', 'groups': []}
        self.now = 1772323203.5
        self.backend = Mock()
        self.backend.execute.side_effect = AssertionError('must never execute')
        self.dispatcher = SocialDispatcher(self.config, self.backend, clock=lambda: self.now)
        self.context = ActionContext('friend', self.config['target'], True)
        self.plan = self.dispatcher.queue.draft('add_friend', {'keywords': 'fixture-account-to-add'}, self.context)
        self.now = 1772323204.75
        self.notice = self.dispatcher.queue.claim_approval_notice(self.plan['id'], self.plan['hash'], self.context)
        self.now = 1772323206

    def write_command(self, text, stamp=1772323205):
        fixture = self.fixture
        with closing(sqlite3.connect(fixture.snapshot / 'message/message_0.db')) as db:
            db.execute(f'UPDATE [{fixture.table}] SET message_content=?,create_time=? WHERE server_id=9001', (text, stamp)); db.commit()
        file = fixture.snapshot / 'snapshot-manifest.json'
        manifest = json.loads(file.read_text('utf8'))
        manifest['databases'] = [{'database': p.relative_to(fixture.snapshot).as_posix(), 'authenticated': True,
                                  'plaintext_sha256': digest_file(p)} for p in fixture.snapshot.rglob('*.db')]
        file.write_text(json.dumps(manifest), encoding='utf8')
        catalog = list_native_owner_texts(fixture.snapshot, expected_account_username=OWNER, expected_source_id='fixture',
            account_id=self.config['accountId'], owner_native_chat_key='wechat:' + SENDER, expected_peer_username=SENDER)
        fixture.file.unlink()
        script = """
import fs from 'node:fs';import {createNativeOwnerTextInbox} from './native-owner-text-inbox.mjs';
const p=JSON.parse(fs.readFileSync(0,'utf8'));const q=createNativeOwnerTextInbox({...p.config,now:()=>1772323206000});
const init=q.initialize({...p.catalog,messages:[]},{cutoverAtMs:1772323200000});q.enqueue(p.catalog);
const c=q.pending()[0];q.submit([c.claimId]);console.log(JSON.stringify({epoch:init.authorityEpoch,id:c.claimId}));
"""
        node_config = {'dataRoot': self.config['dataRoot'], 'accountId': self.config['accountId'],
                       'ownerNativeChatKey': 'wechat:' + SENDER, 'expectedPeerUsername': SENDER}
        result = subprocess.run(['node', '--input-type=module', '-e', script], input=json.dumps({'config': node_config, 'catalog': catalog}),
                                text=True, encoding='utf8', capture_output=True, cwd=HERE, check=True)
        proof = json.loads(result.stdout)
        fixture.epoch, fixture.claim_id = proof['epoch'], proof['id']

    def apply(self, **kwargs):
        return apply_native_owner_decision(self.dispatcher, self.config, self.fixture.claim_id, self.fixture.epoch,
            active_authority_provider=kwargs.pop('active_authority_provider', self.fixture.active), clock=lambda: self.now, **kwargs)

    def ledger(self):
        return Path(self.config['dataRoot']) / 'config/native-owner-text-approvals' / self.fixture.epoch / self.fixture.claim_id

    def test_real_native_code_approves_exact_plan_once_no_execute_or_ui_grant(self):
        self.write_command(self.notice['approveCode'])
        result = self.apply()
        self.assertEqual(result, {'handled': True, 'decisionStatus': 'approved', 'code': 'native-owner-decision-approved',
            'planId': self.plan['id'], 'hash': self.plan['hash'], 'action': 'add_friend', 'approvalMessageId': 'wechat-server:9001'})
        plan = self.dispatcher.queue.inspect(self.plan['id'], self.context)
        self.assertEqual(plan['approvalMessageId'], 'wechat-server:9001')
        self.assertEqual(plan['approvedBy'], self.config['target'])
        marker = self.ledger().read_text('utf8')
        self.assertNotIn(self.notice['approveCode'], marker)
        self.assertIn(self.plan['hash'], marker)
        self.assertEqual(self.apply()['code'], 'native-owner-decision-already-reserved')
        restarted = SocialDispatcher(self.config, self.backend, clock=lambda: self.now)
        result = apply_native_owner_decision(restarted, self.config, self.fixture.claim_id, self.fixture.epoch,
            active_authority_provider=self.fixture.active, clock=lambda: self.now)
        self.assertEqual(result['code'], 'native-owner-decision-already-reserved')
        self.assertEqual(self.dispatcher._approvals, {})
        self.backend.execute.assert_not_called()

    def test_reject_code_only_rejects(self):
        self.write_command(self.notice['rejectCode'])
        with patch.object(self.dispatcher.queue, 'approve', side_effect=AssertionError('wrong op')):
            result = self.apply()
        self.assertEqual(result['decisionStatus'], 'rejected')
        self.assertEqual(self.dispatcher.queue.inspect(self.plan['id'], self.context)['status'], 'rejected')

    def test_explicit_plan_hash_without_notice_can_decide(self):
        plan = self.dispatcher.queue.draft('add_friend', {'keywords': 'second-fixture'}, self.context)
        self.write_command('批准操作 ' + plan['id'] + ' ' + plan['hash'], stamp=1772323206)
        result = self.apply()
        self.assertEqual(result['decisionStatus'], 'approved')

    def test_invalid_unresolved_code_and_old_timestamp_make_no_ledger(self):
        for text, stamp in [('not a command', 1772323205), ('00000000', 1772323205),
                            (self.notice['approveCode'], 1772323203)]:
            self.write_command(text, stamp)
            with self.subTest(textlen=len(text), stamp=stamp):
                self.assertFalse(self.apply()['handled'])
                self.assertFalse(self.ledger().exists())

    def test_same_second_notice_floor_is_supported_not_before_previous_second(self):
        self.write_command(self.notice['approveCode'], stamp=1772323204)
        self.assertEqual(self.apply()['decisionStatus'], 'approved')

    def test_inactive_and_no_claimed_notice_deny_without_ledger(self):
        self.write_command(self.notice['approveCode'])
        self.assertFalse(self.apply(active_authority_provider=None)['handled'])
        self.assertFalse(self.ledger().exists())
        with self.dispatcher.queue._locked() as state:
            state['plans'][self.plan['id']].pop('approvalNotice'); self.dispatcher.queue._save(state)
        self.assertEqual(self.apply()['code'], 'native-owner-decision-notice-not-fresh')
        self.assertFalse(self.ledger().exists())

    def test_partial_marker_and_decision_failure_never_retried(self):
        self.write_command(self.notice['approveCode'])
        self.ledger().parent.mkdir(parents=True)
        self.ledger().write_bytes(b'{partial')
        with patch.object(self.dispatcher.queue, 'approve') as approve:
            self.assertEqual(self.apply()['code'], 'native-owner-decision-already-reserved')
            approve.assert_not_called()
        self.ledger().unlink()  # Isolated fixture only: separate case, not a runtime recovery policy.
        with patch.object(self.dispatcher.queue, 'approve', side_effect=RuntimeError('private exception must not leak')):
            result = self.apply()
        self.assertEqual(result, {'handled': True, 'decisionStatus': 'unknown', 'code': 'native-owner-decision-unknown'})
        self.assertTrue(self.ledger().exists())
        self.assertEqual(self.apply()['code'], 'native-owner-decision-already-reserved')

    def test_fsync_failure_preserves_marker_and_blocks_approval(self):
        self.write_command(self.notice['approveCode'])
        with patch.object(module.os, 'fsync', side_effect=OSError('fixture fsync failure')), patch.object(self.dispatcher.queue, 'approve') as approve:
            result = self.apply()
            approve.assert_not_called()
        self.assertEqual(result['decisionStatus'], 'unknown')
        self.assertTrue(self.ledger().exists())
        self.assertEqual(self.apply()['code'], 'native-owner-decision-already-reserved')

    def test_authority_revoked_after_reservation_does_not_decide_or_retry(self):
        self.write_command(self.notice['approveCode'])
        calls = []
        def active():
            calls.append(True)
            return {**self.fixture.active(), 'active': len(calls) <= 2}
        with patch.object(self.dispatcher.queue, 'approve') as approve:
            self.assertEqual(self.apply(active_authority_provider=active)['decisionStatus'], 'unknown')
            approve.assert_not_called()
        self.assertTrue(self.ledger().exists())
        self.assertEqual(self.apply()['code'], 'native-owner-decision-already-reserved')

    def test_group_origin_plan_cannot_be_native_owner_approved(self):
        self.dispatcher.groups = frozenset({'fixture-group'}); self.dispatcher.queue.groups = self.dispatcher.groups
        plan = self.dispatcher.queue.draft('create_group', {'contacts': ['friend-fixture']}, ActionContext('group', 'fixture-group'))
        self.write_command('批准操作 ' + plan['id'] + ' ' + plan['hash'], stamp=1772323206)
        self.assertEqual(self.apply()['code'], 'native-owner-decision-plan-not-current')
        self.assertFalse(self.ledger().exists())

    def test_resolver_rechecked_after_reservation_and_explicit_reject_works(self):
        self.write_command(self.notice['approveCode'])
        original = self.dispatcher.queue.resolve_approval_code
        calls = []
        def rotate(*args, **kwargs):
            calls.append(True)
            return original(*args, **kwargs) if len(calls) == 1 else None
        with patch.object(self.dispatcher.queue, 'resolve_approval_code', side_effect=rotate), patch.object(self.dispatcher.queue, 'approve') as approve:
            self.assertEqual(self.apply()['decisionStatus'], 'unknown')
            approve.assert_not_called()
        self.assertTrue(self.ledger().exists())
        # New genuine source identity/epoch, isolated fixture only.
        self.write_command('拒绝操作 ' + self.plan['id'] + ' ' + self.plan['hash'])
        self.assertEqual(self.apply()['decisionStatus'], 'rejected')

    def test_existing_directory_or_marker_readback_damage_never_decides(self):
        self.write_command(self.notice['approveCode'])
        self.ledger().mkdir(parents=True)
        with patch.object(self.dispatcher.queue, 'approve') as approve:
            self.assertEqual(self.apply()['code'], 'native-owner-decision-already-reserved')
            approve.assert_not_called()
        self.ledger().rmdir()
        original = module._read
        def damaged(file):
            value, token = original(file)
            return ({**value, 'planHash': '0' * 64}, token) if file == self.ledger() else (value, token)
        with patch.object(module, '_read', side_effect=damaged), patch.object(self.dispatcher.queue, 'approve') as approve:
            self.assertEqual(self.apply()['decisionStatus'], 'unknown')
            approve.assert_not_called()
        self.assertEqual(self.apply()['code'], 'native-owner-decision-already-reserved')


if __name__ == '__main__':
    unittest.main()
