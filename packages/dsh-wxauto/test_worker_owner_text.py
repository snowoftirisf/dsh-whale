import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import worker
import worker_owner_text as module
from worker_owner_text import WorkerOwnerTextBridge, WorkerOwnerTextFatal
from native_owner_text_authority import NativeOwnerTextAuthority
import test_worker_social as worker_fixture


def event(identity='message-one', target='owner', kind='friend', media='text'):
    return {'event': 'message', 'id': identity, 'target': target, 'kind': kind, 'mediaType': media,
            'sender': 'fixture', 'text': 'private fixture content', 'images': [], 'files': []}


class WorkerOwnerTextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = {'dataRoot': str(self.root), 'target': 'owner', 'accountId': 'fixture-account',
            'capture': {'enabled': True, 'accountUsername': 'wxid_actor_fixture', 'sourceId': 'fixture'},
            'nativeVoiceSources': {'ownerNativeChatKey': 'wechat:wxid_owner_fixture'}}
        self.published, self.emitted = [], []

    def bridge(self, **kwargs):
        return WorkerOwnerTextBridge(self.config, publish=self.published.append, emit=self.emitted.append, **kwargs)

    def command(self, op, **extra):
        return {'id': 'request-fixture', 'target': 'owner', 'op': 'owner-text-' + op, **extra}

    def test_absent_marker_ui_pass_is_unchanged_and_constructor_does_not_initialize(self):
        bridge = self.bridge()
        original = event()
        bridge.route(original)
        self.assertEqual(self.published, [original])
        self.assertEqual(bridge.status()['mode'], 'ui')
        self.assertFalse((self.root / 'config').exists())
        self.assertEqual(bridge.command(self.command('status'), dispatcher=None)['value']['mode'], 'ui')
        self.assertFalse((self.root / 'config').exists())

    def test_prepare_defers_owner_only_and_abort_drains_original_envelopes_once(self):
        bridge = self.bridge()
        prepared = bridge.command(self.command('prepare'), dispatcher=None)
        self.assertEqual(prepared['value']['version'], 1)
        self.assertIs(prepared['value']['readsStopped'], False)
        ticket = prepared['value']['ticket']
        first, media, group = event(), event('image', media='image'), event('group', 'group-a', 'group')
        bridge.route_batch([first, media, group])
        self.assertEqual(self.published, [media, group])
        result = bridge.command(self.command('abort', ticket=ticket), dispatcher=None)
        self.assertEqual(result['value']['drained'], 1)
        self.assertEqual(result['value']['version'], 1)
        self.assertEqual(self.published, [media, group, first])
        self.assertEqual(self.published[-1], first)
        self.assertFalse(bridge.command(self.command('abort', ticket=ticket), dispatcher=None)['ok'])
        self.assertEqual(len(self.published), 3)

    def test_overflow_fsync_preserves_previous_current_and_unprocessed_batch_before_fatal(self):
        bridge = self.bridge(controller_factory=lambda config: NativeOwnerTextAuthority(config, max_deferred=2))
        bridge.command(self.command('prepare'), dispatcher=None)
        records = [event(str(i)) for i in range(5)]
        with self.assertRaises(WorkerOwnerTextFatal):
            bridge.route_batch(records)
        files = list((self.root / 'config/native-owner-text-deferred').glob('*.json'))
        self.assertEqual(len(files), 1)
        backup = json.loads(files[0].read_text('utf8'))
        self.assertEqual(backup['events'], records)
        self.assertEqual(self.published, [])
        self.assertTrue(bridge.status()['readsStopped'])
        self.assertNotIn('private fixture content', json.dumps(self.emitted))

    def test_persistence_failure_stops_and_does_not_claim_durable_backup(self):
        bridge = self.bridge(controller_factory=lambda config: NativeOwnerTextAuthority(config, max_deferred=1))
        bridge.command(self.command('prepare'), dispatcher=None)
        bridge.route(event())
        with patch.object(bridge, '_persist', side_effect=OSError('private path')), self.assertRaises(WorkerOwnerTextFatal):
            bridge.route(event('overflow'))
        self.assertTrue(bridge.stopped)
        self.assertEqual(self.emitted[-1]['code'], 'owner-text-deferral-persistence-failed')
        self.assertNotIn('recoveryFile', self.emitted[-1])

    def test_shutdown_pending_deferred_is_durable_once_and_ui_stop_is_inert(self):
        bridge = self.bridge()
        bridge.close();self.assertFalse((self.root / 'config').exists())
        bridge = self.bridge();bridge.command(self.command('prepare'), dispatcher=None)
        original = [event('first'), event('second')]
        bridge.route_batch(original);bridge.close();bridge.close()
        files = list((self.root / 'config/native-owner-text-deferred').glob('*.json'))
        self.assertEqual(len(files), 1)
        self.assertEqual(json.loads(files[0].read_text('utf8'))['events'], original)
        self.assertEqual(self.published, [])
        self.assertEqual(self.emitted[-1]['event'], 'owner-text-deferred-saved')

    def test_shutdown_persistence_failure_is_not_reported_saved(self):
        bridge = self.bridge();bridge.command(self.command('prepare'), dispatcher=None);bridge.route(event())
        with patch.object(bridge, '_persist', side_effect=OSError('fixture disk failure')), self.assertRaises(WorkerOwnerTextFatal):
            bridge.close()
        self.assertFalse(bridge.recovery_saved)
        self.assertEqual(self.emitted[-1]['code'], 'owner-text-shutdown-persistence-failed')

    def test_native_suppresses_owner_text_and_emits_content_free_hint(self):
        class NativeFixture(NativeOwnerTextAuthority):
            def restore(instance):
                instance._mode, instance._epoch = 'native', 'a' * 32
        bridge = self.bridge(controller_factory=NativeFixture)
        bridge.route(event())
        self.assertEqual(self.published, [])
        self.assertEqual(self.emitted, [{'event': 'native-owner-text-observed', 'id': 'message-one',
            'target': 'owner', 'kind': 'friend', 'authorityEpoch': 'a' * 32}])
        self.assertEqual(bridge.active_authority(), {'active': True, 'authorityEpoch': 'a' * 32,
            'accountId': 'fixture-account', 'ownerNativeChatKey': 'wechat:wxid_owner_fixture'})

    def test_successful_commit_returns_same_versioned_status_envelope(self):
        class CommitFixture(NativeOwnerTextAuthority):
            def commit(instance, ticket, epoch):
                self.assertEqual(ticket, instance.status()['ticket'])
                instance._mode, instance._epoch, instance._ticket = 'native', epoch, None
                return instance.status()
        bridge = self.bridge(controller_factory=CommitFixture)
        prepared = bridge.command(self.command('prepare'), dispatcher=None)
        committed = bridge.command(self.command('commit', ticket=prepared['value']['ticket'], authorityEpoch='a' * 32), dispatcher=None)
        self.assertTrue(committed['ok'])
        self.assertEqual(committed['value'], bridge.status())
        self.assertEqual(committed['value']['version'], 1)
        self.assertIs(committed['value']['readsStopped'], False)

    def test_private_ops_schema_owner_busy_gate_and_decision_provider(self):
        bridge = self.bridge()
        for command in [{**self.command('status'), 'target': 'group-a'}, {**self.command('status'), 'extra': True},
                        self.command('abort', ticket='not-a-ticket'), {**self.command('status'), 'id': ''}]:
            self.assertFalse(bridge.command(command, dispatcher=None)['ok'])
        for mode in ({'delivery_active': True}, {'independent': True}):
            self.assertEqual(bridge.command(self.command('prepare'), dispatcher=None, **mode)['code'], 'owner-text-worker-busy')
        with patch.object(module, 'apply_native_owner_decision', return_value={'handled': False, 'code': 'fixture-denied'}) as decide:
            result = bridge.command(self.command('decision', claimId='b' * 32, authorityEpoch='a' * 32), dispatcher=object())
            self.assertTrue(result['ok'])
            self.assertFalse(decide.call_args.kwargs['active_authority_provider']()['active'])


class WorkerOwnerTextLoopTests(unittest.TestCase):
    def setUp(self):
        self.fixture = worker_fixture.WorkerSocialTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.config.update(accountId='fixture-account', capture={'enabled': True, 'accountUsername': 'wxid_actor_fixture', 'sourceId': 'fixture'},
                                   nativeVoiceSources={'ownerNativeChatKey': 'wechat:wxid_owner_fixture'})

    def inbound(self, count=1):
        return {'owner': [{'id': str(i), 'text': 'private fixture content', 'sender': 'fixture', 'mediaType': 'text'} for i in range(count)]}

    def test_default_real_restore_preserves_ui_publish_and_ready_heartbeat(self):
        events, _, _ = self.fixture.run_worker([None, None], inbound=self.inbound(), clock_step=11)
        messages = [item for item in events if item['event'] == 'message']
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['text'], 'private fixture content')
        states = [item['ownerTextAuthority'] for item in events if item['event'] in ('ready', 'heartbeat')]
        self.assertTrue(states)
        self.assertTrue(all(item['mode'] == 'ui' and item['version'] == 1 for item in states))

    def test_actual_loop_prepare_then_abort_routes_through_original_social_observer(self):
        shared = {}
        def steps():
            yield {'op': 'owner-text-prepare', 'id': 'prepare', 'target': 'owner'}
            yield None
            yield {'op': 'owner-text-abort', 'id': 'abort', 'target': 'owner', 'ticket': shared['ticket']}
            yield {'op': 'stop'}
        def emitted(item):
            if item.get('event') == 'owner-text-result' and item.get('id') == 'prepare':
                shared['ticket'] = item['value']['ticket']
        dispatcher = self.fixture.dispatcher()
        with patch.object(dispatcher, 'observe_incoming', wraps=dispatcher.observe_incoming) as observe:
            events, _, _ = self.fixture.run_worker(steps(), inbound=self.inbound(), dispatcher=dispatcher, on_emit=emitted)
        self.assertEqual(len([item for item in events if item['event'] == 'message']), 1)
        self.assertEqual(observe.call_count, 1)
        self.assertEqual(observe.call_args.args[0]['id'], 'evt:owner:0')

    def test_actual_loop_stop_preparing_preserves_all_ui_events(self):
        events, _, _ = self.fixture.run_worker([
            {'op': 'owner-text-prepare', 'id': 'prepare', 'target': 'owner'}, None, {'op': 'stop'}], inbound=self.inbound(3))
        files = list((Path(self.fixture.config['dataRoot']) / 'config/native-owner-text-deferred').glob('*.json'))
        self.assertEqual(len(files), 1)
        saved = json.loads(files[0].read_text('utf8'))['events']
        self.assertEqual([item['id'] for item in saved], ['evt:owner:0','evt:owner:1','evt:owner:2'])
        self.assertFalse(any(item['event'] == 'message' for item in events))
        self.assertEqual(events[-1]['event'], 'owner-text-deferred-saved')

    def test_actual_loop_native_hint_does_not_mint_ui_grant(self):
        class NativeFixture(NativeOwnerTextAuthority):
            def restore(instance):
                instance._mode, instance._epoch = 'native', 'a' * 32
        factory = lambda config, **kw: WorkerOwnerTextBridge(config, controller_factory=NativeFixture, **kw)
        dispatcher = self.fixture.dispatcher()
        with patch.object(worker, 'WorkerOwnerTextBridge', side_effect=factory), patch.object(dispatcher, 'observe_incoming') as observe:
            events, _, _ = self.fixture.run_worker([None], inbound=self.inbound(), dispatcher=dispatcher)
        observe.assert_not_called()
        self.assertEqual(len([item for item in events if item['event'] == 'native-owner-text-observed']), 1)
        self.assertFalse(any(item['event'] == 'message' for item in events))

    def test_actual_loop_overflow_persists_all_and_returns_without_retry(self):
        factory = lambda config, **kw: WorkerOwnerTextBridge(config, controller_factory=lambda c: NativeOwnerTextAuthority(c, max_deferred=2), **kw)
        with patch.object(worker, 'WorkerOwnerTextBridge', side_effect=factory):
            events, operations, _ = self.fixture.run_worker([
                {'op': 'owner-text-prepare', 'id': 'prepare', 'target': 'owner'}, None, None], inbound=self.inbound(4))
        self.assertEqual(len([op for op in operations if op[0] == 'poll']), 1)
        self.assertFalse(any(item['event'] == 'chat-read-retrying' for item in events))
        files = list((Path(self.fixture.config['dataRoot']) / 'config/native-owner-text-deferred').glob('*.json'))
        self.assertEqual(len(files), 1)
        self.assertEqual(len(json.loads(files[0].read_text('utf8'))['events']), 4)

    def test_actual_loop_busy_and_independent_prepare_denied_but_status_readonly(self):
        events, _, _ = self.fixture.run_worker([
            {'op': 'begin-reply', 'id': 'begin', 'replyId': 'reply', 'target': 'owner'},
            {'op': 'owner-text-prepare', 'id': 'prepare', 'target': 'owner'},
            {'op': 'owner-text-status', 'id': 'status', 'target': 'owner'}])
        replies = {item['id']: item for item in events if item['event'] == 'owner-text-result'}
        self.assertEqual(replies['prepare']['code'], 'owner-text-worker-busy')
        self.assertTrue(replies['status']['ok'])
        events, _, _ = self.fixture.run_worker([
            {'op': 'owner-text-prepare', 'id': 'prepare', 'target': 'owner'}], independent=True)
        self.assertEqual(next(item for item in events if item['event'] == 'owner-text-result')['code'], 'owner-text-worker-busy')

    def test_invalid_persisted_authority_blocks_before_any_monitor_or_publish(self):
        config = Path(self.fixture.config['dataRoot']) / 'config'
        config.mkdir(parents=True)
        (config / 'native-owner-text-authority.json').write_text('{invalid', encoding='utf8')
        events, operations, _ = self.fixture.run_worker([None], inbound=self.inbound())
        self.assertEqual(operations, [])
        self.assertFalse(any(item['event'] in ('message', 'ready') for item in events))
        self.assertEqual(events[-1]['code'], 'owner-text-authority-restore-blocked')


if __name__ == '__main__':
    unittest.main()
