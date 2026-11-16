"""Only fake controls and durable hook fixtures; no wxauto import or live UI."""
import copy
import unittest
from types import SimpleNamespace
from social_actions import _digest
from social_friend_verification import FriendVerificationContinuation,_sha

class Control:
    def __init__(self,klass,name,kind,rid,bounds,events):
        self.ClassName,self.Name,self.ControlTypeName=klass,name,kind
        self.rid=rid;self.bounds=bounds;self.events=events;self.children=[];self.ProcessId=123
        self.NativeWindowHandle=99;self.IsEnabled=True;self.IsOffscreen=False;self.exists=True;self.value='default'
        self.on_set=None;self.on_read=None;self.on_click=None
    @property
    def BoundingRectangle(self):return SimpleNamespace(**dict(zip(('left','top','right','bottom'),self.bounds)))
    def Exists(self,_):return self.exists
    def GetRuntimeId(self):return self.rid
    def GetChildren(self):return self.children
    def GetValuePattern(self):return self
    @property
    def Value(self):
        self.events.append('read')
        if self.on_read:self.on_read()
        return self.value
    def SetValue(self,value):
        self.events.append('set');self.value=value
        if self.on_set:self.on_set()
    def Click(self,**kwargs):
        self.events.append('click')
        if self.on_click:self.on_click()

class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.events=[];self.parameters={'keywords':'fixture-id','expectedProfileName':'fixture-person','addmsg':'approved exact greeting'}
        self.form=Control('mmui::VerifyFriendWindow','', 'WindowControl',(1,), (0,0,500,500),self.events)
        self.edit=Control('mmui::XValidatorTextEdit','发送添加朋友申请','EditControl',(2,), (10,10,400,200),self.events)
        self.confirm=Control('mmui::XOutlineButton','确定','ButtonControl',(3,), (20,300,120,350),self.events)
        self.remark=Control('mmui::XLineEdit','修改备注','EditControl',(4,), (10,220,400,250),self.events)
        self.form.children=[self.edit,self.confirm,self.remark]
        self.snapshot={'source':'wxauto-uia-friend-verification','schemaVersion':1,'planId':'a'*32,
            'parametersHash':_digest('add_friend',self.parameters),'querySha256':_sha(self.parameters['keywords']),
            'expectedProfileNameSha256':_sha(self.parameters['expectedProfileName']),'pid':123,'formHandle':99,
            'formRuntimeId':[1],'formBounds':[0,0,500,500],'greetingRuntimeId':[2],'greetingBounds':[10,10,400,200],
            'confirmRuntimeId':[3],'confirmBounds':[20,300,120,350]}
        self.foreground=self.form
        self.uia=SimpleNamespace(GetForegroundControl=lambda:self.foreground)
        self.claim=lambda:self.events.append('claim') or True
        self.mark=lambda:self.events.append('mark') or True
        self.guard=lambda:True
    def helper(self):
        return FriendVerificationContinuation(self.form,self.snapshot,self.parameters,uia=self.uia,
            claimOnce=self.claim,markAttempt=self.mark,guard=self.guard)

    def test_exact_greeting_claim_before_input_mark_before_once_click_closed_is_unknown(self):
        self.confirm.on_click=lambda:setattr(self.form,'exists',False)
        helper=self.helper();self.assertEqual(self.events,[])
        result=helper.perform()
        self.assertTrue(result['formClosed']);self.assertFalse(result['deliveryConfirmed']);self.assertFalse(result['confirmed'])
        self.assertTrue(result['confirmClickAttempted']);self.assertEqual(result['outcome'],'unknown')
        self.assertEqual(self.edit.value,self.parameters['addmsg']);self.assertEqual(self.remark.value,'default')
        self.assertEqual(self.events,['claim','set','read','read','mark','read','click'])
        with self.assertRaisesRegex(RuntimeError,'already-used'):helper.perform()
        self.assertEqual(self.events.count('click'),1)

    def test_failed_claim_or_guard_never_inputs_or_confirms(self):
        for hook in ('claim','guard'):
            with self.subTest(hook=hook):
                self.events.clear();setattr(self,hook,lambda:False)
                result=self.helper().perform()
                self.assertFalse(result['inputAttempted']);self.assertFalse(result['confirmClickAttempted'])
                self.assertNotIn('set',self.events);self.assertNotIn('click',self.events)

    def test_exact_control_uniqueness_pid_visibility_bounds_rid_and_foreground(self):
        changes=[lambda:self.form.children.append(self.edit),lambda:setattr(self.edit,'ProcessId',999),
            lambda:setattr(self.confirm,'IsEnabled',False),lambda:setattr(self.edit,'IsOffscreen',True),
            lambda:setattr(self.edit,'bounds',(10,10,600,200)),lambda:setattr(self.edit,'rid',(99,)),
            lambda:setattr(self,'foreground',SimpleNamespace(NativeWindowHandle=100,ProcessId=123))]
        for change in changes:
            self.setUp();change();result=self.helper().perform()
            self.assertFalse(result['inputAttempted']);self.assertNotIn('claim',self.events);self.assertNotIn('click',self.events)

    def test_realistic_depth_ten_controls_are_found_without_relaxing_identity(self):
        children=self.form.children
        current=self.form
        for index in range(9):
            node=Control('mmui::XContainer','', 'PaneControl',(100+index,), (0,0,500,500),self.events)
            current.children=[node];current=node
        current.children=children  # greeting and confirm at observed depth10.
        result=self.helper().perform()
        self.assertTrue(result['confirmClickAttempted']);self.assertEqual(self.events.count('click'),1)

    def test_semantic_leaf_virtual_children_are_not_walked_or_matched(self):
        text=Control('mmui::XTextView','static label','TextControl',(5,), (10,260,400,290),self.events)
        self.form.children.append(text)
        for leaf in (self.edit,self.confirm,text):
            def forbidden():raise AssertionError('semantic virtual children expanded')
            leaf.GetChildren=forbidden
        # The real edit remains at depth10; its virtual children cannot exhaust
        # the bounded container walk or create phantom selector duplicates.
        children=self.form.children;current=self.form
        for index in range(9):
            node=Control('mmui::XContainer','','PaneControl',(100+index,),(0,0,500,500),self.events)
            current.children=[node];current=node
        current.children=children
        result=self.helper().perform()
        self.assertTrue(result['confirmClickAttempted']);self.assertEqual(self.events.count('click'),1)

    def test_real_duplicate_edit_or_confirm_still_rejects_before_claim(self):
        for original in ('edit','confirm'):
            self.setUp();node=getattr(self,original)
            duplicate=Control(node.ClassName,node.Name,node.ControlTypeName,(777,),node.bounds,self.events)
            self.form.children.append(duplicate)
            result=self.helper().perform()
            self.assertEqual(result['code'],'social-verification-control-not-unique')
            self.assertFalse(result['inputAttempted']);self.assertNotIn('claim',self.events);self.assertNotIn('click',self.events)

    def test_deep_checkbox_virtual_children_are_not_visited_or_changed(self):
        current=self.form
        for index in range(12):
            node=Control('mmui::XContainer','','PaneControl',(100+index,),(0,0,500,500),self.events)
            current.children.append(node);current=node
        checkbox=Control('mmui::CheckBox','permission','CheckBoxControl',(999,),(10,10,20,20),self.events)
        current.children.append(checkbox)
        def forbidden():raise AssertionError('permission virtual children expanded')
        checkbox.GetChildren=forbidden
        result=self.helper().perform()
        self.assertTrue(result['confirmClickAttempted'])
        self.assertEqual(self.events.count('click'),1)
        self.assertEqual(checkbox.value,'default')

    def test_mark_failure_never_clicks_and_reports_unknown_durable_outcome(self):
        def mark():self.events.append('mark');raise OSError('private error and path')
        self.mark=mark;result=self.helper().perform()
        self.assertTrue(result['inputAttempted']);self.assertFalse(result['confirmClickAttempted'])
        self.assertEqual(result['attemptMarkOutcome'],'unknown');self.assertEqual(result['outcome'],'unknown')
        self.assertNotIn('click',self.events);self.assertNotIn('private',str(result))

    def test_readback_or_revalidation_failure_does_not_click(self):
        for mutate in (lambda:setattr(self.edit,'value','wrong greeting'),lambda:setattr(self.confirm,'rid',(77,))):
            self.setUp();self.edit.on_set=mutate;result=self.helper().perform()
            self.assertTrue(result['inputAttempted']);self.assertFalse(result['confirmClickAttempted'])
            self.assertNotIn('mark',self.events);self.assertNotIn('click',self.events)

    def test_second_read_is_checked_and_guards_rechecked_after_durable_mark(self):
        reads={'n':0}
        def read():
            reads['n']+=1
            if reads['n']==2:self.edit.value='changed on second read'
        self.edit.on_read=read;result=self.helper().perform()
        self.assertEqual(result['code'],'social-verification-greeting-readback-mismatch');self.assertNotIn('click',self.events)
        self.setUp()
        def mark():self.events.append('mark');self.confirm.rid=(77,);return True
        self.mark=mark;result=self.helper().perform()
        self.assertTrue(result['attemptMarked']);self.assertFalse(result['confirmClickAttempted']);self.assertNotIn('click',self.events)

    def test_click_exception_preserves_attempt_and_never_retries(self):
        def click():raise RuntimeError('private SDK text')
        self.confirm.on_click=click;helper=self.helper();result=helper.perform()
        self.assertTrue(result['confirmClickAttempted']);self.assertTrue(result['attemptMarked'])
        self.assertEqual(result['outcome'],'unknown');self.assertEqual(result['code'],'social-verification-operation-outcome-unknown')
        self.assertNotIn('private',str(result))
        with self.assertRaises(RuntimeError):helper.perform()
        self.assertEqual(self.events.count('click'),1)

    def test_changed_plan_or_extra_snapshot_fields_fail_inert_constructor(self):
        for change in ('greeting','hash','extra'):
            self.setUp()
            if change=='greeting':self.parameters['addmsg']='unapproved change'
            elif change=='hash':self.snapshot['parametersHash']='b'*64
            else:self.snapshot['arbitraryPath']='private'
            with self.assertRaisesRegex(RuntimeError,'configuration-invalid'):self.helper()
            self.assertEqual(self.events,[])

if __name__=='__main__':unittest.main()
