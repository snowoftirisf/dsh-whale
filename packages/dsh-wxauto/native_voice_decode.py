"""Bounded CPU SILK decoder. CLI is intended to run in an isolated subprocess.

No UI, network, torch or ASR/model imports. Only the default decoder lazily
imports the pinned pysilk-mod package. No source audio is modified.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid
import wave

HEADER = b'\x02#!SILK_V3'
MAX_SOURCE = 2 * 1024 * 1024
MAX_DURATION_MS = 120000
SAMPLE_RATE = 16000
PADDING_MS = 120  # At most one 100ms SILK packet plus a 20ms frame boundary.


class VoiceDecodeError(RuntimeError):
    pass


def _fail(code):
    raise VoiceDecodeError('native-voice-decode-' + code)


def _guard(path, base, *, exists=False):
    path, base = Path(path), Path(base)
    if not path.is_absolute() or not base.is_absolute():
        _fail('path-denied')
    # Check every actual ancestor before resolve can hide a reparse point.
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
                _fail('link-denied')
    resolved = path.resolve(strict=exists)
    if not resolved.is_relative_to(base.resolve()):
        _fail('path-denied')
    if resolved.exists():
        info = resolved.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            _fail('file-denied')
    return resolved


def _read_source(file, expected):
    before = file.stat()
    if not len(HEADER) + 3 <= before.st_size <= MAX_SOURCE or before.st_nlink != 1:
        _fail('source-size')
    with file.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        fields = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        if fields(before) != fields(opened):
            _fail('source-changed')
        blob = stream.read(MAX_SOURCE + 1)
        if fields(opened) != fields(os.fstat(stream.fileno())):
            _fail('source-changed')
    if hashlib.sha256(blob).hexdigest() != expected:
        _fail('source-hash-mismatch')
    if not blob.startswith(HEADER):
        _fail('unsupported-header')
    # Validate the Tencent length-prefixed packet framing before native code.
    # This conservative profile caps a packet at five 250-byte SILK frames.
    offset, packets = len(HEADER), 0
    while offset < len(blob):
        if offset + 2 > len(blob):
            _fail('source-framing')
        size = int.from_bytes(blob[offset:offset + 2], 'little', signed=True)
        offset += 2
        if not 1 <= size <= 1250 or offset + size > len(blob):
            _fail('source-framing')
        offset += size
        packets += 1
        if packets > MAX_DURATION_MS // 20:
            _fail('source-framing')
    return blob


def decode_native_voice(source, *, data_root, expected_sha256, duration_ms, output=None, decoder=None):
    """Decode one hash-qualified incoming SILK into an atomic mono16k PCM WAV.

    decoder injection is for offline tests. With the real backend only
    pysilk-mod==1.6.4 is accepted. Existing output is reused solely by exact WAV
    hash equality; different output is never overwritten. Failures are fixed
    codes and never contain file names, audio or decoder exception text.
    """
    temporary = None
    try:
        if not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
            _fail('invalid-hash')
        if type(duration_ms) is not int or not 1 <= duration_ms <= MAX_DURATION_MS:
            _fail('invalid-duration')
        root = Path(data_root)
        if not root.is_absolute():
            _fail('path-denied')
        source = _guard(source, root / 'voice/incoming-native', exists=True)
        destination = _guard(output if output is not None else source.with_suffix('.wav'), root / 'voice')
        if destination.suffix.lower() != '.wav' or destination == source:
            _fail('output-denied')
        blob = _read_source(source, expected_sha256)
        if decoder is None:
            from importlib.metadata import version
            if version('pysilk-mod') != '1.6.4':
                _fail('decoder-version')
            import pysilk
            decoder = pysilk.decode
            decoder_version = '1.6.4'
        else:
            decoder_version = 'injected-test-decoder'
        pcm = decoder(blob, to_wav=False, sample_rate=SAMPLE_RATE)
        if not isinstance(pcm, (bytes, bytearray, memoryview)) or not 0 < len(pcm) <= SAMPLE_RATE * 2 * 120:
            _fail('invalid-pcm')
        if len(pcm) % (SAMPLE_RATE * 2 // 50):  # Whole 20ms mono int16 frames.
            _fail('invalid-pcm')
        frames = len(pcm) // 2
        actual_ms = frames * 1000 / SAMPLE_RATE
        if abs(actual_ms - duration_ms) > PADDING_MS:
            _fail('duration-mismatch')
        if not any(pcm):
            _fail('silent-pcm')
        # Revalidate the exact original source after native decoding as well.
        source = _guard(source, root / 'voice/incoming-native', exists=True)
        if _read_source(source, expected_sha256) != blob:
            _fail('source-changed')
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(SAMPLE_RATE)
            wav.writeframes(bytes(pcm))
        encoded = buffer.getvalue(); digest = hashlib.sha256(encoded).hexdigest()
        destination = _guard(destination, root / 'voice')
        cached = destination.exists()
        if cached:
            if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                _fail('output-hash-conflict')
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            _guard(destination, root / 'voice')
            temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.tmp')
            with temporary.open('xb') as stream:
                stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
            _guard(destination, root / 'voice')
            try:
                if os.name == 'nt':
                    # Windows rename fails if destination exists (no replace).
                    os.rename(temporary, destination)
                else:
                    os.link(temporary, destination, follow_symlinks=False)
                    temporary.unlink()
                temporary = None
            except FileExistsError:
                _guard(destination, root / 'voice', exists=True)
                if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                    _fail('output-hash-conflict')
                cached = True
        return {'file': str(destination), 'sha256': digest, 'sourceSha256': expected_sha256,
                'durationMilliseconds': actual_ms, 'sampleRate': SAMPLE_RATE, 'channels': 1,
                'sampleWidthBytes': 2, 'frames': frames, 'backend': 'pysilk-mod',
                'decoderVersion': decoder_version, 'cached': cached}
    except VoiceDecodeError:
        raise
    except Exception:
        raise VoiceDecodeError('native-voice-decode-failed') from None
    finally:
        if temporary is not None:
            try: temporary.unlink(missing_ok=True)
            except OSError: pass


def decoder_status():
    try:
        from importlib.metadata import version
        if version('pysilk-mod')!='1.6.4':_fail('decoder-version')
        import pysilk
        if not callable(pysilk.decode):_fail('decoder-unavailable')
        return {'decoderReady':True,'decoderVersion':'1.6.4','backend':'pysilk-mod','cpuOnly':True}
    except VoiceDecodeError:raise
    except Exception:raise VoiceDecodeError('native-voice-decode-dependency-unavailable') from None


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self, message): _fail('invalid-arguments')
    try:
        if (sys.argv[1:] if argv is None else argv)==['--status']:
            print(json.dumps({'ok':True,'value':decoder_status()}));return 0
        parser = Parser(description='Isolated CPU decoder for hash-qualified incoming Tencent SILK.')
        for name in ('data-root', 'source', 'expected-sha256'):
            parser.add_argument('--' + name, required=True)
        parser.add_argument('--duration-ms', type=int, required=True)
        parser.add_argument('--output')
        args = parser.parse_args(argv)
        value = decode_native_voice(args.source, data_root=args.data_root,
            expected_sha256=args.expected_sha256, duration_ms=args.duration_ms, output=args.output)
        print(json.dumps({'ok': True, 'value': value}, ensure_ascii=False))
        return 0
    except VoiceDecodeError as error:
        print(json.dumps({'ok': False, 'code': str(error)}))
        return 2


if __name__ == '__main__': sys.exit(main())
