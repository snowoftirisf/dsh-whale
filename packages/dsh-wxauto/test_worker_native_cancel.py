"""Private diagnostic IPC tests: fake UI/player only; never operate WeChat."""
import io
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
import wave
from unittest.mock import patch

import worker


class NativeCancelWorkerTests(unittest.TestCase):
    def run_worker(self, commands, *, gate=None, outcome=None, mismatch=False, inspection_texts=(), menu_error=False):
        actions=[]
        state={'target':'owner'}
        class Monitor:
            def __init__(self,*args,**kwargs):pass
            def initialize(self):pass
            def select(self,target):
                actions.append(('select',target));state['target']=target
            def candidates(self):return []
            def read(self,target):return []
            def note_outbound(self,target):actions.append(('outbound',target))
        class Voice:
            def __init__(self,wx,config):pass
            def ready(self):return True
            def _record(self,file,**kwargs):
                actions.append(('record',file,kwargs))
                if isinstance(outcome,Exception):raise outcome
                return {'canceled':True} if outcome is None else outcome
            def send(self,file,**kwargs):
                actions.append(('send',file,kwargs));return True
        class Message:
            type='text';attr='friend'
            def __init__(self,text):self.content=text
            @property
            def sender(self):actions.append(('sender-read',));return 'private sender never exported'
        controls=[NS(ClassName='mmui::ChatTextItemView',message=Message(text)) for text in inspection_texts]
        wx=NS(ChatInfo=lambda:{'chat_name':'wrong' if mismatch else state['target'],
                               'chat_type':'friend' if state['target']=='owner' else 'group'},
            _api=NS(control=object(),_chat_api=NS(msgbox=NS(GetChildren=lambda:controls))))
        class MenuError(RuntimeError):
            facts={}
        def close_menu(root,uia):
            actions.append(('close-menu',))
            if menu_error:raise MenuError('avatar-menu-close-unconfirmed')
            return {'closed':False,'menuCount':0}
        modules={'wxauto_connection':NS(connect=lambda:wx),
            'message_watch':NS(MainWindowMonitor=Monitor,event_key=lambda *args:'unused'),
            'native_voice':NS(NativeVoice=Voice),
            'social_ipc':NS(SocialDispatcher=lambda config:None,OPS=set()),
            'wxauto4':NS(uia=object()),'wxauto4.msgs.msg':NS(parse_msg=lambda control,parent:control.message),
            'native_voice_menus':NS(close_avatar_context_menu=close_menu,MenuCleanupError=MenuError)}
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root=Path(directory)
            config=root/'worker.json'
            config.write_text(json.dumps({'target':'owner','groups':['group'],'dataRoot':str(root),
                'statePath':str(root/'receipts.json')}),encoding='utf8')
            output=io.StringIO()
            environment=dict(os.environ)
            environment.pop('WHALE_NATIVE_CANCEL_DIAGNOSTIC',None)
            if gate is not None:environment['WHALE_NATIVE_CANCEL_DIAGNOSTIC']=gate
            with patch.dict(os.environ,environment,clear=True),patch.dict(sys.modules,modules), \
                    patch.object(sys,'argv',['worker.py',str(config)]), \
                    patch.object(sys,'stdin',io.StringIO('\n'.join(json.dumps(c) for c in commands))), \
                    patch.object(worker,'OUT',output), \
                    patch.object(worker.threading,'Thread',lambda target,**kwargs:NS(start=target)):
                worker.main()
            events=[json.loads(line) for line in output.getvalue().splitlines()]
            claims=json.loads((root/'receipts.json').read_text('utf8')) if (root/'receipts.json').exists() else {'claims':[]}
        return events,actions,claims

    def commands(self,**overrides):
        probe={'op':'probe-native-voice','id':'r:probe','replyId':'r','target':'group',
               'filepath':'fixture.wav','diagnosticCancelOnly':True,**overrides}
        return [{'op':'begin-reply','id':'r:begin','replyId':'r','target':probe['target']},probe,
                {'op':'end-reply','id':'r:end','replyId':'r','target':probe['target']}, {'op':'stop'}]

    def test_default_disabled_and_both_gates_required(self):
        for gate,flag in [(None,True),('0',True),('1',False),('1',1),('1','true')]:
            with self.subTest(gate=gate,flag=flag):
                events,actions,claims=self.run_worker(self.commands(diagnosticCancelOnly=flag),gate=gate)
                ack=next(e for e in events if e.get('id')=='r:probe')
                self.assertFalse(ack['ok'])
                self.assertEqual(ack['code'],'native-cancel-diagnostic-disabled')
                self.assertFalse(any(a[0] in ('record','send') for a in actions))
                self.assertEqual(claims['claims'],[])

    def test_enabled_probe_uses_trusted_route_and_never_send_or_outbound(self):
        events,actions,claims=self.run_worker(self.commands(context={'target':'forged','kind':'friend'},kind='friend'),gate='1')
        ack=next(e for e in events if e.get('id')=='r:probe')
        self.assertEqual(ack,{'event':'ack','id':'r:probe','ok':True,'canceled':True})
        record=next(a for a in actions if a[0]=='record')
        self.assertEqual(record[2],{'confirm_send':False,
            'context':{'target':'group','kind':'group','commandId':'r:probe'}})
        self.assertFalse(any(a[0] in ('send','outbound') for a in actions))
        self.assertEqual(claims['claims'],['r:probe'])

    def test_route_exact_chat_and_reply_boundary_are_not_bypassed(self):
        for commands,mismatch in [(self.commands(target='stranger'),False),
                                  (self.commands(),True),
                                  ([self.commands()[1],{'op':'stop'}],False)]:
            with self.subTest(mismatch=mismatch,commands=commands):
                events,actions,claims=self.run_worker(commands,gate='1',mismatch=mismatch)
                self.assertTrue(any(e.get('id')=='r:probe' and not e['ok'] for e in events))
                self.assertFalse(any(a[0] in ('record','send') for a in actions))
                self.assertEqual(claims['claims'],[])

    def test_uncertain_cancellation_is_not_success_and_cannot_repeat_claim(self):
        for outcome in ({'canceled':False},{'canceled':1},True,RuntimeError('fixture-cancel-unknown')):
            with self.subTest(outcome=outcome):
                commands=self.commands()
                commands.insert(2,dict(commands[1]))
                events,actions,claims=self.run_worker(commands,gate='1',outcome=outcome)
                acks=[e for e in events if e.get('id')=='r:probe']
                self.assertTrue(all(not e['ok'] for e in acks))
                self.assertEqual(acks[1]['code'],'already-claimed')
                self.assertEqual(sum(a[0]=='record' for a in actions),1)
                self.assertFalse(any(a[0] in ('send','outbound') for a in actions))

    def test_inspection_reads_one_matching_sender_without_emitting_a_turn(self):
        text='unique incoming diagnostic text'
        digest=hashlib.sha256(text.encode()).hexdigest()
        events,actions,_=self.run_worker(self.commands(inspectIncomingTextSha256=digest,dismissAvatarMenu=True),
            gate='1',inspection_texts=['unrelated',text])
        ack=next(e for e in events if e.get('id')=='r:probe')
        self.assertTrue(ack['canceled'])
        self.assertEqual(ack['inspection'],{'matchedCount':1,'sha256':digest})
        self.assertEqual([a[0] for a in actions if a[0] in ('sender-read','close-menu','record')],
                         ['sender-read','close-menu','record'])
        self.assertFalse(any(e['event']=='message' for e in events))
        serialized=json.dumps(events)
        self.assertNotIn(text,serialized)
        self.assertNotIn('private sender',serialized)

    def test_inspection_must_be_unique_and_does_not_read_sender_on_ambiguity(self):
        text='ambiguous';digest=hashlib.sha256(text.encode()).hexdigest()
        for texts in [(),(text,text)]:
            with self.subTest(count=len(texts)):
                events,actions,_=self.run_worker(self.commands(inspectIncomingTextSha256=digest),gate='1',inspection_texts=texts)
                ack=next(e for e in events if e.get('id')=='r:probe')
                self.assertFalse(ack['ok'])
                self.assertEqual(ack['inspection'],{'matchedCount':len(texts),'sha256':digest})
                self.assertFalse(any(a[0] in ('sender-read','record','send') for a in actions))

    def test_cleanup_unknown_stops_before_recording_and_normal_voice_never_uses_helper(self):
        events,actions,_=self.run_worker(self.commands(dismissAvatarMenu=True),gate='1',menu_error=True)
        self.assertEqual(next(e for e in events if e.get('id')=='r:probe')['code'],'avatar-menu-close-unconfirmed')
        self.assertFalse(any(a[0] in ('record','send') for a in actions))
        events,actions,_=self.run_worker(self.commands(op='send-voice',dismissAvatarMenu=True))
        self.assertTrue(next(e for e in events if e.get('id')=='r:probe')['ok'])
        self.assertTrue(any(a[0]=='send' for a in actions))
        self.assertFalse(any(a[0] in ('close-menu','record','sender-read') for a in actions))


class CancelProbeToolTests(unittest.TestCase):
    def setUp(self):
        file=Path(__file__).resolve().parents[2]/'tools/probe_worker_native_voice_cancel.py'
        spec=importlib.util.spec_from_file_location('_cancel_probe_fixture',file)
        self.tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.tool)
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/'source'
        (self.source/'config').mkdir(parents=True)
        (self.source/'voice/outbox').mkdir(parents=True)
        (self.source/'config/wxauto.local.json').write_text(json.dumps(
            {'target':'owner','groups':['group'],'secretMustNotCopy':'private-secret'}),encoding='utf8')
        (self.source/'voice/native-wechat.local.json').write_text(json.dumps({'verified':True}),encoding='utf8')
        with wave.open(str(self.source/'voice/outbox'/self.tool.SAMPLE),'wb') as audio:
            audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(24000)
            audio.writeframes(b'\0\0'*40800)

    def test_pause_guard_requires_explicit_false(self):
        for value in (True,None,0):
            with self.subTest(value=value),patch.object(self.tool,'load_json',return_value={'ready':value}):
                with self.assertRaisesRegex(RuntimeError,'explicitly-paused'):
                    self.tool.require_paused()
        with patch.object(self.tool,'load_json',return_value={'ready':False}):
            self.tool.require_paused()

    def test_fixture_does_not_copy_production_receipts_or_secrets(self):
        with patch.object(self.tool,'ROOT',self.root):
            folder,config,sample,duration=self.tool.prepare_fixture(self.source,'group','group')
        self.assertAlmostEqual(duration,1.7)
        value=json.loads(config.read_text('utf8'))
        self.assertEqual(value['groups'],['group'])
        self.assertNotIn('secretMustNotCopy',value)
        self.assertFalse(Path(value['statePath']).exists())
        self.assertEqual(sample.read_bytes(),(self.source/'voice/outbox'/self.tool.SAMPLE).read_bytes())
        self.assertTrue(folder.is_relative_to(self.root/'artifacts'))

    def test_hidden_worker_protocol_is_cancel_only_and_message_bodies_discarded(self):
        import queue
        events=queue.Queue();events.put(json.dumps({'event':'ready'}))
        commands=[];state={'exit':None}
        class Stdout:
            def __iter__(self):
                while True:
                    line=events.get(timeout=2)
                    if line is None:return
                    yield line
        class Stdin:
            def write(self,line):
                command=json.loads(line);commands.append(command)
                if command['op']=='stop':
                    state['exit']=0;events.put(None);return
                events.put(json.dumps({'event':'message','text':'private body never exported','sender':'private sender'}))
                ack={'event':'ack','id':command['id'],'ok':True}
                if command['op']=='probe-native-voice':ack['canceled']=True
                events.put(json.dumps(ack))
            def flush(self):pass
        process=NS(stdin=Stdin(),stdout=Stdout(),pid=987654,returncode=0,
                   poll=lambda:state['exit'],wait=lambda timeout:0)
        args=NS(source_root=str(self.source),group='group',target='group',inspect_incoming_sha256=None,
                dismiss_avatar_menu=False)
        with patch.object(self.tool,'ROOT',self.root),patch.object(self.tool,'require_paused'), \
                patch.object(self.tool.subprocess,'Popen',return_value=process) as spawn, \
                patch('sys.stdout',io.StringIO()):
            self.assertEqual(self.tool.run_cancel_probe(args),0)
        self.assertEqual([c['op'] for c in commands],['begin-reply','probe-native-voice','end-reply','stop'])
        self.assertTrue(commands[1]['diagnosticCancelOnly'])
        self.assertEqual(spawn.call_args.kwargs['env']['WHALE_NATIVE_CANCEL_DIAGNOSTIC'],'1')
        self.assertEqual(spawn.call_args.kwargs['creationflags'],self.tool.subprocess.CREATE_NO_WINDOW)
        result_file=next((self.root/'artifacts').glob('*/result.local.json'))
        raw=result_file.read_text('utf8');result=json.loads(raw)
        self.assertTrue(result['canceled'])
        self.assertGreaterEqual(result['messageEventsDiscarded'],1)
        self.assertNotIn('private body',raw);self.assertNotIn('private sender',raw)


if __name__=='__main__':unittest.main()
