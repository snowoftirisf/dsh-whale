"""Unregistered, inert candidate fixtures. No installed wxauto connection."""
import copy
import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace as NS
import unittest
from social_actions import _digest
from social_add_friend_candidate import AddFriendCandidate
from test_social_contact_observation import profile,ReplayControl,ROW_ID,KEY_ID,VALUE_ID


def rectangle(left=0,top=0,right=500,bottom=500):
    return NS(left=left,top=top,right=right,bottom=bottom)


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.wx=NS()
        self.parameters={'keywords':'_1P3Y50K','expectedProfileName':'🛌','addmsg':'fixture greeting'}
        self.plan={'id':'a'*32,'hash':_digest('add_friend',self.parameters),'action':'add_friend',
            'parameters':copy.deepcopy(self.parameters),'status':'claimed','approvedBy':'owner',
            'approvalMessageId':'fixture-real-owner-private-source','origin':{'kind':'friend','target':'owner'},'expiresAt':1000}
        self.opened=0;self.closed=0;self.clicked=0;self.provider_calls=0;self.close_unknown=False;self.button_error=False
        self.profile=profile();self.profile.ProcessId=7
        self.profile.children[0].Name='🛌';self.profile.children[1].Name='🛌';self.profile.children.pop()
        self.form=NS(ClassName='mmui::AddFriendWindow',ControlTypeName='WindowControl',ProcessId=7,
            NativeWindowHandle=12,IsOffscreen=0,GetRuntimeId=lambda:[10],BoundingRectangle=rectangle(),GetChildren=lambda:[],Name='private form',AutomationId='')
        self.main=NS(NativeWindowHandle=1,ProcessId=7,IsOffscreen=0,GetRuntimeId=lambda:[1])
        self.search=NS(GetValuePattern=lambda:NS(Value='_1P3Y50K'))
        def click(**kwargs):
            self.assertEqual(kwargs,{'move':True,'show_window':False})
            self.clicked+=1
            if self.button_error:raise RuntimeError('fixture-native-exception-after-click')
        self.button=NS(ClassName='mmui::XOutlineButton',ControlTypeName='ButtonControl',Name='添加到通讯录',
            ProcessId=7,IsOffscreen=0,IsEnabled=1,GetRuntimeId=lambda:[11],BoundingRectangle=rectangle(100,100,300,150),Click=click)
        self.helpers=NS(nodes=lambda form:[self.profile,self.search,self.button],
            unique=lambda roots,cls,name:self.search if name=='搜索' else self.button)
        self.profile.ClassName='mmui::ProfileHeaderUi';self.search.ClassName='mmui::XValidatorTextEdit'
        self.client={'clientVersion':'4.2.0.12','clientSha256':'a'*64}
        def provider():self.provider_calls+=1;return copy.deepcopy(self.plan)
        @contextmanager
        def open_lookup(wx,config,groups,**kwargs):
            self.opened+=1
            self.assertIs(wx,self.wx)
            kwargs['plan_check']()
            try:
                yield {'wx':wx,'form':self.form,'formGuard':lambda:None,'pid':7,'helpers':self.helpers,
                    'main':self.main,'ownedRoots':lambda:[self.main,self.form],'client':self.client,
                    'uia':NS(GetForegroundWindow=lambda:12),
                    'report':{'profileIdentityMatched':False,'identityEvidence':'id-unavailable'}}
            finally:
                self.closed+=1
                if self.close_unknown:raise RuntimeError('social-chat-restore-failed')
        self.candidate=AddFriendCandidate(self.wx,{'target':'owner'},plan_id=self.plan['id'],plan_hash=self.plan['hash'],
            plan_provider=provider,allowed_groups=('group',),client_probe=lambda wx:self.client,
            refresh_chat=lambda *args:None,clock=lambda:100,open_lookup=open_lookup)

    def test_constructor_is_inert_and_no_action_binding_is_registered(self):
        self.assertEqual((self.opened,self.provider_calls,self.clicked),(0,0,0))
        self.assertEqual(self.candidate.binding().implementation_id,'unregistered-owner-approved-add-contact-candidate-v1')
        self.assertEqual(self.provider_calls,0)
        self.assertIsNone(self.candidate.last_observation)
        self.candidate.close()
        self.assertIsNone(self.candidate.last_observation)

    def test_approved_claimed_name_basis_clicks_only_observed_button_once_then_unknown(self):
        before=self.candidate.observe_before(self.wx,self.parameters)
        self.assertEqual(before['identityBasis'],'owner-reviewed-query-and-name')
        self.assertFalse(before['publicWechatIdVerified'])
        self.assertEqual(self.clicked,0)
        result=self.candidate.perform(self.wx,self.parameters)
        with self.assertRaisesRegex(RuntimeError,'already-attempted'):
            self.candidate.perform(self.wx,self.parameters)
        after=self.candidate.observe_after(self.wx,self.parameters,result,before)
        self.assertFalse(after['confirmed'])
        self.assertEqual(after['outcome'],'unknown')
        self.assertFalse(after['applicationSubmittedVerified'])
        self.assertEqual((self.clicked,self.opened,self.closed),(1,1,1))
        self.assertEqual(self.candidate.last_before,before)
        self.assertTrue(self.candidate.last_observation['ownershipStable'])
        self.candidate.close();self.assertEqual(self.closed,1)
        self.assertNotIn('🛌',str(before))
        self.assertNotIn('fixture greeting',str(after))

    def test_pending_unclaimed_expired_wrong_source_or_hash_cannot_navigate(self):
        baseline=copy.deepcopy(self.plan)
        for change in ({'status':'pending'},{'status':'approved'},{'approvedBy':'other'},
                       {'approvalMessageId':''},{'expiresAt':99},{'id':'b'*32},
                       {'origin':{'kind':'group','target':'group'}}):
            self.plan={**copy.deepcopy(baseline),**change}
            with self.subTest(change=list(change)),self.assertRaises(RuntimeError):
                self.candidate.observe_before(self.wx,self.parameters)
        self.assertEqual((self.opened,self.clicked),(0,0))

    def test_geometry_or_name_change_after_before_refuses_click_and_outer_close_works(self):
        self.candidate.observe_before(self.wx,self.parameters)
        self.button.BoundingRectangle=rectangle(101,100,301,150)
        with self.assertRaisesRegex(RuntimeError,'controls-changed'):
            self.candidate.perform(self.wx,self.parameters)
        self.assertEqual(self.clicked,0)
        self.candidate.close();self.assertEqual(self.closed,1)
        self.assertIsNone(self.candidate.last_observation)

    def test_name_mismatch_cleans_owned_context_without_click(self):
        self.profile.children[1].Name='different candidate'
        with self.assertRaisesRegex(RuntimeError,'reviewed-name-mismatch'):
            self.candidate.observe_before(self.wx,self.parameters)
        self.assertEqual((self.clicked,self.closed),(0,1))

    def add_public_id(self,value):
        self.profile.children.append(ReplayControl('mmui::XHBoxView',id=ROW_ID,children=[
            ReplayControl('mmui::XTextView','WeChat ID:',KEY_ID,'TextControl'),
            ReplayControl('mmui::ProfileTextView',value,VALUE_ID,'TextControl')]))

    def test_same_reviewed_name_cannot_override_mismatched_ambiguous_or_invalid_public_id(self):
        for mode in ('mismatch','ambiguous','invalid'):
            with self.subTest(mode=mode):
                self.setUp()
                self.add_public_id('different_public_id' if mode=='mismatch' else 'invalid ID' if mode=='invalid' else '_1P3Y50K')
                if mode=='ambiguous':self.add_public_id('_1P3Y50K')
                with self.assertRaisesRegex(RuntimeError,'public-id-contradiction'):
                    self.candidate.observe_before(self.wx,self.parameters)
                self.assertEqual(self.clicked,0)
                self.assertEqual(self.closed,1)

    def test_public_id_becoming_contradictory_after_before_refuses_perform(self):
        before=self.candidate.observe_before(self.wx,self.parameters)
        self.assertEqual(before['identityEvidence'],'id-unavailable')
        self.add_public_id('different_public_id')
        with self.assertRaisesRegex(RuntimeError,'public-id-contradiction'):
            self.candidate.perform(self.wx,self.parameters)
        self.assertEqual(self.clicked,0)
        self.candidate.close()

    def test_before_uses_fresh_matching_public_id_instead_of_stale_lookup_report(self):
        self.add_public_id('_1P3Y50K')
        before=self.candidate.observe_before(self.wx,self.parameters)
        self.assertEqual(before['identityEvidence'],'matched')
        self.assertTrue(before['publicWechatIdVerified'])
        self.candidate.close()

    def test_exception_after_click_never_retries_and_unknown_close_stays_fatal(self):
        self.candidate.observe_before(self.wx,self.parameters)
        self.button_error=True
        with self.assertRaises(RuntimeError):self.candidate.perform(self.wx,self.parameters)
        with self.assertRaisesRegex(RuntimeError,'already-attempted'):self.candidate.perform(self.wx,self.parameters)
        captures=[]
        def children():
            self.assertEqual(self.closed,0)
            captures.append(1)
            return []
        self.form.GetChildren=children
        self.close_unknown=True
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError,'social-chat-restore-failed'):self.candidate.close()
        self.assertEqual((self.clicked,self.closed),(1,1))
        self.assertEqual(len(captures),1)
        self.assertTrue(self.candidate.restoration_failed)
        self.assertIsNotNone(self.candidate.last_observation)

    def test_deep_send_button_and_values_are_metadata_only_despite_hidden_zero_rect_nodes(self):
        def node(rid,name,children=(),offscreen=0,bounds=None,value=None):
            return NS(ClassName='mmui::XButton',ControlTypeName='ButtonControl',ProcessId=7,NativeWindowHandle=0,
                IsOffscreen=offscreen,Name=name,AutomationId='fixture-id',GetRuntimeId=lambda:[rid],
                BoundingRectangle=bounds or rectangle(),GetChildren=lambda:list(children),
                GetValuePattern=lambda:NS(Value=value) if value is not None else None)
        send=node(44,'发送',value='private typed verification')
        zero=node(43,'zero container',[send],bounds=rectangle(0,0,0,0))
        deep=node(42,'nested',[zero])
        hidden=node(45,'hidden private text',offscreen=1)
        self.candidate.observe_before(self.wx,self.parameters)
        self.candidate.perform(self.wx,self.parameters)
        self.form.GetChildren=lambda:[deep,hidden]
        self.candidate.observe_after(self.wx,self.parameters,{},self.candidate.last_before)
        snapshot=self.candidate.last_observation
        self.assertEqual(self.clicked,1)
        self.assertTrue(snapshot['ownershipStable'])
        self.assertEqual(len(snapshot['nodes']),4)
        by_role=[n for n in snapshot['nodes'] if n['semanticRole']=='send']
        self.assertEqual(len(by_role),1)
        self.assertEqual(by_role[0]['depth'],3)
        self.assertEqual(by_role[0]['valueSha256'],hashlib.sha256(b'private typed verification').hexdigest())
        self.assertTrue(any(n['geometryAvailable']is False for n in snapshot['nodes']))
        self.assertNotIn('private typed verification',json.dumps(snapshot))
        self.assertNotIn('hidden private text',json.dumps(snapshot))
        before=copy.deepcopy(snapshot)
        self.candidate.close()
        self.assertEqual(self.candidate.last_observation,before)


if __name__=='__main__':unittest.main()
