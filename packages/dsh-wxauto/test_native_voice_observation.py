import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import worker
from message_watch import MainWindowMonitor, event_key
import test_message_watch as fixtures
Row = fixtures.Row


class NativeVoiceObservationTests(unittest.TestCase):
    def monitor(self, *, enabled=True, voice_direction='friend', include_peers=False):
        old = Row(1, 'old text')
        voice = Row(2, '语音4"秒未播放')
        voice.ClassName = 'mmui::ChatVoiceItemView'
        voice.direction = voice_direction
        rows = [old, voice]
        if include_peers:
            rows += [Row(3, 'new text'), Row(4, '图片')]
            rows[-1].ClassName = 'mmui::ChatImageItemView'
        monitor = MainWindowMonitor(NS(_api=NS(_chat_api=NS(msgbox=NS(WheelDown=lambda **kw: None)))),
            {'group': 'group'}, receive_voice=True, image_directory='fixture-images', native_voice_sources=enabled)
        monitor.cursor.seed('group', [old])
        monitor.current = lambda: 'group'
        monitor.select = lambda name: None
        monitor.controls = lambda: rows
        monitor.sessions = lambda: {'group': {'time': '12:00', 'content': rows[-1].Name, 'isnew': False, 'new_count': 0}}
        calls = []
        def direction(row):
            calls.append(('direction', row.key))
            return getattr(row, 'direction', 'friend')
        def parse(row):
            calls.append(('parse', row.key))
            def to_text():
                calls.append(('to-text', row.key))
                return 'legacy actual transcript'
            return NS(attr='friend', type='voice' if row is voice else 'image' if row.key == 4 else 'text',
                      content=row.Name, sender='member', to_text=to_text)
        monitor.message_direction = direction
        monitor.parse_incoming = parse
        monitor.transcribe_voice = lambda message, name: message.to_text()
        return monitor, calls

    def test_voice_only_hint_has_no_sender_or_text_and_no_sdk_or_direction(self):
        for direction in ('friend', 'self'):
            with self.subTest(direction=direction):
                monitor, calls = self.monitor(voice_direction=direction)
                records = monitor.read('group')
                self.assertEqual(len(records), 1)
                self.assertEqual(set(records[0]), {'observation', 'id'})
                self.assertEqual(records[0]['observation'], 'native-voice')
                self.assertEqual(calls, [])
                self.assertEqual(monitor.read('group'), [])
                self.assertIn((2,), [key for key, _ in monitor.cursor.rows['group']])

    def test_same_batch_text_and_image_retained_not_hidden_by_voice_hint(self):
        monitor, calls = self.monitor(include_peers=True)
        with patch.dict(sys.modules, {'incoming_images': NS(download_image=lambda *a, **kw: {'path': 'fixture.png'}, capture_emotion=lambda *a, **kw: {})}):
            records = monitor.read('group')
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0]['observation'], 'native-voice')
        self.assertEqual([row['mediaType'] for row in records[1:]], ['text', 'image'])
        self.assertEqual(records[-1]['images'], [{'path': 'fixture.png'}])
        self.assertFalse(any(key == 2 for _, key in calls))
        self.assertEqual(monitor.read('group'), [])

    def test_disabled_preserves_sdk_voice_transcription(self):
        monitor, calls = self.monitor(enabled=False)
        records = monitor.read('group')
        self.assertEqual(records[0]['mediaType'], 'voice')
        self.assertIn('legacy actual transcript', records[0]['text'])
        self.assertEqual(calls, [('direction', 2), ('parse', 2), ('to-text', 2)])
        self.assertNotIn('observation', records[0])

    def test_startup_positive_boundary_can_observe_voice_without_sender_inference(self):
        setup = fixtures.StartupUnreadTests()
        voice = Row(2, '语音4"秒未播放')
        voice.ClassName = 'mmui::ChatVoiceItemView'
        monitor, _ = setup.monitor({'owner': [Row(1, 'old'), voice]}, {'owner': setup.info('[语音] 4"', 1)})
        monitor.native_voice_sources = True
        monitor.message_direction = lambda row: self.fail('observation cannot inspect direction')
        monitor.parse_private = lambda *args: self.fail('observation cannot parse SDK')
        monitor.initialize()
        result = monitor.read('owner')
        self.assertEqual(result[0]['observation'], 'native-voice')
        self.assertEqual(monitor.read('owner'), [])


class NativeVoiceWorkerObservationTests(unittest.TestCase):
    def run_worker(self, config_value=None, *, independent=False, readiness='1'):
        options = []
        class ScriptedIPC:
            def __init__(self): self.commands = []
            def put(self, value): self.commands.append(value)
            def get(self, timeout=None): return self.commands.pop(0)
            def empty(self): return True  # Future command arrival after idle poll.
        class Monitor:
            def __init__(self, wx, routes, **kwargs):
                options.append(kwargs)
                self.native = kwargs.get('native_voice_sources') is True
            def initialize(self): pass
            def candidates(self): return ['owner']
            def read(self, name):
                voice = {'observation': 'native-voice', 'id': 'voice-observation'} if self.native else {
                    'id': 'legacy-voice', 'sender': 'owner', 'text': 'legacy transcript', 'mediaType': 'voice'}
                return [voice, {'id': 'text', 'sender': 'owner', 'text': 'actual text', 'mediaType': 'text'}]
        modules = {'wxauto_connection': NS(connect=lambda: NS()),
            'message_watch': NS(MainWindowMonitor=Monitor, event_key=event_key),
            'native_voice': NS(NativeVoice=lambda *args: NS(ready=lambda: False)),
            'social_ipc': NS(SocialDispatcher=lambda config: None, OPS=set()),
            'social_wxauto_backend': NS(running_client=lambda wx: {'clientVersion': 'fixture'}),
            'independent_windows': NS(create_window_monitor=lambda wx, routes, **kwargs: NS(
                initialize=lambda: Monitor(wx, routes, **kwargs), status=lambda: {'mode': 'independent'}))}
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            config = {'target': 'owner', 'groups': [], 'statePath': str(root / 'claims.json'),
                      'independentWindows': {'enabled': independent}}
            if config_value is not None:
                config['nativeVoiceSources'] = config_value
            path = root / 'worker.json'
            path.write_text(json.dumps(config), encoding='utf8')
            output = io.StringIO()
            environment=dict(os.environ)
            environment.pop('WHALE_NATIVE_VOICE_READY',None)
            if readiness is not None:
                environment['WHALE_NATIVE_VOICE_READY']=readiness
            # Read one idle iteration through actual main(), then stop.
            with patch.dict(os.environ,environment,clear=True), \
                    patch.dict(sys.modules, modules), patch.object(sys, 'argv', ['worker.py', str(path)]), \
                    patch.object(sys, 'stdin', io.StringIO('null\n{"op":"stop"}\n')), \
                    patch.object(worker, 'OUT', output), \
                    patch.object(worker.queue, 'Queue', ScriptedIPC), \
                    patch.object(worker.threading, 'Thread', lambda target, **kw: NS(start=target)), \
                    patch('importlib.metadata.version', return_value='fixture'):
                worker.main()
            events = [json.loads(line) for line in output.getvalue().splitlines()]
        return events, options

    def test_actual_worker_emits_observation_not_message_and_capability_is_distinct(self):
        events, options = self.run_worker({'enabled': True})
        ready = next(event for event in events if event['event'] == 'ready')
        self.assertTrue(ready['capabilities']['nativeVoiceSourceObservationEnabled'])
        self.assertFalse(ready['capabilities']['nativeVoice'])
        hint = next(event for event in events if event['event'] == 'native-voice-observed')
        self.assertEqual(set(hint), {'event', 'id', 'target', 'kind'})
        self.assertEqual(hint['kind'], 'friend')
        self.assertEqual([e['text'] for e in events if e['event'] == 'message'], ['actual text'])
        self.assertTrue(options[0]['native_voice_sources'])

    def test_disabled_or_non_boolean_setting_keeps_legacy_events(self):
        for setting in (None, {'enabled': False}, {'enabled': 'true'}):
            with self.subTest(setting=setting):
                events, _ = self.run_worker(setting)
                self.assertFalse(any(e['event'] == 'native-voice-observed' for e in events))
                self.assertEqual([e['mediaType'] for e in events if e['event'] == 'message'], ['voice', 'text'])

    def test_independent_mode_does_not_enable_native_observation(self):
        events, options = self.run_worker({'enabled': True}, independent=True)
        self.assertNotIn('native_voice_sources', options[0])
        ready = next(event for event in events if event['event'] == 'ready')
        self.assertFalse(ready['capabilities']['nativeVoiceSourceObservationEnabled'])
        self.assertFalse(any(e['event'] == 'native-voice-observed' for e in events))

    def test_config_enabled_without_verified_readiness_preserves_legacy_path(self):
        for readiness in (None,'0','true'):
            with self.subTest(readiness=readiness):
                events,options=self.run_worker({'enabled':True},readiness=readiness)
                ready=next(event for event in events if event['event']=='ready')
                self.assertFalse(ready['capabilities']['nativeVoiceSourceObservationEnabled'])
                self.assertNotIn('native_voice_sources',options[0])
                self.assertFalse(any(e['event']=='native-voice-observed' for e in events))
                self.assertEqual([e['mediaType'] for e in events if e['event']=='message'],['voice','text'])


if __name__ == '__main__':
    unittest.main()
