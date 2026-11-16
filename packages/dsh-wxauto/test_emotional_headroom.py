"""CPU fake-model contract checks; no weights, network or GPU allocation."""
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np
import soundfile as sf
try:
    import torch
except ImportError:
    torch=None

import emotional_voice_runtime as runtime

Headroom=runtime.load_headroom_helper(runtime.ROOT) if torch is not None else None


class FakeVocoder(torch.nn.Module if torch is not None else object):
    use_tanh_at_final=False
    def __init__(self):
        super().__init__();self.conv_post=torch.nn.Identity()
    def forward(self,value):return self.conv_post(value).clamp(-1,1)


class FakeIndex:
    def __init__(self,**kwargs):
        self.bigvgan=FakeVocoder();self.arguments=kwargs;self.fail=False;self.calls=[]
    def infer(self,**kwargs):
        self.calls.append(kwargs)
        value=self.bigvgan(torch.tensor([[[1.25,-.3,0,.2,-1.4]]]))
        if self.fail:raise RuntimeError('fake-inference-failed')
        if kwargs.get('output_path'):sf.write(kwargs['output_path'],value.numpy().reshape(-1),16000)
        return value


@unittest.skipIf(torch is None,'Run headroom contract checks in .venv-index-tts')
class RuntimeHeadroomTests(unittest.TestCase):
    def test_proxy_scales_before_first_clamp_only_current_stats_no_pcm(self):
        model=FakeIndex();contexts=[]
        def factory(*args,**kwargs):
            h=Headroom(*args,**kwargs);contexts.append(h);return h
        proxy=runtime.HeadroomModelProxy(model,factory)
        state=torch.random.get_rng_state().clone()
        first=proxy.infer(text='private text',emo_text='private emotion')
        self.assertTrue(torch.equal(state,torch.random.get_rng_state()))
        self.assertAlmostEqual(float(first.abs().max()),.95,places=6)
        self.assertEqual(proxy.last_headroom['wouldClipCount'],2)
        self.assertEqual(proxy.last_headroom['afterClipCount'],0)
        self.assertTrue(all(type(v) in (int,float) for v in proxy.last_headroom.values()))
        self.assertNotIn('private',json.dumps(proxy.last_headroom))
        proxy.infer(text='another request')
        self.assertEqual(proxy.last_headroom['segments'],1)
        self.assertEqual(proxy.last_headroom['samples'],5)
        self.assertTrue(all(h.records==[] and not h.capture for h in contexts))
        self.assertEqual(len(model.bigvgan.conv_post._forward_hooks),0)

    def test_failure_clears_old_receipt_removes_hook_and_unlocks(self):
        model=FakeIndex();proxy=runtime.HeadroomModelProxy(model,Headroom)
        proxy.infer();model.fail=True
        with self.assertRaisesRegex(RuntimeError,'fake-inference-failed'):proxy.infer()
        self.assertIsNone(proxy.last_headroom)
        self.assertEqual(len(model.bigvgan.conv_post._forward_hooks),0)
        model.fail=False;proxy.infer()
        self.assertEqual(proxy.last_headroom['segments'],1)

    def test_reentrant_calls_do_not_stack_hooks(self):
        model=FakeIndex();proxy=runtime.HeadroomModelProxy(model,Headroom)
        def nested(**kwargs):return proxy.infer()
        model.infer=nested
        with self.assertRaisesRegex(RuntimeError,'already-active'):proxy.infer()
        self.assertEqual(len(model.bigvgan.conv_post._forward_hooks),0)

    def test_official_factory_returns_proxy_with_cpu_fake_constructor(self):
        module=types.ModuleType('indextts.infer_v2_5');module.IndexTTS2=FakeIndex
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root=Path(tmp);models=root/'voice-data/models/index-tts2.5'
            with mock.patch.dict(sys.modules,{'indextts.infer_v2_5':module}),\
                 mock.patch.object(torch.cuda,'is_available',return_value=True),\
                 mock.patch.object(runtime,'load_headroom_helper',return_value=Headroom),\
                 mock.patch.object(runtime.os,'chdir'),\
                 mock.patch.object(sys,'path',list(sys.path)),\
                 mock.patch.dict(runtime.os.environ,{}):
                proxy=runtime.official_model(root,models,True)
        self.assertIsInstance(proxy,runtime.HeadroomModelProxy)
        self.assertIs(proxy.arguments['use_qwen_emo'],True)
        self.assertIs(proxy.arguments['use_bf16'],True)
        self.assertIs(proxy.arguments['use_cuda_kernel'],False)
        self.assertAlmostEqual(float(proxy.infer().abs().max()),.95,places=6)

    def test_synthesize_returns_only_numeric_headroom_metadata(self):
        # Reuse the existing approval/path fixture; its old fake factory remains
        # unchanged and is intentionally not forced to pretend it has a vocoder.
        from test_emotional_voice_runtime import EmotionalVoiceTests
        fixture=EmotionalVoiceTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        model=FakeIndex();fixture.speech.model_factory=lambda *args:runtime.HeadroomModelProxy(model,Headroom)
        result=fixture.speech.run({'op':'synthesize','text':'private text','emotion':'开心'})
        self.assertTrue(all(type(v) in (int,float) for v in result['headroom'].values()))
        self.assertEqual(result['headroom']['wouldClipCount'],2)
        self.assertNotIn('private text',json.dumps(result))
        data,_=sf.read(result['file']);self.assertLess(np.max(np.abs(data)),.951)
        self.assertEqual(len(model.bigvgan.conv_post._forward_hooks),0)


if __name__=='__main__':unittest.main()
