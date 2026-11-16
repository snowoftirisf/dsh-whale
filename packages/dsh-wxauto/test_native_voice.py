import unittest
import io
import hashlib
import json
import tempfile
import contextlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from native_voice import NativeVoice


class NativeRecordingLifecycleTests(unittest.TestCase):
    """All UI/key/player operations are fakes; no Win32 API or D: writes."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / 'voice/outbox/test.wav'
        self.file.parent.mkdir(parents=True); self.file.write_bytes(b'test fixture, never played')
        self.state = {'panel': False, 'start': True, 'foreground': 123, 'activeCalls': 0,
                      'alive': True, 'kills': 0, 'waits': 0, 'dropAfterPlay': False,
                      'loseCancelFocus': False, 'disappearOnActivate': False}
        self.keys = []
        self.native_keys = []
        state = self.state
        def activate(**kwargs):
            state['activeCalls'] += 1
            if state['loseCancelFocus'] and state['activeCalls'] > 1: state['foreground'] = 456
            if state['disappearOnActivate']: state['panel'] = False
        self.root = SimpleNamespace(NativeWindowHandle=123, SetActive=activate,
            GroupControl=lambda **kwargs: SimpleNamespace(Exists=lambda _: state['panel']),
            TextControl=lambda **kwargs: SimpleNamespace(Exists=lambda _: state['panel']),
            ButtonControl=lambda **kwargs: SimpleNamespace(Exists=lambda _: True),
            EditControl=lambda **kwargs: SimpleNamespace(Exists=lambda _: True,SetFocus=lambda: None,Click=lambda **kw:None,HasKeyboardFocus=True))
        self.wx = SimpleNamespace(_api=SimpleNamespace(control=self.root,
            _chat_api=SimpleNamespace(msgbox=SimpleNamespace(GetChildren=lambda: []))))
        self.transport = NativeVoice(self.wx, {'dataRoot': self.temp.name, 'buttonName': 'fixture voice',
                                               'recordingLabels': ['fixture cancel']})
        def press(key, **kwargs):
            self.keys.append(('down', key))
            if key == 0xA5 and state['start']: state['panel'] = True
            if key == 0x08: state['panel'] = False
        def release(key, **kwargs): self.keys.append(('up', key))
        def native_event(vk, scan, flags, extra):
            self.native_keys.append((vk,scan,flags,extra))
            if flags & 2:release(vk)
            else:press(vk)
        self.uia = SimpleNamespace(PressKey=press,ReleaseKey=release,
            keybd_event=native_event,KeyboardEventFlag=SimpleNamespace(ExtendedKey=1,KeyDown=0,KeyUp=2),
            GetForegroundControl=lambda: SimpleNamespace(NativeWindowHandle=state['foreground']))
        class Input(io.StringIO):
            def write(inner, value):
                if value == 'PLAY\n' and state['dropAfterPlay']: state['panel'] = False
                return super().write(value)
        self.input = Input()
        def kill(): state['kills'] += 1; state['alive'] = False
        def wait(**kwargs): state['waits'] += 1; return 0
        self.player = SimpleNamespace(stdin=self.input,stdout=io.StringIO('{"ready":true}\n{"played":true}\n'),
            poll=lambda: None if state['alive'] else 0,kill=kill,wait=wait)
        counter = [0]
        def monotonic(): counter[0] += .25; return counter[0]
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.spawn = self.stack.enter_context(patch('native_voice.subprocess.Popen', return_value=self.player))
        self.stack.enter_context(patch.object(self.transport, 'ready', return_value=True))
        self.menu_cleanup=self.stack.enter_context(patch('native_voice.close_avatar_context_menu',return_value={'closed':False,'menuCount':0}))
        self.stack.enter_context(patch.object(self.transport, 'record_start_diagnostic', return_value={}))
        self.stack.enter_context(patch.dict('sys.modules', {'wxauto4': SimpleNamespace(uia=self.uia)}))
        self.stack.enter_context(patch('native_voice.time.monotonic', side_effect=monotonic))
        self.stack.enter_context(patch('native_voice.time.sleep', return_value=None))

    def test_existing_recorder_is_rejected_without_key_or_playback(self):
        self.state['panel'] = True
        with self.assertRaisesRegex(RuntimeError, 'already-active'): self.transport.probe(str(self.file))
        self.spawn.assert_not_called(); self.assertEqual(self.keys, [])

    def test_unknown_post_release_direction_stays_unknown_without_sdk_parsing_or_resend(self):
        row=SimpleNamespace(ClassName='mmui::ChatVoiceItemView',GetRuntimeId=lambda:(456,))
        calls=[0]
        def rows():
            calls[0]+=1
            return [] if calls[0]==1 else [row]
        self.wx._api._chat_api.msgbox.GetChildren=rows
        with patch('native_voice.read_message_direction',side_effect=RuntimeError('capture-unavailable')) as direction, \
                self.assertRaisesRegex(RuntimeError,'voice-send-outcome-unknown'):
            self.transport.send(str(self.file))
        self.assertTrue(direction.called)
        self.assertEqual(self.keys.count(('down',0xA5)),1)
        self.assertEqual(self.input.getvalue(),'PLAY\n')

    def test_recording_editor_click_requires_foreground_and_verified_focus(self):
        calls=[]
        editor=SimpleNamespace(Exists=lambda _:True,Click=lambda **kw:calls.append(kw),HasKeyboardFocus=True)
        self.root.EditControl=lambda **kw:editor
        self.state['foreground']=456
        with self.assertRaisesRegex(RuntimeError,'voice-input-focus-not-confirmed'):
            self.transport.click_recording_input(self.uia)
        self.assertEqual(calls,[])
        self.state['foreground']=123
        self.transport.click_recording_input(self.uia)
        self.assertEqual(len(calls),1)
        editor.HasKeyboardFocus=False
        with self.assertRaisesRegex(RuntimeError,'voice-input-focus-not-confirmed'):
            self.transport.click_recording_input(self.uia)
        self.assertEqual(self.keys,[])

    def test_switch_away_during_diagnostic_aborts_before_global_recording_shortcut(self):
        def switch_away(*args, **kwargs):
            self.state['foreground']=456
            return {}
        self.transport.record_start_diagnostic.side_effect=switch_away
        with self.assertRaisesRegex(RuntimeError,'voice-input-focus-not-confirmed'):
            self.transport.send(str(self.file))
        self.assertEqual(self.keys,[])
        self.assertEqual(self.input.getvalue(),'')
        self.assertEqual(self.state['kills'],1)

    def test_verified_menu_cleanup_reacquires_editor_before_native_recording(self):
        self.menu_cleanup.return_value={'closed':True,'menuCount':1,'method':'escape'}
        result=self.transport.probe(str(self.file))
        self.assertTrue(result['canceled']);self.assertTrue(result['menuCleanup']['closed'])
        self.assertEqual(self.state['activeCalls'],3) # Initial focus, menu transition, cancellation.
        self.menu_cleanup.assert_called_once_with(self.root,self.uia)

    def test_unverified_menu_refuses_before_shortcut_and_reaps_audio_child(self):
        self.menu_cleanup.side_effect=RuntimeError('avatar-menu-shape-unverified')
        with self.assertRaisesRegex(RuntimeError,'avatar-menu-shape-unverified'):
            self.transport.probe(str(self.file))
        self.assertEqual(self.keys,[]);self.assertEqual(self.input.getvalue(),'')
        self.assertEqual(self.state['kills'],1);self.assertEqual(self.state['waits'],1)

    def test_failed_start_never_sends_backspace_to_an_unconfirmed_editor(self):
        self.state['start'] = False
        with self.assertRaisesRegex(RuntimeError, 'recording-not-confirmed'): self.transport.send(str(self.file))
        self.assertNotIn(('down',0x08), self.keys)
        self.assertIn(('up',0xA5), self.keys)
        self.assertNotIn('PLAY', self.input.getvalue())
        self.assertEqual(self.state['kills'],1); self.assertEqual(self.state['waits'],1)

    def test_first_confirmed_sample_is_not_overturned_by_a_second_observation(self):
        with patch.object(self.transport,'recording',side_effect=[True,False]) as samples:
            result = self.transport.probe(str(self.file))
        self.assertTrue(result['canceled']); self.assertEqual(samples.call_count,1)
        self.assertEqual(self.input.getvalue(),'PLAY\n')
        self.assertEqual(self.keys.count(('down',0x08)),1)

    def test_probe_does_not_claim_cancellation_if_recorder_disappeared_before_cancel(self):
        self.state['dropAfterPlay'] = True
        with self.assertRaisesRegex(RuntimeError,'voice-(?:probe-)?cancel-outcome-unknown'):
            self.transport.probe(str(self.file))
        self.assertNotIn(('down',0x08),self.keys)
        self.assertIn(('up',0xA5),self.keys)
        self.assertEqual(self.state['kills'],1)

    def test_cancel_focus_failure_still_releases_alt_and_reaps_playback_process(self):
        self.state['loseCancelFocus'] = True
        self.player.stdout = io.StringIO('{"ready":true}\n{"played":false}\n')
        with self.assertRaisesRegex(RuntimeError,'voice-cancel-outcome-unknown'):
            self.transport.send(str(self.file))
        self.assertNotIn(('down',0x08),self.keys)
        self.assertIn(('up',0xA5),self.keys)
        self.assertEqual(self.state['kills'],1)
        self.assertEqual(self.state['waits'],1)

    def test_disappearance_while_acquiring_cancel_foreground_is_not_a_confirmed_cancel(self):
        self.state['panel'] = True; self.state['disappearOnActivate'] = True
        self.assertFalse(self.transport.cancel_recording(self.uia))
        self.assertEqual(self.keys,[])

    def test_even_failed_alt_release_reaps_the_playback_child(self):
        native_event = self.uia.keybd_event
        def broken_release(vk, scan, flags, extra):
            if vk == 0xA5 and flags & 2: raise RuntimeError('fixture-key-release-failed')
            return native_event(vk,scan,flags,extra)
        self.uia.keybd_event = broken_release
        with self.assertRaisesRegex(RuntimeError,'fixture-key-release-failed'):
            self.transport.probe(str(self.file))
        self.assertEqual(self.state['kills'],1)
        self.assertEqual(self.state['waits'],1)

    def test_right_alt_scan_code_and_flags_pair_on_cancelled_probe(self):
        result=self.transport.probe(str(self.file))
        self.assertTrue(result['canceled'])
        self.assertEqual(self.native_keys,[(0xA5,0x38,1,0),(0xA5,0x38,3,0)])
        self.assertIn(('down',0x08),self.keys)  # Backspace uses the original API.
        self.assertEqual(self.keys.count(('up',0x08)),1)

    def test_right_alt_down_exception_still_attempts_matching_native_release(self):
        native_event=self.uia.keybd_event
        def fail_after_down(vk,scan,flags,extra):
            native_event(vk,scan,flags,extra)
            if flags==1:raise RuntimeError('fixture-injected-down-failed')
        self.uia.keybd_event=fail_after_down
        with self.assertRaisesRegex(RuntimeError,'fixture-injected-down-failed'):
            self.transport.probe(str(self.file))
        self.assertEqual(self.native_keys,[(0xA5,0x38,1,0),(0xA5,0x38,3,0)])
        self.assertEqual(self.state['kills'],1)
        self.assertEqual(self.state['waits'],1)


class NativeGuardTests(unittest.TestCase):
    def test_cancel_words_in_chat_cannot_fake_a_native_recording_panel(self):
        panel=SimpleNamespace(Exists=lambda _:False)
        root=SimpleNamespace(GroupControl=lambda **kw:panel,
            TextControl=lambda **kw:SimpleNamespace(Exists=lambda _:True))
        transport=NativeVoice(SimpleNamespace(_api=SimpleNamespace(control=root)),{'recordingLabels':['取消']})
        self.assertFalse(transport.recording())
        panel.Exists=lambda _:True
        self.assertTrue(transport.recording())

    def test_recording_shortcut_requires_actual_editor_focus_and_foreground(self):
        editor=SimpleNamespace(Exists=lambda _:True,SetFocus=lambda:None,HasKeyboardFocus=True)
        root=SimpleNamespace(SetActive=lambda **kw:None,EditControl=lambda **kw:editor,NativeWindowHandle=123)
        transport=NativeVoice(SimpleNamespace(_api=SimpleNamespace(control=root)),{})
        uia=SimpleNamespace(GetForegroundControl=lambda:SimpleNamespace(NativeWindowHandle=456))
        with self.assertRaisesRegex(RuntimeError,'voice-input-focus-not-confirmed'):
            transport.focus_input(uia)
        uia.GetForegroundControl=lambda:SimpleNamespace(NativeWindowHandle=123)
        editor.HasKeyboardFocus=False
        with self.assertRaisesRegex(RuntimeError,'voice-input-focus-not-confirmed'):
            transport.focus_input(uia)
        editor.HasKeyboardFocus=True
        transport.focus_input(uia)

    def test_current_install_never_advertises_or_starts_unverified_recording(self):
        self.assertFalse(NativeVoice(object(),{}).ready())
        self.assertFalse(NativeVoice(object(),{'verified':True,'buttonName':'语音','recordingLabels':['松开发送']}).ready())

    def test_client_version_drift_disables_previously_verified_transport(self):
        transport=NativeVoice(object(),{'verified':True,'buttonName':'语音',
            'recordingLabels':['松开发送'],'clientExe':'test.exe','clientVersion':'4.1.9.1'})
        fake=SimpleNamespace(GetFileVersionInfo=lambda *args:{'FileVersionMS':4*65536+2,'FileVersionLS':1})
        with patch.dict('sys.modules',{'win32api':fake}):
            self.assertFalse(transport.ready())

    def test_migrated_voice_outbox_is_passed_to_playback_process(self):
        # Playback imports voice_runtime in another interpreter. Its path check
        # must use the same migrated root as the sender, rather than legacy C:.
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            filename=Path(directory)/'voice'/'outbox'/'sample.wav'
            filename.parent.mkdir(parents=True)
            filename.write_bytes(b'placeholder')
            root=SimpleNamespace(ButtonControl=lambda **kwargs: SimpleNamespace(Exists=lambda _:True),
                                 GroupControl=lambda **kwargs: SimpleNamespace(Exists=lambda _:False))
            transport=NativeVoice(SimpleNamespace(_api=SimpleNamespace(control=root)),
                {'dataRoot':directory,'buttonName':'语音'})
            player=SimpleNamespace(stdout=io.StringIO('{"ready":false}\n'),
                poll=lambda:0, wait=lambda **kwargs:0)
            with patch.object(transport,'ready',return_value=True), \
                    patch.dict('sys.modules',{'wxauto4':SimpleNamespace(uia=object())}), \
                    patch('native_voice.subprocess.Popen',return_value=player) as spawn:
                with self.assertRaisesRegex(RuntimeError,'virtual-playback-not-ready'):
                    transport.send(str(filename))
            self.assertEqual(spawn.call_args.kwargs['env']['WHALE_DATA_ROOT'],str(Path(directory).resolve()))


class NativeTargetContextTests(unittest.TestCase):
    setUp = NativeRecordingLifecycleTests.setUp

    def send(self):
        return self.transport.send(str(self.file),target='trusted group',kind='group',command_id='op:123')

    def test_target_changed_during_player_ready_never_presses_alt_or_plays(self):
        self.wx.ChatInfo=lambda:{'chat_name':'some other chat','chat_type':'friend'}
        with self.assertRaisesRegex(RuntimeError,'voice-target-mismatch'):
            self.send()
        self.assertEqual(self.keys,[])
        self.assertEqual(self.input.getvalue(),'')
        self.assertEqual(self.state['kills'],1)

    def test_required_target_unavailable_aborts_without_shortcut(self):
        self.wx.ChatInfo=lambda:{}
        with self.assertRaisesRegex(RuntimeError,'voice-target-unavailable'):
            self.send()
        self.assertEqual(self.keys,[])
        self.assertEqual(self.input.getvalue(),'')

    def test_readable_change_before_play_cancels_confirmed_recording(self):
        replies=iter([{'chat_name':'trusted group','chat_type':'group'},
                      {'chat_name':'other','chat_type':'group'}])
        self.wx.ChatInfo=lambda:next(replies)
        with self.assertRaisesRegex(RuntimeError,'voice-target-mismatch'):
            self.send()
        self.assertEqual(self.input.getvalue(),'')
        self.assertIn(('down',0x08),self.keys)
        self.assertIn(('up',0xA5),self.keys)
        self.assertFalse(self.state['panel'])

    def test_readable_change_after_play_cancels_before_release_send(self):
        replies=iter([{'chat_name':'trusted group','chat_type':'group'}]*2+
                     [{'chat_name':'other','chat_type':'friend'}])
        self.wx.ChatInfo=lambda:next(replies)
        with self.assertRaisesRegex(RuntimeError,'voice-target-mismatch'):
            self.send()
        self.assertEqual(self.input.getvalue(),'PLAY\n')
        self.assertLess(self.keys.index(('down',0x08)),self.keys.index(('up',0xA5)))
        self.assertEqual(self.state['kills'],1)

    def test_unavailable_header_during_recorder_does_not_fake_mismatch(self):
        count=[0]
        def info():
            count[0]+=1
            if count[0]==1:return {'chat_name':'trusted group','chat_type':'group'}
            raise LookupError('recorder hides header')
        self.wx.ChatInfo=info
        # There is no fake self voice row, so the result remains explicitly
        # unknown, but the verified recording was allowed to play/release once.
        with patch.dict('sys.modules', {'wxauto4.msgs.msg':SimpleNamespace(parse_msg=lambda *args:None)}), \
                self.assertRaisesRegex(RuntimeError,'voice-send-outcome-unknown'):
            self.send()
        self.assertEqual(self.input.getvalue(),'PLAY\n')
        self.assertEqual(self.keys.count(('down',0xA5)),1)
        self.assertNotIn(('down',0x08),self.keys)

    def test_diagnostic_failure_does_not_replace_recording_timeout(self):
        self.wx.ChatInfo=lambda:{'chat_name':'trusted group','chat_type':'group'}
        self.transport.record_start_diagnostic.side_effect=OSError('fixture write failure')
        self.state['start']=False
        with self.assertRaisesRegex(RuntimeError,'recording-not-confirmed'):
            self.send()
        calls=self.transport.record_start_diagnostic.call_args_list
        self.assertEqual([c.kwargs['phase'] for c in calls],['before-alt','timeout'])
        self.assertEqual([c.kwargs['context']['commandId'] for c in calls],['op:123','op:123'])
        self.assertEqual(self.input.getvalue(),'')
        self.assertIn(('up',0xA5),self.keys)

    def test_invalid_partial_context_fails_before_any_player(self):
        with self.assertRaisesRegex(RuntimeError,'voice-context-invalid'):
            self.transport.send(str(self.file),target='trusted group')
        self.spawn.assert_not_called()


class NativeDiagnosticTests(unittest.TestCase):
    def test_bounded_two_stage_context_has_no_chat_name_or_content(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            (Path(directory)/'voice').mkdir()
            root=SimpleNamespace(NativeWindowHandle=123,
                GroupControl=lambda **kw:SimpleNamespace(Exists=lambda _:False),
                TextControl=lambda **kw:SimpleNamespace(Exists=lambda _:False))
            uia=SimpleNamespace(GetForegroundControl=lambda:SimpleNamespace(ClassName='Main',NativeWindowHandle=123),
                GetFocusedControl=lambda:SimpleNamespace(ClassName='Editor'),IsKeyPressed=lambda key:False)
            transport=NativeVoice(SimpleNamespace(_api=SimpleNamespace(control=root)),
                {'dataRoot':directory,'recordingLabels':['cancel']})
            context={'target':'private target name','kind':'group','commandId':'op:123'}
            transport.record_start_diagnostic(uia,context=context,phase='before-alt',elapsed_ms=0)
            transport.record_start_diagnostic(uia,context=context,phase='timeout',elapsed_ms=5010)
            raw=(Path(directory)/'voice/native-transport-diagnostic.local.json').read_text('utf8')
            result=json.loads(raw)
            self.assertNotIn(context['target'],raw)
            self.assertEqual([s['phase'] for s in result['stages']],['before-alt','timeout'])
            self.assertEqual(result['commandId'],'op:123')
            self.assertEqual(result['targetSha256'],hashlib.sha256(context['target'].encode()).hexdigest())
            self.assertEqual(result['targetKind'],'group')
            context['commandId']='op:next'
            transport.record_start_diagnostic(uia,context=context,phase='before-alt',elapsed_ms=0)
            updated=json.loads((Path(directory)/'voice/native-transport-diagnostic.local.json').read_text('utf8'))
            self.assertEqual(len(updated['stages']),1)


class NativeInputFocusSettleTests(unittest.TestCase):
    """Only fake UIA state/clock; no activation, keys, recording or media."""
    def exercise(self, foregrounds, focuses, *, error=False):
        state={'active':0,'focus':0,'foregroundReads':0,'focusReads':0,'time':0.0,'sleeps':[]}
        def activate(**kwargs):state['active']+=1;return False
        def setfocus():state['focus']+=1;return True
        class Editor:
            def Exists(self,_):return 1
            def SetFocus(self):return setfocus()
            @property
            def HasKeyboardFocus(self):
                index=state['focusReads'];state['focusReads']+=1
                return focuses[min(index,len(focuses)-1)]
        editor=Editor()
        root=SimpleNamespace(NativeWindowHandle=123,SetActive=activate,EditControl=lambda **_:editor)
        transport=NativeVoice(SimpleNamespace(_api=SimpleNamespace(control=root)),{})
        def foreground():
            index=state['foregroundReads'];state['foregroundReads']+=1
            return SimpleNamespace(NativeWindowHandle=foregrounds[min(index,len(foregrounds)-1)])
        def sleep(seconds):state['sleeps'].append(seconds);state['time']+=seconds
        with patch('native_voice.time.monotonic',side_effect=lambda:state['time']), \
                patch('native_voice.time.sleep',side_effect=sleep):
            if error:
                with self.assertRaisesRegex(RuntimeError,'voice-input-focus-not-confirmed'):
                    transport.focus_input(SimpleNamespace(GetForegroundControl=foreground))
            else:transport.focus_input(SimpleNamespace(GetForegroundControl=foreground))
        self.assertEqual(state['active'],1)
        self.assertEqual(state['focus'],1)
        self.assertLessEqual(state['time'],.500001)
        return state

    def test_delayed_qt_focus_succeeds_with_actual_integer_com_bool(self):
        state=self.exercise([456,123,123],[0,1])
        self.assertEqual(state['focusReads'],2)
        self.assertEqual(len(state['sleeps']),1)

    def test_never_acquired_foreground_times_out_without_reactivation(self):
        state=self.exercise([456],[1],error=True)
        self.assertAlmostEqual(state['time'],.5)
        self.assertGreater(len(state['sleeps']),1)

    def test_foreground_acquired_then_lost_aborts_immediately(self):
        state=self.exercise([456,123,456],[0,1],error=True)
        self.assertEqual(state['foregroundReads'],3)
        self.assertEqual(state['focusReads'],1)
        self.assertEqual(len(state['sleeps']),1)

    def test_foreground_already_acquired_before_setfocus_is_not_stolen_back(self):
        state=self.exercise([123,456],[1],error=True)
        self.assertEqual(state['foregroundReads'],2)
        self.assertEqual(state['focusReads'],0)
        self.assertEqual(state['sleeps'],[])

    def test_never_acquired_keyboard_focus_times_out_even_in_target_foreground(self):
        self.exercise([123],[0],error=True)

    def test_focus_requires_bool_or_com_integer_one(self):
        for value in (None,'1',1.0,2):
            with self.subTest(value=value):self.exercise([123],[value],error=True)
        self.exercise([123],[True])


if __name__=='__main__':unittest.main()
