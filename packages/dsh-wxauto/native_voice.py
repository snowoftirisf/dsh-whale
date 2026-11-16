"""Our wxauto-only native voice transport; enabled only after live verification.

Same waveform interface is usable by future singing backends. No Plus dependency,
protocol injection, WeChat database access, system-default audio device changes
or fallback attachment delivery. A missing UI prerequisite fails before playback.
"""
import json
import hashlib
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from native_voice_menus import close_avatar_context_menu
from message_direction import read_message_direction


def _right_alt(uia, *, down):
    """wxauto native key event with the right-Alt scan code (E0 38)."""
    flags = uia.KeyboardEventFlag.ExtendedKey
    flags |= uia.KeyboardEventFlag.KeyDown if down else uia.KeyboardEventFlag.KeyUp
    uia.keybd_event(0xA5, 0x38, flags, 0)


class NativeVoice:
    def __init__(self,wx,config):
        self.wx=wx
        self.config=config

    def ready(self):
        c=self.config
        if not (c.get('verified') is True and c.get('recordingLabels') and c.get('buttonName')
                and c.get('clientExe') and c.get('clientVersion')):
            return False
        try:
            import win32api
            info=win32api.GetFileVersionInfo(c['clientExe'],'\\')
            version='.'.join(map(str,[info['FileVersionMS']>>16,info['FileVersionMS']&65535,
                info['FileVersionLS']>>16,info['FileVersionLS']&65535]))
            return version==c['clientVersion']
        except Exception:
            return False

    def root(self):
        return self.wx._api.control

    def recording(self):
        root=self.root()
        # A chat message can contain the same words as the cancel label. Require
        # the observed native recording panel as well as its actual cancel cue.
        return (self.recording_panel()
                and any(root.TextControl(Name=name).Exists(0) for name in self.config['recordingLabels']))

    def recording_panel(self):
        return self.root().GroupControl(ClassName='mmui::ChatVoiceRecordView').Exists(0)

    def cancel_recording(self, uia):
        # Never send Backspace to an unconfirmed editor or another application.
        if not self.recording_panel():
            return False
        root=self.root()
        root.SetActive(waitTime=0.1)
        if uia.GetForegroundControl().NativeWindowHandle!=root.NativeWindowHandle:
            raise RuntimeError('voice-cancel-outcome-unknown')
        if not self.recording_panel():
            return False
        uia.PressKey(0x08,waitTime=0)
        uia.ReleaseKey(0x08,waitTime=0)
        until=time.monotonic()+1
        while self.recording_panel() and time.monotonic()<until:time.sleep(.05)
        if self.recording_panel():raise RuntimeError('voice-cancel-outcome-unknown')
        return True

    def focus_input(self, uia):
        # UIA SetFocus on the main window does not focus Qt's chat editor.
        # Only acquire foreground while sending, never during passive polling.
        root = self.root()
        root.SetActive(waitTime=0.1)
        editor = root.EditControl(ClassName='mmui::ChatInputField')
        if not editor.Exists(0):
            raise RuntimeError('voice-input-unavailable')
        seen_target = uia.GetForegroundControl().NativeWindowHandle == root.NativeWindowHandle
        editor.SetFocus()
        # Qt may grant foreground first and update UIA keyboard focus later.
        # Observe a bounded settle window, never re-activate or re-focus. Once
        # the target has foreground, losing it aborts instead of stealing it back.
        deadline = time.monotonic() + .5
        while True:
            foreground = uia.GetForegroundControl().NativeWindowHandle == root.NativeWindowHandle
            if seen_target and not foreground:
                raise RuntimeError('voice-input-focus-not-confirmed')
            seen_target = seen_target or foreground
            focused = editor.HasKeyboardFocus
            if foreground and type(focused) in (bool, int) and focused == 1:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError('voice-input-focus-not-confirmed')
            time.sleep(min(.025, remaining))

    def target_status(self, context):
        # Installed ChatInfo is compiled: whether it returns fresh header UI or
        # cached data is not proven. This is an API recheck, not a guarantee
        # against a user switching chats. Never label unavailable as matched.
        if context is None:
            return 'legacy-unscoped'
        try:
            info = self.wx.ChatInfo()
        except Exception:
            return 'unavailable'
        if not isinstance(info, dict):
            return 'unavailable'
        name, kind = info.get('chat_name'), info.get('chat_type')
        if ((isinstance(name, str) and name and name != context['target'])
                or (isinstance(kind, str) and kind and kind != context['kind'])):
            return 'mismatch'
        return 'matched' if name == context['target'] and kind == context['kind'] else 'unavailable'

    def click_recording_input(self, uia):
        # Qt can report UIA focus while the native recording shortcut is still
        # inactive. Request a click on the exact editor; only the later native
        # panel observation proves recording. Never alter the editor value.
        root = self.root()
        if uia.GetForegroundControl().NativeWindowHandle != root.NativeWindowHandle:
            raise RuntimeError('voice-input-focus-not-confirmed')
        editor = root.EditControl(ClassName='mmui::ChatInputField')
        if not editor.Exists(0):
            raise RuntimeError('voice-input-unavailable')
        editor.Click(waitTime=.1)
        if (uia.GetForegroundControl().NativeWindowHandle != root.NativeWindowHandle
                or type(editor.HasKeyboardFocus) not in (bool, int)
                or editor.HasKeyboardFocus != 1):
            raise RuntimeError('voice-input-focus-not-confirmed')

    def check_target(self, context, *, required):
        status = self.target_status(context)
        if status == 'mismatch':
            raise RuntimeError('voice-target-mismatch')
        if required and status == 'unavailable':
            raise RuntimeError('voice-target-unavailable')
        return status

    def record_start_diagnostic(self, uia, *, context=None, phase='timeout', elapsed_ms=None):
        """Bounded UI/key facts only; no chat text, audio or account names."""
        root=self.root()
        result={'schemaVersion':2,'at':time.time(),'phase':phase,'elapsedMs':elapsed_ms,
            'foregroundClass':uia.GetForegroundControl().ClassName,
            'foregroundIsTarget':uia.GetForegroundControl().NativeWindowHandle==root.NativeWindowHandle,
            'focusedClass':uia.GetFocusedControl().ClassName,
            'rightAltDown':uia.IsKeyPressed(0xA5),'leftCtrlDown':uia.IsKeyPressed(0xA2),
            'rightCtrlDown':uia.IsKeyPressed(0xA3),
            'recordPanel':root.GroupControl(ClassName='mmui::ChatVoiceRecordView').Exists(0),
            'cancelLabels':[root.TextControl(Name=n).Exists(0) for n in self.config['recordingLabels']]}
        if context is not None:
            result.update(commandId=context['commandId'], targetKind=context['kind'],
                targetSha256=hashlib.sha256(context['target'].encode('utf8')).hexdigest())
        if self.config.get('dataRoot'):
            file=Path(self.config['dataRoot'])/'voice'/'native-transport-diagnostic.local.json'
            # Keep only this attempt's bounded before/timeout facts. Never append
            # audio, names or chat text to a growing diagnostic history.
            if phase == 'before-alt':
                self._start_diagnostic = result
            # Preserve the existing top-level latest-sample fields for readers.
            output={**result,'stages':[result]}
            prior=getattr(self,'_start_diagnostic',None)
            if (phase != 'before-alt' and prior is not None
                    and prior.get('commandId') == result.get('commandId')):
                output['stages']=[prior,result]
            file.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
        return result

    def send(self,filename,*,target=None,kind=None,command_id=None):
        """Context is supplied only by the allowlisted worker route, not tools."""
        context=None
        if any(value is not None for value in (target,kind,command_id)):
            if (not isinstance(target,str) or not target or kind not in ('friend','group')
                    or not isinstance(command_id,str) or not command_id or len(command_id)>512
                    or any(ord(c)<32 for c in command_id)):
                raise RuntimeError('voice-context-invalid')
            context={'target':target,'kind':kind,'commandId':command_id}
        return self._record(filename,confirm_send=True,context=context)

    def probe(self,filename):
        """Local acceptance only: play into the native recorder, then cancel."""
        return self._record(filename,confirm_send=False)

    def _record(self,filename,*,confirm_send,context=None):
        if self.config.get('recorderMode') == 'click-panel-v1':
            from native_voice_click_runtime import record_click_panel
            return record_click_panel(self, filename, confirm_send=confirm_send, context=context)
        from wxauto4 import uia
        if not self.ready():raise RuntimeError('native-voice-not-verified')
        filename=Path(filename).resolve(strict=True)
        voice_data=Path(self.config['dataRoot'])/'voice' if self.config.get('dataRoot') else Path(__import__('os').environ.get('WHALE_RUNTIME_ROOT') or Path(__file__).resolve().parents[2])/'voice-data'
        allowed=(voice_data/'outbox').resolve()
        if not filename.is_relative_to(allowed) or filename.suffix.lower()!='.wav':
            raise RuntimeError('voice-file-denied')
        root=self.root()
        if self.recording_panel():raise RuntimeError('native-recorder-already-active')
        if not root.ButtonControl(Name=self.config['buttonName']).Exists(0):
            raise RuntimeError('recording-button-unavailable')
        python=Path(__import__('os').environ.get('WHALE_RUNTIME_ROOT') or Path(__file__).resolve().parents[2])/'.venv-whale-voice'/'Scripts'/'python.exe'
        player=subprocess.Popen([str(python),'-u',str(Path(__file__).with_name('voice_playback.py')),
            str(filename),self.config.get('outputDevice','CABLE Input (VB-Audio Virtual Cable)')],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            text=True,encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW,
            env={**os.environ, **({'WHALE_DATA_ROOT': str(Path(self.config['dataRoot']).resolve())}
                if self.config.get('dataRoot') else {})})
        events=queue.Queue()
        def read():
            for line in player.stdout:
                try:events.put(json.loads(line))
                except ValueError:pass
            events.put({'exited':True})
        threading.Thread(target=read,daemon=True).start()
        held=False
        played=False
        send_permitted=False
        self._start_diagnostic=None
        try:
            event=events.get(timeout=15)
            if event.get('ready') is not True:raise RuntimeError('virtual-playback-not-ready')
            before={tuple(c.GetRuntimeId()) for c in self.wx._api._chat_api.msgbox.GetChildren()}
            self.focus_input(uia)
            # Sender resolution can leave a native avatar popup owning Qt's
            # keyboard grab even while UIA reports the editor has focus. Close
            # only the observed avatar-menu shapes; never click an avatar/item.
            menu_cleanup=close_avatar_context_menu(root,uia)
            if menu_cleanup.get('closed') is True:
                self.focus_input(uia)  # Reacquire editor after the verified UI transition.
            self.click_recording_input(uia)
            # Player startup may take seconds. Re-query the public route API
            # after that wait, immediately before any recording shortcut.
            try:self.record_start_diagnostic(uia,context=context,phase='before-alt',elapsed_ms=0)
            except Exception:pass
            self.check_target(context,required=True)
            # Diagnostics/header reads may take time. If the user switched away
            # since the click, abort instead of sending a global shortcut to it.
            if uia.GetForegroundControl().NativeWindowHandle != root.NativeWindowHandle:
                raise RuntimeError('voice-input-focus-not-confirmed')
            started=time.monotonic()
            # Attempted key injection owns a matching release even if a native
            # wrapper raises after delivering the key-down event.
            held=True
            _right_alt(uia,down=True)
            until=time.monotonic()+5
            confirmed=False
            while time.monotonic()<until:
                if self.recording():
                    confirmed=True
                    break
                time.sleep(0.05)
            if not confirmed:
                try:self.record_start_diagnostic(uia,context=context,phase='timeout',elapsed_ms=round((time.monotonic()-started)*1000))
                except Exception:pass  # Diagnostics cannot change an uncertain action's outcome.
                raise RuntimeError('recording-not-confirmed')
            # The recorder can temporarily hide header controls. No live proof
            # establishes ChatInfo availability under that panel, so only a
            # readable mismatch aborts here; lack of header data is not proof
            # of a target change. The required pre-shortcut check still applies.
            self.check_target(context,required=False)
            player.stdin.write('PLAY\n');player.stdin.flush()
            event=events.get(timeout=58)
            if event.get('played') is not True:raise RuntimeError('audio-playback-failed')
            played=True
            if not confirm_send:
                if self.cancel_recording(uia) is not True:
                    raise RuntimeError('voice-probe-cancel-outcome-unknown')
                _right_alt(uia,down=False)
                held=False
                return {'canceled':True,'playback':event,'menuCleanup':menu_cleanup}
            self.check_target(context,required=False)
            send_permitted=True
            _right_alt(uia,down=False)
            held=False
            # A successful audio write is not proof of a sent WeChat voice row.
            until=time.monotonic()+8
            while time.monotonic()<until:
                for control in self.wx._api._chat_api.msgbox.GetChildren():
                    if tuple(control.GetRuntimeId()) in before:continue
                    if control.ClassName not in ('mmui::ChatVoiceItemView','mmui::ChatVoiceMessageItemView'):continue
                    try:
                        if read_message_direction(control,root,
                                msgbox=self.wx._api._chat_api.msgbox)=='self':return True
                    except Exception:
                        # Release already happened. Missing readback evidence
                        # stays unknown, never a failed send eligible for replay.
                        continue
                time.sleep(0.15)
            raise RuntimeError('voice-send-outcome-unknown')
        finally:
            try:
                if held:
                    # Cancel only the actual recorder, never an editor draft.
                    try:
                        if not played or not confirm_send or not send_permitted:
                            self.cancel_recording(uia)
                    finally:_right_alt(uia,down=False)
            finally:
                # A failed cancellation/key release must not orphan the player.
                if player.poll() is None:player.kill()
                player.wait(timeout=5)
