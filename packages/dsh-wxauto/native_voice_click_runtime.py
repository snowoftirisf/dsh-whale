"""Explicit click-panel adapter using the existing continuous audio helper.

Import is inert. NativeVoice chooses this only for an explicit recorderMode;
the default right-Alt transport is unchanged. A send uncertainty never retries.
"""
import json
import hashlib
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import traceback

from message_direction import read_message_direction
from native_voice_click_panel import ClickPanelError, send_click_panel


class ContinuousPlayer:
    def __init__(self, filename, config):
        plugin = Path(__file__).resolve().parent
        python = Path(__import__('os').environ.get('WHALE_RUNTIME_ROOT') or plugin.parents[1]) / '.venv-whale-voice/Scripts/python.exe'
        self.process = subprocess.Popen(
            [str(python), '-u', str(plugin / 'voice_playback.py'), str(filename),
             config.get('outputDevice', 'CABLE Input (VB-Audio Virtual Cable)')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW,
            env={**os.environ, 'WHALE_DATA_ROOT': str(Path(config['dataRoot']).resolve())})
        self.events = queue.Queue()
        self.play_requested = False
        def read():
            try:
                for line in self.process.stdout:
                    try: self.events.put(json.loads(line))
                    except ValueError: pass
            finally: self.events.put({'exited': True})
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def wait_ready(self, *, timeout):
        return self.events.get(timeout=timeout)

    def play(self):
        if self.play_requested:
            raise RuntimeError('native-click-play-already-requested')
        self.play_requested = True
        self.process.stdin.write('PLAY\n')
        self.process.stdin.flush()

    def wait_played(self, *, timeout):
        self.playback = self.events.get(timeout=timeout)
        return self.playback

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        self.reader.join(timeout=1)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None: stream.close()


class NewSelfVoiceReadback:
    def __init__(self, instance, context):
        self.instance, self.context = instance, context

    def baseline(self):
        return {tuple(c.GetRuntimeId()) for c in self.instance.wx._api._chat_api.msgbox.GetChildren()}

    def confirm(self, before, *, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.instance.check_target(self.context, required=False)
            root = self.instance.root()
            msgbox = self.instance.wx._api._chat_api.msgbox
            for control in msgbox.GetChildren():
                if tuple(control.GetRuntimeId()) in before:
                    continue
                if control.ClassName not in ('mmui::ChatVoiceItemView', 'mmui::ChatVoiceMessageItemView'):
                    continue
                try:
                    if read_message_direction(control, root, msgbox=msgbox) == 'self':
                        return True
                except Exception:
                    continue
            time.sleep(.15)
        return False


def record_click_panel(instance, filename, *, confirm_send, context):
    if (type(confirm_send) is not bool or not isinstance(context, dict)
            or context.get('kind') not in ('friend', 'group')
            or not isinstance(context.get('target'), str) or not context['target']
            or not isinstance(context.get('commandId'), str) or not context['commandId']):
        raise RuntimeError('native-click-context-invalid')
    if not instance.ready():
        raise RuntimeError('native-voice-not-verified')
    root = Path(instance.config['dataRoot']).resolve(strict=True)
    filename = Path(filename).resolve(strict=True)
    if not filename.is_relative_to((root / 'voice/outbox').resolve()) or filename.suffix.lower() != '.wav':
        raise RuntimeError('voice-file-denied')
    from wxauto4 import uia
    player = ContinuousPlayer(filename, instance.config)
    result = None
    facts = None
    error = None
    frames = []
    try:
        result = send_click_panel(instance, uia, context, player=player,
            readback=NewSelfVoiceReadback(instance, context), confirm_send=confirm_send)
        facts = result['facts']
    except ClickPanelError as exc:
        facts, error = exc.facts, str(exc)
        underlying = exc.__context__ or exc
        frames = [{'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                  for frame in traceback.extract_tb(underlying.__traceback__)[-8:]]
        raise
    finally:
        if facts is not None:
            try:
                report = {'at': time.time(), 'mode': 'click-panel-v1',
                    'confirmSend': confirm_send, 'facts': facts, 'error': error, 'failureFrames': frames,
                    'commandSha256': hashlib.sha256(context['commandId'].encode('utf8')).hexdigest()}
                (root / 'voice/native-click-panel.local.json').write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
            except Exception:
                pass  # Telemetry cannot alter a confirmed or uncertain send outcome.
    if confirm_send:
        return result['sent'] is True
    return {'canceled': result['canceled'], 'playback': player.playback,
            'clickPanel': result['facts']}
