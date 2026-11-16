"""Injected PCM tests only; never decode private audio or import pysilk/UI/models."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
import wave
from unittest.mock import patch

from native_voice_decode import decode_native_voice, VoiceDecodeError, HEADER, main


class NativeVoiceDecodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root / 'voice/incoming-native/chat/source.silk'
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(HEADER + b'\x01\x00x')
        self.expected = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.calls = []
        def decoder(blob, **kwargs):
            self.calls.append((blob, kwargs)); return struct.pack('<h', 1000) * 16000
        self.decoder = decoder
    def run_decode(self, **kwargs):
        options = dict(data_root=self.root, expected_sha256=self.expected, duration_ms=1000, decoder=self.decoder)
        options.update(kwargs)
        return decode_native_voice(self.source, **options)

    def test_exact_decoder_contract_and_self_written_wav(self):
        result = self.run_decode()
        self.assertEqual(self.calls[0][1], {'to_wav': False, 'sample_rate': 16000})
        with wave.open(result['file']) as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()), (1, 2, 16000, 16000))
        self.assertEqual(result['sha256'], hashlib.sha256(Path(result['file']).read_bytes()).hexdigest())
        self.assertFalse(result['cached']); self.assertEqual(result['decoderVersion'], 'injected-test-decoder')
        self.assertTrue(self.run_decode()['cached'])

    def test_existing_output_hash_conflict_never_overwrites(self):
        output = self.source.with_suffix('.wav'); output.write_bytes(b'user existing file')
        with self.assertRaisesRegex(VoiceDecodeError, 'output-hash-conflict'): self.run_decode()
        self.assertEqual(output.read_bytes(), b'user existing file')

    def test_declared_duration_padding_and_limits(self):
        self.assertEqual(self.run_decode(duration_ms=900)['durationMilliseconds'], 1000)
        for duration in (True, 0, 120001):
            with self.assertRaisesRegex(VoiceDecodeError, 'invalid-duration'): self.run_decode(duration_ms=duration)
        with self.assertRaisesRegex(VoiceDecodeError, 'duration-mismatch'): self.run_decode(duration_ms=800)

    def test_hash_header_framing_and_size_fail_before_decoder(self):
        with self.assertRaisesRegex(VoiceDecodeError, 'hash-mismatch'): self.run_decode(expected_sha256='0' * 64)
        for blob,code in [(b'#!SILK_V3\x02\x00xx','unsupported-header'),(HEADER+b'\x00\x00x','source-framing'),
                          (HEADER+b'\x02\x00x','source-framing'),(HEADER+b'\xff\xffx','source-framing'),
                          (HEADER+b'x'*(2*1024*1024),'source-size')]:
            self.source.write_bytes(blob)
            with self.assertRaisesRegex(VoiceDecodeError,code):
                self.run_decode(expected_sha256=hashlib.sha256(blob).hexdigest())
        self.assertEqual(self.calls, [])

    def test_invalid_pcm_and_fixed_error_redact_backend_details(self):
        for pcm in (b'', b'\x01', b'\x00'*32000, b'\x01'*(16000*2*121)):
            with self.assertRaisesRegex(VoiceDecodeError,'invalid-pcm|silent-pcm'):
                self.run_decode(decoder=lambda *a,**k:pcm)
        def broken(*a,**k): raise RuntimeError('private source/name/body')
        with self.assertRaisesRegex(VoiceDecodeError,'native-voice-decode-failed') as error:
            self.run_decode(decoder=broken)
        self.assertNotIn('private',str(error.exception))

    def test_source_changed_during_decode_refuses_output(self):
        def decoder(*args,**kwargs):
            self.source.write_bytes(HEADER+b'\x01\x00y')
            return struct.pack('<h', 1)*16000
        with self.assertRaisesRegex(VoiceDecodeError,'hash-mismatch'):self.run_decode(decoder=decoder)
        self.assertFalse(self.source.with_suffix('.wav').exists())

    def test_source_and_output_paths_are_confined(self):
        with self.assertRaisesRegex(VoiceDecodeError,'path-denied'):
            self.run_decode(data_root=self.root/'other')
        with self.assertRaisesRegex(VoiceDecodeError,'path-denied'):
            self.run_decode(output=self.root/'outside.wav')
        with self.assertRaisesRegex(VoiceDecodeError,'output-denied'):
            self.run_decode(output=self.root/'voice/output.mp3')
        result=self.run_decode(output=self.root/'voice/decoded/chat/approved.wav')
        self.assertTrue(Path(result['file']).is_relative_to(self.root/'voice'))

    def test_hardlinked_source_or_output_is_denied(self):
        linked=self.source.with_name('linked.silk')
        try:os.link(self.source,linked)
        except OSError:self.skipTest('host does not allow fixture hardlinks')
        with self.assertRaisesRegex(VoiceDecodeError,'file-denied'):self.run_decode()
        linked.unlink()
        output=self.source.with_suffix('.wav');output.write_bytes(b'existing')
        os.link(output,output.with_name('linked.wav'))
        with self.assertRaisesRegex(VoiceDecodeError,'file-denied'):self.run_decode()

    def test_symlink_path_is_denied(self):
        link=self.source.with_name('linked.silk')
        try:link.symlink_to(self.source)
        except OSError:self.skipTest('host does not allow fixture symlinks')
        with self.assertRaisesRegex(VoiceDecodeError,'link-denied'):
            decode_native_voice(link,data_root=self.root,expected_sha256=self.expected,duration_ms=1000,decoder=self.decoder)

    def test_cli_fixed_envelope_without_decoder_or_model_import(self):
        out=io.StringIO()
        with contextlib.redirect_stdout(out),patch('native_voice_decode.decode_native_voice',return_value={'fixture':True}):
            code=main(['--data-root',str(self.root),'--source',str(self.source),'--expected-sha256',self.expected,'--duration-ms','1000'])
        self.assertEqual(code,0);self.assertEqual(json.loads(out.getvalue()),{'ok':True,'value':{'fixture':True}})
        out=io.StringIO()
        with contextlib.redirect_stdout(out):code=main([])
        self.assertEqual(code,2);self.assertEqual(json.loads(out.getvalue())['code'],'native-voice-decode-invalid-arguments')


if __name__=='__main__':unittest.main()
