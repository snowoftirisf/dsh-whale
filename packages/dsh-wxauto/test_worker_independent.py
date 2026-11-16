"""Worker integration on fake wxauto windows; no UI, process control or sends."""
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import worker
from test_independent_windows import FakeMain,FakeChat,ROUTES,live_fixture_receipt,parse


class SendingFakeChat(FakeChat):
    def __init__(self,target,kind,handle,actions):super().__init__(target,kind,handle);self.actions=actions
    def SendMsg(self,text,**kwargs):self.actions.append(('text',self.info['chat_name'],text));return True
    def SendFiles(self,path):self.actions.append(('file',self.info['chat_name'],str(path)));return True


class WorkerIndependentTests(unittest.TestCase):
    def run_worker(self,commands,*,on_event=None,receipt=True):
        actions=[];main=FakeMain()
        main.chats=[SendingFakeChat('主人','friend',2,actions),SendingFakeChat('测试群','group',3,actions)]
        class Voice:
            def __init__(self,chat,config):self.chat=chat
            def ready(self):return True
            def send(self,file,*,target,kind,command_id):
                if self.chat is main:raise AssertionError('independent voice must not record into main')
                assert target==self.chat.ChatInfo()['chat_name']
                assert kind==self.chat.ChatInfo()['chat_type']
                assert command_id.endswith(':voice')
                actions.append(('voice',self.chat.ChatInfo()['chat_name'],file));return True
        modules={'wxauto_connection':SimpleNamespace(connect=lambda:main),
                 'native_voice':SimpleNamespace(NativeVoice=Voice),
                 'social_wxauto_backend':SimpleNamespace(running_client=lambda wx:{'clientVersion':'4.2.0.1000'}),
                 'social_ipc':SimpleNamespace(SocialDispatcher=lambda config:None,OPS=set()),
                 'wxauto4.msgs.msg':SimpleNamespace(parse_msg=parse)}
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root=Path(directory);config_dir=root/'config';config_dir.mkdir()
            config=config_dir/'config.json';claims=config_dir/'claims.json'
            config.write_text(json.dumps({'target':'主人','groups':['测试群'],'statePath':str(claims),
                'dataRoot':str(root),'receiveFiles':False,'independentWindows':{'enabled':True}},ensure_ascii=False),encoding='utf8')
            if receipt:
                value=live_fixture_receipt();value['checks']['nativeVoiceSend']=True
                (config_dir/'independent-live-acceptance.json').write_text(json.dumps(value,ensure_ascii=False),encoding='utf8')
            output=io.StringIO();original_emit=worker.emit
            def emit(event):
                original_emit(event)
                if on_event:on_event(event,main)
            with patch.dict(sys.modules,modules),patch.object(sys,'argv',['worker.py',str(config)]), \
                 patch.object(sys,'stdin',io.StringIO('\n'.join(json.dumps(c,ensure_ascii=False) for c in commands))), \
                 patch.object(worker,'OUT',output),patch.object(worker,'emit',side_effect=emit), \
                 patch.object(worker.threading,'Thread',lambda target,**kwargs:SimpleNamespace(start=target)), \
                 patch('importlib.metadata.version',return_value='41.1.7'), \
                 patch.object(worker,'meme_file',return_value=Path('synthetic-meme.png')),patch('message_watch.time.sleep'):
                worker.main()
            persisted=json.loads(claims.read_text(encoding='utf8')) if claims.exists() else {'claims':[]}
            events=[json.loads(line) for line in output.getvalue().splitlines()]
        return main,actions,events,persisted

    def test_text_image_and_voice_use_each_bound_chat_and_never_main(self):
        commands=[]
        for index,target in enumerate(ROUTES):
            reply='r'+str(index)
            commands.extend([{'op':'begin-reply','id':reply+':begin','replyId':reply,'target':target},
                {'op':'send','id':reply+':text','replyId':reply,'target':target,'text':'你好'},
                {'op':'send-file','id':reply+':file','replyId':reply,'target':target,'filepath':'synthetic-meme.png'},
                {'op':'send-voice','id':reply+':voice','replyId':reply,'target':target,'filepath':'synthetic-voice.wav',
                 'kind':'untrusted-kind','voiceContext':{'target':'untrusted alternate target','commandId':'forged'}},
                {'op':'end-reply','id':reply+':end','replyId':reply,'target':target}])
        main,actions,events,_=self.run_worker(commands)
        acks=[e for e in events if e['event']=='ack']
        self.assertEqual(len(acks),len(commands));self.assertTrue(all(e['ok'] for e in acks),acks)
        self.assertEqual([(a[0],a[1]) for a in actions],[('text','主人'),('file','主人'),('voice','主人'),('text','测试群'),('file','测试群'),('voice','测试群')])
        self.assertEqual(main.switches,[]);self.assertEqual(main.sessions_called,0)
        ready=next(e for e in events if e['event']=='ready')
        self.assertEqual(ready['capabilities']['windowMonitor']['mode'],'independent')

    def test_closed_or_renamed_window_after_begin_prevents_send_and_claim(self):
        commands=[{'op':'begin-reply','id':'begin','replyId':'r','target':'测试群'},
                  {'op':'send','id':'send','replyId':'r','target':'测试群','text':'不得误投递'},
                  {'op':'end-reply','id':'end','replyId':'r','target':'测试群'}]
        for failure in ('closed','renamed'):
            def after(event,main):
                if event.get('id')=='begin' and event.get('ok'):
                    if failure=='closed':main.chats[1]._api.control.alive=False
                    else:main.chats[1].info['chat_name']='其他聊天'
            main,actions,events,persisted=self.run_worker(commands,on_event=after)
            send=next(e for e in events if e.get('id')=='send')
            self.assertFalse(send['ok']);self.assertTrue(send['code'].startswith('independent-window-'))
            self.assertEqual(actions,[]);self.assertNotIn('send',persisted['claims']);self.assertEqual(main.switches,[])

    def test_enabled_without_live_receipt_keeps_old_main_mode(self):
        main,actions,events,_=self.run_worker([],receipt=False)
        ready=next(e for e in events if e['event']=='ready')
        self.assertEqual(ready['capabilities']['windowMonitor']['mode'],'main')
        self.assertEqual(main.enumerated,0);self.assertEqual(actions,[])


if __name__=='__main__':unittest.main()
