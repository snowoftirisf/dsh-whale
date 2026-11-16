"""Synthetic process PCM only: no real recording, browser, model or GPU."""
import hashlib
import sys
import tempfile
import time
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import soundfile as sf
import voice_runtime

class VideoCaptureTests(unittest.TestCase):
    def test_waveform_hash_and_duration_describe_the_actual_file_without_exposing_its_path(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            pcm=np.zeros((96000,2),dtype=np.float32)
            events=[]
            class Capture:
                def __init__(self,*,pid,on_data):
                    self.pid=pid;self.on_data=on_data;self.thread=None;events.append(('open',pid))
                    self.format={'sample_rate':48000,'channels':2,'bits_per_sample':32,'sample_format':'float32'}
                    self._backend=SimpleNamespace(_native=SimpleNamespace(is_process_specific=lambda:True))
                def start(self):
                    events.append('start')
                    def emit():
                        time.sleep(.005);self.on_data(pcm.tobytes(),-1)  # Installed SDK callback contract.
                    self.thread=threading.Thread(target=emit);self.thread.start()
                def stop(self):
                    events.append('stop')
                    if self.thread:self.thread.join(1)
            speech=voice_runtime.Speech()
            with patch.dict(sys.modules,{'proctap':SimpleNamespace(ProcessAudioCapture=Capture)}),\
                 patch.object(voice_runtime,'DATA',Path(folder)),\
                 patch.object(speech,'video_sources',return_value=[{'pid':123}]),\
                 patch.object(speech,'transcribe',return_value={'text':'','backend':'sensevoice-int8','audioStatus':'no-detected-speech'}):
                value=speech.listen_video({'pid':123,'seconds':2,'expiresAt':time.time()*1000+10000})
            file=next((Path(folder)/'captures').glob('*.wav'))
            self.assertEqual(value['audioSha256'],hashlib.sha256(file.read_bytes()).hexdigest())
            self.assertEqual(value['captureId'],file.stem)
            self.assertEqual((sf.info(file).frames,sf.info(file).samplerate,sf.info(file).channels),(96000,48000,2))
            self.assertEqual((value['capturedSeconds'],value['sampleRate'],value['channels']),(2,48000,2))
            self.assertEqual(value['audioStatus'],'no-detected-speech')
            self.assertEqual(value['captureProof'],{'protocolVersion':2,'processSpecificCapture':True,
                'captureScopeVersion':1,'callbackFrameNormalizationVersion':1})
            self.assertNotIn('file',value);self.assertNotIn('path',value)
            self.assertEqual(events,[('open',123),'start','stop'])

    def test_unapproved_process_cannot_start_capture_or_create_audio(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            speech=voice_runtime.Speech()
            with patch.dict(sys.modules,{'proctap':SimpleNamespace(ProcessAudioCapture=lambda **kw:self.fail('no recording'))}),\
                 patch.object(voice_runtime,'DATA',Path(folder)),\
                 patch.object(speech,'video_sources',return_value=[]):
                with self.assertRaisesRegex(ValueError,'audio-process-denied'):speech.listen_video({'pid':999,'seconds':2})
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_system_fallback_never_starts_or_transcribes_in_the_legacy_entry(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            starts=[]
            class Fallback:
                def __init__(self,*,pid,on_data):
                    self.pid=pid;self._backend=SimpleNamespace(_native=SimpleNamespace(is_process_specific=lambda:False))
                def start(self):starts.append(True)
                def stop(self):pass
            speech=voice_runtime.Speech()
            with patch.dict(sys.modules,{'proctap':SimpleNamespace(ProcessAudioCapture=Fallback)}),\
                 patch.object(voice_runtime,'DATA',Path(folder)),\
                 patch.object(speech,'video_sources',return_value=[{'pid':123}]),\
                 patch.object(speech,'transcribe',side_effect=AssertionError('fallback audio cannot reach ASR')):
                with self.assertRaisesRegex(RuntimeError,'scope-unverified'):
                    speech.listen_video({'pid':123,'seconds':1})
            self.assertEqual(starts,[]);self.assertEqual(list(Path(folder).iterdir()),[])

if __name__=='__main__':unittest.main()
