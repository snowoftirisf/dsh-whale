"""Fake installed-SDK contracts only; no actual SDK construction or capture."""
import array
from types import SimpleNamespace
import unittest

from dedicated_process_audio import DedicatedProcessAudio, PROCESS_AUDIO_SCOPE_VERSION


class FakeCapture:
    def __init__(self,pid,on_data):
        self.pid=pid;self.callback=on_data;self.starts=0;self.stops=0;self.specific=True;self.start_hook=None
        self._backend=SimpleNamespace(_native=SimpleNamespace(is_process_specific=lambda:self.specific))
        self.format={'sample_rate':48000,'channels':2,'bits_per_sample':32,'sample_format':'float32'}
    def start(self):
        self.starts+=1
        if self.start_hook:self.start_hook(self)
    def stop(self):self.stops+=1
    def feed(self,frames=48,count=-1):self.callback(array.array('f',[.1]*(frames*2)).tobytes(),count)


class DedicatedTests(unittest.TestCase):
    def make(self,configure=None):
        self.created=[];self.received=[]
        def factory(**kwargs):
            capture=FakeCapture(**kwargs)
            if configure:configure(capture)
            self.created.append(capture);return capture
        return DedicatedProcessAudio(123,lambda pcm,frames:self.received.append((pcm,frames)),capture_factory=factory)

    def test_constructor_inert_and_real_sdk_minus_one_becomes_exact_frames(self):
        adapter=self.make();self.assertEqual(self.created,[]);self.assertFalse(adapter.scope_verified)
        self.assertEqual(PROCESS_AUDIO_SCOPE_VERSION,1);adapter.start();self.assertTrue(adapter.scope_verified)
        self.assertEqual(adapter.scope_version,1);self.assertEqual(adapter.normalization_version,1)
        with self.assertRaises(AttributeError):adapter.scope_verified=True
        capture=self.created[0];capture.feed(480,count=-1);capture.feed(96,count=96)
        self.assertEqual([n for _,n in self.received],[480,96])
        self.assertEqual([len(data) for data,_ in self.received],[480*8,96*8]);self.assertTrue(adapter.check())
        adapter.stop();self.assertFalse(adapter.scope_verified);capture.feed(48)
        self.assertEqual(len(self.received),2);adapter.stop();self.assertEqual(capture.stops,1)

    def test_false_missing_throwing_or_integer_scope_never_starts_or_dispatches(self):
        def throwing():raise RuntimeError('private SDK details')
        configurations=[lambda c:setattr(c,'specific',False),lambda c:setattr(c,'specific',1),
            lambda c:setattr(c,'_backend',SimpleNamespace()),
            lambda c:setattr(c._backend._native,'is_process_specific',None),
            lambda c:setattr(c._backend._native,'is_process_specific',throwing)]
        for configure in configurations:
            with self.subTest(configure=configure):
                adapter=self.make(configure)
                with self.assertRaisesRegex(RuntimeError,'scope-unverified'):adapter.start()
                capture=self.created[0];capture.feed();self.assertEqual(capture.starts,0)
                self.assertEqual(self.received,[]);self.assertFalse(adapter.scope_verified)
                with self.assertRaisesRegex(RuntimeError,'scope-unverified'):adapter.check()

    def test_unknown_format_and_wrong_pid_fail_before_start(self):
        for configure in (lambda c:c.format.update(sample_rate=44100),lambda c:c.format.pop('sample_format'),
                          lambda c:setattr(c,'format',None),lambda c:setattr(c,'pid',456)):
            adapter=self.make(configure)
            with self.assertRaises(RuntimeError):adapter.start()
            self.assertEqual(self.created[0].starts,0);self.created[0].feed();self.assertEqual(self.received,[])

    def test_during_start_unverified_callback_and_post_start_fallback_are_blocked(self):
        def configure(c):
            def hook(c):c.feed();c.specific=False
            c.start_hook=hook
        adapter=self.make(configure)
        with self.assertRaisesRegex(RuntimeError,'scope-unverified'):adapter.start()
        self.assertEqual(self.created[0].starts,1);self.assertEqual(self.created[0].stops,1)
        self.assertEqual(self.received,[]);self.assertFalse(adapter.scope_verified)

    def test_malformed_count_or_bytes_latches_fault_and_never_dispatches(self):
        for data,count in ((b'bad',-1),(b'',-1),(b'\0'*16,3),(b'\0'*16,0),(b'\0'*16,-2),(b'\0'*16,True)):
            adapter=self.make();adapter.start();capture=self.created[0];capture.callback(data,count)
            self.assertEqual(self.received,[]);self.assertFalse(adapter.scope_verified)
            with self.assertRaisesRegex(RuntimeError,'pcm-invalid'):adapter.check()
            capture.feed();self.assertEqual(self.received,[])

    def test_later_scope_or_format_change_blocks_the_next_callback(self):
        for mutate in (lambda c:setattr(c,'specific',False),lambda c:c.format.update(channels=1)):
            adapter=self.make();adapter.start();capture=self.created[0];capture.feed();mutate(capture);capture.feed()
            self.assertEqual(len(self.received),1);self.assertFalse(adapter.scope_verified)
            with self.assertRaises(RuntimeError):adapter.check()

    def test_check_detects_scope_change_without_audio_and_consumer_error_is_safe(self):
        adapter=self.make();adapter.start();self.created[0].specific=False
        with self.assertRaisesRegex(RuntimeError,'scope-unverified'):adapter.check()
        self.assertFalse(adapter.scope_verified)
        adapter=self.make();adapter.start()
        adapter._consumer=lambda *_:(_ for _ in ()).throw(ValueError('private transcript'))
        self.created[0].feed();self.assertFalse(adapter.scope_verified)
        with self.assertRaisesRegex(RuntimeError,'^dedicated-process-audio-consumer-failed$'):adapter.check()


if __name__=='__main__':unittest.main()
