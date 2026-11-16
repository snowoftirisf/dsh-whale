"""Isolated generic backend/factory lifecycle tests, no live social actions."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from social_actions import ActionContext,_digest
from social_wxauto_backend import WxautoSocialBackend,ActionBinding
from social_friend_request_binding import create_friend_request_binding,require_full_plan


class FakeFullCandidate:
    instances=[]
    def __init__(self,wx,config,**options):
        self.wx,self.config,self.options=wx,config,options;self.closed=False;self.restorationUnavailable=False
        self.events=config['fixtureEvents'];self.events.append('construct');self.__class__.instances.append(self)
    def evidence(self,phase,parameters):
        value={'source':'wxauto-uia','action':'add_friend','phase':phase,'clientVersion':'4.2.0.12',
            'parametersHash':_digest('add_friend',parameters),'observedAt':100,'controls':[{'fixture':True}],
            'confirmed':phase=='after','confirmationKind':'friend-request-submission',
            'applicationSubmittedVerified':True,'friendshipConfirmed':False}
        if phase=='before' and self.config.get('bad_before'):value['parametersHash']='0'*64
        if phase=='after' and self.config.get('bad_semantics'):value['friendshipConfirmed']=True
        return value
    def observe_before(self,wx,parameters):
        self.events.append('before');self.options['plan_provider']()
        if self.config.get('raise_before'):raise RuntimeError('fixture-private-preflight')
        if self.config.get('change_client'):self.config['fixtureClient']['clientSha256']='b'*64
        return self.evidence('before',parameters)
    def perform(self,wx,parameters):
        self.events.append('perform');self.options['plan_provider']()
        if self.config.get('raise_perform'):raise RuntimeError('fixture-private-click')
        return {'fixture':True}
    def observe_after(self,wx,parameters,result,before):
        self.events.append('after')
        if self.config.get('raise_after'):raise RuntimeError('fixture-private-after')
        result=self.evidence('after',parameters)
        if self.config.get('swallowed_cleanup'):
            self.closed=True;self.restorationUnavailable=True;result['restorationUnavailable']=True;self.events.append('swallowed-close')
        return result
    def close(self):
        if self.closed:return
        self.closed=True;self.events.append('cleanup')
        if self.config.get('raise_cleanup'):raise RuntimeError('fixture-private-restore')


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temp.cleanup)
        self.events=[];self.client={'executableName':'Weixin.exe','clientVersion':'4.2.0.12','clientSha256':'a'*64}
        self.config={'dataRoot':self.temp.name,'target':'owner','groups':[],'fixtureEvents':self.events,'fixtureClient':self.client}
        self.wx=SimpleNamespace(ChatInfo=lambda:{'chat_name':'owner','chat_type':'friend'})
        self.backend=WxautoSocialBackend(self.wx,self.config,client_probe=lambda _:copy.deepcopy(self.client),clock=lambda:100,
            refresh_chat=lambda *a:self.events.append('refresh'))
        self.template=create_friend_request_binding(self.backend,candidate_factory=FakeFullCandidate)
        self.backend.bindings['add_friend']=self.template
        self.params={'keywords':'fixture_id','expectedProfileName':'fixture-person','addmsg':'approved greeting'}
        self.owner=ActionContext('friend','owner',True)
        self.plan=self.backend.plans.draft('add_friend',self.params,self.owner)
        self.update(status='claimed',approvedBy='owner',approvalMessageId='fixture-owner-message',approvedAt=90,
            approvalNotice={'purpose':'add-friend-request','version':2,'state':'claimed','noticeId':'c'*32,'originTarget':'owner','claimedAt':80})
        FakeFullCandidate.instances=[]
    def update(self,**changes):
        with self.backend.plans._locked() as state:
            state['plans'][self.plan['id']].update(changes);self.backend.plans._save(state)
        self.plan=self.backend.plans.inspect(self.plan['id'],self.owner)
    def run_action(self):
        return self.backend._execute('add_friend',self.params,idempotency_key=self.plan['id'],
            acceptance=('add_friend',self.template.fingerprint(),self.client['clientSha256']))

    def test_template_inert_bound_fingerprint_stable_and_exact_plan_provider(self):
        self.assertEqual(self.events,[])
        bound=self.template.bind_plan(self.plan)
        self.assertEqual(bound.fingerprint(),self.template.fingerprint())
        candidate=FakeFullCandidate.instances[-1]
        self.assertEqual(candidate.options['plan_id'],self.plan['id']);self.assertEqual(candidate.options['plan_hash'],self.plan['hash'])
        self.assertEqual(candidate.options['plan_provider']()['id'],self.plan['id'])
        self.update(status='unknown')
        with self.assertRaisesRegex(RuntimeError,'scope-required'):candidate.options['plan_provider']()
        bound.cleanup();bound.cleanup();self.assertEqual(self.events.count('cleanup'),1)
        self.assertFalse(self.backend.receipt_file.exists())

    def test_valid_lifecycle_cleanup_precedes_restore_without_capability_promotion(self):
        result=self.run_action();self.assertEqual(result['status'],'confirmed')
        self.assertEqual(self.events,['construct','before','perform','after','cleanup','refresh'])
        self.assertFalse(self.backend.receipt_file.exists())
        self.assertFalse(self.backend.capabilities()['actions']['add_friend']['liveVerified'])

    def test_invalid_plan_hash_owner_or_expiry_cannot_construct_factory(self):
        for change in [{'status':'approved'},{'approvedBy':'other'},{'approvalMessageId':''},{'expiresAt':99},
            {'origin':{'kind':'group','target':'owner'}}]:
            self.setUp();self.update(**change);result=self.run_action()
            self.assertEqual(result['status'],'failed');self.assertNotIn('construct',self.events)

    def test_old_initial_notice_and_invalid_full_notice_never_construct(self):
        for change in [{'purpose':'add-friend-initial-contact'},{'version':1},{'state':'sent'},{'noticeId':'invalid'},
            {'originTarget':'other'},{'claimedAt':91},{'claimedAt':float('nan')}]:
            self.setUp();notice={**self.plan['approvalNotice'],**change}
            # Direct helper validation also rejects non-finite values without
            # asking the plan store to persist invalid JSON.
            if change.get('claimedAt')!=change.get('claimedAt'):
                with self.assertRaises(RuntimeError):require_full_plan(self.backend,{**self.plan,'approvalNotice':notice})
            else:
                self.update(approvalNotice=notice);result=self.run_action();self.assertEqual(result['status'],'failed')
            self.assertNotIn('construct',self.events)

    def test_all_preflight_gate_perform_after_failures_cleanup_once(self):
        for flag,status in [('raise_before','failed'),('bad_before','failed'),('change_client','failed'),
                            ('raise_perform','unknown'),('raise_after','unknown')]:
            self.setUp();self.config[flag]=True;result=self.run_action()
            self.assertEqual(result['status'],status);self.assertEqual(self.events.count('cleanup'),1)
            self.assertLess(self.events.index('cleanup'),self.events.index('refresh'))

    def test_cleanup_failure_preserves_primary_outcome_and_skips_unsafe_restore(self):
        for flag,status in [('bad_before','failed'),('raise_perform','unknown'),(None,'confirmed')]:
            self.setUp();self.config['raise_cleanup']=True
            if flag:self.config[flag]=True
            result=self.run_action();self.assertEqual(result['status'],status)
            self.assertEqual(result['cleanupFailureCode'],'social-chat-restore-failed')
            self.assertTrue(self.backend.chat_binding_failed);self.assertNotIn('refresh',self.events)
            if flag=='bad_before':self.assertEqual(result['code'],'social-target-or-parameters-unverified')

    def test_swallowed_candidate_cleanup_failure_is_sticky_not_second_cleanup_success(self):
        self.config['swallowed_cleanup']=True;result=self.run_action()
        self.assertEqual(result['status'],'confirmed');self.assertTrue(self.backend.chat_binding_failed)
        self.assertEqual(result['cleanupFailureCode'],'social-chat-restore-failed')
        self.assertNotIn('cleanup',self.events);self.assertNotIn('refresh',self.events)

    def test_full_after_semantics_gate_demotes_inconsistent_confirmation(self):
        self.config['bad_semantics']=True;result=self.run_action()
        self.assertEqual(result['status'],'unknown');self.assertFalse(self.backend.receipt_file.exists())

    def test_source_dependency_bytecode_changes_fingerprint(self):
        from types import FunctionType
        dep=self.template.source_dependencies[0]
        changed=FunctionType(dep.__code__.replace(co_consts=(*dep.__code__.co_consts,'fixture-change')),dep.__globals__,dep.__name__,dep.__defaults__,dep.__closure__)
        altered=ActionBinding(self.template.implementation_id,self.template.perform,self.template.observe_before,self.template.observe_after,
            bind_plan=self.template.bind_plan,cleanup=self.template.cleanup,source_dependencies=(changed,*self.template.source_dependencies[1:]))
        self.assertNotEqual(altered.fingerprint(),self.template.fingerprint())

    def test_submitted_but_unrestored_acceptance_is_terminal_without_promoting_receipt(self):
        from social_wxauto_backend import run_local_acceptance
        self.update(status='approved');self.config['raise_cleanup']=True
        result=run_local_acceptance(self.backend,plan_id=self.plan['id'],expected_hash=self.plan['hash'],
            action='add_friend',fingerprint=self.template.fingerprint())
        self.assertEqual(result['status'],'confirmed');self.assertFalse(result['acceptancePromoted'])
        self.assertTrue(result['receipt']['observation']['applicationSubmittedVerified'])
        self.assertFalse(self.backend.receipt_file.exists());self.assertNotIn('refresh',self.events)
        self.assertTrue(self.backend.chat_binding_failed)

if __name__=='__main__':unittest.main()
