import unittest
from types import SimpleNamespace as NS
from avatar_input_trace import install_avatar_input_trace


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.calls=[];self.events=[];self.now=10
        outer=self
        class Control:
            ProcessId=1;ClassName='mmui::ChatTextItemView';Name='PRIVATE BODY NEVER EXPORT'
            BoundingRectangle=NS(left=1,top=2,right=99,bottom=100)
            def GetRuntimeId(self):return [1,2,3]
            def Click(self,*args,**kwargs):outer.calls.append(('Click',args,kwargs));return 'original-result'
            def RightClick(self,*args,**kwargs):outer.calls.append(('RightClick',args,kwargs));raise RuntimeError('original-error')
            def DoubleClick(self,*args,**kwargs):outer.calls.append(('DoubleClick',args,kwargs));return False
        self.C=Control;self.c=Control()
        self.original=self.C.Click
        self.restore=install_avatar_input_trace(NS(Control=self.C),NS(ProcessId=1),self.events.append,
            expires_at=50,clock=lambda:self.now)
        self.addCleanup(self.restore)

    def test_original_once_return_and_exception_preserved_no_body(self):
        self.assertEqual(self.c.Click(pos='lefttop',x=51,y=30,secret='PRIVATE'),'original-result')
        with self.assertRaisesRegex(RuntimeError,'original-error'):self.c.RightClick()
        self.assertEqual(len(self.calls),2);self.assertEqual(len(self.events),2)
        self.assertNotIn('PRIVATE',str(self.events))
        self.assertEqual(self.events[0]['parameters'],{'pos':'lefttop','x':51,'y':30})

    def test_observer_failure_does_not_change_action_or_retry(self):
        self.restore()
        def broken(value):raise ValueError('observer')
        self.restore=install_avatar_input_trace(NS(Control=self.C),NS(ProcessId=1),broken,expires_at=50,clock=lambda:10)
        self.assertFalse(self.c.DoubleClick());self.assertEqual(len(self.calls),1)

    def test_expired_foreign_and_other_controls_unobserved(self):
        self.now=60;self.c.Click();self.now=10
        self.c.ProcessId=2;self.c.Click();self.c.ProcessId=1
        self.c.ClassName='Unrelated';self.c.Click()
        self.assertEqual(len(self.calls),3);self.assertEqual(self.events,[])

    def test_broken_control_properties_do_not_change_original(self):
        self.c.GetRuntimeId=lambda:(_ for _ in ()).throw(ValueError('private'))
        self.assertEqual(self.c.Click(),'original-result');self.assertEqual(self.events,[])

    def test_restore_and_expiry_validation(self):
        self.restore();self.assertIs(self.C.Click,self.original)
        for expiry in (True,9,1000,float('nan')):
            with self.assertRaises(ValueError):install_avatar_input_trace(NS(Control=self.C),NS(ProcessId=1),self.events.append,expires_at=expiry,clock=lambda:10)

    def test_post_rightclick_observation_preserves_original_exception_and_does_not_add_input(self):
        self.restore();probes=[]
        def probe(root,uia):
            probes.append(root.ProcessId)
            return {'observed':True,'menuCount':1,'menus':[{'shape':'peer-at-and-pat','runtimeIdSha256':'fixture'}]}
        self.restore=install_avatar_input_trace(NS(Control=self.C),NS(ProcessId=1),self.events.append,
            expires_at=50,clock=lambda:self.now,menu_probe=probe)
        with self.assertRaisesRegex(RuntimeError,'original-error'):self.c.RightClick(x=51,y=30,pos='lefttop')
        self.assertEqual(len(self.calls),1)
        self.assertEqual(probes,[1])
        self.assertEqual(self.events[-1]['phase'],'after-input')
        self.assertEqual(self.events[-1]['menuObservation']['menus'][0]['shape'],'peer-at-and-pat')
        self.assertNotIn('PRIVATE',str(self.events))
        self.c.Click();self.now=60
        with self.assertRaisesRegex(RuntimeError,'original-error'):self.c.RightClick()
        self.assertEqual(probes,[1])

    def test_post_observer_failure_cannot_change_result_or_trigger_retry(self):
        self.restore()
        def probe(*args):raise ValueError('private diagnostic failure')
        self.restore=install_avatar_input_trace(NS(Control=self.C),NS(ProcessId=1),self.events.append,
            expires_at=50,clock=lambda:10,menu_probe=probe)
        # A raising original remains raising even if the post-observer fails.
        with self.assertRaisesRegex(RuntimeError,'original-error'):self.c.RightClick()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(len(self.events),1)


if __name__=='__main__':unittest.main()
