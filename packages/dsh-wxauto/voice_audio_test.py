import unittest
import numpy as np
from voice_audio import segments, output_format, stereo_waveform, PlaybackFeed


class SegmentTests(unittest.TestCase):
    def test_audio_waits_as_silence_then_survives_variable_callback_boundaries(self):
        source=np.arange(14,dtype=np.float32).reshape(7,2)/20
        feed=PlaybackFeed(source)
        early=np.ones((3,2),dtype=np.float32)
        self.assertFalse(feed.fill(early))
        self.assertEqual(feed.position,0)
        np.testing.assert_array_equal(early,0)
        feed.started=True
        output=[]
        for size in (2,3,4):
            block=np.ones((size,2),dtype=np.float32)
            done=feed.fill(block)
            output.append(block)
        self.assertTrue(done)
        result=np.concatenate(output)
        np.testing.assert_array_equal(result[:7],source)
        np.testing.assert_array_equal(result[7:],0)

    def test_mono_voice_is_duplicated_into_two_identical_channels(self):
        source=np.array([[0.1],[-0.2],[0.3]],dtype=np.float32)
        result=stereo_waveform(source)
        self.assertEqual(result.shape,(3,2))
        np.testing.assert_array_equal(result[:,0],source[:,0])
        np.testing.assert_array_equal(result[:,1],source[:,0])

    def test_stereo_voice_keeps_its_channels_and_multichannel_is_rejected(self):
        source=np.zeros((10,2),dtype=np.float32)
        self.assertIs(stereo_waveform(source),source)
        with self.assertRaisesRegex(ValueError,'stereo-source-required'):
            stereo_waveform(np.zeros((10,16),dtype=np.float32))

    def test_virtual_endpoint_resampling_preserves_duration_and_pitch(self):
        class Backend:
            class PortAudioError(Exception):pass
            @classmethod
            def check_output_settings(cls,**settings):
                if settings['samplerate'] != 48000:raise cls.PortAudioError('unsupported rate')
        source=np.sin(2*np.pi*440*np.arange(24000)/24000).astype(np.float32).reshape(-1,1)
        converted,sr=output_format(source,24000,13,{'default_samplerate':48000},Backend)
        self.assertEqual(sr,48000)
        self.assertEqual(len(converted),48000)
        peak=np.argmax(np.abs(np.fft.rfft(converted[:,0])))
        self.assertEqual(peak,440)
        self.assertEqual(converted.dtype,np.float32)

    def test_supported_endpoint_does_not_resample(self):
        from types import SimpleNamespace
        source=np.zeros((24000,1),dtype=np.float32)
        backend=SimpleNamespace(check_output_settings=lambda **kwargs:None,PortAudioError=RuntimeError)
        result,sr=output_format(source,24000,13,{'default_samplerate':48000},backend)
        self.assertIs(result,source)
        self.assertEqual(sr,24000)

    def test_long_speech_or_song_keeps_every_sample_and_limits_duration(self):
        source=np.sin(np.arange(12900,dtype=np.float32)/40)
        parts=list(segments(source,100))
        np.testing.assert_array_equal(np.concatenate(parts),source)
        self.assertTrue(all(50<=len(p)<=5500 for p in parts))

    def test_very_short_tail_is_not_left_unsendable(self):
        source=np.zeros(5510,dtype=np.float32)
        parts=list(segments(source,100))
        self.assertEqual(sum(map(len,parts)),5510)
        self.assertTrue(all(50<=len(p)<=5500 for p in parts))


if __name__=='__main__':unittest.main()
