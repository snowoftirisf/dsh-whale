"""Synthetic staged UI and journal contracts, never actual wxauto or fsync."""
import copy
from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from social_actions import _digest
from social_friend_request_candidate import FriendRequestCandidate
from social_friend_verification import _nodes
from social_contact_observation import DISPLAY_ID
from test_social_friend_verification import Control


class Journal:
    """Memory fake, explicitly NOT production durability acceptance."""
    def __init__(self,events):self.events=events;self.records={};self.bad_claim=False;self.bad_mark=False;self.bad_read=False
    def _value(self,plan,hash_,snap,stage):
        r={'kind':'friend-request-verification-journal','planId':plan,'parametersHash':hash_,
            'claimId':'c'*32,'snapshotSha256':snap,'stage':stage,'persisted':True}
        self.records[stage]=r;return copy.deepcopy(r)
    def claim(self,plan,hash_,snap):
        self.events.append('journal-claim')
        return True if self.bad_claim else self._value(plan,hash_,snap,'claimed')
    def mark(self,plan,hash_,claim,snap):
        self.events.append('journal-mark')
        if self.bad_mark:raise OSError('private-path')
        return self._value(plan,hash_,snap,'confirm-attempt')
    def readback(self,plan,hash_,claim,stage):
        self.events.append('journal-readback')
        r=copy.deepcopy(self.records.get(stage))
        if self.bad_read and r:r['planId']='d'*32
        return r


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.events=[];self.parameters={'keywords':'fixture_id','expectedProfileName':'fixture-person','addmsg':'approved greeting'}
        self.hash=_digest('add_friend',self.parameters)
        self.plan={'id':'a'*32,'action':'add_friend','hash':self.hash,'parameters':copy.deepcopy(self.parameters),
            'status':'claimed','origin':{'kind':'friend','target':'owner'},'approvedBy':'owner','approvalMessageId':'fixture-approval','expiresAt':1000}
        self.wx=object()
        def node(klass,name,kind,key,bounds=(0,0,500,500),handle=2):
            c=Control(klass,name,kind,(key,),bounds,self.events);c.NativeWindowHandle=handle;c.AutomationId='';return c
        self.main=node('mmui::MainWindow','','WindowControl',1,handle=1)
        self.profile=node('mmui::AddFriendWindow','添加朋友','WindowControl',2)
        self.query=node('mmui::XValidatorTextEdit','搜索','EditControl',3,(5,5,100,30));self.query.value=self.parameters['keywords']
        self.header=node('mmui::ProfileHeaderUi','','GroupControl',4)
        self.display=node('mmui::XTextView','fixture-person','TextControl',5);self.display.AutomationId=DISPLAY_ID
        self.avatar=node('mmui::ContactHeadView','fixture-person','ButtonControl',6)
        self.header.children=[self.display,self.avatar]
        self.add=node('mmui::XOutlineButton','添加到通讯录','ButtonControl',7,(10,350,150,400))
        self.profile.children=[self.query,self.header,self.add]
        self.verify=node('mmui::VerifyFriendWindow','','WindowControl',8,handle=3);self.verify.exists=False
        self.edit=node('mmui::XValidatorTextEdit','发送添加朋友申请','EditControl',9,(10,10,400,200),handle=3)
        self.confirm=node('mmui::XOutlineButton','确定','ButtonControl',10,(20,300,120,350),handle=3)
        self.verify.children=[self.edit,self.confirm]
        self.extra=[];self.foreground=self.profile
        def roots():return [c for c in [self.main,self.profile,self.verify,*self.extra] if c.exists]
        self.roots=roots
        def guard():
            if self.foreground is not self.profile:raise RuntimeError('fixture-not-profile-foreground')
        def unique(roots,klass,name):
            found=[n for root in roots for n in _nodes(root) if n.ClassName==klass and n.Name==name]
            if len(found)!=1:raise RuntimeError('fixture-not-unique')
            return found[0]
        self.uia=SimpleNamespace(GetForegroundControl=lambda:self.foreground,GetForegroundWindow=lambda:self.foreground.NativeWindowHandle)
        self.session={'wx':self.wx,'form':self.profile,'formGuard':guard,'ownedRoots':roots,'pid':123,
            'main':self.main,'uia':self.uia,'helpers':SimpleNamespace(nodes=_nodes,unique=unique),
            'client':{'clientVersion':'4.2.0.12','clientSha256':'b'*64}}
        @contextmanager
        def lookup(*a,**kw):
            self.events.append('open-owned-profile')
            try:yield self.session
            finally:
                self.events.append('close-owned-profile');self.profile.exists=False;self.foreground=self.main
                self.events.append('restore-chat')
        self.lookup=lookup
        def add():
            self.events.append('add-contact');self.verify.exists=True;self.foreground=self.verify
        def confirm():
            self.events.append('submit-request');self.verify.exists=False;self.foreground=self.profile;self.add.Name='等待验证'
        self.add.on_click=add;self.confirm.on_click=confirm
        self.journal=Journal(self.events)
        self.candidate=FriendRequestCandidate(self.wx,{'target':'owner'},plan_id=self.plan['id'],plan_hash=self.hash,
            plan_provider=lambda:copy.deepcopy(self.plan),clock=lambda:100,client_probe=lambda _:self.session['client'],
            open_lookup=self.lookup,journal=self.journal)

    def run_request(self):
        before=self.candidate.observe_before(self.wx,self.parameters)
        performed=self.candidate.perform(self.wx,self.parameters)
        return self.candidate.observe_after(self.wx,self.parameters,performed,before)

    def test_original_preflight_one_add_one_confirm_then_exact_pending_and_restore(self):
        result=self.run_request()
        self.assertTrue(result['requestPending']);self.assertTrue(result['applicationSubmittedVerified'])
        self.assertTrue(result['confirmed']);self.assertFalse(result['friendshipConfirmed'])
        self.assertEqual(result['confirmationKind'],'friend-request-submission');self.assertTrue(result['greetingReadbackVerified'])
        self.assertEqual(result['clientVersion'],'4.2.0.12');self.assertEqual(len(result['controls']),1)
        self.assertFalse(result['restorationUnavailable']);self.assertFalse(result['automaticRetry'])
        self.assertEqual(self.events.count('add-contact'),1);self.assertEqual(self.events.count('submit-request'),1)
        self.assertLess(self.events.index('journal-claim'),self.events.index('set'))
        self.assertLess(self.events.index('journal-mark'),self.events.index('submit-request'))
        self.assertEqual(self.edit.value,self.parameters['addmsg']);self.assertEqual(self.events[-1],'restore-chat')
        with self.assertRaises(RuntimeError):self.candidate.perform(self.wx,self.parameters)

    def test_preexisting_pending_same_button_runtime_never_applies_again(self):
        self.add.Name='等待验证'
        with self.assertRaisesRegex(RuntimeError,'already-pending'):self.candidate.observe_before(self.wx,self.parameters)
        self.assertNotIn('add-contact',self.events);self.assertNotIn('journal-claim',self.events)

    def test_bool_claim_or_tampered_readback_never_inputs_or_confirms(self):
        for attr in ('bad_claim','bad_read'):
            # Each unknown-modal context deliberately remains open. Its later
            # generator finalization must never mutate the next case's UI.
            case=type(self)(methodName='runTest');case.setUp()
            setattr(case.journal,attr,True)
            result=case.run_request()
            self.assertFalse(result['requestPending']);self.assertFalse(result['verification']['inputAttempted'])
            self.assertNotIn('set',case.events);self.assertNotIn('submit-request',case.events)
            self.assertTrue(result['restorationUnavailable']);self.assertTrue(case.verify.exists)

    def test_attempt_journal_failure_keeps_verify_form_and_never_confirms(self):
        self.journal.bad_mark=True;result=self.run_request()
        self.assertFalse(result['requestPending']);self.assertTrue(result['verification']['inputAttempted'])
        self.assertEqual(result['verification']['attemptMarkOutcome'],'unknown')
        self.assertNotIn('submit-request',self.events);self.assertTrue(result['restorationUnavailable'])
        self.assertNotIn('close-owned-profile',self.events)

    def test_unknown_modal_after_add_contact_is_never_cleared_or_submitted(self):
        normal=self.add.on_click
        def add():
            normal();extra=Control('mmui::UnknownDialog','','WindowControl',(66,),(0,0,100,100),self.events)
            extra.NativeWindowHandle=66;self.extra=[extra]
        self.add.on_click=add;result=self.run_request()
        self.assertFalse(result['requestPending']);self.assertTrue(result['restorationUnavailable'])
        self.assertIsNone(result['verification']);self.assertNotIn('set',self.events);self.assertNotIn('submit-request',self.events)

    def test_form_closed_without_pending_or_wrong_profile_is_not_confirmed(self):
        for change in ('pending','query','name'):
            self.setUp();normal=self.confirm.on_click
            def click():
                normal()
                if change=='pending':self.add.Name='添加到通讯录'
                elif change=='query':self.query.value='other_id'
                else:self.display.Name='other-person'
            self.confirm.on_click=click;result=self.run_request()
            self.assertFalse(result['requestPending']);self.assertFalse(result['applicationSubmittedVerified'])
            self.assertFalse(result['friendshipConfirmed'])

    def test_expired_or_nonclaimed_original_plan_prevents_navigation(self):
        for key,value in [('status','unknown'),('expiresAt',99),('approvedBy','someone-else')]:
            self.setUp();self.plan[key]=value
            with self.assertRaisesRegex(RuntimeError,'approved-claimed-plan-required'):self.candidate.observe_before(self.wx,self.parameters)
            self.assertNotIn('open-owned-profile',self.events)

    def test_same_plan_journal_receipt_scope_and_snapshot_must_match(self):
        self.journal.claim=lambda *a:{'kind':'friend-request-verification-journal','planId':'e'*32,
            'parametersHash':self.hash,'claimId':'c'*32,'snapshotSha256':a[-1],'stage':'claimed','persisted':True}
        result=self.run_request();self.assertFalse(result['verification']['inputAttempted']);self.assertNotIn('submit-request',self.events)

    def test_pending_label_and_forged_result_cannot_replace_this_execution_claim_and_mark(self):
        for corrupt in ('claim','mark','readback','greeting'):
            self.setUp();before=self.candidate.observe_before(self.wx,self.parameters)
            performed=self.candidate.perform(self.wx,self.parameters)
            self.assertEqual(self.add.Name,'等待验证')
            if corrupt=='claim':self.candidate._verification_claim=None
            elif corrupt=='mark':self.candidate._verification_mark=None
            elif corrupt=='readback':self.journal.bad_read=True
            else:self.candidate.verification_result['code']='social-verification-greeting-readback-mismatch'
            result=self.candidate.observe_after(self.wx,self.parameters,performed,before)
            self.assertFalse(result['confirmed']);self.assertFalse(result['applicationSubmittedVerified']);self.assertFalse(result['friendshipConfirmed'])

    def test_request_confirmation_does_not_register_or_promote_normal_capability(self):
        from social_wxauto_backend import load_observed_bindings
        result=self.run_request();self.assertTrue(result['confirmed'])
        # A fake test outcome must not install a binding or create a receipt.
        self.assertNotIn('add_friend',load_observed_bindings(self.wx,{}))

    def test_fresh_python_wrappers_with_same_native_identity_are_accepted(self):
        class Wrapper:
            def __init__(self,control):self.control=control
            def __getattr__(self,name):return getattr(self.control,name)
        self.session['ownedRoots']=lambda:[Wrapper(c) for c in self.roots()]
        result=self.run_request()
        self.assertTrue(result['confirmed']);self.assertFalse(result['friendshipConfirmed'])
        self.assertEqual(self.events.count('submit-request'),1)

    def test_passive_delayed_close_observation_never_reclicks_and_satisfies_backend_evidence(self):
        from social_wxauto_backend import WxautoSocialBackend
        now={'value':0.}
        def delayed_click():self.events.append('submit-request')
        self.confirm.on_click=delayed_click
        self.candidate.passive_clock=lambda:now['value']
        def sleep(seconds):
            now['value']+=seconds;self.verify.exists=False;self.foreground=self.profile;self.add.Name='等待验证'
        self.candidate.passive_sleep=sleep
        before=self.candidate.observe_before(self.wx,self.parameters)
        performed=self.candidate.perform(self.wx,self.parameters)
        self.assertFalse(performed['verification']['formClosed'])
        result=self.candidate.observe_after(self.wx,self.parameters,performed,before)
        self.assertTrue(result['confirmed']);self.assertTrue(result['postClosureObservation']['formClosed'])
        self.assertGreater(result['postClosureObservation']['observations'],1)
        self.assertFalse(result['postClosureObservation']['timedOut'])
        self.assertEqual(self.events.count('submit-request'),1)
        self.assertTrue(WxautoSocialBackend._evidence(SimpleNamespace(clock=lambda:100),result,'add_friend',
            self.parameters,self.session['client'],'after',100))
        broken={**result,'confirmed':False}
        self.assertFalse(WxautoSocialBackend._evidence(SimpleNamespace(clock=lambda:100),broken,'add_friend',
            self.parameters,self.session['client'],'after',100))

    def test_passive_close_timeout_is_unknown_without_reclick_or_cleanup(self):
        self.confirm.on_click=lambda:self.events.append('submit-request')
        self.candidate.closure_timeout=0
        result=self.run_request()
        self.assertFalse(result['confirmed']);self.assertFalse(result['requestPending'])
        self.assertTrue(result['postClosureObservation']['timedOut'])
        self.assertTrue(result['restorationUnavailable']);self.assertTrue(self.verify.exists)
        self.assertEqual(self.events.count('submit-request'),1)
        self.assertNotIn('close-owned-profile',self.events)

    def test_profile_discovery_prunes_deep_details_but_independently_checks_header(self):
        self.candidate._session=self.session
        detail=Control('mmui::ProfileDetailNormalUi','','GroupControl',(88,),(0,0,500,500),self.events)
        def forbidden():raise AssertionError('unrelated private detail expanded')
        detail.GetChildren=forbidden;self.profile.children.append(detail)
        reads={'count':0};children=list(self.header.children)
        def header_children():reads['count']+=1;return children
        self.header.GetChildren=header_children
        state=self.candidate._profile_state(self.parameters)
        self.assertFalse(state['pending']);self.assertGreaterEqual(reads['count'],2)
        self.assertNotIn('add-contact',self.events)
        # Compound-leaf discovery does not exempt the actual header identity.
        self.display.Name='wrong-profile'
        with self.assertRaisesRegex(RuntimeError,'profile-name-mismatch'):
            self.candidate._profile_state(self.parameters)

    def test_profile_discovery_retains_relevant_query_and_waiting_button_outside_details(self):
        self.candidate._session=self.session;self.add.Name='等待验证'
        detail=Control('mmui::ProfileDetailNormalUi','','GroupControl',(88,),(0,0,500,500),self.events)
        current=detail
        for index in range(25):
            node=Control('QWidget','','GroupControl',(1000+index,),(0,0,500,500),self.events)
            current.children=[node];current=node
        self.profile.children.append(detail)
        first=self.candidate._profile_state(self.parameters);second=self.candidate._profile_state(self.parameters)
        self.assertEqual(first,second);self.assertTrue(first['pending'])
        self.query.value='other_id'
        with self.assertRaisesRegex(RuntimeError,'profile-query-mismatch'):
            self.candidate._profile_state(self.parameters)

if __name__=='__main__':unittest.main()
