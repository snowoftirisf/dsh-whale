import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import worker


class WorkerDeliveryTests(unittest.TestCase):
    def test_no_monitor_switch_between_bubbles_and_failure_can_release_lock(self):
        actions = []
        current = {'name':'A'}

        class Monitor:
            def __init__(self, *args, **kwargs): pass
            histories = {}
            def initialize(self): pass
            def read(self, target):
                actions.append(('read',target))
                return []
            def note_outbound(self, target):
                actions.append(('outbound',target))
            def select(self, target):
                current['name'] = target
                actions.append(('select', target))
            def candidates(self):
                actions.append(('poll', None))
                return []

        def send(text, **kwargs):
            actions.append(('send',current['name'],text))
            return text != 'fail'

        wx = SimpleNamespace(ChatInfo=lambda:{'chat_name':current['name'],'chat_type':'group'},SendMsg=send)
        modules = {
            'wxauto_connection':SimpleNamespace(connect=lambda:wx),
            'message_watch':SimpleNamespace(MainWindowMonitor=Monitor,event_key=lambda *args:'id'),
            'native_voice':SimpleNamespace(NativeVoice=lambda *args:SimpleNamespace(ready=lambda:False)),
        }
        commands = []
        for target, texts in [('A',['one','two','fail']),('B',['three'])]:
            reply = 'reply-'+target
            commands.append(dict(op='begin-reply',id=reply+':begin',target=target,replyId=reply))
            for i,text in enumerate(texts):
                commands.append(dict(op='send',id=reply+':'+str(i),target=target,replyId=reply,text=text))
            commands.append(dict(op='end-reply',id=reply+':end',target=target,replyId=reply))
        # Run the stdin reader deterministically; the real implementation only
        # enqueues from that thread, never invoking wxauto there.
        thread = lambda target, **kwargs: SimpleNamespace(start=target)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            config = Path(directory)/'config.json'
            config.write_text(json.dumps({'target':'owner','groups':['A','B'],'statePath':str(Path(directory)/'claims.json')}),encoding='utf-8')
            output = io.StringIO()
            with patch.dict(sys.modules,modules), patch.object(sys,'argv',['worker.py',str(config)]), patch.object(sys,'stdin',io.StringIO('\n'.join(map(json.dumps,commands)))), patch.object(worker,'OUT',output), patch.object(worker.threading,'Thread',thread):
                worker.main()
            acks = [json.loads(line) for line in output.getvalue().splitlines() if json.loads(line)['event']=='ack']
            self.assertEqual(len(acks),len(commands))
            self.assertEqual([ack['id'] for ack in acks if not ack['ok']],['reply-A:2'])
            self.assertEqual([ack.get('code') for ack in acks if not ack['ok']],['send-not-confirmed'],msg=json.dumps(acks))
            self.assertNotIn(('poll',None), actions)
            self.assertEqual([a for a in actions if a[0]=='read'],[])
            self.assertEqual([a for a in actions if a[0]=='send'],[('send','A','one'),('send','A','two'),('send','A','fail'),('send','B','three')])

    def test_three_chat_failures_do_not_exit_or_block_another_chat_and_recover_on_a_later_retry(self):
        import queue
        clock={'value':0};reads={'A':0,'B':0}
        ticks=iter([0,1,10,11,30,31,60])
        empty_error=queue.Empty
        class Input:
            def get(self,timeout):
                try:clock['value']=next(ticks)
                except StopIteration:return {'op':'stop'}
                raise empty_error()
            def empty(self):return True
        class Monitor:
            def __init__(self,*args,**kwargs):pass
            def initialize(self):pass
            def candidates(self):return ['A','B']
            def read(self,name):
                reads[name]+=1
                if name=='A' and reads[name]<=3:raise RuntimeError('new-message-not-visible')
                return [{'id':name,'text':'message '+name,'sender':'member'}]
        modules={
            'wxauto_connection':SimpleNamespace(connect=lambda:object()),
            'message_watch':SimpleNamespace(MainWindowMonitor=Monitor,event_key=lambda run,name,key:name+key),
            'native_voice':SimpleNamespace(NativeVoice=lambda *args:SimpleNamespace(ready=lambda:False)),
        }
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            config=Path(directory)/'config.json'
            config.write_text(json.dumps({'target':'owner','groups':['A','B'],'statePath':str(Path(directory)/'claims.json')}),encoding='utf-8')
            output=io.StringIO()
            with patch.dict(sys.modules,modules),patch.object(sys,'argv',['worker.py',str(config)]),patch.object(worker,'OUT',output),patch.object(worker.queue,'Queue',Input),patch.object(worker.threading,'Thread',lambda *args,**kwargs:SimpleNamespace(start=lambda:None)),patch.object(worker.time,'monotonic',lambda:clock['value']):
                worker.main()
        events=[json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(reads,{'A':4,'B':7})
        self.assertEqual([e['target'] for e in events if e['event']=='message'],['B','A'])
        retries=[e for e in events if e['event']=='chat-read-retrying']
        self.assertEqual([e['retryAfterSeconds'] for e in retries],[10,20,30])
        self.assertTrue(any(e['event']=='chat-read-recovered' and e['target']=='A' for e in events))
        self.assertFalse(any(e['event'] in ('paused','fatal') for e in events))


if __name__ == '__main__':
    unittest.main()
