"""Offline interface/path tests with tiny generated fixtures; never load GPU."""
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from emotional_voice_runtime import (AUX_FILES, BACKEND, MAIN_FILES, REVISION, EmotionalSpeech,
                                     digest, main, no_network, validate_emotion)


class EmotionalVoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / '中文工作区'
        self.data = self.root / 'private'
        self.voice = self.data / 'voice'
        self.voice.mkdir(parents=True)
        self.reference = self.voice / 'reference.wav'
        sf.write(self.reference, np.ones(100) * .1, 100)
        source = self.root / 'sources/index-tts/indextts/infer_v2_5.py'
        source.parent.mkdir(parents=True); source.write_text('# fixture, not an implementation')
        self.models = self.root / 'voice-data/models'
        self.model_dir = self.models / 'index-tts2.5'
        self.model_dir.mkdir(parents=True)
        self.manifest = {'status': 'all-files-verified', 'revision': REVISION, 'completed': {}, 'auxiliary': []}
        for name in MAIN_FILES | AUX_FILES:
            file = self.model_dir / name
            file.parent.mkdir(parents=True, exist_ok=True); file.write_bytes(b'fixture')
            row = {'bytes': file.stat().st_size, 'sha256': digest(file)}
            if name in AUX_FILES:
                self.manifest['auxiliary'].append({**row, 'destination': str(file)})
            else:
                self.manifest['completed'][name] = row
        self.save_manifest()
        self.config = {'backend': BACKEND, 'approved': True, 'referenceAudioBase': 'dataRoot',
                       'referenceAudio': 'voice/reference.wav'}
        self.save_config()
        self.factories, self.calls = [], []
        self.duration = 1
        self.speech = EmotionalSpeech(root=self.root, data_root=self.data, model_root=self.models,
            model_factory=self.factory, dependencies=lambda root: {'environmentReady': True, 'dependenciesReady': True})

    def factory(self, root, model_dir, emotion):
        self.factories.append((root, model_dir, emotion))
        outer = self
        class Model:
            def infer(self, **kwargs):
                outer.calls.append(kwargs)
                print('upstream diagnostic banner')
                sf.write(kwargs['output_path'], np.ones(int(100 * outer.duration)) * .1, 100)
        return Model()

    def save_config(self):
        (self.voice / 'voice.local.json').write_text(json.dumps(self.config), encoding='utf8')

    def save_manifest(self):
        (self.model_dir / 'download-manifest.json').write_text(json.dumps(self.manifest), encoding='utf8')

    def test_status_is_independent_of_approval_and_never_loads_gpu(self):
        self.config['approved'] = False; self.save_config()
        state = self.speech.run({'op': 'status'})
        self.assertTrue(state['modelsReady']); self.assertFalse(state['ready'])
        self.assertFalse(state['voiceSelected']); self.assertTrue(state['emotionSupported'])
        self.assertEqual(self.factories, [])
        with self.assertRaisesRegex(RuntimeError, 'not-approved'):
            self.speech.run({'op': 'synthesize', 'text': 'hello'})
        self.assertEqual(self.factories, [])

    def test_explicit_backend_required_and_no_qwen_fallback(self):
        self.config['backend'] = 'qwen3-tts'; self.save_config()
        with self.assertRaisesRegex(RuntimeError, 'backend-not-selected'):
            self.speech.run({'op': 'synthesize', 'text': 'hello'})
        self.assertFalse(self.speech.status()['ready']); self.assertEqual(self.factories, [])

    def test_revocation_during_inference_does_not_publish_audio(self):
        original=self.factory
        def factory(*args):
            model=original(*args)
            infer=model.infer
            def change(**kwargs):
                infer(**kwargs)
                self.config['approved']=False
                self.save_config()
            model.infer=change
            return model
        self.speech.model_factory=factory
        with self.assertRaisesRegex(RuntimeError,'voice-approval-changed'):
            self.speech.run({'op':'synthesize','text':'你好'})
        self.assertEqual(list((self.voice/'outbox').glob('*.wav')),[])

    def test_pinned_reference_hash_is_required_to_match_actual_bytes(self):
        self.config['referenceSha256']='0'*64;self.save_config()
        self.assertFalse(self.speech.status()['ready'])
        self.assertEqual(self.factories,[])

    def test_release_drops_cached_model_and_next_call_reloads(self):
        self.speech.run({'op':'synthesize','text':'你好'})
        self.assertEqual(self.speech.run({'op':'release'}),{'released':True,'ttsLoaded':False})
        self.assertIsNone(self.speech.model)
        self.speech.run({'op':'synthesize','text':'你好'})
        self.assertEqual(len(self.factories),2)

    def assert_timings(self, result, *, reloaded):
        timings=result['timings']
        self.assertEqual(set(timings),{'preflightSeconds','hashVerifySeconds','modelLoadSeconds','inferenceSeconds','postprocessSeconds','totalSeconds','modelReloaded'})
        self.assertIs(timings['modelReloaded'],reloaded)
        self.assertTrue(all(type(value) in (int,float) and np.isfinite(value) and value>=0
                            for key,value in timings.items() if key!='modelReloaded'))
        self.assertGreaterEqual(timings['totalSeconds'],sum(value for key,value in timings.items() if key not in ('totalSeconds','modelReloaded')))
        self.assertNotIn(str(self.root),json.dumps(timings))
        self.assertNotIn('private spoken text',json.dumps(timings))
        if reloaded:
            self.assertGreater(timings['hashVerifySeconds'],0)
            self.assertGreater(timings['modelLoadSeconds'],0)
        else:
            self.assertEqual(timings['hashVerifySeconds'],0)
            self.assertEqual(timings['modelLoadSeconds'],0)

    def test_warm_second_call_times_no_reload_or_full_weight_hash_and_release_reloads(self):
        clock=iter(range(1000))
        with patch('emotional_voice_runtime.time.monotonic',side_effect=lambda:float(next(clock))), \
                patch.object(self.speech,'model_files',wraps=self.speech.model_files) as files:
            cold=self.speech.run({'op':'synthesize','text':'private spoken text'})
            warm=self.speech.run({'op':'synthesize','text':'private spoken text'})
            self.assert_timings(cold,reloaded=True)
            self.assert_timings(warm,reloaded=False)
            self.assertEqual(len(self.factories),1)
            self.assertEqual(sum(call.kwargs.get('verify_hashes') is True for call in files.call_args_list),1)
            self.speech.release()
            released=self.speech.run({'op':'synthesize','text':'private spoken text'})
            self.assert_timings(released,reloaded=True)
            self.assertEqual(len(self.factories),2)
            self.assertEqual(sum(call.kwargs.get('verify_hashes') is True for call in files.call_args_list),2)

    def test_emotion_upgrade_times_reload_once_and_reuses_for_later_emotion_or_neutral(self):
        clock=iter(range(1000))
        with patch('emotional_voice_runtime.time.monotonic',side_effect=lambda:float(next(clock))), \
                patch.object(self.speech,'model_files',wraps=self.speech.model_files) as files:
            neutral=self.speech.run({'op':'synthesize','text':'private spoken text'})
            upgraded=self.speech.run({'op':'synthesize','text':'private spoken text','emotion':'开心'})
            emotion_warm=self.speech.run({'op':'synthesize','text':'private spoken text','emotion':'轻声'})
            neutral_warm=self.speech.run({'op':'synthesize','text':'private spoken text'})
            self.assert_timings(neutral,reloaded=True)
            self.assert_timings(upgraded,reloaded=True)
            self.assert_timings(emotion_warm,reloaded=False)
            self.assert_timings(neutral_warm,reloaded=False)
            self.assertEqual([row[2] for row in self.factories],[False,True])
            self.assertEqual(sum(call.kwargs.get('verify_hashes') is True for call in files.call_args_list),2)

    def test_warmup_preloads_enabled_emotion_without_audio_and_all_later_calls_reuse(self):
        self.config['emotionDirectionsEnabled']=True;self.save_config()
        files_before=set(self.voice.rglob('*'))
        clock=iter(range(1000))
        with patch('emotional_voice_runtime.time.monotonic',side_effect=lambda:float(next(clock))), \
                patch.object(self.speech,'model_files',wraps=self.speech.model_files) as files:
            first=self.speech.run({'id':'warm','op':'warmup'})
            self.assert_timings(first,reloaded=True)
            self.assertTrue(first['ttsLoaded']);self.assertTrue(first['emotionModelLoaded'])
            self.assertEqual(self.calls,[])
            self.assertEqual(set(self.voice.rglob('*')),files_before)
            second=self.speech.run({'op':'warmup'})
            self.assert_timings(second,reloaded=False)
            self.assertEqual(first['timings']['inferenceSeconds'],0)
            self.assertEqual(first['timings']['postprocessSeconds'],0)
            neutral=self.speech.run({'op':'synthesize','text':'private spoken text'})
            emotional=self.speech.run({'op':'synthesize','text':'private spoken text','emotion':'轻快'})
            self.assert_timings(neutral,reloaded=False);self.assert_timings(emotional,reloaded=False)
            self.assertEqual([row[2] for row in self.factories],[True])
            self.assertEqual(sum(call.kwargs.get('verify_hashes') is True for call in files.call_args_list),1)
            self.assertNotIn('spk_audio_prompt',json.dumps(first))
            self.assertNotIn(str(self.reference),json.dumps(first))

    def test_warmup_default_neutral_and_release_forces_next_warmup_reload(self):
        first=self.speech.run({'op':'warmup'})
        self.assertTrue(first['ttsLoaded']);self.assertFalse(first['emotionModelLoaded'])
        self.assertEqual(self.calls,[])
        self.speech.release()
        second=self.speech.run({'op':'warmup'})
        self.assertTrue(second['timings']['modelReloaded'])
        self.assertEqual([row[2] for row in self.factories],[False,False])
        self.assertEqual(self.calls,[])

    def test_warmup_arbitrary_options_and_unapproved_selection_never_load(self):
        for key in ('text','emotion','model','path','referenceAudio','voiceId'):
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'unsupported-warmup'):
                self.speech.run({'op':'warmup',key:'not allowed'})
        self.config['approved']=False;self.save_config()
        with self.assertRaisesRegex(RuntimeError,'not-approved'):
            self.speech.run({'op':'warmup'})
        self.assertEqual(self.factories,[]);self.assertEqual(self.calls,[])

    def test_warmup_revocation_before_load_does_not_create_cached_model(self):
        with patch.object(self.speech,'check_selection',side_effect=RuntimeError('voice-approval-changed')):
            with self.assertRaisesRegex(RuntimeError,'approval-changed'):
                self.speech.run({'op':'warmup'})
        self.assertIsNone(self.speech.model)
        self.assertEqual(self.factories,[]);self.assertEqual(self.calls,[])

    def test_warmup_revocation_or_actor_change_during_load_releases_without_audio(self):
        original=self.factory
        for change in ('revoke','actor-change'):
            with self.subTest(change=change):
                self.config['approved']=True;self.config.pop('voiceId',None);self.save_config()
                def factory(*args):
                    model=original(*args)
                    if change=='revoke':self.config['approved']=False
                    else:self.config['voiceId']='another-selection'
                    self.save_config()
                    return model
                self.speech.model_factory=factory
                with self.assertRaisesRegex(RuntimeError,'approval-changed'):
                    self.speech.run({'op':'warmup'})
                self.assertIsNone(self.speech.model)
                self.assertIsNone(self.speech.loaded_signature)
                self.assertFalse(self.speech.model_emotion)
                self.assertEqual(self.calls,[])
                self.assertFalse((self.voice/'outbox').exists())

    def test_real_emotion_parameters_and_neutral_does_not_force_style(self):
        neutral = self.speech.run({'op': 'synthesize', 'text': '你好'})
        self.assertEqual(self.factories[0][2], False)
        self.assertNotIn('use_emo_text', self.calls[0]); self.assertNotIn('emo_text', self.calls[0])
        self.assertNotIn('emotion', neutral)
        result = self.speech.run({'op': 'synthesize', 'text': '你好', 'emotion': '有点开心，但轻声说'})
        self.assertEqual(self.factories[-1][2], True)
        self.assertIs(self.calls[-1]['use_emo_text'], True)
        self.assertEqual(self.calls[-1]['emo_text'], '有点开心，但轻声说')
        self.assertEqual(self.calls[-1]['emo_alpha'],0.3)
        self.assertEqual(result['emotion'], '有点开心，但轻声说')
        self.speech.run({'op': 'synthesize', 'text': '平常说'})
        self.assertEqual(len(self.factories), 2)
        self.assertNotIn('emo_text', self.calls[-1])
        self.assertEqual(self.calls[-1]['lang'], 'ZH')
        self.assertEqual(self.model_dir.relative_to(self.root).as_posix(), 'voice-data/models/index-tts2.5')

    def test_emotion_disable_and_strength_are_local_configuration_not_model_overrides(self):
        self.config['emotionDirectionsEnabled']=False;self.save_config()
        with self.assertRaisesRegex(RuntimeError,'voice-emotion-disabled'):
            self.speech.run({'op':'synthesize','text':'你好','emotion':'开心'})
        self.assertEqual(self.calls,[])
        self.config.update(emotionDirectionsEnabled=True,emotionAlpha=.2);self.save_config()
        self.speech.run({'op':'synthesize','text':'你好','emotion':'开心'})
        self.assertEqual(self.calls[-1]['emo_alpha'],.2)
        with self.assertRaisesRegex(ValueError,'unsupported-synthesis'):
            self.speech.run({'op':'synthesize','text':'你好','emotion':'开心','emo_alpha':1})

    def test_partial_aux_missing_main_and_hash_failure_never_load_model(self):
        self.manifest['auxiliary'].pop(); self.save_manifest()
        self.assertFalse(self.speech.status()['modelsReady'])
        with self.assertRaisesRegex(RuntimeError, 'unavailable'):
            self.speech.run({'op': 'synthesize', 'text': '你好'})
        self.assertEqual(self.factories, [])

    def test_same_size_corrupt_weight_is_rejected_before_model_load(self):
        (self.model_dir / 'gpt.pth').write_bytes(b'corrupt')  # Same size; full hash gate must catch it.
        with self.assertRaisesRegex(ValueError, 'hash-mismatch'):
            self.speech.run({'op': 'synthesize', 'text': '你好'})
        self.assertEqual(self.factories, [])

    def test_reference_traversal_and_external_path_denied(self):
        for value in ('../voice/reference.wav', 'config/not-reference.wav'):
            self.config['referenceAudio'] = value; self.save_config()
            self.assertFalse(self.speech.status()['ready'])
        self.config.update(referenceAudioBase=None, referenceAudio=str(self.model_dir / 'config.yaml'))
        self.save_config(); self.assertFalse(self.speech.status()['referenceReady'])

    def test_output_segments_keep_all_samples_and_are_at_most_55_seconds(self):
        self.duration = 111
        result = self.speech.run({'op': 'synthesize', 'text': '你好'})
        self.assertGreater(len(result['segments']), 2)
        total = 0
        for segment in result['segments']:
            file = Path(segment['file'])
            self.assertTrue(file.is_relative_to(self.voice / 'outbox'))
            data, rate = sf.read(file); total += len(data)
            self.assertLessEqual(len(data) / rate, 55)
        self.assertEqual(total, 11100)
        self.assertEqual(result['backend'], BACKEND)

    def test_emotion_and_input_validation_before_loading(self):
        for value in ('\n开心', '开心\r一点', 'x' * 161, '[[wx-voice]]', '<|speaker|>', ['happy']):
            with self.assertRaises(ValueError): validate_emotion(value)
        self.assertIsNone(validate_emotion(None))
        with self.assertRaisesRegex(ValueError, 'unsupported-synthesis'):
            self.speech.run({'op': 'synthesize', 'text': 'hi', 'referenceAudio': '/override.wav'})
        with self.assertRaisesRegex(ValueError, 'unsupported-operation'):
            self.speech.run({'op': 'transcribe', 'file': 'arbitrary'})
        self.assertEqual(self.factories, [])

    def test_stdio_matches_existing_client_and_hides_upstream_stdout(self):
        output = io.StringIO()
        main(io.StringIO(json.dumps({'id': 'one', 'op': 'synthesize', 'text': '你好'}) + '\n' +
                        '[]\n' + json.dumps({'id': 'stop', 'op': 'stop'}) + '\n'), output, self.speech)
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]['ok']); self.assertIn('value', rows[0])
        self.assertFalse(rows[1]['ok']); self.assertEqual(rows[1]['detail'], '')
        self.assertNotIn(str(self.reference), json.dumps(rows, ensure_ascii=False))
        self.assertEqual(rows[0]['value']['referenceSha256'], digest(self.reference))

    def test_approval_revoked_during_load_prevents_inference(self):
        def load_then_revoke(root, folder, emotion):
            model = self.factory(root, folder, emotion)
            self.config['approved'] = False; self.save_config()
            return model
        self.speech.model_factory = load_then_revoke
        with self.assertRaisesRegex(RuntimeError, 'approval-changed'):
            self.speech.run({'op': 'synthesize', 'text': '你好'})
        self.assertEqual(self.calls, [])

    def test_network_blocked_even_when_library_ignores_offline_flags(self):
        original = socket.create_connection
        with no_network():
            with self.assertRaisesRegex(RuntimeError, 'network-forbidden'):
                socket.create_connection(('example.invalid', 443))
            with socket.socket() as sock:
                with self.assertRaisesRegex(RuntimeError, 'network-forbidden'):
                    sock.connect(('127.0.0.1', 443))
        self.assertIs(socket.create_connection, original)


if __name__ == '__main__':
    unittest.main()
