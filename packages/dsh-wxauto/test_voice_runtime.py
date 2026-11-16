import unittest
from pathlib import Path
from voice_runtime import audio_file, DATA, clone_parameters, Speech


class VoicePathTests(unittest.TestCase):
    def test_releasing_gpu_speech_preserves_cpu_recognizer(self):
        speech=Speech();asr,vad=object(),object()
        speech.asr,speech.vad=asr,vad
        speech.tts,speech.clone_prompt=object(),object();speech.tts_kind='base'
        self.assertTrue(speech.run({'op':'release'})['released'])
        self.assertIs(speech.asr,asr);self.assertIs(speech.vad,vad)
        self.assertIsNone(speech.tts);self.assertIsNone(speech.clone_prompt)

    def test_original_cantonese_embedding_mode_does_not_use_a_synthetic_transcript(self):
        self.assertEqual(clone_parameters({'xVectorOnly':True,'referenceText':'unrelated preview text'}),(True,None))
        self.assertEqual(clone_parameters({'xVectorOnly':True}),(True,None))

    def test_legacy_context_clone_still_requires_exact_reference_transcript(self):
        self.assertEqual(clone_parameters({'referenceText':'原音逐字稿'}),(False,'原音逐字稿'))
        with self.assertRaisesRegex(ValueError,'reference-transcript-required'):clone_parameters({})
        with self.assertRaisesRegex(ValueError,'invalid-x-vector-mode'):clone_parameters({'xVectorOnly':'true'})

    def test_cannot_read_arbitrary_local_file(self):
        with self.assertRaises(ValueError): audio_file(__file__)

    def test_audio_extensions_are_checked_even_in_private_storage(self):
        DATA.mkdir(parents=True,exist_ok=True)
        filename=DATA/'not-audio-test.txt'
        filename.write_text('test','utf-8')
        try:
            with self.assertRaises(ValueError): audio_file(filename)
        finally:
            filename.unlink()


if __name__=='__main__': unittest.main()
