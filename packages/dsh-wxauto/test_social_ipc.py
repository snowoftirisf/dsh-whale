import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from social_ipc import SocialDispatcher, parse_owner_command


class SocialIpcTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.config = {'dataRoot': self.temp.name, 'target': 'owner', 'groups': ['group-a']}
        self.dispatcher = SocialDispatcher(self.config)

    def draft(self, action='add_friend', parameters=None):
        return self.dispatcher.dispatch({'op': 'social-draft', 'target': 'owner',
            'action': action, 'parameters': parameters or {'keywords': 'new-friend'}})

    def event(self, plan, **updates):
        return {'event': 'message', 'id': 'incoming-owner-message', 'target': 'owner',
            'kind': 'friend', 'mediaType': 'text',
            'text': '批准操作 ' + plan['id'] + ' ' + plan['hash'], **updates}

    def approve(self, plan, **updates):
        return self.dispatcher.dispatch({'op': 'social-approve', 'target': 'owner',
            'planId': plan['id'], 'hash': plan['hash'],
            'approvalMessageId': 'incoming-owner-message', **updates})

    def test_private_group_sync_updates_all_scopes_without_rewriting_plans_or_owner_grants(self):
        from social_wxauto_backend import WxautoSocialBackend
        backend=WxautoSocialBackend(SimpleNamespace(),self.config)
        self.dispatcher.backend=backend
        plan=self.draft();self.dispatcher.observe_incoming(self.event(plan))
        before=self.dispatcher.queue.file.read_bytes();grants=copy.deepcopy(self.dispatcher._approvals)
        groups=frozenset({'group-a','new-group'})
        self.dispatcher.sync_group_routes(groups)
        self.assertTrue(all(value==groups for value in (self.dispatcher.groups,self.dispatcher.queue.groups,backend.groups,backend.plans.groups)))
        self.assertEqual(self.dispatcher.queue.file.read_bytes(),before)
        self.assertEqual(self.dispatcher._approvals,grants)
        with self.assertRaisesRegex(RuntimeError,'owner-private-required'):
            self.approve(plan,target='new-group')
        approved=self.approve(plan)
        self.assertEqual(approved['hash'],plan['hash']);self.assertEqual(approved['approvedBy'],'owner')
        self.assertEqual(approved['approvalMessageId'],'incoming-owner-message')

    def test_group_suspend_blocks_announcement_before_claim_and_reenable_preserves_plan(self):
        self.dispatcher.sync_group_routes(frozenset({'group-a','new-group'}))
        plan=self.dispatcher.dispatch({'op':'social-draft','target':'new-group','action':'set_group_announcement',
            'parameters':{'group':'new-group','text':'draft only'}})
        before=self.dispatcher.queue.file.read_bytes()
        self.dispatcher.sync_group_routes(frozenset({'group-a'}))
        with self.assertRaisesRegex(RuntimeError,'social-target-denied'):
            self.dispatcher.dispatch({'op':'social-status','target':'new-group'})
        with self.assertRaisesRegex(RuntimeError,'social-group-denied'):
            self.dispatcher.dispatch({'op':'social-execute','target':'owner','planId':plan['id'],'hash':plan['hash']})
        self.assertEqual(self.dispatcher.queue.file.read_bytes(),before)
        self.dispatcher.sync_group_routes(frozenset({'group-a','new-group'}))
        restored=self.dispatcher.dispatch({'op':'social-inspect','target':'owner','planId':plan['id']})
        self.assertEqual(restored['hash'],plan['hash']);self.assertEqual(restored['status'],plan['status'])

    def test_group_sync_rejects_invalid_values_before_any_scope_mutation_and_is_not_ipc(self):
        from social_wxauto_backend import WxautoSocialBackend
        backend=WxautoSocialBackend(SimpleNamespace(),self.config);self.dispatcher.backend=backend
        original=self.dispatcher.groups
        for value in (['group-a'],{'group-a'},frozenset({'owner'}),frozenset({' bad '}),frozenset({'a\nb'}),frozenset({None})):
            with self.subTest(value=value),self.assertRaisesRegex(RuntimeError,'sync-invalid'):
                self.dispatcher.sync_group_routes(value)
            self.assertTrue(all(groups==original for groups in (self.dispatcher.groups,self.dispatcher.queue.groups,backend.groups,backend.plans.groups)))
        with self.assertRaisesRegex(RuntimeError,'social-operation-denied'):
            self.dispatcher.dispatch({'op':'sync-group-routes','target':'owner','groups':['new-group']})

    def test_active_new_group_cannot_draft_another_groups_announcement(self):
        self.dispatcher.sync_group_routes(frozenset({'group-a','new-group'}))
        with self.assertRaisesRegex(RuntimeError,'social-group-denied'):
            self.dispatcher.dispatch({'op':'social-draft','target':'new-group','action':'set_group_announcement',
                'parameters':{'group':'group-a','text':'not authorized by this caller'}})

    def test_default_backend_is_unverified_and_cannot_execute_autonomous_action(self):
        status = self.dispatcher.dispatch({'op': 'social-status', 'target': 'owner'})
        self.assertFalse(status['nativeBackendReady'])
        self.assertIsNone(status['clientVersion'])
        plan = self.draft('publish_moment', {'text': 'hello'})
        result = self.dispatcher.dispatch({'op': 'social-execute', 'target': 'owner',
            'planId': plan['id'], 'hash': plan['hash']})
        self.assertEqual(result['executionStatus'], 'unverified')
        self.assertEqual(result['status'], 'ready')

    def test_ipc_cannot_forge_approval_without_observed_private_command(self):
        plan = self.draft()
        with self.assertRaisesRegex(RuntimeError, 'approval-grant-missing'):
            self.approve(plan)
        command = {'op': 'social-approve', 'target': 'owner', 'planId': plan['id'],
            'hash': plan['hash'], 'approvalMessageId': 'fake',
            'context': {'kind': 'friend', 'is_owner': True}}
        with self.assertRaisesRegex(RuntimeError, 'social-command-invalid'):
            self.dispatcher.dispatch(command)

    def test_only_real_private_text_command_creates_single_use_plan_bound_grant(self):
        plan = self.draft()
        invalid = [dict(kind='group', target='group-a'), dict(mediaType='image'), dict(mediaType='quote'),
                   dict(source='history'), dict(source='snapshot'), dict(source='self-account'),
                   dict(direction='outgoing'), dict(target='stranger'), dict(id='')]
        for updates in invalid:
            self.assertIsNone(self.dispatcher.observe_incoming(self.event(plan, **updates)))
        with self.assertRaisesRegex(RuntimeError, 'approval-grant-missing'):
            self.approve(plan)
        self.assertEqual(self.dispatcher.observe_incoming(self.event(plan))['planId'], plan['id'])
        approved = self.approve(plan)
        self.assertEqual(approved['status'], 'approved')
        self.assertEqual(approved['approvalMessageId'], 'incoming-owner-message')
        with self.assertRaisesRegex(RuntimeError, 'approval-grant-missing'):
            self.approve(plan)

    def test_grant_does_not_approve_another_plan_or_changed_hash(self):
        plan, other = self.draft(), self.draft()
        self.dispatcher.observe_incoming(self.event(plan))
        with self.assertRaisesRegex(RuntimeError, 'approval-grant-missing'):
            self.approve(other)
        with self.assertRaisesRegex(RuntimeError, 'approval-grant-missing'):
            self.approve(plan, hash='f' * 64)
        self.assertEqual(self.approve(plan)['status'], 'approved')

    def test_group_cannot_use_owner_grant_or_target_other_groups(self):
        plan = self.draft()
        self.dispatcher.observe_incoming(self.event(plan))
        with self.assertRaisesRegex(RuntimeError, 'owner-private-required'):
            self.approve(plan, target='group-a')
        with self.assertRaisesRegex(RuntimeError, 'group-denied'):
            self.dispatcher.dispatch({'op': 'social-draft', 'target': 'group-a',
                'action': 'set_group_announcement', 'parameters': {'group': 'group-b', 'text': 'no'}})

    def test_rejection_requires_its_own_explicit_command(self):
        plan = self.draft()
        self.dispatcher.observe_incoming(self.event(plan))
        command = {'op': 'social-reject', 'target': 'owner', 'planId': plan['id'],
            'hash': plan['hash'], 'approvalMessageId': 'incoming-owner-message'}
        with self.assertRaisesRegex(RuntimeError, 'approval-grant-missing'):
            self.dispatcher.dispatch(command)
        event = self.event(plan, id='reject-message', text='拒绝操作 ' + plan['id'] + ' ' + plan['hash'])
        self.dispatcher.observe_incoming(event)
        command['approvalMessageId'] = event['id']
        self.assertEqual(self.dispatcher.dispatch(command)['status'], 'rejected')

    def test_protocol_response_never_confuses_presence_with_verified_execution(self):
        response = self.dispatcher.reply({'op': 'social-status', 'id': 'social:1', 'target': 'owner'})
        self.assertEqual(response['event'], 'social-result')
        self.assertEqual(response['id'], 'social:1')
        self.assertTrue(response['ok'])
        self.assertFalse(response['value']['nativeBackendReady'])
        failed = self.dispatcher.reply({'op': 'social-approve', 'id': 'social:2', 'target': 'stranger',
            'planId': 'x', 'hash': 'y', 'approvalMessageId': 'z'})
        self.assertFalse(failed['ok'])
        self.assertEqual(failed['code'], 'social-target-denied')

    def test_commands_are_exact_not_quoted_or_embedded_in_other_text(self):
        plan = self.draft()
        command = self.event(plan)['text']
        self.assertIsNotNone(parse_owner_command(command))
        for text in ('他说' + command, '```' + command + '```', command + ' 然后改成另一个人', '批准操作 ' + plan['id']):
            self.assertIsNone(parse_owner_command(text))


    def test_short_code_is_plan_bound_persistent_and_fresh_private_only(self):
        plan = self.draft()
        self.assertRegex(plan['approvalCodes']['approve'], '^[0-9a-f]{8}$')
        restarted = SocialDispatcher(self.config)
        event = self.event(plan, text=plan['approvalCodes']['approve'])
        for update in ({'kind': 'group', 'target': 'group-a'}, {'source': 'history'}, {'source': 'ocr'},
                       {'mediaType': 'voice'}, {'source': 'snapshot'}, {'direction': 'outgoing'}):
            self.assertIsNone(restarted.observe_incoming({**event, **update}))
        command = restarted.observe_incoming(event)
        self.assertEqual(command, {'operation': 'social-approve', 'planId': plan['id'], 'hash': plan['hash']})
        result = restarted.dispatch({'op': 'social-approve', 'target': 'owner', 'planId': plan['id'],
            'hash': plan['hash'], 'approvalMessageId': event['id']})
        self.assertEqual(result['status'], 'approved')
        reject = {**event, 'id': 'fresh-rejection', 'text': plan['approvalCodes']['reject']}
        self.assertEqual(restarted.observe_incoming(reject)['operation'], 'social-reject')

    def test_notice_claim_is_fixed_durable_and_does_not_execute(self):
        plan = self.draft(parameters={'keywords': 'a-specific-user', 'addmsg': 'hello applicant',
            'remark': 'specific remark', 'tags': ['tag']})
        command = {'op': 'social-approval-notice', 'target': 'owner', 'planId': plan['id'], 'hash': plan['hash']}
        first = self.dispatcher.dispatch(command)
        self.assertTrue(first['claimed'])
        self.assertEqual(first['ownerTarget'], 'owner')
        self.assertEqual(first['approveCode'], plan['approvalCodes']['approve'])
        self.assertEqual(first['rejectCode'], plan['approvalCodes']['reject'])
        self.assertNotEqual(first['approveCode'],first['rejectCode'])
        self.assertRegex(first['noticeId'],'^[0-9a-f]{32}$')
        self.assertEqual(first['noticeVersion'],2)
        self.assertEqual(self.dispatcher.queue.inspect(plan['id'],self.dispatcher._context('owner'))['approvalNotice']['noticeId'],first['noticeId'])
        for text in ['a-specific-user', 'hello applicant', 'specific remark', 'tag', '24小时',
                     '同意：只回复 ' + plan['approvalCodes']['approve'], '拒绝：只回复 ' + plan['approvalCodes']['reject']]:
            self.assertIn(text, first['text'])
        repeated = SocialDispatcher(self.config).dispatch(command)
        self.assertFalse(repeated['claimed']); self.assertNotIn('text', repeated)
        self.assertEqual(repeated['state'], 'already-claimed')
        self.assertEqual(self.dispatcher.queue.inspect(plan['id'], self.dispatcher._context('owner'))['status'], 'pending')
        with self.assertRaisesRegex(RuntimeError, 'social-command-invalid'):
            self.dispatcher.dispatch({**command, 'text': 'forged notice'})
        with self.assertRaisesRegex(RuntimeError, 'social-context-denied'):
            self.dispatcher.dispatch({**command, 'target': 'group-a'})

    def test_notice_and_codes_reject_expiry_ambiguity_and_terminal_plans(self):
        plan = self.draft()
        queue = self.dispatcher.queue
        with queue._locked() as state:
            state['plans'][plan['id']]['expiresAt'] = 0
            queue._save(state)
        self.assertIsNone(self.dispatcher.observe_incoming(self.event(plan, text=plan['approvalCodes']['approve'])))
        notice = self.dispatcher.dispatch({'op': 'social-approval-notice', 'target': 'owner', 'planId': plan['id'], 'hash': plan['hash']})
        self.assertEqual(notice['state'], 'not-pending')
        one, two = self.draft(), self.draft()
        with queue._locked() as state:
            state['plans'][two['id']]['approvalCode'] = one['approvalCodes']['approve']; queue._save(state)
        self.assertIsNone(self.dispatcher.observe_incoming(self.event(one, text=one['approvalCodes']['approve'])))

    def test_notice_requires_approval_action(self):
        plan = self.draft('publish_moment', {'text': 'hello'})
        with self.assertRaisesRegex(RuntimeError, 'not-approval-action'):
            self.dispatcher.dispatch({'op': 'social-approval-notice', 'target': 'owner', 'planId': plan['id'], 'hash': plan['hash']})

    def test_short_code_collision_is_regenerated(self):
        from unittest.mock import patch
        with patch('social_actions.secrets.token_hex', side_effect=['1234abcd','deadbeef','1234abcd','deadbeef','abcd1234','feedbabe']):
            one, two = self.draft(), self.draft()
        self.assertEqual(one['approvalCodes'], {'approve':'1234abcd','reject':'deadbeef'})
        self.assertEqual(two['approvalCodes'], {'approve':'abcd1234','reject':'feedbabe'})


    def test_legacy_pending_notice_assigns_durable_unique_short_code(self):
        from unittest.mock import patch
        one, legacy = self.draft(), self.draft()
        queue = self.dispatcher.queue
        with queue._locked() as state:
            del state['plans'][legacy['id']]['approvalCodes']; state['plans'][legacy['id']]['approvalCode']='aabbccdd'; queue._save(state)
        with patch('social_actions.secrets.token_hex', side_effect=[one['approvalCodes']['approve'], 'fe12dc34','12fe34dc']):
            result = self.dispatcher.dispatch({'op': 'social-approval-notice', 'target': 'owner',
                'planId': legacy['id'], 'hash': legacy['hash']})
        self.assertTrue(result['claimed'])
        self.assertEqual(result['code'], 'fe12dc34')
        restarted = SocialDispatcher(self.config)
        stored = restarted.queue.inspect(legacy['id'], restarted._context('owner'))
        self.assertEqual(stored['approvalCodes']['approve'], result['code'])
        self.assertEqual(stored['approvalCode'],'aabbccdd')
        self.assertIsNone(restarted.observe_incoming(self.event(legacy,text='aabbccdd')))
        self.assertEqual(restarted.observe_incoming(self.event(legacy,text='拒绝操作 aabbccdd'))['operation'],'social-reject')
        resolved = restarted.observe_incoming(self.event(legacy, text='批准操作 ' + result['code']))
        self.assertEqual(resolved['planId'], legacy['id'])
        self.assertEqual(resolved['hash'], legacy['hash'])
        again = restarted.dispatch({'op': 'social-approval-notice', 'target': 'owner',
            'planId': legacy['id'], 'hash': legacy['hash']})
        self.assertFalse(again['claimed']); self.assertNotIn('text', again)

    def test_intent_bound_tokens_reject_wrong_verb_and_bare_code_is_exact(self):
        plan=self.draft();approve=plan['approvalCodes']['approve'];reject=plan['approvalCodes']['reject']
        for text in ('拒绝操作 '+approve,'批准操作 '+reject,'他说'+approve,approve+' 可以',reject+'\n批准'):
            self.assertIsNone(self.dispatcher.observe_incoming(self.event(plan,text=text)))
        for token,operation in ((approve,'social-approve'),(reject,'social-reject')):
            value=self.dispatcher.observe_incoming(self.event(plan,text=token,id='fixture-'+operation))
            self.assertEqual(value,{'operation':operation,'planId':plan['id'],'hash':plan['hash']})
        self.assertNotEqual(approve,reject)

    def test_legacy_pending_refresh_revokes_old_token_and_grant_and_resets_notice_once(self):
        import hashlib
        from unittest.mock import patch
        plan=self.draft();queue=self.dispatcher.queue;old='00aabbcc'
        with queue._locked() as state:
            p=state['plans'][plan['id']];p.pop('approvalCodes');p['approvalCode']=old
            p['approvalNotice']={'state':'claimed','claimedAt':1,'originTarget':'owner','text':'must-not-copy-token-'+old,'noticeId':'a'*32}
            queue._save(state)
        old_event=self.event(plan,text='批准操作 '+old)
        self.assertIsNotNone(self.dispatcher.observe_incoming(old_event))
        before=queue.file.read_bytes()
        with self.assertRaisesRegex(RuntimeError,'hash-mismatch'):
            queue.refresh_pending_approval_codes(plan['id'],'0'*64)
        self.assertEqual(queue.file.read_bytes(),before)
        with patch('social_actions.secrets.token_hex',side_effect=[old,'1111aaaa','1111aaaa','2222bbbb']):
            refreshed=queue.refresh_pending_approval_codes(plan['id'],plan['hash'])
        self.assertEqual(refreshed['status'],'pending')
        self.assertEqual(refreshed['approvalCodes'],{'approve':'1111aaaa','reject':'2222bbbb'})
        self.assertIsNone(queue.resolve_approval_code(old,operation='social-approve'))
        with self.assertRaisesRegex(RuntimeError,'grant-missing'):
            self.approve(plan)
        stored=queue.inspect(plan['id'],self.dispatcher._context('owner'))
        self.assertEqual(stored['hash'],plan['hash']);self.assertEqual(stored['parameters'],plan['parameters'])
        self.assertNotIn('approvalCode',stored);self.assertNotIn('approvalNotice',stored)
        self.assertEqual(stored['approvalCodeRotation']['retiredLegacyCodeSha256'],hashlib.sha256(old.encode()).hexdigest())
        self.assertNotIn(old,queue.file.read_text('utf8'));self.assertNotIn('must-not-copy',queue.file.read_text('utf8'))
        with self.assertRaisesRegex(RuntimeError,'not-eligible'):
            queue.refresh_pending_approval_codes(plan['id'],plan['hash'])
        notice=self.dispatcher.dispatch({'op':'social-approval-notice','target':'owner','planId':plan['id'],'hash':plan['hash']})
        self.assertTrue(notice['claimed']);self.assertEqual(notice['approveCode'],'1111aaaa')
        self.assertTrue(notice['text'].startswith('旧确认编号已作废，请以本条为准。'))
        self.assertNotEqual(notice['noticeId'],'a'*32)
        self.assertEqual(stored['approvalCodeRotation']['previousNotice']['noticeId'],'a'*32)
        self.assertFalse(self.dispatcher.dispatch({'op':'social-approval-notice','target':'owner','planId':plan['id'],'hash':plan['hash']})['claimed'])
        # Retired values are not eligible for reuse by any future plan.
        with patch('social_actions.secrets.token_hex',side_effect=[old,'3333cccc','4444dddd']):
            another=self.draft()
        self.assertEqual(another['approvalCodes']['approve'],'3333cccc')

    def test_maintenance_is_not_ipc_and_never_resets_approved_or_terminal(self):
        for status in ('approved','claimed','unknown','failed','rejected'):
            plan=self.draft();queue=self.dispatcher.queue
            with queue._locked() as state:
                p=state['plans'][plan['id']];p['status']=status;p.pop('approvalCodes');p['approvalCode']='fff0abcd'
                p['approvalNotice']={'state':'claimed','claimedAt':1,'originTarget':'owner'};queue._save(state)
            with self.assertRaisesRegex(RuntimeError,'not-eligible'):
                queue.refresh_pending_approval_codes(plan['id'],plan['hash'])
        with self.assertRaisesRegex(RuntimeError,'operation-denied'):
            self.dispatcher.dispatch({'op':'social-refresh-codes','target':'owner'})

    def test_bare_intent_tokens_do_not_resolve_claimed_unknown(self):
        plan=self.draft();queue=self.dispatcher.queue
        for origin,status in (({'kind':'friend','target':'owner'},'claimed'),
                              ({'kind':'friend','target':'owner'},'unknown')):
            with queue._locked() as state:
                state['plans'][plan['id']].update(origin=origin,status=status);queue._save(state)
            for code in plan['approvalCodes'].values():
                self.assertIsNone(self.dispatcher.observe_incoming(self.event(plan,text=code)))

    def test_group_proposal_bare_tokens_grant_only_fresh_owner_private_and_preserve_intent(self):
        for action,parameters in (('add_friend',{'keywords':'fixture-new-user'}),
                                  ('create_group',{'contacts':['fixture-one','fixture-two']})):
            for intent,operation,expected in (('approve','social-approve','approved'),('reject','social-reject','rejected')):
                with self.subTest(action=action,intent=intent):
                    plan=self.dispatcher.dispatch({'op':'social-draft','target':'group-a',
                        'action':action,'parameters':parameters})
                    code=plan['approvalCodes'][intent]
                    group_event=self.event(plan,id='group-event-'+plan['id'],text=code,kind='group',target='group-a')
                    self.assertIsNone(self.dispatcher.observe_incoming(group_event))
                    event=self.event(plan,id='owner-event-'+plan['id'],text=code)
                    trusted=self.dispatcher.observe_incoming(event)
                    self.assertEqual(trusted,{'operation':operation,'planId':plan['id'],'hash':plan['hash']})
                    result=self.dispatcher.dispatch({'op':operation,'target':'owner','planId':plan['id'],
                        'hash':plan['hash'],'approvalMessageId':event['id']})
                    self.assertEqual(result['status'],expected)


if __name__ == '__main__':
    unittest.main()
