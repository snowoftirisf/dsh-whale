"""Actual worker.main + registration helper, fake wxauto only; no live UI.

ScriptedIPC exposes the next stdin command on get(), modeling spaced IPC arrivals
so the real main loop can poll between them without timing races or real threads.
"""
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import worker
import message_watch
import social_ipc as social_ipc_module
from attachment_registry import AttachmentRegistry,chat_key
from test_dynamic_group_watch import fixture, Row


ACCOUNT = 'own-account'
NATIVE = 'wechat:12345@chatroom'
GROUP = 'new group'


def route(op, ident, **overrides):
    return {'op': op, 'id': ident, 'target': GROUP, 'accountId': ACCOUNT,
            'nativeChatKey': NATIVE, **overrides}


class AutoGroupWorkerTests(unittest.TestCase):
    def run_worker(self, commands, *, enabled=True, independent=False, fail_restore=False,
                   new_on_register=False,attachments=False,social_dispatcher=None):
        f = fixture()
        actions = []
        f.attachment_checks=[]
        if attachments:
            f.state['kind']['fixed group']='group'
            f.state['rows']['fixed group']=[Row(10,'old fixed')]
            f.state['info']['fixed group']={'isnew':False,'new_count':0,'time':'12:00','content':'old fixed'}
        if fail_restore:
            f.state['fail'] = 'owner'
        f.wx.SendMsg = lambda text, **kwargs: actions.append(('send', f.state['current'], text)) or True
        f.wx.SendFiles = lambda path: actions.append(('file-send', f.state['current'],str(path))) or True
        original_read = f.monitor.read
        def read(name):
            actions.append(('read', name))
            if attachments:
                if f.state.get('attachment_delivered'):return []
                f.state['attachment_delivered']=True
                return [{'id':'actual-file-row','sender':'member','text':'received file',
                    'mediaType':'file','files':[{'path':str(f.attachment_file),'name':'fixture.txt'}]}]
            return original_read(name)
        f.monitor.read = read
        f.monitor.initialize = lambda: None
        f.monitor.message_direction = lambda row: 'friend'
        f.monitor.parse_incoming = lambda row: NS(attr='friend', type='text', content=row.Name, sender='member')
        # Only a genuine post-registration arrival is offered as a candidate.
        f.monitor.candidates = lambda: [GROUP] if GROUP in f.monitor.routes and f.state.get('arrival') else []
        def monitor_factory(wx, routes, **kwargs):
            self.assertIs(wx, f.wx)
            f.monitor.routes = routes
            return f.monitor
        class ScriptedIPC:
            def __init__(self): self.commands = []
            def put(self, command): self.commands.append(command)
            def get(self, timeout=None): return self.commands.pop(0)
            def empty(self): return True  # Later commands are modeled as future IPC arrivals.
        selection = NS(initialize=lambda: f.monitor, status=lambda: {'mode': 'independent'})
        def independent_factory(wx, routes, **kwargs):
            monitor_factory(wx, routes)
            return selection
        modules = {
            'wxauto_connection': NS(connect=lambda: f.wx),
            'message_watch': NS(MainWindowMonitor=monitor_factory,
                time=message_watch.time, control_key=message_watch.control_key,
                signature=message_watch.signature, SessionGate=message_watch.SessionGate,
                event_key=lambda run, target, ident: run + ':' + target + ':' + str(ident)),
            'native_voice': NS(NativeVoice=lambda *args: NS(ready=lambda: False)),
            'social_ipc': NS(SocialDispatcher=lambda config: social_dispatcher,
                OPS=social_ipc_module.OPS,_validated_group_routes=social_ipc_module._validated_group_routes),
            'social_wxauto_backend': NS(running_client=lambda wx: {'clientVersion': 'fixture'},
                WxautoSocialBackend=lambda *args,**kwargs:social_dispatcher.backend,
                load_observed_bindings=lambda *args:{}),
            'independent_windows': NS(create_window_monitor=independent_factory),
        }
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            config = root / 'worker.json'
            settings={'target': 'owner', 'groups': ['fixed group'] if attachments else [], 'accountId': ACCOUNT,
                'statePath': str(root / 'claims.json'), 'autoGroups': {'enabled': enabled},
                'independentWindows': {'enabled': independent}}
            if attachments:
                settings.update(dataRoot=str(root),receiveFiles=True,attachmentDirectory=str(root/'media/attachments'))
                f.attachment_file=root/'media/attachments'/chat_key(GROUP)/'fixture.txt'
                f.attachment_file.parent.mkdir(parents=True);f.attachment_file.write_bytes(b'actual registered fixture bytes')
            if social_dispatcher is not None:
                settings['dataRoot']=str(root)
            config.write_text(json.dumps(settings),encoding='utf8')
            def registry(*args,**kwargs):
                value=AttachmentRegistry(*args,**kwargs);f.attachment_registry=value
                return value
            output = io.StringIO()
            original_emit = worker.emit
            def emit(event):
                original_emit(event)
                if event.get('id'):
                    actions.append(('event', event['id']))
                if attachments and (event['event']=='group-route-result' or event['event']=='message'):
                    registry=f.attachment_registry
                    try:paths=registry.received_paths(GROUP,str(f.attachment_file));code=None
                    except RuntimeError as error:paths=[];code=str(error)
                    f.attachment_checks.append({'id':event.get('id'),'event':event['event'],
                        'groupAuthorized':GROUP in registry.routes,'selected':len(paths),'code':code,
                        'fixedRetained':{'owner','fixed group'}<=registry.routes,
                        'crossGroupSelected':len(registry.received_paths('fixed group',str(f.attachment_file))),
                        'receiptCount':len(registry.data['chats'].get(GROUP,{}))})
                if new_on_register and event.get('id') == 'register' and event.get('ok'):
                    f.state['rows'][GROUP].append(Row(3, 'genuinely new group arrival'))
                    f.state['info'][GROUP].update(isnew=True, new_count=1,
                        content='genuinely new group arrival', time='12:02')
                    f.state['arrival'] = True
            # A null command models the Queue timeout/idle point after each IPC
            # operation, including dispatch branches that intentionally continue.
            materialized=[{**command,**({'filepath':str(f.attachment_file)} if attachments and command.get('filepath')=='fixture-incoming-file' else {})} for command in commands]
            scheduled = [item for command in materialized for item in (command, None)]
            payload = '\n'.join(json.dumps(command) for command in [*scheduled, {'op': 'stop'}])
            with patch.dict(sys.modules, modules), \
                    patch.object(sys, 'argv', ['worker.py', str(config)]), \
                    patch.object(sys, 'stdin', io.StringIO(payload)), \
                    patch.object(worker, 'OUT', output), patch.object(worker, 'emit', side_effect=emit), \
                    patch.object(worker,'AttachmentRegistry',side_effect=registry), \
                    patch.object(worker.queue, 'Queue', ScriptedIPC), \
                    patch.object(worker.threading, 'Thread', lambda target, **kwargs: NS(start=target)), \
                    patch('importlib.metadata.version', return_value='fixture'), \
                    patch('message_watch.time.sleep'):
                worker.main()
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            claims = json.loads((root / 'claims.json').read_text()) if (root / 'claims.json').exists() else {'claims': []}
        return events, actions, f, claims

    def result(self, events, ident):
        return next(event for event in events if event.get('id') == ident)

    def test_registration_starts_suspended_no_receive_or_send_until_enable(self):
        commands = [route('auto-group-register', 'register'),
            {'op': 'begin-reply', 'id': 'blocked-begin', 'target': GROUP, 'replyId': 'blocked'},
            {'op': 'send', 'id': 'blocked-send', 'target': GROUP, 'replyId': 'blocked', 'text': 'blocked'},
            route('auto-group-enable', 'enable'),
            {'op': 'begin-reply', 'id': 'begin', 'target': GROUP, 'replyId': 'r'},
            {'op': 'send', 'id': 'send', 'target': GROUP, 'replyId': 'r', 'text': 'allowed'},
            {'op': 'end-reply', 'id': 'end', 'target': GROUP, 'replyId': 'r'}]
        events, actions, _, claims = self.run_worker(commands, new_on_register=True)
        self.assertTrue(self.result(events, 'register')['ok'])
        self.assertFalse(self.result(events, 'blocked-begin')['ok'])
        self.assertFalse(self.result(events, 'blocked-send')['ok'])
        self.assertTrue(self.result(events, 'enable')['ok'])
        messages = [event for event in events if event['event'] == 'message']
        self.assertEqual(len(messages), 1)
        self.assertGreater(events.index(messages[0]), events.index(self.result(events, 'enable')))
        self.assertEqual(messages[0]['text'], 'genuinely new group arrival')
        first_read = next(index for index, action in enumerate(actions) if action[0] == 'read')
        self.assertGreater(first_read, actions.index(('event', 'enable')))
        self.assertEqual([a for a in actions if a[0] == 'send'], [('send', GROUP, 'allowed')])
        self.assertEqual(claims['claims'], ['send'])

    def test_enable_requires_account_and_native_binding(self):
        commands = [route('auto-group-register', 'register'),
                    route('auto-group-enable', 'wrong-account', accountId='other'),
                    route('auto-group-enable', 'wrong-native', nativeChatKey='wechat:other@chatroom'),
                    {'op': 'begin-reply', 'id': 'blocked', 'target': GROUP, 'replyId': 'r'},
                    route('auto-group-enable', 'enable')]
        events, actions, _, _ = self.run_worker(commands, new_on_register=True)
        for ident in ('wrong-account', 'wrong-native'):
            self.assertEqual(self.result(events, ident)['code'], 'dynamic-group-binding-mismatch')
        self.assertFalse(self.result(events, 'blocked')['ok'])
        message = next(event for event in events if event['event'] == 'message')
        self.assertGreater(events.index(message), events.index(self.result(events, 'enable')))

    def test_suspend_blocks_send_but_end_cleans_active_delivery(self):
        commands = [route('auto-group-register', 'register'), route('auto-group-enable', 'enable'),
            {'op': 'begin-reply', 'id': 'begin', 'target': GROUP, 'replyId': 'r'},
            route('auto-group-suspend', 'suspend'),
            {'op': 'send', 'id': 'blocked', 'target': GROUP, 'replyId': 'r', 'text': 'blocked'},
            {'op': 'end-reply', 'id': 'end', 'target': GROUP, 'replyId': 'r'},
            {'op': 'begin-reply', 'id': 'owner-begin', 'target': 'owner', 'replyId': 'owner-r'},
            {'op': 'end-reply', 'id': 'owner-end', 'target': 'owner', 'replyId': 'owner-r'}]
        events, actions, _, claims = self.run_worker(commands)
        self.assertTrue(self.result(events, 'suspend')['ok'])
        self.assertEqual(self.result(events, 'blocked')['code'], 'target-or-operation-denied')
        self.assertTrue(self.result(events, 'end')['ok'])
        self.assertTrue(self.result(events, 'owner-begin')['ok'])
        self.assertEqual(claims['claims'], [])
        self.assertFalse(any(a[0] == 'send' for a in actions))

    def test_disabled_independent_and_busy_registration_denied(self):
        scenarios = [({'enabled': False}, []), ({'independent': True}, []), ({},
            [{'op': 'begin-reply', 'id': 'busy', 'target': 'owner', 'replyId': 'r'}])]
        for options, prefix in scenarios:
            with self.subTest(options=options, prefix=prefix):
                events, actions, f, _ = self.run_worker([*prefix, route('auto-group-register', 'register')], **options)
                self.assertEqual(self.result(events, 'register')['code'], 'dynamic-group-admission-unavailable')
                self.assertNotIn(GROUP, f.monitor.routes)
                self.assertFalse(any(a[0] == 'send' for a in actions))

    def test_real_helper_restore_failure_stops_worker_before_later_commands(self):
        events, actions, f, _ = self.run_worker([route('auto-group-register', 'register'),
            {'op': 'begin-reply', 'id': 'after-fatal', 'target': 'owner', 'replyId': 'r'}], fail_restore=True)
        self.assertEqual(self.result(events, 'register')['code'], 'dynamic-group-chat-restore-failed')
        blocked = next(event for event in events if event['event'] == 'connection-blocked')
        self.assertEqual(blocked['phase'], 'dynamic-group-recovery')
        self.assertFalse(any(event.get('id') == 'after-fatal' for event in events))
        self.assertNotIn(GROUP, f.monitor.routes)
        self.assertFalse(any(a[0] == 'send' for a in actions))

    def test_dynamic_attachment_authority_follows_verified_enable_and_suspend(self):
        commands=[route('auto-group-register','register'),
            route('auto-group-enable','invalid-account',accountId='wrong'),
            route('auto-group-enable','enable'),
            {'op':'begin-reply','id':'begin','target':GROUP,'replyId':'r'},
            {'op':'send-attachment','id':'file-send','target':GROUP,'replyId':'r','filepath':'fixture-incoming-file'},
            route('auto-group-suspend','suspend'),
            {'op':'send-attachment','id':'suspended-send','target':GROUP,'replyId':'r','filepath':'fixture-incoming-file'},
            {'op':'end-reply','id':'end','target':GROUP,'replyId':'r'},
            route('auto-group-enable','reenable'),
            {'op':'begin-reply','id':'begin2','target':GROUP,'replyId':'r2'},
            {'op':'send-attachment','id':'file-send2','target':GROUP,'replyId':'r2','filepath':'fixture-incoming-file'},
            {'op':'end-reply','id':'end2','target':GROUP,'replyId':'r2'},
            {'op':'begin-reply','id':'cross-begin','target':'fixed group','replyId':'cross'},
            {'op':'send-attachment','id':'cross-send','target':'fixed group','replyId':'cross','filepath':'fixture-incoming-file'},
            {'op':'end-reply','id':'cross-end','target':'fixed group','replyId':'cross'}]
        events,actions,f,claims=self.run_worker(commands,attachments=True,new_on_register=True)
        checks={check['id']:check for check in f.attachment_checks if check['event']=='group-route-result'}
        for ident in ('register','invalid-account','suspend'):
            self.assertFalse(checks[ident]['groupAuthorized'])
            self.assertEqual(checks[ident]['code'],'attachment-target-denied')
        message=next(check for check in f.attachment_checks if check['event']=='message')
        self.assertEqual(message['selected'],1);self.assertEqual(message['receiptCount'],1)
        self.assertEqual(checks['suspend']['receiptCount'],1)
        self.assertTrue(checks['reenable']['groupAuthorized']);self.assertEqual(checks['reenable']['selected'],1)
        self.assertTrue(all(check['fixedRetained'] and check['crossGroupSelected']==0 for check in f.attachment_checks))
        self.assertEqual(self.result(events,'suspended-send')['code'],'target-or-operation-denied')
        self.assertFalse(self.result(events,'cross-send')['ok'])
        self.assertTrue(self.result(events,'file-send')['ok']);self.assertTrue(self.result(events,'file-send2')['ok'])
        self.assertEqual([a[1] for a in actions if a[0]=='file-send'],[GROUP,GROUP])
        self.assertEqual(claims['claims'],['file-send','file-send2'])

    def test_reregister_pending_revokes_dynamic_attachment_access_without_deleting_receipt(self):
        events,_,f,_=self.run_worker([route('auto-group-register','register'),route('auto-group-enable','enable'),
            route('auto-group-register','reregister')],attachments=True,new_on_register=True)
        self.assertTrue(self.result(events,'reregister')['ok'])
        check=next(check for check in f.attachment_checks if check['id']=='reregister')
        self.assertFalse(check['groupAuthorized']);self.assertEqual(check['receiptCount'],1)

    def test_original_fixed_attachment_routes_survive_dynamic_control_of_same_fixed_group(self):
        identity={'target':'fixed group','nativeChatKey':'wechat:fixed@chatroom'}
        events,_,f,_=self.run_worker([route('auto-group-register','fixed-register',**identity),
            route('auto-group-enable','fixed-enable',**identity),
            route('auto-group-suspend','fixed-suspend',**identity)],attachments=True)
        self.assertTrue(all(self.result(events,ident)['ok'] for ident in ('fixed-register','fixed-enable','fixed-suspend')))
        self.assertTrue(all(check['fixedRetained'] for check in f.attachment_checks))
        self.assertEqual(f.attachment_registry.routes,{'owner','fixed group'})

    def test_social_dynamic_group_pending_enable_and_suspend_use_active_verified_scope(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            social=social_ipc_module.SocialDispatcher({'target':'owner','groups':[],'dataRoot':directory})
            commands=[route('auto-group-register','register'),
                {'op':'social-status','id':'pending-status','target':GROUP},
                route('auto-group-enable','wrong-enable',accountId='wrong'),
                {'op':'social-status','id':'wrong-status','target':GROUP},
                route('auto-group-enable','enable'),
                {'op':'social-status','id':'active-status','target':GROUP},
                {'op':'social-draft','id':'group-draft','target':GROUP,'action':'set_group_announcement',
                    'parameters':{'group':GROUP,'text':'draft only'}},
                {'op':'social-draft','id':'owner-draft','target':'owner','action':'set_group_announcement',
                    'parameters':{'group':GROUP,'text':'owner draft only'}},
                route('auto-group-suspend','suspend'),
                {'op':'social-status','id':'suspended-status','target':GROUP},
                route('auto-group-enable','reenable'),
                {'op':'social-status','id':'reenabled-status','target':GROUP}]
            events,actions,_,_=self.run_worker(commands,social_dispatcher=social)
            for ident in ('pending-status','wrong-status','suspended-status'):
                self.assertEqual(self.result(events,ident)['code'],'social-target-denied')
            for ident in ('active-status','group-draft','owner-draft','reenabled-status'):
                self.assertTrue(self.result(events,ident)['ok'],self.result(events,ident))
            self.assertFalse(self.result(events,'active-status')['value']['nativeBackendReady'])
            self.assertEqual(social.groups,frozenset({GROUP}));self.assertEqual(social.queue.groups,social.groups)
            self.assertFalse(any(action[0] in ('send','file-send') for action in actions))

    def test_social_fixed_group_suspend_revokes_social_but_not_original_attachment_route(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            social=social_ipc_module.SocialDispatcher({'target':'owner','groups':['fixed group'],'dataRoot':directory})
            identity={'target':'fixed group','nativeChatKey':'wechat:fixed@chatroom'}
            events,_,f,_=self.run_worker([route('auto-group-register','fixed-register',**identity),
                route('auto-group-enable','fixed-enable',**identity),
                route('auto-group-suspend','fixed-suspend',**identity),
                {'op':'social-status','id':'fixed-status','target':'fixed group'}],attachments=True,social_dispatcher=social)
            self.assertTrue(self.result(events,'fixed-suspend')['ok'])
            self.assertEqual(self.result(events,'fixed-status')['code'],'social-target-denied')
            self.assertIn('fixed group',f.attachment_registry.routes)
            self.assertNotIn('fixed group',social.groups)

    def test_social_sync_failure_stops_before_successful_enable_ack_or_next_command(self):
        class FailingBackend(social_ipc_module.UnverifiedSocialBackend):
            def __init__(self):self.calls=0
            def sync_group_routes(self,groups):
                self.calls+=1
                if self.calls==3:raise RuntimeError('synthetic sync failure')
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            social=social_ipc_module.SocialDispatcher({'target':'owner','groups':[],'dataRoot':directory},FailingBackend())
            events,actions,_,_=self.run_worker([route('auto-group-register','register'),route('auto-group-enable','enable'),
                {'op':'social-status','id':'after-failure','target':GROUP}],social_dispatcher=social)
            self.assertTrue(self.result(events,'register')['ok'])
            self.assertFalse(any(event.get('id') in ('enable','after-failure') for event in events))
            blocked=next(event for event in events if event['event']=='connection-blocked')
            self.assertEqual(blocked['code'],'social-group-route-sync-failed')
            self.assertEqual(social.groups,frozenset());self.assertEqual(social.queue.groups,frozenset())
            self.assertFalse(any(action[0] in ('send','file-send') for action in actions))

    def test_verified_rendering_is_forwarded_only_with_successful_admission(self):
        proof=NS(claim_id='fixture-claim',claim_hash='fixture-hash',
                 rendered_messages=(('wechat-server:123','body: keep ordinary colon'),))
        with patch('native_group_handoff.verify_queued_handoff',return_value=proof), \
                patch('dynamic_group_watch.register_group',return_value={'ok':True}):
            events,actions,_,_=self.run_worker([route('auto-group-register','register',nativeClaimId='fixture-claim')])
        value=self.result(events,'register')['value']
        self.assertEqual(value['nativeClaimId'],proof.claim_id)
        self.assertEqual(value['nativeClaimHash'],proof.claim_hash)
        self.assertEqual(value['nativeRenderedMessages'],[{'nativeId':'wechat-server:123','text':'body: keep ordinary colon'}])
        self.assertFalse(any(a[0]=='send' for a in actions))
        with patch('native_group_handoff.verify_queued_handoff',return_value=proof), \
                patch('dynamic_group_watch.register_group',return_value={'ok':False,'code':'fixture-denied'}):
            events,_,_,_=self.run_worker([route('auto-group-register','register',nativeClaimId='fixture-claim')])
        denied=self.result(events,'register')
        self.assertFalse(denied['ok'])
        self.assertNotIn('value',denied)


if __name__ == '__main__':
    unittest.main()
