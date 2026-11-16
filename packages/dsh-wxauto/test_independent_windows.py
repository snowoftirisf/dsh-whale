"""Synthetic UIA objects only; no real WeChat UI operations or sends."""
import copy
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from independent_windows import create_window_monitor,IndependentWindowMonitor,receipt_matches
from message_watch import MainWindowMonitor
from test_message_watch import Row, ReadFixtures

ROUTES={'主人':'friend','测试群':'group'}


def live_fixture_receipt():
    # This fake receipt is never saved outside tests or considered live evidence.
    return {'verified':True,'verificationKind':'live','mode':'independent-windows',
        'verifiedAt':'2026-11-01T12:00:00+00:00','clientVersion':'4.2.0.1000','wxautoVersion':'41.1.7',
        'evidence':['synthetic-fixture-only-not-production-verification'],
        'windowClasses':['mmui::ChatSingleWindow'],
        'routes':[{'target':n,'kind':k,'passed':True} for n,k in ROUTES.items()],
        'checks':{k:True for k in ('passiveRead','targetBinding','textReceive','textSend','fileSend',
            'closedWindowStopsSend','identityChangeStopsSend','imageReceive','voiceReceive','fileReceive','startupOpen')}}


class Control:
    def __init__(self,handle,cls='mmui::ChatSingleWindow'):
        self.NativeWindowHandle=handle;self.ProcessId=11;self.ClassName=cls;self.alive=True;self.runtime=(handle,1)
    def Exists(self,*_):return self.alive
    def GetRuntimeId(self):return self.runtime


class Box:
    def __init__(self):self.rows=[Row(1,'startup history')];self.alive=True
    def Exists(self,*_):return self.alive
    def GetChildren(self):return self.rows
    def WheelDown(self,**kwargs):raise AssertionError('no unverified scrolling in fixture')


class FakeChat:
    def __init__(self,target,kind,handle):
        self.info={'chat_name':target,'chat_type':kind};self.box=Box()
        self._api=SimpleNamespace(control=Control(handle),_chat_api=SimpleNamespace(msgbox=self.box))
        self._api.chat=self
    def ChatInfo(self):return dict(self.info)
    def SendMsg(self,*args,**kwargs):raise AssertionError('tests must never send')
    def SendFiles(self,*args,**kwargs):raise AssertionError('tests must never send')


class FakeMain:
    def __init__(self):
        self.chats=[FakeChat('主人','friend',2),FakeChat('测试群','group',3)]
        self._api=SimpleNamespace(control=Control(1,'mmui::MainWindow'),_chat_api=SimpleNamespace(msgbox=Box()),open_separate_window=self.open)
        self.selected='主人';self.switches=[];self.opened=[];self.sessions_called=0;self.enumerated=0
    def ChatInfo(self):return {'chat_name':self.selected,'chat_type':ROUTES.get(self.selected,'friend')}
    def ChatWith(self,name,exact=True):self.switches.append(name);self.selected=name
    def GetSession(self):self.sessions_called+=1;return []
    def GetAllSubWindow(self):self.enumerated+=1;return self.chats
    def open(self,target,resize=False):
        self.opened.append(target);return FakeChat(target,ROUTES[target],20+len(self.opened))._api


def selection(main,receipt=None,**kwargs):
    return create_window_monitor(main,ROUTES,config={'enabled':True},receipt=receipt or live_fixture_receipt(),
        current_version='4.2.0.1000',wxauto_version='41.1.7',**kwargs)


def parse(row,parent):
    return getattr(row,'message',SimpleNamespace(type='text',attr='friend',sender='测试发言人',content=row.Name))


class IndependentTests(ReadFixtures):
    def test_direction_preparation_never_activates_independent_window(self):
        main=FakeMain();monitor=selection(main).initialize()
        bound=monitor._bound('主人')
        # Real BoundWindowMonitor override checks identity only. Its fake root
        # intentionally has neither SetActive nor a global input implementation.
        bound.prepare_direction_read()
        self.assertEqual(main.switches,[])

    def test_default_and_unverified_modes_make_no_independent_window_calls(self):
        main=FakeMain()
        for config,receipt in [(None,None),({'enabled':True},None),({'enabled':False},live_fixture_receipt())]:
            result=create_window_monitor(main,ROUTES,config=config,receipt=receipt,current_version='4.2.0.1000',wxauto_version='41.1.7')
            self.assertEqual(result.mode,'main');self.assertIs(type(result.monitor),MainWindowMonitor)
        self.assertEqual(main.enumerated,0);self.assertEqual(main.switches,[]);self.assertEqual(main.opened,[])

    def test_exact_version_route_media_and_live_checks_are_required(self):
        for change,options in [({'clientVersion':'4.1.8.107'},{}),({'wxautoVersion':'41.1.6'},{}),
            ({'verificationKind':'unit-test'},{}),({'evidence':[]},{}),({'routes':[]},{}),
            ({'checks':{}},{}),({'windowClasses':['mmui::MainWindow']},{}),
            ({'checks':{**live_fixture_receipt()['checks'],'imageReceive':False}},{'image_directory':'images'})]:
            main=FakeMain();receipt={**live_fixture_receipt(),**change};result=selection(main,receipt,**options)
            self.assertEqual(result.mode,'main');self.assertEqual(main.enumerated,0)
        main=FakeMain();result=selection(main,require_native_voice=True)
        self.assertEqual(result.mode,'main') # no native-voice-send live check

    def test_two_chats_read_new_text_without_switching_main_or_replaying_history(self):
        main=FakeMain();chosen=selection(main);monitor=chosen.initialize()
        self.assertIsInstance(monitor,IndependentWindowMonitor)
        one,two=main.chats;one.box.rows.append(Row(2,'私聊新消息'));two.box.rows.append(Row(2,'群聊新消息'))
        with patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=parse)}):
            self.assertEqual(monitor.candidates(),['主人','测试群'])
            self.assertEqual(monitor.read('主人')[0]['text'],'私聊新消息')
            self.assertEqual(monitor.read('测试群')[0]['text'],'群聊新消息')
            self.assertEqual(monitor.read('主人'),[]);self.assertEqual(monitor.read('测试群'),[])
        self.assertIs(chosen.chat_for('主人'),one);self.assertIs(chosen.chat_for('测试群'),two)
        self.assertEqual(main.switches,[]);self.assertEqual(main.sessions_called,0);self.assertEqual(main.opened,[])
        self.assertEqual(main.selected,'主人')

    def test_media_receivers_are_the_original_parser_with_bound_parent(self):
        main=FakeMain();chosen=selection(main,receive_voice=True,image_directory='image-test',attachment_directory='file-test',data_root='root-test')
        monitor=chosen.initialize();chat=main.chats[1]
        image,voice,file=Row(2,'图片'),Row(3,'语音'),Row(4,'文件')
        image.ClassName='mmui::ChatImageItemView';voice.ClassName='mmui::ChatVoiceItemView';file.ClassName='mmui::ChatFileItemView'
        image.message=SimpleNamespace(type='image',attr='friend',sender='甲')
        voice.message=SimpleNamespace(type='voice',attr='friend',sender='乙',to_text=lambda:'语音识别结果')
        file.message=SimpleNamespace(type='file',attr='friend',sender='丙')
        chat.box.rows.extend([image,voice,file]);parents=[]
        def bound_parse(row,parent):parents.append(parent);return parse(row,parent)
        def download_image(message,directory,ensure_chat):
            self.assertTrue(ensure_chat());self.assertIs(message,image.message);return {'path':'image-test/original.png'}
        def download_file(message,directory,ensure_chat,media_root):
            self.assertTrue(ensure_chat());self.assertIs(message,file.message);self.assertEqual(media_root,'root-test')
            return {'path':'file-test/example.txt','name':'example.txt'}
        with patch.dict(sys.modules,{'wxauto4.msgs.msg':SimpleNamespace(parse_msg=bound_parse)}), \
             patch('incoming_images.download_image',side_effect=download_image) as images, \
             patch('incoming_files.download_file',side_effect=download_file) as files:
            records=monitor.read('测试群')
        self.assertEqual([m['mediaType'] for m in records],['image','voice','file'])
        self.assertEqual(records[0]['images'][0]['path'],'image-test/original.png')
        self.assertIn('语音识别结果',records[1]['text']);self.assertEqual(records[2]['files'][0]['name'],'example.txt')
        self.assertTrue(all(parent is chat._api._chat_api for parent in parents))
        images.assert_called_once();files.assert_called_once();self.assertEqual(main.switches,[])

    def test_closed_window_stops_its_route_without_reopening_or_stopping_other_chat(self):
        main=FakeMain();chosen=selection(main);monitor=chosen.initialize()
        main.chats[1]._api.control.alive=False
        self.assertEqual(monitor.candidates(),['主人'])
        with self.assertRaisesRegex(RuntimeError,'window-closed'):chosen.chat_for('测试群')
        self.assertIs(chosen.chat_for('主人'),main.chats[0])
        self.assertEqual(main.opened,[]);self.assertEqual(main.switches,[])

    def test_changed_identity_kind_or_handle_is_never_returned_for_sending(self):
        for mutation in ('name','kind','runtime','handle','pid'):
            main=FakeMain();chosen=selection(main);monitor=chosen.initialize();chat=main.chats[1]
            if mutation=='name':chat.info['chat_name']='其他群'
            if mutation=='kind':chat.info['chat_type']='friend'
            if mutation=='runtime':chat._api.control.runtime=(99,99)
            if mutation=='handle':chat._api.control.NativeWindowHandle=55
            if mutation=='pid':chat._api.control.ProcessId=22
            with self.assertRaises(RuntimeError):chosen.chat_for('测试群')
            self.assertEqual(monitor.candidates(),['主人']);self.assertEqual(main.opened,[])

    def test_missing_or_ambiguous_window_falls_back_once_during_initialization(self):
        for missing in (True,False):
            main=FakeMain()
            if missing:main.chats=main.chats[:1]
            else:main.chats.append(FakeChat('测试群','group',4))
            chosen=selection(main)
            with patch('message_watch.time.sleep'):
                monitor=chosen.initialize();chosen.initialize()
            self.assertIs(type(monitor),MainWindowMonitor);self.assertEqual(chosen.mode,'main')
            self.assertEqual(main.enumerated,1);self.assertEqual(main.opened,[])

    def test_explicit_one_shot_startup_open_requires_separate_live_check(self):
        main=FakeMain();main.chats=[]
        chosen=create_window_monitor(main,ROUTES,config={'enabled':True,'openMissingAtStartup':True},
            receipt=live_fixture_receipt(),current_version='4.2.0.1000',wxauto_version='41.1.7',chat_factory=lambda core:core.chat)
        chosen.initialize();chosen.initialize()
        self.assertEqual(chosen.mode,'independent');self.assertEqual(main.opened,['主人','测试群'])
        main=FakeMain();main.chats=[];receipt=live_fixture_receipt();receipt['checks']['startupOpen']=False
        chosen=create_window_monitor(main,ROUTES,config={'enabled':True,'openMissingAtStartup':True},
            receipt=receipt,current_version='4.2.0.1000',wxauto_version='41.1.7',chat_factory=lambda core:core.chat)
        with patch('message_watch.time.sleep'):chosen.initialize()
        self.assertEqual(chosen.mode,'main');self.assertEqual(main.opened,[])


if __name__=='__main__':unittest.main()
