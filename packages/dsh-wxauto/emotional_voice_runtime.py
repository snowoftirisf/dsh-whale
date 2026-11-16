"""Private IndexTTS2.5 stdio worker; no ASR, UI, listener or remote fallback."""
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import sys
import threading
import time
import uuid

from attachment_policy import local_path
from data_paths import absolute_data_path, resolve_data_paths

ROOT = Path(__import__('os').environ.get('WHALE_RUNTIME_ROOT') or Path(__file__).resolve().parents[2])
BACKEND = 'indextts2.5'
REVISION = 'c39ce5ba981572cb187443877ff559dfb246ce63'
MAIN_FILES = {'config.yaml', 'gpt.pth', 's2mel.pth', 'codec.pth', 'feat1.pt', 'feat2.pt',
              'wav2vec2bert_stats.pt', 'multilingual_zh_ja_yue_char_del.tiktoken',
              'qwen0.6bemo4-merge/config.json', 'qwen0.6bemo4-merge/model.safetensors',
              'qwen0.6bemo4-merge/tokenizer.json'}
AUX_FILES = {'hf_cache/w2v-bert-2.0/config.json', 'hf_cache/w2v-bert-2.0/preprocessor_config.json',
             'hf_cache/w2v-bert-2.0/model.safetensors', 'hf_cache/campplus_cn_common.bin',
             'hf_cache/bigvgan/config.json', 'hf_cache/bigvgan/bigvgan_generator.pt'}


def digest(file):
    value = hashlib.sha256()
    with file.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def validate_emotion(value):
    if value is None or value == '':
        return None
    if (not isinstance(value, str) or len(value) > 160 or not value.strip()
            or any(ord(c) < 32 or ord(c) == 127 or c in '\u0085\u2028\u2029' for c in value)
            or re.search(r'\[\[|\]\]|<\|[^>]*\|>|\[/?wx-', value, re.I)):
        raise ValueError('invalid-emotion')
    return value.strip()


def dependency_status(root):
    expected = root / '.venv-index-tts' / 'Scripts' / 'python.exe'
    names = ('torch', 'numpy', 'soundfile', 'omegaconf', 'transformers', 'modelscope')
    return {'environmentReady': local_path(sys.executable) == local_path(expected),
            'dependenciesReady': ((root / 'tools/index-tts25/tts_headroom.py').is_file()
                                  and all(importlib.util.find_spec(name) is not None for name in names))}


@contextlib.contextmanager
def no_network():
    """HF offline flags alone do not cover ModelScope/download helpers."""
    previous = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection,
                socket.getaddrinfo, socket.socket.sendto)
    def denied(*args, **kwargs):
        raise RuntimeError('voice-network-forbidden')
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = denied
    socket.getaddrinfo = socket.socket.sendto = denied
    try:
        yield
    finally:
        (socket.socket.connect, socket.socket.connect_ex, socket.create_connection,
         socket.getaddrinfo, socket.socket.sendto) = previous


def load_headroom_helper(root):
    file = root / 'tools/index-tts25/tts_headroom.py'
    if not file.is_file():
        raise RuntimeError('voice-headroom-helper-unavailable')
    spec = importlib.util.spec_from_file_location('_whale_emotional_headroom', file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.VocoderHeadroom


class HeadroomModelProxy:
    """Delegate the official model; retain only the latest numeric gain receipt."""
    def __init__(self, model, headroom_factory):
        self._model = model
        self._headroom_factory = headroom_factory
        self._infer_lock = threading.Lock()
        self.last_headroom = None

    def __getattr__(self, name):
        return getattr(self._model, name)

    def infer(self, *args, **kwargs):
        if not self._infer_lock.acquire(blocking=False):
            raise RuntimeError('voice-inference-already-active')
        self.last_headroom = None
        try:
            with self._headroom_factory(self._model.bigvgan, .95, capture=False) as gain:
                result = self._model.infer(*args, **kwargs)
            if not gain.stats:
                raise RuntimeError('voice-headroom-not-applied')
            # No strings, PCM, hashes, reference paths or spoken text in receipt.
            if not all(type(value) in (int, float) for value in gain.stats.values()):
                raise RuntimeError('invalid-headroom-receipt')
            self.last_headroom = dict(gain.stats)
            return result
        finally:
            self._infer_lock.release()


def official_model(root, model_dir, emotion):
    # Chinese workspace paths break upstream relative resource assumptions.
    os.chdir(root)
    relative = model_dir.relative_to(root).as_posix()
    relative.encode('ascii')
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      HF_HOME=str(root / 'installers/index-tts/hf-cache'),
                      NLTK_DATA=str(root / 'installers/index-tts/nltk-data'))
    sys.path.insert(0, str(root / 'sources/index-tts'))
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('cuda-unavailable')
    from indextts.infer_v2_5 import IndexTTS2
    os.environ['HF_HUB_CACHE'] = str(model_dir / 'hf_cache')
    headroom = load_headroom_helper(root)
    model = IndexTTS2(cfg_path=relative + '/config.yaml', model_dir=relative, use_bf16=True,
                     device='cuda:0', use_cuda_kernel=False, use_deepspeed=False,
                     use_accel=False, use_torch_compile=False, use_qwen_emo=emotion)
    return HeadroomModelProxy(model, headroom)


class EmotionalSpeech:
    def __init__(self, *, root=ROOT, data_root=None, model_root=None, model_factory=official_model,
                 dependencies=dependency_status):
        self.root = local_path(root)
        self.paths = resolve_data_paths(root=data_root or os.environ.get('WHALE_DATA_ROOT', os.path.join(os.path.expanduser('~'), 'WhaleData')))
        self.data = local_path(self.paths['voice'])
        self.config_file = self.data / 'voice.local.json'
        models = model_root or os.environ.get('WHALE_MODEL_ROOT', self.root / 'voice-data/models')
        self.model_dir = local_path(Path(models) / 'index-tts2.5')
        self.model_factory, self.dependencies = model_factory, dependencies
        self.model = None
        self.model_emotion = False
        self.loaded_signature = None

    def config(self):
        file = local_path(self.config_file)
        value = json.loads(file.read_text('utf-8-sig')) if file.exists() else {}
        if not isinstance(value, dict):
            raise ValueError('invalid-voice-config')
        return value

    def reference(self, config):
        value = config.get('referenceAudio')
        if not isinstance(value, str) or not value:
            raise ValueError('reference-audio-required')
        if config.get('referenceAudioBase') == 'dataRoot':
            file = absolute_data_path(value, root=self.paths['root'])
        else:
            file = local_path(value)
        if not any(file != parent and file.is_relative_to(parent) for parent in
                   (self.data, local_path(Path.home() / '.dsh'))):
            raise ValueError('reference-audio-path-denied')
        if (not file.is_file() or file.suffix.lower() not in ('.wav', '.flac', '.mp3', '.ogg', '.m4a')
                or not 0 < file.stat().st_size <= 100 * 1024 ** 2):
            raise ValueError('reference-audio-file-denied')
        if config.get('referenceSha256') is not None and config['referenceSha256'] != digest(file):
            raise ValueError('reference-audio-hash-mismatch')
        return file

    def selection(self, config):
        return (str(self.reference(config)), digest(self.reference(config)), config.get('voiceId'))

    def check_selection(self, expected):
        config = self.config()
        if (config.get('backend') != BACKEND or config.get('approved') is not True
                or self.selection(config) != expected):
            raise RuntimeError('voice-approval-changed')
        return config

    def release(self):
        self.model = None
        self.model_emotion = False
        self.loaded_signature = None
        import gc
        gc.collect()
        # A status/release call must not import torch or initialize CUDA.
        torch = sys.modules.get('torch')
        if torch is not None and torch.cuda.is_initialized():
            torch.cuda.empty_cache()
        return {'released': True, 'ttsLoaded': False}

    def model_files(self, *, verify_hashes=False):
        file = local_path(self.model_dir / 'download-manifest.json')
        manifest = json.loads(file.read_text('utf-8-sig'))
        if manifest.get('status') != 'all-files-verified' or manifest.get('revision') != REVISION:
            raise ValueError('models-incomplete')
        completed = manifest.get('completed', {})
        if not MAIN_FILES.issubset(completed):
            raise ValueError('models-incomplete')
        rows = [(name, info) for name, info in completed.items()]
        aux = {}
        for row in manifest.get('auxiliary', []):
            parts = Path(row['destination']).parts
            if 'hf_cache' not in parts:
                raise ValueError('invalid-model-manifest')
            name = Path(*parts[parts.index('hf_cache'):]).as_posix()
            aux[name] = row
        if not AUX_FILES.issubset(aux):
            raise ValueError('models-incomplete')
        rows += list(aux.items())
        signature = []
        for name, info in rows:
            target = absolute_data_path(name, root=self.model_dir)
            if (not target.is_file() or not isinstance(info.get('bytes'), int) or info['bytes'] <= 0
                    or target.stat().st_size != info['bytes']
                    or not re.fullmatch('[0-9a-f]{64}', info.get('sha256', ''))):
                raise ValueError('models-incomplete')
            if verify_hashes and digest(target) != info['sha256']:
                raise ValueError('model-hash-mismatch')
            signature.append((name, target.stat().st_size, target.stat().st_mtime_ns, info['sha256']))
        return tuple(sorted(signature))

    def status(self):
        config = self.config()
        try:
            self.model_files()
            relative = self.model_dir.relative_to(self.root).as_posix()
            relative.encode('ascii')
            models_ready = (self.root / 'sources/index-tts/indextts/infer_v2_5.py').is_file()
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            models_ready = False
        try:
            self.reference(config)
            reference_ready = True
        except (OSError, ValueError, RuntimeError):
            reference_ready = False
        selected = config.get('approved') is True and reference_ready
        dependencies = self.dependencies(self.root)
        return {'backend': BACKEND, 'modelsReady': models_ready, 'voiceSelected': selected,
                'emotionSupported': True, 'backendSelected': config.get('backend') == BACKEND,
                'referenceReady': reference_ready, **dependencies,
                'ready': bool(config.get('backend') == BACKEND and selected and models_ready
                              and dependencies['environmentReady'] and dependencies['dependenciesReady']),
                'ttsLoaded': self.model is not None, 'emotionModelLoaded': self.model_emotion}

    @staticmethod
    def new_timings():
        # Seconds, measured in this process only. Full model-file hash checking
        # is separate; ordinary reference/approval checks remain in preflight.
        return {name: 0.0 for name in ('preflightSeconds', 'hashVerifySeconds', 'modelLoadSeconds',
                                       'inferenceSeconds', 'postprocessSeconds', 'totalSeconds')}

    def ready_selection(self):
        state = self.status()
        if not state['backendSelected']:
            raise RuntimeError('voice-backend-not-selected')
        if not state['voiceSelected']:
            raise RuntimeError('voice-not-approved-or-reference-unavailable')
        if not state['ready']:
            raise RuntimeError('voice-models-or-environment-unavailable')
        config = self.config()
        selection = self.selection(config)
        signature = self.model_files()
        return config, selection, signature

    def ensure_model(self, signature, emotion, timings):
        """One cache/load strategy, called only under the existing offline guard."""
        if self.model is None or signature != self.loaded_signature or bool(emotion) and not self.model_emotion:
            phase_started = time.monotonic()
            self.model_files(verify_hashes=True)
            timings['hashVerifySeconds'] = time.monotonic() - phase_started
            phase_started = time.monotonic()
            self.model = None  # Never retain two GPU copies when loading QwenEmotion.
            import gc
            gc.collect()
            self.model = self.model_factory(self.root, self.model_dir, bool(emotion))
            self.model_emotion = bool(emotion)
            self.loaded_signature = signature
            timings['modelLoadSeconds'] = time.monotonic() - phase_started
            return True
        return False

    def warmup(self):
        """Preload the locally approved actor's backend without generating audio."""
        started = time.monotonic()
        timings = self.new_timings()
        config, selection, signature = self.ready_selection()
        timings['preflightSeconds'] = time.monotonic() - started
        with no_network():
            try:
                phase_started = time.monotonic()
                config = self.check_selection(selection)
                timings['preflightSeconds'] += time.monotonic() - phase_started
                reloaded = self.ensure_model(signature, config.get('emotionDirectionsEnabled') is True, timings)
                phase_started = time.monotonic()
                self.check_selection(selection)
                timings['preflightSeconds'] += time.monotonic() - phase_started
            except Exception:
                # Never keep a newly warmed model after a revoked/changed actor
                # selection or an uncertain load. release does not initialize CUDA.
                self.release()
                raise
        timings['totalSeconds'] = time.monotonic() - started
        timings['modelReloaded'] = reloaded
        return {'ttsLoaded': self.model is not None, 'emotionModelLoaded': self.model_emotion,
                'timings': timings}

    def synthesize(self, args):
        started = time.monotonic()
        timings = self.new_timings()
        text = args.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > 1500:
            raise ValueError('invalid-speech-length')
        emotion = validate_emotion(args.get('emotion'))
        _, selection, signature = self.ready_selection()
        timings['preflightSeconds'] = time.monotonic() - started
        with no_network():
            model_reloaded = self.ensure_model(signature, bool(emotion), timings)
            # Loading may take minutes: re-read approval immediately before inference.
            phase_started = time.monotonic()
            config = self.check_selection(selection)
            if emotion and config.get('emotionDirectionsEnabled') is False:
                raise RuntimeError('voice-emotion-disabled')
            folder = local_path(self.data / 'outbox')
            folder.mkdir(parents=True, exist_ok=True)
            output = local_path(folder / (uuid.uuid4().hex + '.wav'))
            kwargs = {'spk_audio_prompt': str(self.reference(config)), 'text': text.strip(),
                      'output_path': str(output), 'lang': 'ZH', 'verbose': False, 'use_random': False}
            if emotion:
                alpha = config.get('emotionAlpha', 0.3)
                if type(alpha) not in (float, int) or not 0 <= alpha <= 1:
                    raise ValueError('invalid-emotion-strength')
                kwargs.update(use_emo_text=True, emo_text=emotion, emo_alpha=alpha)
            timings['preflightSeconds'] += time.monotonic() - phase_started
            try:
                phase_started = time.monotonic()
                self.model.infer(**kwargs)
                timings['inferenceSeconds'] = time.monotonic() - phase_started
                phase_started = time.monotonic()
                self.check_selection(selection)
                result = self.audio_result(output, folder, emotion)
                try:
                    self.check_selection(selection)
                except Exception:
                    for segment in result['segments']:
                        Path(segment['file']).unlink(missing_ok=True)
                    raise
                result['referenceSha256'] = selection[1]
                if isinstance(self.model, HeadroomModelProxy):
                    result['headroom'] = dict(self.model.last_headroom)
                timings['postprocessSeconds'] = time.monotonic() - phase_started
            except Exception:
                output.unlink(missing_ok=True)
                raise
        timings['totalSeconds'] = time.monotonic() - started
        timings['modelReloaded'] = model_reloaded
        result['timings'] = timings
        return result

    def audio_result(self, output, folder, emotion):
        import numpy as np
        import soundfile as sf
        from voice_audio import segments
        audio, rate = sf.read(str(local_path(output)), dtype='float32', always_2d=True)
        if not len(audio) or audio.shape[1] not in (1, 2) or rate <= 0 or not np.isfinite(audio).all():
            raise RuntimeError('invalid-generated-audio')
        duration = len(audio) / rate
        pieces = [{'file': str(output), 'durationSeconds': round(duration, 2)}]
        if duration > 55:
            pieces = []
            for chunk in segments(audio, rate, max_seconds=55):
                file = local_path(folder / (uuid.uuid4().hex + '.wav'))
                sf.write(str(file), chunk, rate)
                pieces.append({'file': str(file), 'durationSeconds': round(len(chunk) / rate, 2)})
        result = {'file': str(output), 'durationSeconds': round(duration, 2), 'sampleRate': rate,
                  'backend': BACKEND, 'segments': pieces}
        if emotion:
            result['emotion'] = emotion
        return result

    def run(self, request):
        if request.get('op') == 'status':
            return self.status()
        if request.get('op') == 'release':
            if set(request) - {'id', 'op'}:
                raise ValueError('unsupported-release-parameters')
            return self.release()
        if request.get('op') == 'warmup':
            if set(request) - {'id', 'op'}:
                raise ValueError('unsupported-warmup-parameters')
            return self.warmup()
        if request.get('op') == 'synthesize':
            if set(request) - {'id', 'op', 'text', 'emotion', 'diagnostic'}:
                raise ValueError('unsupported-synthesis-parameters')
            return self.synthesize(request)
        raise ValueError('unsupported-operation')


def main(input_stream=None, output_stream=None, speech=None):
    input_stream, output_stream = input_stream or sys.stdin, output_stream or sys.stdout
    speech = speech or EmotionalSpeech()
    for line in input_stream:
        request = {}
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError('invalid-request')
            if request.get('op') == 'stop':
                break
            started = time.monotonic()
            with contextlib.redirect_stdout(sys.stderr):
                value = speech.run(request)
            result = {'id': request.get('id'), 'ok': True, 'value': value,
                      'wallSeconds': round(time.monotonic() - started, 2)}
        except Exception as error:
            code = str(error) if isinstance(error, (RuntimeError, ValueError)) and re.fullmatch('[a-z0-9-]+', str(error)) else type(error).__name__
            result = {'id': request.get('id') if isinstance(request, dict) else None, 'ok': False,
                      'code': code, 'detail': str(error)[:500] if isinstance(request, dict) and request.get('diagnostic') else ''}
        output_stream.write(json.dumps(result, ensure_ascii=False) + '\n')
        output_stream.flush()


if __name__ == '__main__':
    main()
