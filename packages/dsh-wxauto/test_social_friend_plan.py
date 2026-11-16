"""Plan-bound inert lookup tests; no live wxauto or final friend action."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from social_actions import ActionContext
from social_ipc import SocialDispatcher
from social_wxauto_backend import WxautoSocialBackend
from social_friend_search import FriendSearch,valid_search_query
from social_contact_observation import read_contact_profile,read_candidate_profile,read_friend_search_result
from social_friend_observe import profile_button_metadata,OBSERVED_VERSION,OBSERVED_CLIENT_SHA
from test_social_friend_search import fixture
from test_social_contact_observation import profile,search_window


class FriendPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.now=100
        self.owner=ActionContext('friend','owner',True)
        self.wx=NS()
        self.backend=WxautoSocialBackend(self.wx,{'dataRoot':self.temp.name,'target':'owner','groups':['group']},
            client_probe=lambda wx:{'executableName':'Weixin.exe','clientVersion':OBSERVED_VERSION,
                'clientSha256':OBSERVED_CLIENT_SHA},clock=lambda:self.now,refresh_chat=lambda *args:None)

    def plan(self,action='add_friend',context=None):
        return self.backend.plans.draft(action,{'keywords':'_Test50K'} if action=='add_friend' else {'text':'fixture'},context or self.owner)

    def test_literal_query_leading_underscore_survives_search_and_result_reader(self):
        window,edit,button=fixture()
        self.assertTrue(valid_search_query('_Test50K'))
        FriendSearch(window,client_pid=7,foreground=lambda:123).dispatch('_Test50K')
        self.assertEqual(edit.Value,'_Test50K')
        self.assertEqual(button.clicks,1)
        root=search_window();root.children[0].value='_Test50K'
        result=read_friend_search_result(root,'_Test50K',client_pid=4242,window_handle=9001,foreground=lambda:9001)
        self.assertTrue(result['queryValueMatched'])
        self.assertFalse(result['accountMatched'])
        for bad in (' _Test50K','_Test50K\n','../file','<script>','a b'):
            self.assertFalse(valid_search_query(bad))

    def test_candidate_display_comes_from_actual_header_and_exact_id_not_owner_rules(self):
        root=profile();root.children[-1].children[-1].Name='_Test50K'
        self.assertTrue(read_candidate_profile(root,'_Test50K')['identityMatched'])
        self.assertEqual(read_candidate_profile(root,'_different')['identityEvidence'],'id-mismatch')
        with self.assertRaisesRegex(RuntimeError,'identity-value-invalid'):
            read_contact_profile(root,'Fixture Friend',expected_wechat_id='_Test50K')
        root.children[1].Name='different avatar'
        self.assertEqual(read_candidate_profile(root,'_Test50K')['identityEvidence'],'unsupported-profile')

    def test_candidate_stable_missing_id_never_matches_but_remains_readable(self):
        root=profile();root.children.pop()
        result=read_candidate_profile(root,'_Test50K')
        self.assertEqual(result['identityEvidence'],'id-unavailable')
        self.assertFalse(result['identityMatched'])

    def test_backend_derives_exact_query_from_pending_plan_and_never_executes(self):
        plan=self.plan()
        with patch('social_friend_observe.observe_plan',return_value={'chatRestored':True}) as observe:
            self.backend.observe('friend-search-plan',plan_id=plan['id'],plan_hash=plan['hash'])
            args=observe.call_args
            self.assertIs(args.args[0],self.wx)
            self.assertEqual(args.kwargs['plan']['parameters']['keywords'],'_Test50K')
            self.assertEqual(args.kwargs['plan_check']()['id'],plan['id'])
        self.assertEqual(self.backend.plans.inspect(plan['id'],self.owner)['status'],'pending')

    def test_approved_terminal_lookup_is_read_only_and_does_not_reopen_execution(self):
        from social_friend_acceptance import accept_friend_initial
        for status in ('failed','unknown','confirmed'):
            plan=self.plan()
            context=ActionContext('friend','owner',True,approval_message_id='fixture-owner-input',
                approval_plan_id=plan['id'],approval_plan_hash=plan['hash'])
            self.backend.plans.approve(plan['id'],plan['hash'],context)
            fixture_backend=NS(capabilities=lambda:{'clientVersion':'fixture','actions':{
                'add_friend':{'liveVerified':True,'clientVersion':'fixture'}}},
                execute=lambda *args,**kwargs:{'status':status,'receipt':{'synthetic':True},'code':'fixture-terminal'})
            terminal=self.backend.plans.execute(plan['id'],plan['hash'],context,fixture_backend)
            self.assertEqual(terminal['status'],status)
            with patch('social_friend_observe.observe_plan',return_value={'chatRestored':True}) as observe:
                self.backend.observe('friend-search-plan',plan_id=plan['id'],plan_hash=plan['hash'])
                self.assertEqual(observe.call_args.kwargs['plan_check']()['status'],status)
            with patch('social_add_friend_candidate.AddFriendCandidate') as candidate:
                result=accept_friend_initial(self.backend,plan_id=plan['id'],plan_hash=plan['hash'])
                self.assertEqual(result['executionStatus'],'not-dispatched')
                candidate.assert_not_called()
            self.assertEqual(self.backend.plans.inspect(plan['id'],self.owner)['status'],status)

    def test_wrong_hash_expired_rejected_wrong_action_or_group_origin_never_navigates(self):
        first=self.plan();wrong=self.plan('publish_moment')
        group=self.plan(context=ActionContext('group','group'))
        rejected=self.plan();self.backend.plans.reject(rejected['id'],rejected['hash'],self.owner)
        cases=[(first['id'],'0'*64),(wrong['id'],wrong['hash']),(group['id'],group['hash']),
               (rejected['id'],rejected['hash'])]
        for pid,digest in cases:
            with self.subTest(plan=pid),patch('social_friend_observe.observe_plan') as observe:
                with self.assertRaises(RuntimeError):self.backend.observe('friend-search-plan',plan_id=pid,plan_hash=digest)
                observe.assert_not_called()
        self.now=first['expiresAt']
        with patch('social_friend_observe.observe_plan') as observe:
            with self.assertRaises(RuntimeError):self.backend.observe('friend-search-plan',plan_id=first['id'],plan_hash=first['hash'])
            observe.assert_not_called()

    def test_dispatcher_requires_plan_hash_and_owner_and_never_accepts_query_arguments(self):
        dispatcher=SocialDispatcher({'dataRoot':self.temp.name,'target':'owner','groups':['group']},self.backend)
        plan=self.plan();command={'op':'social-observe','target':'owner','purpose':'friend-search-plan',
            'planId':plan['id'],'hash':plan['hash']}
        for invalid in ({**command,'account':'_Test50K'},{**command,'target':'group'},
                        {k:v for k,v in command.items() if k!='hash'}):
            with patch('social_friend_observe.observe_plan') as observe:
                with self.assertRaises(RuntimeError):dispatcher.dispatch(invalid)
                observe.assert_not_called()

    def test_button_metadata_is_read_only_hashed_and_bounded(self):
        buttons=[NS(ControlTypeName='ButtonControl',ProcessId=7,IsOffscreen=0,ClassName='mmui::XButton',Name=n)
                 for n in ('添加到通讯录','发消息','private candidate avatar')]
        result=profile_button_metadata(NS(),7,lambda root:buttons)
        self.assertEqual([v['label'] for v in result],['add-contact','send-message','unknown'])
        self.assertNotIn('private candidate avatar',str(result))
        self.assertTrue(all(set(v)=={'class','type','nameSha256','label'} for v in result))


if __name__=='__main__':unittest.main()
