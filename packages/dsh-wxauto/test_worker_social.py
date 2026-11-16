"""Exercise actual worker.main JSONL routing with fake wxauto, never real UI."""
import copy
from contextlib import nullcontext
import io
import json
from pathlib import Path
import queue
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import worker
from social_actions import ActionContext
from social_ipc import SocialDispatcher


class WorkerSocialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.config = {'target': 'owner', 'groups': ['group-a'],
            'statePath': str(self.base / 'claims.json'), 'dataRoot': str(self.base / 'data')}
        self.owner = ActionContext('friend', 'owner', True)

    def dispatcher(self, backend=None):
        return SocialDispatcher(self.config, backend)

    def run_worker(self, steps, *, inbound=None, dispatcher=None, data_root=True, on_emit=None, independent=False,
                   clock_step=None, deferred_direction=None, queued_commands=0):
        config = copy.deepcopy(self.config)
        if independent: config['independentWindows']={'enabled':True}
        if not data_root:
            config.pop('dataRoot')
        config_file = self.base / 'config.json'
        config_file.write_text(json.dumps(config), encoding='utf8')
        iterator = iter(steps)
        messages = copy.deepcopy(inbound or {})
        operations = []
        current = {'target': 'owner'}
        fake_clock = {'now':0.0}
        class Input:
            def get(self, timeout):
                if clock_step is not None:fake_clock['now'] += clock_step
                command = next(iterator, {'op': 'stop'})
                if command is None:
                    raise queue.Empty()
                return command
            def empty(self): return True
            def qsize(self):return queued_commands
        class Monitor:
            def __init__(self, *args, **kwargs):
                if deferred_direction is not None:self.deferred_direction=deferred_direction
            def initialize(self): pass
            def select(self, target):
                current['target'] = target
                operations.append(('select', target))
            def candidates(self): operations.append(('poll',)); return list(messages)
            def read(self, target): return messages.pop(target)
            def note_outbound(self, target): pass
        wx = SimpleNamespace(ChatInfo=lambda: {'chat_name': current['target'],
            'chat_type': 'friend' if current['target'] == 'owner' else 'group'},
            SendMsg=lambda text, **kwargs: operations.append(('send', current['target'], text)) or True)
        modules = {'wxauto_connection': SimpleNamespace(connect=lambda: wx),
            'message_watch': SimpleNamespace(MainWindowMonitor=Monitor,
                event_key=lambda run, target, identity: 'evt:' + target + ':' + identity),
            'native_voice': SimpleNamespace(NativeVoice=lambda *args: SimpleNamespace(ready=lambda: False))}
        if independent:
            modules['independent_windows']=SimpleNamespace(create_window_monitor=lambda *a,**kw:
                SimpleNamespace(initialize=lambda:Monitor(),status=lambda:{'mode':'independent'}))
        output = io.StringIO()
        original_emit = worker.emit
        from social_wxauto_backend import WxautoSocialBackend
        def emit(event):
            if on_emit:
                on_emit(event)
            original_emit(event)
        # The normal reader thread only queues input. Here its queue is a
        # deterministic test fixture, so no real thread or stdin is required.
        with patch.dict(sys.modules, modules), patch.object(sys, 'argv', ['worker.py', str(config_file)]), \
                patch.object(worker.time,'monotonic',lambda:fake_clock['now']) if clock_step is not None else nullcontext(), \
                patch.object(worker, 'OUT', output), patch.object(worker, 'emit', emit), \
                patch.object(worker.queue, 'Queue', Input), \
                patch.object(worker.threading, 'Thread', lambda *args, **kwargs: SimpleNamespace(start=lambda: None)), \
                patch('social_wxauto_backend.WxautoSocialBackend', side_effect=lambda *args,**kw:
                    dispatcher.backend if dispatcher else WxautoSocialBackend(*args,**kw)), \
                patch('social_ipc.SocialDispatcher', side_effect=lambda config: dispatcher or SocialDispatcher(config)) as factory:
            worker.main()
            factory_calls = factory.call_count
        return [json.loads(line) for line in output.getvalue().splitlines()], operations, factory_calls

    def test_active_reply_emits_heartbeat_without_receive_poll_or_text(self):
        commands=[{'op':'begin-reply','id':'r:begin','replyId':'r','target':'owner'},None,None,
            {'op':'end-reply','id':'r:end','replyId':'r','target':'owner'},None]
        events,operations,_=self.run_worker(commands,clock_step=11,queued_commands=3,
            deferred_direction={'owner':{'private-message-id':{'text':'never-export-this'},'other':{}},
                                'group-a':{'g':{}},'unregistered-private':{'x':{}}})
        heartbeats=[e for e in events if e['event']=='heartbeat']
        active=[e for e in heartbeats if e['deliveryActive']]
        self.assertGreaterEqual(len(active),2)
        self.assertTrue(all(e['polls']==0 for e in active))
        self.assertTrue(all(e['queuedCommands']==3 for e in active))
        self.assertTrue(all(e['deferredDirectionRows']=={'owner':2,'group-a':1} for e in active))
        self.assertNotIn('never-export-this',json.dumps(heartbeats))
        self.assertNotIn('private-message-id',json.dumps(heartbeats))
        self.assertNotIn('unregistered-private',json.dumps(heartbeats))
        self.assertTrue(any(not e['deliveryActive'] and e['polls']>0 for e in heartbeats))
        self.assertEqual(sum(o==('poll',) for o in operations),1)

    def test_normal_poll_heartbeat_retains_ten_second_cadence(self):
        events,operations,_=self.run_worker([None]*5,clock_step=4)
        heartbeats=[e for e in events if e['event']=='heartbeat']
        self.assertEqual(len(heartbeats),1)
        self.assertEqual(heartbeats[0]['polls'],3)
        self.assertFalse(heartbeats[0]['deliveryActive'])
        self.assertEqual(heartbeats[0]['queuedCommands'],0)
        self.assertEqual(heartbeats[0]['deferredDirectionRows'],{})

    @staticmethod
    def approve(plan, *, target='owner', message_id='evt:owner:owner-approval'):
        return {'op': 'social-approve', 'id': 'social:approve', 'target': target,
                'planId': plan['id'], 'hash': plan['hash'], 'approvalMessageId': message_id}

    @staticmethod
    def execute(plan):
        return {'op': 'social-execute', 'id': 'social:execute', 'target': 'owner',
                'planId': plan['id'], 'hash': plan['hash']}

    def test_owner_private_text_is_observed_before_emit_and_ipc_approval_works(self):
        social = self.dispatcher()
        plan = social.queue.draft('add_friend', {'keywords': 'new-friend'}, self.owner)
        record = {'id': 'owner-approval', 'sender': 'owner', 'mediaType': 'text',
                  'text': '批准操作 ' + plan['id'] + ' ' + plan['hash']}
        observed = []
        def check(event):
            if event['event'] == 'message':
                self.assertIn(event['id'], social._approvals)
                observed.append(event['id'])
        events, operations, _ = self.run_worker([None, self.approve(plan), self.execute(plan)],
            inbound={'owner': [record]}, dispatcher=social, on_emit=check)
        self.assertEqual(observed, ['evt:owner:owner-approval'])
        results = [event for event in events if event['event'] == 'social-result']
        self.assertEqual(results[0]['value']['status'], 'approved')
        self.assertEqual(results[0]['value']['approvalMessageId'], observed[0])
        self.assertEqual(results[1]['value']['executionStatus'], 'unverified')
        self.assertFalse(any(operation[0] == 'send' for operation in operations))

    def test_ipc_cannot_invent_owner_approval_and_unknown_backend_is_not_ready(self):
        social = self.dispatcher()
        plan = social.queue.draft('add_friend', {'keywords': 'new-friend'}, self.owner)
        events, _, _ = self.run_worker([self.approve(plan), {'op': 'social-status', 'id': 'status', 'target': 'owner'}], dispatcher=social)
        results = [event for event in events if event['event'] == 'social-result']
        self.assertFalse(results[0]['ok'])
        self.assertEqual(results[0]['code'], 'social-approval-grant-missing')
        self.assertFalse(results[1]['value']['nativeBackendReady'])
        self.assertEqual(social.queue.inspect(plan['id'], self.owner)['status'], 'pending')

    def test_group_and_private_image_approval_text_do_not_create_grants(self):
        for source, media_type in [('group-a', 'text'), ('owner', 'image'), ('owner', 'quote')]:
            with self.subTest(source=source, media_type=media_type):
                social = self.dispatcher()
                plan = social.queue.draft('add_friend', {'keywords': 'new-friend'}, self.owner)
                record = {'id': 'owner-approval', 'sender': 'owner', 'mediaType': media_type,
                          'text': '批准操作 ' + plan['id'] + ' ' + plan['hash']}
                events, _, _ = self.run_worker([None, self.approve(plan)], inbound={source: [record]}, dispatcher=social)
                result = next(event for event in events if event['event'] == 'social-result')
                self.assertFalse(result['ok'])
                self.assertEqual(result['code'], 'social-approval-grant-missing')
                self.assertEqual(social.queue.inspect(plan['id'], self.owner)['status'], 'pending')

    def test_reply_transaction_blocks_social_execution_before_backend_dispatch(self):
        calls = []
        backend = SimpleNamespace(capabilities=lambda: {'clientVersion': '4.2.0.1000',
            'actions': {'publish_moment': {'clientVersion': '4.2.0.1000', 'liveVerified': True}}},
            execute=lambda *args, **kwargs: calls.append(args) or {'status': 'confirmed', 'receipt': 'would-send'})
        social = self.dispatcher(backend)
        plan = social.queue.draft('publish_moment', {'text': 'hello'}, self.owner)
        commands = [{'op': 'begin-reply', 'id': 'r:begin', 'replyId': 'r', 'target': 'owner'},
            self.execute(plan), {'op': 'end-reply', 'id': 'r:end', 'replyId': 'r', 'target': 'owner'}]
        events, _, _ = self.run_worker(commands, dispatcher=social)
        result = next(event for event in events if event['event'] == 'social-result')
        self.assertFalse(result['ok'])
        self.assertEqual(result['code'], 'social-reply-in-progress')
        self.assertEqual(calls, [])
        self.assertEqual(social.queue.inspect(plan['id'], self.owner)['status'], 'ready')

    def test_legacy_configuration_has_explicit_unavailable_without_breaking_text_send(self):
        commands = [{'op': 'social-status', 'id': 'social:legacy', 'target': 'owner'},
            {'op': 'begin-reply', 'id': 'r:begin', 'replyId': 'r', 'target': 'owner'},
            {'op': 'send', 'id': 'r:1', 'replyId': 'r', 'target': 'owner', 'text': 'normal reply'},
            {'op': 'end-reply', 'id': 'r:end', 'replyId': 'r', 'target': 'owner'}]
        events, operations, factory_calls = self.run_worker(commands, data_root=False)
        self.assertEqual(factory_calls, 0)
        result = next(event for event in events if event['event'] == 'social-result')
        self.assertFalse(result['ok'])
        self.assertEqual(result['code'], 'social-unavailable')
        self.assertIn(('send', 'owner', 'normal reply'), operations)
        self.assertTrue(all(event['ok'] for event in events if event['event'] == 'ack'))

    def test_confirmed_social_reply_precedes_block_and_no_queued_send_or_poll_runs(self):
        backend=SimpleNamespace(chat_binding_failed=False,last_chat_recovery=None,
            capabilities=lambda:{'clientVersion':'4.2.0.1000','actions':{
                'publish_moment':{'clientVersion':'4.2.0.1000','liveVerified':True}}})
        def execute(*args,**kwargs):
            backend.chat_binding_failed=True
            backend.last_chat_recovery={'restored':False,'targetKind':'friend',
                'usedOwnerFallback':False,'code':'social-chat-restore-failed'}
            return {'status':'confirmed','receipt':{'synthetic':True}}
        backend.execute=execute
        social=self.dispatcher(backend)
        plan=social.queue.draft('publish_moment',{'text':'fixture'},self.owner)
        events,operations,_=self.run_worker([self.execute(plan),None,
            {'op':'begin-reply','id':'later','replyId':'later','target':'owner'},
            {'op':'send','id':'send','replyId':'later','target':'owner','text':'must not send'}],
            dispatcher=social,inbound={'owner':[{'id':'unread','sender':'owner','mediaType':'text','text':'fixture'}]})
        reply=next(e for e in events if e['event']=='social-result')
        self.assertEqual(reply['value']['status'],'confirmed')
        self.assertFalse(reply['value']['chatRecovery']['restored'])
        blocked=next(e for e in events if e['event']=='connection-blocked')
        self.assertEqual(blocked['code'],'social-chat-restore-failed')
        self.assertLess(events.index(reply),events.index(blocked))
        self.assertEqual(operations,[])
        self.assertFalse(any(e['event'] in ('message','ack') for e in events))

    def test_independent_windows_refuse_unverified_social_navigation(self):
        calls=[]
        backend=SimpleNamespace(capabilities=lambda:{'clientVersion':'fixture','actions':{}},
            execute=lambda *a,**kw:calls.append('must not execute'))
        social=self.dispatcher(backend)
        plan=social.queue.draft('publish_moment',{'text':'fixture'},self.owner)
        events,_,_=self.run_worker([self.execute(plan)],dispatcher=social,independent=True)
        reply=next(e for e in events if e['event']=='social-result')
        self.assertFalse(reply['ok'])
        self.assertEqual(reply['code'],'social-independent-window-unverified')
        self.assertEqual(calls,[])
        self.assertEqual(social.queue.inspect(plan['id'],self.owner)['status'],'ready')

    def test_observe_main_dispatch_is_owner_only_and_never_grants_write_capability(self):
        calls=[]
        backend=SimpleNamespace(capabilities=lambda:{'clientVersion':'fixture','actions':{}},
            observe=lambda purpose:calls.append(purpose) or {'purpose':purpose,'readOnly':True,'chatRestored':True})
        social=self.dispatcher(backend)
        command={'op':'social-observe','id':'observe','target':'owner','purpose':'friend-search-owner'}
        events,operations,_=self.run_worker([command,{**command,'id':'group','target':'group-a'},
            {'op':'social-status','id':'status','target':'owner'}],dispatcher=social)
        replies={e['id']:e for e in events if e['event']=='social-result'}
        self.assertTrue(replies['observe']['ok'])
        self.assertFalse(replies['group']['ok'])
        self.assertEqual(replies['group']['code'],'social-owner-private-required')
        self.assertTrue(all(not v['liveVerified'] for v in replies['status']['value']['actions'].values()))
        self.assertEqual(calls,['friend-search-owner'])
        self.assertEqual(operations,[])

    def test_observe_is_refused_during_reply_and_in_independent_mode(self):
        for independent in (False,True):
            calls=[]
            social=self.dispatcher(SimpleNamespace(observe=lambda purpose:calls.append(purpose)))
            command={'op':'social-observe','id':'observe','target':'owner','purpose':'friend-search-owner'}
            begin=[] if independent else [{'op':'begin-reply','id':'begin','replyId':'r','target':'owner'}]
            events,_,_=self.run_worker(begin+[command],dispatcher=social,independent=independent)
            reply=next(e for e in events if e['event']=='social-result')
            self.assertFalse(reply['ok'])
            self.assertEqual(calls,[])

    def test_observe_restore_failure_stops_worker_before_next_send_or_poll(self):
        backend=SimpleNamespace(chat_binding_failed=False)
        def observe(purpose):
            backend.chat_binding_failed=True
            raise RuntimeError('social-chat-restore-failed')
        backend.observe=observe
        social=self.dispatcher(backend)
        events,operations,_=self.run_worker([
            {'op':'social-observe','id':'observe','target':'owner','purpose':'friend-search-owner'},
            None,{'op':'begin-reply','id':'later','replyId':'later','target':'owner'}],dispatcher=social)
        self.assertFalse(next(e for e in events if e['event']=='social-result')['ok'])
        self.assertEqual(next(e for e in events if e['event']=='connection-blocked')['code'],'social-chat-restore-failed')
        self.assertFalse(any(e['event']=='ack' for e in events))
        self.assertEqual(operations,[])


    def test_initial_friend_acceptance_uses_reply_and_independent_window_guards(self):
        command={'op':'social-acceptance','id':'initial','target':'owner',
            'purpose':'add-friend-initial-contact','planId':'a'*32,'hash':'b'*64}
        for independent in (False, True):
            calls=[]
            backend=SimpleNamespace(accept_friend_initial=lambda **kwargs:calls.append(kwargs))
            begin=[] if independent else [{'op':'begin-reply','id':'begin','replyId':'r','target':'owner'}]
            events,_,_=self.run_worker(begin+[command],dispatcher=self.dispatcher(backend),independent=independent)
            response=next(e for e in events if e['event']=='social-result')
            self.assertFalse(response['ok']);self.assertEqual(calls,[])

    def test_initial_acceptance_unknown_result_precedes_block_and_stops_later_send_poll(self):
        backend=SimpleNamespace(chat_binding_failed=False)
        def accept(**kwargs):
            backend.chat_binding_failed=True
            return {'id':kwargs['plan_id'],'hash':kwargs['plan_hash'],'status':'unknown',
                'executionStatus':'dispatched','chatRestored':False}
        backend.accept_friend_initial=accept
        command={'op':'social-acceptance','id':'initial','target':'owner',
            'purpose':'add-friend-initial-contact','planId':'a'*32,'hash':'b'*64}
        events,operations,_=self.run_worker([command,None,
            {'op':'begin-reply','id':'later','replyId':'later','target':'owner'}],dispatcher=self.dispatcher(backend))
        response=next(e for e in events if e['event']=='social-result')
        blocked=next(e for e in events if e['event']=='connection-blocked')
        self.assertTrue(response['ok']);self.assertEqual(response['value']['status'],'unknown')
        self.assertLess(events.index(response),events.index(blocked))
        self.assertFalse(any(e['event']=='ack' for e in events));self.assertEqual(operations,[])

if __name__ == '__main__':
    unittest.main()
