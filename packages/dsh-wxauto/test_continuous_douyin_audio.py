"""No real recording, models, process spawning or UI: injected capture/Speech."""
import array
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from continuous_douyin_audio import ContinuousDouyinAudio
import continuous_douyin_audio as runtime


def pcm(frames,value=.1):
    return array.array('f',[value]*(frames*2)).tobytes()


class FakeCapture:
    scope_version=1
    normalization_version=1
    def __init__(self,callback):self.callback=callback;self.starts=0;self.stops=0;self.scope_verified=False
    def start(self):self.starts+=1;self.scope_verified=True;return True
    def stop(self):self.stops+=1;self.scope_verified=False
    def check(self):
        if self.scope_verified is not True:raise RuntimeError('fixture-unverified')
        return True
    def feed(self,frames,value=.1):self.callback(pcm(frames,value),frames)


class FakeSpeech:
    def __init__(self):self.calls=[];self.block=None;self.entered=threading.Event();self.text='synthetic audio';self.audio_status=None
    def transcribe(self,file):
        with wave.open(file,'rb') as f:
            facts=(f.getframerate(),f.getnchannels(),f.getnframes())
        self.calls.append((file,facts,hashlib.sha256(Path(file).read_bytes()).hexdigest()));self.entered.set()
        if self.block:self.block.wait(2)
        return {'text':self.text,'backend':'sensevoice-int8',**({'audioStatus':self.audio_status} if self.audio_status else {})}


class ContinuousTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name).resolve();self.config={'op':'start','pid':123,'cacheRoot':str(root/'cache'),
            'modelRoot':str(root/'models'),'queueSegments':1}
        self.events=[];self.captures=[];self.speech=FakeSpeech();self.factories=0;self.sources=[{'pid':123,'name':'msedge.exe'}]
        def capture(pid,callback):
            self.assertEqual(pid,123);value=FakeCapture(callback);self.captures.append(value);return value
        def speech():self.factories+=1;return self.speech
        self.options={'capture_factory':capture,'speech_factory':speech,'source_provider':lambda:self.sources,
            'emit':self.events.append,'clock':lambda:1000.,'chunk_frames':48,'stop_timeout':.1}
        self.instance=ContinuousDouyinAudio(self.config,**self.options)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        if self.speech.block:self.speech.block.set()
        self.instance.stop()
        if self.instance.worker:self.instance.worker.join(2)

    def wait(self,predicate,seconds=2):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(.005)
        self.fail('synthetic worker did not reach expected state')

    def test_denied_pid_does_not_construct_capture_or_load_speech(self):
        self.sources=[]
        with self.assertRaisesRegex(RuntimeError,'process-denied'):self.instance.start()
        self.assertEqual(self.captures,[]);self.assertEqual(self.factories,0)
        self.assertFalse(Path(self.config['cacheRoot']).exists())

    def test_exact_ten_second_frames_wav_hash_and_cpu_instance_reuse(self):
        self.instance=ContinuousDouyinAudio(self.config,**{**self.options,'chunk_frames':480000})
        self.instance.start();self.captures[-1].feed(480000)
        self.wait(lambda:any(e['event']=='segment' for e in self.events))
        first=next(e for e in self.events if e['event']=='segment')
        self.assertEqual(first['durationSeconds'],10);self.assertEqual(first['frames'],480000)
        self.assertEqual(self.speech.calls[0][1],(48000,2,480000))
        self.assertEqual(first['wavSha256'],self.speech.calls[0][2])
        self.assertEqual(first['videoIdentity'],'unknown');self.assertFalse(first['playingProof'])
        self.assertEqual(first['timestampBasis'],'callback-arrival-minus-audio-frames')
        started=next(e for e in self.events if e['event']=='started')
        self.assertEqual(started['protocolVersion'],2);self.assertTrue(started['processSpecificCapture'])
        self.assertEqual(started['captureScopeVersion'],1);self.assertEqual(started['callbackFrameNormalizationVersion'],1)
        self.wait(lambda:not Path(self.speech.calls[0][0]).exists())
        self.captures[-1].feed(480000);self.wait(lambda:len(self.speech.calls)==2)
        self.wait(lambda:len([e for e in self.events if e['event']=='segment'])==2)
        self.assertEqual(self.factories,1)

    def test_slow_asr_does_not_block_callback_and_reports_bounded_queue_gaps(self):
        self.speech.block=threading.Event();self.instance.start();capture=self.captures[-1]
        capture.feed(48);self.assertTrue(self.speech.entered.wait(1))
        started=time.monotonic();capture.feed(48);capture.feed(48)
        self.assertLess(time.monotonic()-started,.2)
        self.assertLessEqual(self.instance.jobs.qsize(),1);self.assertLessEqual(len(self.instance.pending),2)
        self.assertEqual(self.instance.dropped_frames,48)
        self.assertEqual(self.instance.pending_gaps['asr-backpressure']['frames'],48)
        self.speech.block.set();self.wait(lambda:len([e for e in self.events if e['event']=='segment'])==2)
        self.wait(lambda:any(e['event']=='gap' for e in self.events))
        gap=next(e for e in self.events if e['event']=='gap')
        self.assertEqual((gap['reason'],gap['frames']),('asr-backpressure',48))

    def test_pause_stops_capture_transcribes_exact_tail_and_resume_ignores_old_callbacks(self):
        self.instance.start();old=self.captures[-1];old.feed(24);self.instance.pause()
        self.assertEqual(old.stops,1);self.assertTrue(next(e for e in self.events if e['event']=='paused')['captureStopped'])
        self.wait(lambda:any(e['event']=='segment' for e in self.events))
        tail=next(e for e in self.events if e['event']=='segment')
        self.assertEqual(tail['frames'],24);self.assertEqual(tail['durationSeconds'],24/48000)
        self.assertEqual(tail['callbackObservedAt'],1000.);self.assertEqual(self.speech.calls[0][1],(48000,2,24))
        self.assertFalse(any(e.get('reason')=='pause-tail' for e in self.events))
        self.instance.start();new=self.captures[-1];old.feed(48);self.assertEqual(len(self.instance.pending),0)
        new.feed(48);self.wait(lambda:len([e for e in self.events if e['event']=='segment'])==2)
        self.assertEqual(len(self.speech.calls),2)

    def test_stop_is_bounded_discards_unprocessed_and_never_emits_late_transcript(self):
        self.speech.block=threading.Event();self.instance.start();capture=self.captures[-1]
        capture.feed(48);self.assertTrue(self.speech.entered.wait(1));capture.feed(48)
        started=time.monotonic();self.assertTrue(self.instance.stop());self.assertLess(time.monotonic()-started,.5)
        self.assertEqual(capture.stops,1)
        gaps=[e for e in self.events if e['event']=='gap'];self.assertEqual(sum(e['frames'] for e in gaps),96)
        self.speech.block.set();self.instance.worker.join(1)
        self.assertFalse(any(e['event']=='segment' for e in self.events))
        self.instance.stop();self.assertEqual(len([e for e in self.events if e['event']=='stopped']),1)

    def test_target_disappearance_stops_and_pause_resume_revalidates_pid(self):
        self.instance.start();capture=self.captures[-1];self.sources=[];self.instance.check_target()
        self.assertTrue(self.instance.closed);self.assertEqual(capture.stops,1)
        self.assertTrue(any(e.get('reason')=='target-unavailable' for e in self.events))

    def test_empty_transcription_is_not_called_silence_and_bad_pcm_is_gap(self):
        self.instance.start();capture=self.captures[-1];self.speech.text='';capture.feed(48)
        self.wait(lambda:any(e['event']=='segment' for e in self.events))
        self.assertEqual(next(e for e in self.events if e['event']=='segment')['asrStatus'],'no-transcript')
        self.speech.audio_status='no-detected-speech';capture.feed(48)
        self.wait(lambda:len([e for e in self.events if e['event']=='segment'])==2)
        self.assertEqual([e for e in self.events if e['event']=='segment'][-1]['asrStatus'],'no-speech')
        capture.callback(b'bad-bytes',48)
        self.wait(lambda:any(e.get('reason')=='invalid-callback' for e in self.events))

    def test_import_and_invalid_config_do_not_open_capture(self):
        for update in ({'pid':999,'op':'listen-system'},{'scope':'public'},{'queueSegments':100}):
            with self.assertRaises(RuntimeError):ContinuousDouyinAudio({**self.config,**update},**self.options)
        self.assertEqual(self.captures,[])

    def test_capture_stop_failure_is_not_reported_as_confirmed_pause(self):
        self.instance.start()
        def fail():raise RuntimeError('private SDK diagnostic')
        self.captures[-1].stop=fail
        self.instance.pause()
        self.assertTrue(self.instance.closed)
        self.assertFalse(self.events[-1]['captureStopped'])
        self.assertTrue(self.instance.stop())
        self.assertFalse(self.events[-1]['captureStopped'])
        self.assertNotIn('private SDK',json.dumps(self.events))

    def test_cli_eof_stops_capture_without_real_models_or_new_process(self):
        self.speech.video_sources=lambda:self.sources
        def speech_factory():
            self.assertEqual(runtime.os.environ['CUDA_VISIBLE_DEVICES'],'')
            self.assertEqual(runtime.os.environ['HF_HUB_OFFLINE'],'1')
            return self.speech
        capture=self.options['capture_factory']
        output=io.StringIO()
        with patch.dict(runtime.os.environ,dict(runtime.os.environ)),patch.dict('sys.modules',{
                'voice_runtime':SimpleNamespace(Speech=speech_factory),
                'dedicated_process_audio':SimpleNamespace(DedicatedProcessAudio=lambda pid,on_data:capture(pid,on_data))}),\
                patch.object(runtime.sys,'stdin',io.StringIO(json.dumps(self.config)+'\n')),\
                patch.object(runtime.sys,'stdout',output),patch.object(runtime.os,'_exit') as hard_exit:
            runtime.main()
        events=[json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(self.captures[-1].stops,1)
        self.assertEqual(events[-1]['event'],'stopped');self.assertTrue(events[-1]['captureStopped'])
        self.assertEqual(self.speech.calls,[]);hard_exit.assert_not_called()

    def test_multiple_partial_pause_resume_sequences_never_duplicate_or_recount_frames(self):
        for frames in (17,31,11):
            self.instance.start();self.captures[-1].feed(frames);self.instance.pause()
            self.wait(lambda:len([e for e in self.events if e['event']=='segment'])==len(self.captures))
        segments=[e for e in self.events if e['event']=='segment']
        self.assertEqual([e['sequence'] for e in segments],[0,1,2]);self.assertEqual([e['frames'] for e in segments],[17,31,11])
        self.assertEqual([x[1][2] for x in self.speech.calls],[17,31,11])
        self.assertEqual(sum(e['durationSeconds'] for e in segments),59/48000)
        self.instance.pause();self.assertEqual(self.instance.sequence,3)

    def test_partial_tail_backpressure_reports_gap_instead_of_fake_transcript(self):
        self.speech.block=threading.Event();self.instance.start();capture=self.captures[-1]
        capture.feed(48);self.assertTrue(self.speech.entered.wait(1));capture.feed(48);capture.feed(13)
        self.instance.pause();self.assertEqual(self.instance.dropped_frames,13)
        self.assertTrue(any(e['event']=='gap' and e['frames']==13 for e in self.events))
        self.speech.block.set();self.wait(lambda:len([e for e in self.events if e['event']=='segment'])==2)
        self.assertEqual([e['frames'] for e in self.events if e['event']=='segment'],[48,48])

    def test_unverified_scope_does_not_accept_even_a_synchronous_start_callback(self):
        def factory(pid,callback):
            value=FakeCapture(callback)
            def start():value.callback(pcm(48),48)  # No scope proof; startup callback is not accepted.
            value.start=start;self.captures.append(value);return value
        self.instance=ContinuousDouyinAudio(self.config,**{**self.options,'capture_factory':factory})
        with self.assertRaisesRegex(RuntimeError,'capture-start-failed'):self.instance.start()
        self.assertFalse(any(e['event']=='started' for e in self.events));self.assertEqual(self.speech.calls,[])
        self.assertFalse(self.instance.accepting)

    def test_periodic_scope_failure_stops_with_safe_code_and_no_fallback(self):
        self.instance.start();capture=self.captures[-1]
        def check():raise RuntimeError('private native scope details')
        capture.check=check;self.instance.check_target()
        self.assertTrue(self.instance.closed);self.assertEqual(capture.stops,1)
        self.assertTrue(any(e.get('code')=='continuous-audio-process-scope-unverified' for e in self.events))
        self.assertNotIn('private native',json.dumps(self.events));self.assertEqual(len(self.captures),1)

    def test_wrong_or_noninteger_scope_versions_never_emit_started(self):
        for name,value in [('scope_version',True),('normalization_version',2)]:
            events=[];captures=[]
            def factory(pid,callback):
                capture=FakeCapture(callback);setattr(capture,name,value);captures.append(capture);return capture
            instance=ContinuousDouyinAudio(self.config,**{**self.options,'capture_factory':factory,'emit':events.append})
            with self.assertRaisesRegex(RuntimeError,'capture-start-failed'):instance.start()
            self.assertTrue(instance.closed);self.assertEqual(captures[0].stops,1)
            self.assertFalse(any(e['event']=='started' for e in events));self.assertFalse(instance.accepting)
            instance.stop()

    def test_pause_capture_stop_timeout_never_flushes_unconfirmed_tail(self):
        release=threading.Event();self.addCleanup(release.set)
        self.instance.start();capture=self.captures[-1];capture.feed(19)
        capture.stop=lambda:release.wait(2)
        self.instance.pause();self.assertTrue(self.instance.closed)
        paused=next(e for e in self.events if e['event']=='paused');self.assertFalse(paused['captureStopped'])
        self.assertFalse(any(e['event']=='segment' for e in self.events));self.assertEqual(self.speech.calls,[])
        self.assertTrue(any(e['event']=='gap' and e['frames']==19 for e in self.events))
        self.assertTrue(self.instance.stop());self.assertFalse(self.events[-1]['captureStopped'])
        release.set()

    def test_blocked_gap_output_cannot_block_capture_and_coalesces_exact_loss(self):
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        def emit(event):
            if event['event']=='gap' and not entered.is_set():
                entered.set();release.wait(2)
            self.events.append(event)
        self.instance=ContinuousDouyinAudio(self.config,**{**self.options,'emit':emit})
        self.speech.block=threading.Event();self.instance.start();capture=self.captures[-1]
        capture.feed(48);self.assertTrue(self.speech.entered.wait(1));capture.feed(48);capture.feed(48)
        self.speech.block.set();self.assertTrue(entered.wait(1))
        # ASR/output is now blocked by a simulated unread stdout. Capture must
        # still consume callbacks, with bounded metadata rather than a write.
        started=time.monotonic()
        for _ in range(200):capture.feed(48)
        self.assertLess(time.monotonic()-started,.3)
        self.assertLessEqual(self.instance.jobs.qsize(),1)
        self.assertLessEqual(len(self.instance.pending_gaps),16)
        self.assertEqual(sum(e['frames'] for e in self.instance.pending_gaps.values()),200*48)
        self.assertEqual(self.instance.dropped_frames,201*48)
        release.set()
        self.wait(lambda:sum(e['frames'] for e in self.events if e['event']=='gap')==201*48)
        self.assertEqual(sum(e['gapCount'] for e in self.events if e['event']=='gap'),201)


if __name__=='__main__':unittest.main()
