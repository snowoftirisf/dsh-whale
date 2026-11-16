"""Private JSON-lines speech worker. No WeChat controls, HTTP listener or LLM.

ASR: official sherpa-onnx SenseVoice API (CPU). TTS: official Qwen3-TTS API.
The choice of CPU SenseVoice and Silero VAD was informed by Yuuyuko-uu's
https://github.com/Yuuyuko-uu/dsh-fish-buddy ; no UI/assets/code copied.
"""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid
import threading

OUT = sys.stdout
ROOT = Path(__import__('os').environ.get('WHALE_RUNTIME_ROOT') or Path(__file__).resolve().parents[2])
LEGACY_DATA = ROOT / 'voice-data'
DATA_ROOT = Path(os.environ['WHALE_DATA_ROOT']).resolve() if os.environ.get('WHALE_DATA_ROOT') else None
DATA = DATA_ROOT / 'voice' if DATA_ROOT else LEGACY_DATA
# Model weights are optional external assets, not duplicated during migration.
MODELS = Path(os.environ.get('WHALE_MODEL_ROOT', LEGACY_DATA / 'models'))
CONFIG = DATA / 'voice.local.json'


def clone_parameters(config):
    x_vector_only=config.get('xVectorOnly',False)
    if not isinstance(x_vector_only,bool):raise ValueError('invalid-x-vector-mode')
    reference_text=None if x_vector_only else str(config.get('referenceText','')).strip()
    if not x_vector_only and not reference_text:raise ValueError('reference-transcript-required')
    return x_vector_only,reference_text


def audio_file(value):
    p = Path(value).resolve(strict=True)
    allowed = [DATA.resolve(), (Path.home() / '.dsh').resolve()]
    if not any(p.is_relative_to(root) for root in allowed) or not p.is_file():
        raise ValueError('audio-path-denied')
    if p.suffix.lower() not in ('.wav', '.flac', '.mp3', '.ogg', '.m4a') or p.stat().st_size > 100 * 1024**2:
        raise ValueError('audio-file-denied')
    return p


class Speech:
    def __init__(self):
        self.asr = None
        self.vad = None
        self.tts = None
        self.tts_kind = None
        self.clone_prompt = None
        self.clone_key = None

    def config(self):
        return json.loads(CONFIG.read_text('utf-8-sig')) if CONFIG.exists() else {}

    def video_sources(self):
        import psutil
        sources = []
        for process in psutil.process_iter(['pid','name','cmdline']):
            try:
                info = process.info
                command = ' '.join(info['cmdline'] or []).lower()
                if info['name'].lower() not in ('msedge.exe','chrome.exe') or '--type=' in command:
                    continue
                # Capture only the dedicated DSH Douyin browser, never arbitrary
                # desktop audio, WeChat calls or the user's personal browser.
                if '--user-data-dir=' not in command or '.dsh' not in command or 'douyin' not in command:
                    continue
                sources.append({'pid':info['pid'],'name':info['name']})
            except (psutil.AccessDenied,psutil.NoSuchProcess):
                pass
        return sources

    def listen_video(self,args):
        import numpy as np
        import soundfile as sf
        from dedicated_process_audio import DedicatedProcessAudio
        pid = args.get('pid')
        if time.time()*1000>args.get('expiresAt',float('inf')):
            raise RuntimeError('audio-request-expired')
        seconds = args.get('seconds',12)
        if not isinstance(seconds,(int,float)) or not 1<=seconds<=30:
            raise ValueError('invalid-capture-duration')
        if pid not in {x['pid'] for x in self.video_sources()}:
            raise ValueError('audio-process-denied')
        blocks = []
        frames = 0
        lock = threading.Lock()
        def on_audio(pcm,count):
            nonlocal frames
            with lock:
                if frames < 48000*31:
                    blocks.append(bytes(pcm));frames+=count
        capture=DedicatedProcessAudio(pid=pid,on_data=on_audio)
        started=None
        try:
            capture.start()
            started=time.time();deadline=time.monotonic()+seconds
            while time.monotonic()<deadline:
                capture.check()
                time.sleep(min(.05,max(0,deadline-time.monotonic())))
            capture.check()
        finally:
            capture.stop()
        with lock:
            data=np.frombuffer(b''.join(blocks),dtype=np.float32)
        if len(data)%2: data=data[:-1]
        folder=DATA/'captures';folder.mkdir(parents=True,exist_ok=True)
        filename=folder/(uuid.uuid4().hex+'.wav')
        sf.write(str(filename),data.reshape(-1,2),48000)
        value=self.transcribe(str(filename))
        return {**value,'source':'dedicated-douyin-browser-audio','startedAt':started,
                'capturedSeconds':round(len(data)/2/48000,2),
                'audioSha256':hashlib.sha256(filename.read_bytes()).hexdigest(),
                'captureId':filename.stem,'sampleRate':48000,'channels':2,
                'captureProof':{'protocolVersion':2,'processSpecificCapture':True,
                    'captureScopeVersion':1,'callbackFrameNormalizationVersion':1},
                'scope':'只转写这一段实际播放的对白，不代表理解整条视频或音乐'}

    def transcribe(self, value):
        import numpy as np
        import soundfile as sf
        import sherpa_onnx
        samples, sr = sf.read(str(audio_file(value)), dtype='float32', always_2d=True)
        if len(samples) / sr > 600:
            raise ValueError('audio-too-long')
        samples = samples.mean(axis=1)
        if sr != 16000:
            from math import gcd
            from scipy.signal import resample_poly
            factor = gcd(sr,16000)
            samples = resample_poly(samples,16000//factor,sr//factor).astype(np.float32)
        # SenseVoice can hallucinate words on silence. Require speech evidence.
        if not len(samples) or float(np.max(np.abs(samples))) < 0.0001:
            return {'text':'', 'backend':'sensevoice-int8', 'audioStatus':'no-detected-speech',
                    'durationSeconds':round(len(samples)/16000,2)}
        vad_config = sherpa_onnx.VadModelConfig()
        vad_config.silero_vad.model = str(MODELS/'vad'/'silero_vad.onnx')
        vad_config.silero_vad.min_speech_duration = 0.1
        vad_config.sample_rate = 16000
        vad = sherpa_onnx.VoiceActivityDetector(vad_config, buffer_size_in_seconds=600)
        for at in range(0,len(samples),512):
            block = samples[at:at+512]
            if len(block)<512: block=np.pad(block,(0,512-len(block)))
            vad.accept_waveform(block)
        vad.flush()
        if vad.empty():
            return {'text':'', 'backend':'sensevoice-int8', 'audioStatus':'no-detected-speech',
                    'durationSeconds':round(len(samples)/16000,2)}
        if self.asr is None:
            folder = MODELS / 'sensevoice-int8'
            self.asr = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=str(folder / 'model.int8.onnx'), tokens=str(folder / 'tokens.txt'),
                num_threads=4, use_itn=True, language='auto', provider='cpu')
        stream = self.asr.create_stream()
        stream.accept_waveform(16000, np.asarray(samples, dtype=np.float32))
        self.asr.decode_stream(stream)
        return {'text': stream.result.text.strip(), 'backend': 'sensevoice-int8',
                'durationSeconds': round(len(samples)/16000, 2)}

    def load_tts(self, kind):
        if self.tts_kind == kind:
            return self.tts
        import gc
        import torch
        from qwen_tts import Qwen3TTSModel
        # Design is used only for auditions. Never hold both 1.7B models on GPU.
        self.tts = None
        self.tts_kind = None
        self.clone_prompt = None
        self.clone_key = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        if not torch.cuda.is_available():
            raise RuntimeError('cuda-unavailable')
        folder = MODELS / ('qwen-voicedesign' if kind == 'design' else 'qwen-base')
        self.tts = Qwen3TTSModel.from_pretrained(str(folder), device_map='cuda:0',
            dtype=torch.bfloat16, attn_implementation='sdpa', local_files_only=True)
        self.tts_kind = kind
        return self.tts

    def synthesize(self, args, design=False):
        import soundfile as sf
        text = str(args.get('text', '')).strip()
        if not text or len(text) > 1500:
            raise ValueError('invalid-speech-length')
        model = self.load_tts('design' if design else 'base')
        if design:
            description = str(args.get('description', '')).strip()
            if not description:
                raise ValueError('voice-description-required')
            wavs, sr = model.generate_voice_design(text=text, language='Chinese', instruct=description)
        else:
            config = self.config()
            reference_value = config.get('referenceAudio', '')
            if config.get('referenceAudioBase') == 'dataRoot':
                if DATA_ROOT is None:
                    raise ValueError('voice-data-root-required')
                reference_value = DATA_ROOT / reference_value
            reference = audio_file(reference_value)
            x_vector_only,reference_text = clone_parameters(config)
            key = (str(reference), reference.stat().st_mtime_ns, reference_text, x_vector_only)
            if self.clone_key != key:
                self.clone_prompt = model.create_voice_clone_prompt(ref_audio=str(reference),
                    ref_text=reference_text, x_vector_only_mode=x_vector_only)
                self.clone_key = key
            wavs, sr = model.generate_voice_clone(text=text, language='Chinese',
                voice_clone_prompt=self.clone_prompt)
        folder = DATA / ('auditions' if design else 'outbox')
        folder.mkdir(parents=True, exist_ok=True)
        filename = folder / (uuid.uuid4().hex + '.wav')
        sf.write(str(filename), wavs[0], sr)
        duration = len(wavs[0]) / sr
        pieces = [{'file':str(filename),'durationSeconds':round(duration,2)}]
        if not design and duration>55:
            from voice_audio import segments
            pieces=[]
            for segment in segments(wavs[0],sr):
                segment_file=folder/(uuid.uuid4().hex+'.wav')
                sf.write(str(segment_file),segment,sr)
                pieces.append({'file':str(segment_file),'durationSeconds':round(len(segment)/sr,2)})
        return {'file': str(filename), 'durationSeconds': round(duration, 2),
                'sampleRate': sr, 'backend': 'qwen3-tts', 'segments':pieces}

    def run(self, args):
        op = args.get('op')
        if op == 'release':
            if set(args) - {'id','op'}:
                raise ValueError('unsupported-release-parameters')
            self.tts = self.tts_kind = self.clone_prompt = self.clone_key = None
            import gc
            gc.collect()
            torch = sys.modules.get('torch')
            if torch is not None and torch.cuda.is_initialized():
                torch.cuda.empty_cache()
            return {'released': True, 'ttsLoaded': False}
        if op == 'status':
            def complete(folder):
                p=MODELS/folder
                return all((p/name).is_file() for name in ('config.json','model.safetensors','speech_tokenizer/model.safetensors'))
            return {'asrReady': all((MODELS/'sensevoice-int8'/p).is_file() for p in ('model.int8.onnx','tokens.txt')) and (MODELS/'vad'/'silero_vad.onnx').is_file(),
                'baseReady': complete('qwen-base'),
                'designReady': complete('qwen-voicedesign'),
                'voiceSelected': self.config().get('approved') is True and bool(self.config().get('referenceAudio')),
                'asrLoaded': self.asr is not None, 'ttsLoaded': self.tts_kind}
        if op == 'transcribe':
            return self.transcribe(args['file'])
        if op == 'video-sources':
            return {'sources':self.video_sources()}
        if op == 'listen-video':
            return self.listen_video(args)
        if op in ('synthesize', 'design'):
            return self.synthesize(args, design=op == 'design')
        raise ValueError('unsupported-operation')


def main():
    speech = Speech()
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            if request.get('op') == 'stop':
                break
            started = time.monotonic()
            with contextlib.redirect_stdout(sys.stderr):
                value = speech.run(request)
            result = {'id': request.get('id'), 'ok': True, 'value': value,
                      'wallSeconds': round(time.monotonic()-started, 2)}
        except Exception as exc:
            # Never echo text/reference paths into production status logs.
            result = {'id': request.get('id'), 'ok': False, 'code': type(exc).__name__,
                      'detail': str(exc)[:500] if request.get('diagnostic') else ''}
        OUT.write(json.dumps(result, ensure_ascii=False) + '\n')
        OUT.flush()


if __name__ == '__main__':
    main()
