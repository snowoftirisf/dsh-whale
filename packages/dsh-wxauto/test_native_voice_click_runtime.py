import io
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import native_voice_click_runtime as runtime
from native_voice import NativeVoice


class ClickRuntimeTests(unittest.TestCase):
    def test_opt_in_dispatch_preserves_cancel_mode_and_original_context(self):
        instance = NativeVoice(None, {'recorderMode': 'click-panel-v1'})
        context = {'target': 'fixture', 'kind': 'friend', 'commandId': 'fixture:once'}
        with patch.object(runtime, 'record_click_panel', return_value={'canceled': True}) as delegate:
            self.assertEqual(instance._record('fixture.wav', confirm_send=False, context=context), {'canceled': True})
        delegate.assert_called_once_with(instance, 'fixture.wav', confirm_send=False, context=context)

    def test_missing_context_refuses_before_player_or_ui_import(self):
        with patch.object(runtime, 'ContinuousPlayer') as player:
            with self.assertRaisesRegex(RuntimeError, 'context-invalid'):
                runtime.record_click_panel(None, 'fixture.wav', confirm_send=False, context=None)
        player.assert_not_called()

    def test_outside_outbox_refuses_before_spawning(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            root = Path(temp)
            file = root / 'outside.wav'
            file.write_bytes(b'fixture only')
            instance = SimpleNamespace(ready=lambda: True, config={'dataRoot': temp})
            context = {'target': 'fixture', 'kind': 'friend', 'commandId': 'fixture:once'}
            with patch.object(runtime, 'ContinuousPlayer') as player:
                with self.assertRaisesRegex(RuntimeError, 'voice-file-denied'):
                    runtime.record_click_panel(instance, file, confirm_send=True, context=context)
            player.assert_not_called()

    def test_original_continuous_stream_protocol_has_one_play_and_owned_cleanup(self):
        writes = []
        class Input(io.StringIO):
            def write(self, value):
                writes.append(value)
                return super().write(value)
        state = {'killed': 0, 'waited': 0}
        process = SimpleNamespace(
            stdin=Input(), stdout=io.StringIO('{"ready":true}\n{"played":true}\n'),
            poll=lambda: None,
            kill=lambda: state.update(killed=state['killed'] + 1),
            wait=lambda **kw: state.update(waited=state['waited'] + 1))
        with patch.object(runtime.subprocess, 'Popen', return_value=process) as spawn:
            player = runtime.ContinuousPlayer('fixture.wav', {'dataRoot': 'fixture-data'})
            self.assertTrue(player.wait_ready(timeout=1)['ready'])
            player.play()
            with self.assertRaisesRegex(RuntimeError, 'already-requested'):
                player.play()
            self.assertTrue(player.wait_played(timeout=1)['played'])
            player.close()
        self.assertEqual(writes, ['PLAY\n'])
        self.assertEqual(state, {'killed': 1, 'waited': 1})
        args, kwargs = spawn.call_args
        self.assertEqual(Path(args[0][2]).name, 'voice_playback.py')
        self.assertIn('CABLE Input', args[0][4])
        self.assertIn('WHALE_DATA_ROOT', kwargs['env'])

    def test_new_self_row_confirmation_never_parses_sender_avatar(self):
        old = SimpleNamespace(GetRuntimeId=lambda: [1], ClassName='mmui::ChatVoiceItemView')
        new = SimpleNamespace(GetRuntimeId=lambda: [2], ClassName='mmui::ChatVoiceItemView')
        msgbox = SimpleNamespace(GetChildren=lambda: [old, new])
        root = object()
        checks = []
        instance = SimpleNamespace(root=lambda: root,
            check_target=lambda *a, **kw: checks.append(kw),
            wx=SimpleNamespace(_api=SimpleNamespace(_chat_api=SimpleNamespace(msgbox=msgbox))))
        readback = runtime.NewSelfVoiceReadback(instance, {'target': 'fixture'})
        with patch.object(runtime, 'read_message_direction', return_value='self') as direction:
            self.assertTrue(readback.confirm({(1,)}, timeout=1))
        direction.assert_called_once_with(new, root, msgbox=msgbox)
        self.assertEqual(checks, [{'required': False}])


if __name__ == '__main__':
    unittest.main()
