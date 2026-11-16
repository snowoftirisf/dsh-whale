import unittest
from types import SimpleNamespace as NS
from test_message_direction import Control
from PIL import Image
from group_voice_transcription import transcribe_group_voice


class GroupVoiceTranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.root=Control('mmui::MainWindow',(0,0,500,500),[1])
        self.row=Control('mmui::ChatVoiceItemView',(10,50,90,80),[2],Image.new('RGB',(80,30),(100,120,140)))
        self.box=Control('mmui::RecyclerListView',(0,40,500,400),[3])
        self.front=NS(NativeWindowHandle=123,ProcessId=100)
        self.uia=NS(GetForegroundControl=lambda:self.front,ImageGrab=NS(grab=lambda **k:self.row.image))
        self.chat={'chat_name':'group','chat_type':'group'}
        self.wx=NS(ChatInfo=lambda:dict(self.chat),_api=NS(control=self.root,_chat_api=NS(msgbox=self.box)))
        self.clicks=[];self.old_calls=[]
        self.row.RightClick=lambda **kw:self.clicks.append(kw)
        old_calls=self.old_calls
        class Message:
            attr='friend';type='voice'
            def right_click(self):old_calls.append('bad fixed SDK coordinate')
            def to_text(self):self.right_click();return 'actual SDK result'
        self.message=Message();self.message.control=self.row
        self.locator=lambda image:{'x':35,'y':15}
        self.detector=lambda image:('left',20)
    def read(self):return transcribe_group_voice(self.message,self.wx,target='group',uia=self.uia,locator=self.locator,detector=self.detector)
    def test_sdk_result_retained_with_one_qualified_click_and_method_restored(self):
        self.assertEqual(self.read(),'actual SDK result')
        self.assertEqual(self.clicks,[{'pos':'lefttop','x':35,'y':15}]);self.assertEqual(self.old_calls,[])
        self.assertNotIn('right_click',self.message.__dict__)
        self.message.right_click();self.assertEqual(len(self.old_calls),1)
    def test_cached_transcript_needs_no_capture_or_click(self):
        self.message.to_text=lambda:'already transcribed'
        self.assertEqual(self.read(),'already transcribed')
        self.assertEqual(self.row.captures,[]);self.assertEqual(self.clicks,[])
    def test_original_exception_and_instance_override_restored(self):
        original=lambda:None;self.message.right_click=original
        def fail():self.message.right_click();raise ValueError('private SDK failure')
        self.message.to_text=fail
        with self.assertRaisesRegex(ValueError,'private SDK failure'):self.read()
        self.assertIs(self.message.right_click,original);self.assertEqual(len(self.clicks),1)
    def test_uncertain_direction_point_foreground_geometry_and_chat_never_click(self):
        cases=['self','unknown','point','foreground','row-change','size','chat','locator-failure']
        for case in cases:
            with self.subTest(case=case):
                self.setUp()
                if case=='self':self.detector=lambda image:('right',20)
                elif case=='unknown':self.detector=lambda image:('left',float('nan'))
                elif case=='point':self.locator=lambda image:{'x':True,'y':15}
                elif case=='foreground':self.front.NativeWindowHandle=456
                elif case=='row-change':
                    def changed(image):self.row.Name='changed private';return {'x':35,'y':15}
                    self.locator=changed
                elif case=='size':self.row.image=Image.new('RGB',(81,30),(100,120,140))
                elif case=='chat':self.chat['chat_type']='friend'
                else:self.locator=lambda image:(_ for _ in ()).throw(RuntimeError('voice-transcription-no-bubble'))
                with self.assertRaises(RuntimeError):self.read()
                self.assertEqual(self.clicks,[]);self.assertNotIn('right_click',self.message.__dict__)
    def test_sdk_menu_steps_are_revalidated_and_excess_clicks_refused(self):
        def repeated():
            for _ in range(4):self.message.right_click()
        self.message.to_text=repeated
        with self.assertRaisesRegex(RuntimeError,'click-budget-exceeded'):self.read()
        self.assertEqual(len(self.clicks),3)
    def test_dispatch_failure_is_not_retried(self):
        def failed(**kwargs):self.clicks.append(kwargs);raise RuntimeError('dispatch outcome unknown')
        self.row.RightClick=failed
        with self.assertRaisesRegex(RuntimeError,'dispatch outcome unknown'):self.read()
        self.assertEqual(len(self.clicks),1);self.assertNotIn('right_click',self.message.__dict__)


if __name__=='__main__':unittest.main()
