"""Full acceptance adapter over real queue/core/factory, fake UI candidate only."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from social_actions import ActionContext
from social_ipc import SocialDispatcher
from social_friend_request_acceptance import _notice_valid
from social_friend_request_binding import create_friend_request_binding
from social_wxauto_backend import WxautoSocialBackend
from test_social_friend_request_binding import FakeFullCandidate


class FullAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temp.cleanup)
        self.events=[];self.now=100
        self.client={'executableName':'Weixin.exe','clientVersion':'4.2.0.12','clientSha256':'a'*64}
        self.config={'dataRoot':self.temp.name,'target':'owner','groups':['group-a'],
            'fixtureEvents':self.events,'fixtureClient':self.client}
        self.wx=SimpleNamespace(ChatInfo=lambda:{'chat_name':'group-a','chat_type':'group'})
        self.backend=WxautoSocialBackend(self.wx,self.config,clock=lambda:self.now,
            client_probe=lambda _:copy.deepcopy(self.client),refresh_chat=lambda *args:self.events.append('refresh'))
        self.social=SocialDispatcher(self.config,self.backend,clock=lambda:self.now)
        self.owner=ActionContext('friend','owner',True);self.factories=0
        def factory(backend,**options):
            self.factories+=1
            self.assertIs(options['lookup_refresh'],self.backend.refresh_chat)
            return create_friend_request_binding(backend,candidate_factory=FakeFullCandidate,**options)
        self.mock=patch('social_friend_request_binding.create_friend_request_binding',side_effect=factory)
        self.mock.start();self.addCleanup(self.mock.stop);FakeFullCandidate.instances=[]

    def plan(self,full=True):
        p=self.social.dispatch({'op':'social-draft','target':'owner','action':'add_friend',
            'parameters':{'keywords':'synthetic-account','expectedProfileName':'synthetic-name','addmsg':'synthetic-only'}})
        notice=self.social.dispatch({'op':'social-approval-notice','target':'owner','planId':p['id'],'hash':p['hash'],
            **({'purpose':'add-friend-request'} if full else {})})
        return p,notice

    def approve(self,plan,notice):
        event={'event':'message','id':'synthetic-owner-approval-'+plan['id'],'target':'owner','kind':'friend',
            'mediaType':'text','text':notice['approveCode']}
        self.assertIsNotNone(self.social.observe_incoming(event))
        return self.social.dispatch({'op':'social-approve','target':'owner','planId':plan['id'],'hash':plan['hash'],
            'approvalMessageId':event['id']})

    def command(self,p,**changes):
        return {'op':'social-acceptance','id':'synthetic-full','target':'owner','purpose':'add-friend-request',
            'planId':p['id'],'hash':p['hash'],**changes}

    def test_full_notice_scope_requires_fresh_owner_approval_and_only_then_binds(self):
        plan,notice=self.plan();self.assertEqual(notice['purpose'],'add-friend-request')
        pending=self.social.dispatch(self.command(plan));self.assertEqual(pending['executionStatus'],'not-dispatched')
        self.assertEqual(self.factories,0)
        self.approve(plan,notice)
        value=self.social.dispatch(self.command(plan))
        self.assertEqual(value['status'],'confirmed');self.assertEqual(value['executionStatus'],'dispatched')
        self.assertEqual(value['confirmationKind'],'friend-request-submission')
        self.assertTrue(value['applicationSubmittedVerified']);self.assertFalse(value['friendshipConfirmed'])
        self.assertTrue(value['chatRestored']);self.assertEqual(self.backend.bindings,{})
        self.assertTrue(value['acceptancePromoted'])
        self.assertLess(self.events.index('cleanup'),self.events.index('refresh'))
        self.assertTrue(self.backend.receipt_file.exists())
        self.assertNotIn('synthetic-account',json.dumps(value));self.assertNotIn('parameters',value)
        previous=list(self.events);again=self.social.dispatch(self.command(plan))
        self.assertEqual(again['executionStatus'],'not-dispatched');self.assertEqual(previous,self.events)

    def test_old_initial_approval_cannot_upgrade_to_full_or_reissue_notice(self):
        plan,notice=self.plan(full=False)
        again=self.social.dispatch({'op':'social-approval-notice','target':'owner','planId':plan['id'],'hash':plan['hash'],
            'purpose':'add-friend-request'})
        self.assertFalse(again['claimed'])
        self.approve(plan,notice)
        with self.assertRaisesRegex(RuntimeError,'purpose-approval-required'):self.social.dispatch(self.command(plan))
        self.assertEqual(self.factories,0);self.assertEqual(self.events,[])

    def test_wrong_hash_group_context_expiry_and_invalid_notice_timeline_do_not_construct(self):
        plan,notice=self.plan();self.approve(plan,notice)
        for changes in ({'target':'group-a'},{'hash':'b'*64},{'fingerprint':'c'*64},{'purpose':'not-a-purpose'}):
            with self.subTest(changes=changes),self.assertRaises(RuntimeError):self.social.dispatch(self.command(plan,**changes))
        self.now=plan['expiresAt'];self.assertEqual(self.social.dispatch(self.command(plan))['executionStatus'],'not-dispatched')
        self.assertEqual(self.factories,0)
        for notice_changes,approved in (({'claimedAt':101},100),({'claimedAt':100},101),({'version':1},100),
                                       ({'noticeId':'bad'},100),({'originTarget':'other'},100),({'claimedAt':float('nan')},100)):
            candidate={'approvalNotice':{**notice,'version':2,'state':'claimed','originTarget':'owner','claimedAt':100,**notice_changes},'approvedAt':approved}
            self.assertFalse(_notice_valid(candidate,'owner',100))

    def test_unknown_or_invalid_success_semantics_never_certify_and_never_retry(self):
        for option in ('raise_after','bad_semantics'):
            plan,notice=self.plan();self.approve(plan,notice);self.config[option]=True
            value=self.social.dispatch(self.command(plan));self.assertEqual(value['status'],'unknown')
            self.assertNotIn('applicationSubmittedVerified',value);self.assertFalse(self.backend.receipt_file.exists())
            before=list(self.events);self.assertEqual(self.social.dispatch(self.command(plan))['executionStatus'],'not-dispatched')
            self.assertEqual(self.events,before);self.config.pop(option)

    def test_confirmed_submission_survives_cleanup_failure_but_stops_chat_binding(self):
        plan,notice=self.plan();self.approve(plan,notice);self.config['raise_cleanup']=True
        value=self.social.dispatch(self.command(plan))
        self.assertEqual(value['status'],'confirmed');self.assertTrue(value['applicationSubmittedVerified'])
        self.assertFalse(value['friendshipConfirmed']);self.assertFalse(value['chatRestored'])
        self.assertFalse(value['acceptancePromoted'])
        self.assertEqual(value['acceptanceCode'],'social-acceptance-chat-recovery-unverified')
        self.assertFalse(self.backend.receipt_file.exists())
        self.assertTrue(self.backend.chat_binding_failed);self.assertNotIn('refresh',self.events)
        self.assertEqual(self.events.count('cleanup'),1)

    def test_optional_notice_purpose_keeps_legacy_api_and_initial_dispatch_separate(self):
        methods=[]
        self.social.backend=SimpleNamespace(accept_friend_initial=lambda **kw:methods.append('initial') or {'status':'fixture'},
            accept_friend_request=lambda **kw:methods.append('full') or {'status':'fixture'})
        fake={'id':'a'*32,'hash':'b'*64}
        self.social.dispatch(self.command(fake,purpose='add-friend-initial-contact'))
        self.social.dispatch(self.command(fake));self.assertEqual(methods,['initial','full'])
        plan,notice=self.plan(full=False);self.assertNotIn('purpose',notice)
        with self.assertRaisesRegex(RuntimeError,'purpose-denied'):
            other=self.social.dispatch({'op':'social-draft','target':'owner','action':'add_friend','parameters':{'keywords':'different'}})
            self.social.dispatch({'op':'social-approval-notice','target':'owner','planId':other['id'],'hash':other['hash'],'purpose':'invalid'})


if __name__=='__main__':unittest.main()
