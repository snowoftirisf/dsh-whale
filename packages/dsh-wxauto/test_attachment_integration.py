import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import worker
from attachment_registry import AttachmentRegistry, chat_key
from data_paths import resolve_data_paths
from message_watch import MainWindowMonitor
from test_message_watch import Row, ReadFixtures


class AttachmentIntegrationTests(ReadFixtures):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'data'
        self.paths = resolve_data_paths(root=self.root)
        self.received = self.paths['media'] / 'attachments'
        self.routes = {'owner': 'friend', 'group': 'group'}

    def registry(self):
        return AttachmentRegistry(self.paths['config'] / 'attachment-receipts.local.json',
                                  self.root, self.received, self.routes)

    def receive(self, target='owner', content=b'received document'):
        file = self.received / chat_key(target) / 'unique-report.pdf'
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)
        metadata = {'path': str(file), 'name': 'report.pdf', 'size': len(content),
                    'mediaType': 'file', 'contentStatus': 'unknown'}
        return file, metadata

    def test_registry_persists_only_current_chat_received_paths(self):
        file, metadata = self.receive()
        self.registry().register('owner', metadata)
        registry = self.registry()
        self.assertEqual(registry.received_paths('owner'), [file])
        self.assertEqual(registry.received_paths('group'), [])
        serialized = registry.file.read_text(encoding='utf8')
        self.assertNotIn(str(self.root), serialized)
        with self.assertRaisesRegex(RuntimeError, 'attachment-path-denied'):
            registry.register('group', metadata)
        with self.assertRaisesRegex(RuntimeError, 'attachment-target-denied'):
            registry.register('stranger', metadata)

    def test_registry_hashes_actual_bytes_and_rejects_claimed_identity_mismatch(self):
        file, metadata = self.receive()
        registry = self.registry()
        for wrong in ({'sha256': '0' * 64}, {'size': metadata['size'] + 1}):
            with self.subTest(wrong=wrong), self.assertRaisesRegex(RuntimeError, 'receipt-mismatch'):
                registry.register('owner', {**metadata, **wrong})
        self.assertEqual(registry.received_paths('owner'), [])
        registered = registry.register('owner', metadata)
        self.assertEqual(registered['sha256'], hashlib.sha256(file.read_bytes()).hexdigest())
        self.assertEqual(registered['size'], file.stat().st_size)

    def test_registry_does_not_authorize_modified_missing_or_legacy_file(self):
        file, metadata = self.receive()
        registry = self.registry()
        registry.register('owner', metadata)
        # Same length modification defeats size-only validation.
        file.write_bytes(b'changed! document')
        self.assertEqual(self.registry().received_paths('owner'), [])
        file.unlink()
        self.assertEqual(self.registry().received_paths('owner'), [])
        file.write_bytes(b'received document')
        self.assertEqual(self.registry().received_paths('owner', file), [file])
        value = json.loads(registry.file.read_text('utf8'))
        for entry in value['chats']['owner'].values():
            entry.pop('sha256')
        registry.file.write_text(json.dumps(value), encoding='utf8')
        self.assertEqual(self.registry().received_paths('owner'), [])

    def test_selected_receipt_does_not_hash_unrelated_files(self):
        file, metadata = self.receive()
        registry = self.registry()
        registry.register('owner', metadata)
        other = file.with_name('other.pdf')
        other.write_bytes(b'another document')
        registry.register('owner', {'path': str(other)})
        with patch.object(registry, '_identity', wraps=registry._identity) as identity:
            self.assertEqual(registry.received_paths('owner', file), [file])
        identity.assert_called_once_with(file, 'owner')

    def monitor(self, row, enabled=True):
        monitor = MainWindowMonitor(SimpleNamespace(_api=SimpleNamespace(_chat_api=object())),
            {'owner': 'friend'}, attachment_directory=str(self.received) if enabled else None,
            data_root=self.root)
        old = Row(1, 'old')
        monitor.cursor.seed('owner', [old])
        monitor.current = lambda: 'owner'
        monitor.select = lambda name: None
        monitor.controls = lambda: [old, row]
        monitor.sessions = lambda: {}
        return monitor

    def test_file_row_download_is_qualified_once_and_preserves_unknown_content(self):
        row = Row(2, '文件\nreport.pdf')
        row.ClassName = 'mmui::ChatFileItemView'
        file, metadata = self.receive()
        message = SimpleNamespace(type='file', attr='friend', sender='member')
        with patch.dict(sys.modules, {'wxauto4.msgs.msg': SimpleNamespace(parse_msg=lambda *args: message)}), \
                patch('incoming_files.download_file', return_value=metadata) as download:
            monitor = self.monitor(row)
            records = monitor.read('owner')
            self.assertEqual(records[0]['files'], [metadata])
            self.assertIn('尚未读取内容', records[0]['text'])
            self.assertEqual(records[0]['sender'], 'member')
            self.assertEqual(monitor.read('owner'), [])
            download.assert_called_once()
            self.assertEqual(download.call_args.kwargs['media_root'], self.root)

    def test_file_bubble_self_messages_and_disabled_receive_never_download(self):
        row = Row(2, '文件\nreport.pdf')
        row.ClassName = 'mmui::ChatBubbleReferItemView'
        message = SimpleNamespace(type='file', attr='self', sender='bot')
        with patch.dict(sys.modules, {'wxauto4.msgs.msg': SimpleNamespace(parse_msg=lambda *args: message)}), \
                patch('incoming_files.download_file') as download:
            self.assertEqual(self.monitor(row).read('owner'), [])
            message.attr = 'friend'
            self.assertEqual(self.monitor(row, enabled=False).read('owner'), [])
            download.assert_not_called()

    def test_download_failure_keeps_message_and_marks_content_unknown(self):
        row = Row(2, '文件\nexpired.pdf')
        row.ClassName = 'mmui::ChatFileItemView'
        message = SimpleNamespace(type='file', attr='friend', sender='member')
        with patch.dict(sys.modules, {'wxauto4.msgs.msg': SimpleNamespace(parse_msg=lambda *args: message)}), \
                patch('incoming_files.download_file', side_effect=RuntimeError('expired')):
            record = self.monitor(row).read('owner')[0]
            self.assertEqual(record['files'], [])
            self.assertIn('内容未知', record['text'])
            self.assertEqual(record['mediaError'], 'expired')

    def run_worker(self, commands, *, sdk_result=True, wx_api=None, after_file_send=lambda: None):
        sent = []
        current = {'name': 'owner'}
        class Monitor:
            def __init__(self, *args, **kwargs):
                self.files_enabled = bool(kwargs.get('attachment_directory'))
            def initialize(self): pass
            def read(self, name): return []
            def candidates(self): return []
            def note_outbound(self, name): pass
            def select(self, name): current['name'] = name
        def send_files(file):
            sent.append((current['name'], file))
            after_file_send()
            return sdk_result
        wx = SimpleNamespace(ChatInfo=lambda: {'chat_name': current['name'], 'chat_type': self.routes[current['name']]},
                             SendFiles=send_files)
        if wx_api is not None: wx._api = wx_api
        modules = {'wxauto_connection': SimpleNamespace(connect=lambda: wx),
                   'message_watch': SimpleNamespace(MainWindowMonitor=Monitor, event_key=lambda *args: 'unused'),
                   'native_voice': SimpleNamespace(NativeVoice=lambda *args: SimpleNamespace(ready=lambda: False))}
        config = self.base / 'worker-config.json'
        config.write_text(json.dumps({'target': 'owner', 'groups': ['group'], 'statePath': str(self.base / 'claims.json'),
            'dataRoot': str(self.root), 'receiveFiles': True, 'attachmentDirectory': str(self.received)}), encoding='utf8')
        output = io.StringIO()
        with patch.dict(sys.modules, modules), patch.object(sys, 'argv', ['worker.py', str(config)]), \
                patch.object(sys, 'stdin', io.StringIO('\n'.join(map(json.dumps, commands)))), \
                patch.object(worker, 'OUT', output), \
                patch.object(worker.threading, 'Thread', lambda target, **kwargs: SimpleNamespace(start=target)):
            worker.main()
        return [json.loads(line) for line in output.getvalue().splitlines()], sent

    def test_worker_none_return_remains_unknown_with_type_only(self):
        file, metadata = self.receive()
        self.registry().register('owner', metadata)
        events, sent = self.run_worker(self.commands(file), sdk_result=None)
        ack = next(e for e in events if e.get('id') == 'r:1')
        self.assertEqual(len(sent), 1)
        self.assertFalse(ack['ok'])
        self.assertEqual(ack['code'], 'send-not-confirmed')
        self.assertEqual(ack['sdkResult'], {'type': 'NoneType'})

    def test_worker_void_attachment_uses_unique_new_self_file_readback_once(self):
        file, metadata = self.receive()
        self.registry().register('owner', metadata)
        rows = []
        root = SimpleNamespace(GetRuntimeId=lambda: [10], ProcessId=100, NativeWindowHandle=20)
        box = SimpleNamespace(GetRuntimeId=lambda: [11], ProcessId=100, IsOffscreen=0, GetChildren=lambda: rows[:])
        row = SimpleNamespace(GetRuntimeId=lambda: [12], ClassName='mmui::ChatBubbleItemView',
            ControlTypeName='ListItemControl', Name='文件\n'+file.name+'\n6B\n微信电脑版', GetChildren=lambda: [])
        api = SimpleNamespace(control=root, _chat_api=SimpleNamespace(msgbox=box))
        with patch('file_send_readback.read_message_direction', return_value='self') as direction:
            events, sent = self.run_worker(self.commands(file), sdk_result=None, wx_api=api,
                after_file_send=lambda: rows.append(row))
        ack = next(e for e in events if e.get('id') == 'r:1')
        self.assertEqual(len(sent), 1)
        self.assertTrue(ack['ok'])
        self.assertEqual(ack['renderedFileEvidence']['kind'], 'rendered-file-row')
        self.assertFalse(ack['renderedFileEvidence']['recipientBytesVerified'])
        self.assertEqual(direction.call_count, 2)

    def test_worker_false_wxresponse_hashes_message_without_leaking_payload(self):
        from wxauto4.param import WxResponse
        import hashlib
        file, metadata = self.receive()
        self.registry().register('owner', metadata)
        private = 'private account/path diagnostic fixture'
        result = WxResponse.failure(private, data={'path': str(file)})
        events, sent = self.run_worker(self.commands(file), sdk_result=result)
        ack = next(e for e in events if e.get('id') == 'r:1')
        self.assertEqual(len(sent), 1)
        self.assertFalse(ack['ok'])
        self.assertEqual(ack['code'], 'send-not-confirmed')
        self.assertEqual(ack['sdkResult'], {'type': 'WxResponse', 'knownStatus': 'failure',
            'messageLength': len(private), 'messageSha256': hashlib.sha256(private.encode()).hexdigest()})
        self.assertNotIn(private, json.dumps(ack))
        self.assertNotIn(str(file), json.dumps(ack))

    def commands(self, file, target='owner', operation='send-attachment'):
        return [dict(op='begin-reply', id='r:begin', target=target, replyId='r'),
                dict(op=operation, id='r:1', target=target, replyId='r', filepath=str(file)),
                dict(op='end-reply', id='r:end', target=target, replyId='r')]

    def test_worker_sends_registered_file_and_reports_capabilities(self):
        file, metadata = self.receive()
        self.registry().register('owner', metadata)
        events, sent = self.run_worker(self.commands(file))
        self.assertEqual(sent, [('owner', str(file))])
        self.assertTrue(next(e for e in events if e['event'] == 'ready')['capabilities']['fileReceive'])
        self.assertTrue(all(e['ok'] for e in events if e['event'] == 'ack'))
        events, sent = self.run_worker(self.commands(file))
        self.assertEqual(sent, [])  # a persisted operation id cannot send twice
        self.assertTrue(any(e.get('code') == 'already-claimed' for e in events))

    def test_worker_rejects_cross_chat_and_unregistered_files(self):
        file, metadata = self.receive()
        self.registry().register('owner', metadata)
        events, sent = self.run_worker(self.commands(file, target='group'))
        self.assertEqual(sent, [])
        self.assertTrue(any(e.get('code') == 'attachment-path-denied' for e in events))
        file, metadata = self.receive('group')
        events, sent = self.run_worker(self.commands(file, target='group'))
        self.assertEqual(sent, [])
        self.assertTrue(any(e.get('code') == 'attachment-not-registered' for e in events))

    def test_worker_does_not_send_registered_path_after_bytes_change(self):
        file, metadata = self.receive()
        self.registry().register('owner', metadata)
        file.write_bytes(b'changed! document')
        events, sent = self.run_worker(self.commands(file))
        self.assertEqual(sent, [])
        self.assertTrue(any(e.get('code') == 'attachment-not-registered' for e in events))

    def test_worker_outbox_send_keeps_meme_send_file_policy_unchanged(self):
        file = self.paths['outbox'] / chat_key('owner') / 'report.txt'
        file.parent.mkdir(parents=True)
        file.write_text('new report')
        events, sent = self.run_worker(self.commands(file))
        self.assertEqual(sent, [('owner', str(file))])
        # Change id to avoid testing claim rejection instead of meme policy.
        commands = self.commands(file, operation='send-file')
        commands[1]['id'] = 'r:2'
        events, sent = self.run_worker(commands)
        self.assertEqual(sent, [])
        self.assertTrue(any(e.get('code') == 'meme-file-denied' for e in events))

    def test_corrupt_registry_disables_only_file_capability(self):
        file = self.paths['config'] / 'attachment-receipts.local.json'
        file.parent.mkdir(parents=True)
        file.write_text('{bad json', encoding='utf8')
        events, sent = self.run_worker([])
        ready = next(e for e in events if e['event'] == 'ready')
        self.assertFalse(ready['capabilities']['fileSend'])
        self.assertTrue(any(e.get('code') == 'file-capability-disabled' for e in events))

    def test_worker_registers_received_file_before_publishing_event(self):
        import queue
        file, metadata = self.receive()
        delivered = {'value': False}
        class Input:
            def get(self, timeout):
                if delivered['value']:
                    return {'op': 'stop'}
                delivered['value'] = True
                raise queue.Empty()
            def empty(self): return True
        class Monitor:
            def __init__(self, *args, **kwargs): pass
            def initialize(self): pass
            def candidates(self): return ['owner']
            def read(self, target):
                return [{'id': 'file-message', 'text': '[收到附件]', 'sender': 'member',
                         'mediaType': 'file', 'files': [metadata]}]
        modules = {'wxauto_connection': SimpleNamespace(connect=lambda: object()),
            'message_watch': SimpleNamespace(MainWindowMonitor=Monitor, event_key=lambda *args: 'file-id'),
            'native_voice': SimpleNamespace(NativeVoice=lambda *args: SimpleNamespace(ready=lambda: False))}
        config = self.base / 'worker-config.json'
        config.write_text(json.dumps({'target': 'owner', 'statePath': str(self.base / 'claims.json'),
            'dataRoot': str(self.root), 'receiveFiles': True}), encoding='utf8')
        output = io.StringIO()
        events = []
        original_emit = worker.emit
        def emit(value):
            if value['event'] == 'message':
                self.assertEqual(self.registry().received_paths('owner'), [file])
                events.append(value)
            original_emit(value)
        with patch.dict(sys.modules, modules), patch.object(sys, 'argv', ['worker.py', str(config)]), \
                patch.object(worker, 'OUT', output), patch.object(worker, 'emit', emit), \
                patch.object(worker.queue, 'Queue', Input), \
                patch.object(worker.threading, 'Thread', lambda *args, **kwargs: SimpleNamespace(start=lambda: None)):
            worker.main()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['files'], [metadata])


if __name__ == '__main__':
    unittest.main()
