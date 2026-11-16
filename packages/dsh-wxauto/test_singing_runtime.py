"""Private singing interface tests; no GPU, real source downloads or production IO."""
import io,json,os,socket,tempfile,unittest
from pathlib import Path
import numpy as np,soundfile as sf
from singing_runtime import SingingRuntime,digest,main


class SingingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.data=self.root/'private';self.voice=self.data/'voice';self.voice.mkdir(parents=True)
        self.reference=self.voice/'reference.wav';sf.write(self.reference,np.full(44100,.1),44100,subtype='PCM_24')
        self.config={'approved':True,'referenceAudioBase':'dataRoot','referenceAudio':'voice/reference.wav'};self.save_config()
        self.key='a'*16;self.guide_id='b'*32;self.folder=self.voice/'song-guides'/self.key/self.guide_id;self.folder.mkdir(parents=True)
        self.guide_file=self.folder/'guide.wav';self.duration=1;self.write_guide()
        self.calls=[];self.verify_calls=[];self.on_factory=None;self.on_render=None;self.output_rate=44100;self.output_duration=None
        self.runtime=SingingRuntime(data_root=self.data,asset_root=self.root,factory=self.factory,
            assets=self.assets,dependencies=lambda root:{'environmentReady':True,'dependenciesReady':True})

    def save_config(self):
        (self.voice/'voice.local.json').write_text(json.dumps(self.config),encoding='utf8')

    def write_guide(self):
        sf.write(self.guide_file,np.full(int(44100*self.duration),.1),44100,subtype='PCM_24')
        self.metadata={'schemaVersion':1,'kind':'whale-song-guide','guideId':self.guide_id,'jobId':self.guide_id,
            'chatKey':self.key,'guideFile':'guide.wav','audioKind':'isolated-vocal','verified':True,
            'guideSha256':digest(self.guide_file),'sourceAudioSha256':'c'*64,'sourceUrl':'https://example.org/song',
            'sourceStartSeconds':10,'sourceDurationSeconds':self.duration,'durationSeconds':self.duration,
            'sampleRate':44100,'channels':1,'dryVocalMethod':'demucs-htdemucs','vocalIsolation':'model-estimated',
            'listeningAccepted':False,'musicalSourceVerified':False}
        self.save_metadata()

    def save_metadata(self):
        (self.folder/'metadata.json').write_text(json.dumps(self.metadata),encoding='utf8')

    def assets(self,root,models,verify):
        self.verify_calls.append(verify);return {'modelsReady':True,'modelHashesVerified':verify}

    def factory(self,root,models):
        self.calls.append('factory')
        if self.on_factory:self.on_factory()
        outer=self
        class FakeBackend:
            def render(self,guide,reference,work):
                outer.calls.append(('render',digest(guide),digest(reference)))
                print('upstream progress must not corrupt JSONL')
                if outer.on_render:outer.on_render()
                output=work/'rendered.wav';seconds=outer.output_duration or outer.duration
                sf.write(output,np.full(int(outer.output_rate*seconds),.2),outer.output_rate,subtype='FLOAT')
                return {'file':str(output),'checkpointAudit':{'fake':True}}
        return FakeBackend()

    def request(self,**extra):
        return {'id':'request','op':'synthesize','guideId':self.guide_id,'chatKey':self.key,**extra}

    def test_status_separates_approval_reference_dependencies_without_loading_backend(self):
        self.config['approved']=False;self.save_config()
        state=self.runtime.run({'op':'status'})
        self.assertTrue(state['modelsReady']);self.assertTrue(state['referenceReady']);self.assertFalse(state['voiceApproved'])
        self.assertFalse(state['ready']);self.assertFalse(state['cpuFallback']);self.assertFalse(state['gpuLoadedByStatus']);self.assertEqual(self.calls,[])
        self.assertFalse((self.voice/'.singing-work').exists())

    def test_success_uses_only_fixed_reference_and_returns_native_segments_with_provenance(self):
        result=self.runtime.run(self.request())
        self.assertEqual(self.calls[1][1],self.metadata['guideSha256']);self.assertEqual(self.calls[1][2],digest(self.reference))
        self.assertIn(True,self.verify_calls);self.assertEqual(result['sampleRate'],44100);self.assertEqual(len(result['segments']),1)
        self.assertTrue(Path(result['file']).is_relative_to(self.voice/'outbox'))
        self.assertFalse(result['metadata']['musicalSourceVerified']);self.assertFalse(result['metadata']['listeningAccepted'])
        self.assertEqual(result['metadata']['audioKind'],'voice-converted-guide');self.assertEqual(list((self.voice/'.singing-work').iterdir()),[])

    def test_no_override_reference_target_path_or_text_input(self):
        for extra in ({'referenceAudio':str(self.reference)},{'target':'owner'},{'guideFile':str(self.guide_file)},{'text':'lyrics'}):
            with self.subTest(extra=list(extra)),self.assertRaisesRegex(RuntimeError,'unexpected-argument'):self.runtime.run(self.request(**extra))
        self.assertEqual(self.calls,[])

    def select_singing_reference(self):
        chosen=self.voice/'singing.wav';sf.write(chosen,np.full(44100,.3),44100,subtype='PCM_24')
        selection={'approved':True,'parentReferenceSha256':digest(self.reference),
            'referenceAudioBase':'dataRoot','referenceAudio':'voice/singing.wav',
            'referenceSha256':digest(chosen),'voiceId':'accepted-singing'}
        (self.voice/'singing.local.json').write_text(json.dumps(selection),encoding='utf8')
        return chosen,selection

    def test_separate_singing_selection_preserves_speech_and_uses_approved_reference(self):
        before=(self.voice/'voice.local.json').read_bytes();chosen,_=self.select_singing_reference()
        self.runtime.run(self.request())
        self.assertEqual(self.calls[1][2],digest(chosen))
        self.assertEqual((self.voice/'voice.local.json').read_bytes(),before)

    def test_changed_parent_cannot_use_singing_selection(self):
        self.select_singing_reference();sf.write(self.reference,np.full(44100,.4),44100,subtype='PCM_24')
        with self.assertRaisesRegex(RuntimeError,'parent-reference-changed'):self.runtime.run(self.request())
        self.assertEqual(self.calls,[])

    def test_separate_singing_selection_does_not_override_speech_revocation(self):
        self.select_singing_reference();self.config['approved']=False;self.save_config()
        with self.assertRaisesRegex(RuntimeError,'not-approved'):self.runtime.run(self.request())
        self.assertEqual(self.calls,[])

    def test_singing_selection_revocation_during_render_prevents_publication(self):
        self.select_singing_reference()
        def revoke():
            file=self.voice/'singing.local.json';value=json.loads(file.read_text());value['approved']=False
            file.write_text(json.dumps(value),encoding='utf8')
        self.on_render=revoke
        with self.assertRaisesRegex(RuntimeError,'invalid-selection'):self.runtime.run(self.request())
        self.assertFalse((self.voice/'outbox').exists())

    def test_cross_chat_source_metadata_and_hash_mismatches_are_rejected(self):
        cases=[('chatKey','d'*16),('guideFile','../private.wav'),('audioKind','isolated-singing-vocal'),('verified',False),('sourceUrl','file:///private'),('guideSha256','0'*64)]
        for key,value in cases:
            old=self.metadata[key];self.metadata[key]=value;self.save_metadata()
            with self.subTest(key=key),self.assertRaises((RuntimeError,ValueError)):self.runtime.run(self.request())
            self.metadata[key]=old;self.save_metadata()
        self.assertEqual(self.calls,[])

    def test_ids_cannot_escape_fixed_directory(self):
        for guide_id,key in [('../secret',self.key),(self.guide_id,'../private'),('B'*32,self.key)]:
            with self.assertRaisesRegex(RuntimeError,'invalid-'):self.runtime.run(self.request(guideId=guide_id,chatKey=key))
        self.assertEqual(self.calls,[])

    def test_hardlink_guide_is_not_a_way_to_read_private_audio(self):
        private=self.root/'private-chat.wav';self.guide_file.replace(private)
        try:os.link(private,self.guide_file)
        except OSError:self.skipTest('hardlinks unavailable')
        with self.assertRaisesRegex(RuntimeError,'path-denied'):self.runtime.run(self.request())
        self.assertEqual(self.calls,[])

    def test_unapproved_and_revoked_during_backend_load_never_render(self):
        self.config['approved']=False;self.save_config()
        with self.assertRaisesRegex(RuntimeError,'not-approved'):self.runtime.run(self.request())
        self.assertEqual(self.calls,[])
        self.config['approved']=True;self.save_config()
        def revoke():self.config['approved']=False;self.save_config()
        self.on_factory=revoke
        with self.assertRaisesRegex(RuntimeError,'not-approved|approval-changed'):self.runtime.run(self.request())
        self.assertEqual(self.calls,['factory']);self.assertFalse((self.voice/'outbox').exists())

    def test_revocation_after_inference_and_guide_changes_never_publish(self):
        def revoke():self.config['approved']=False;self.save_config()
        self.on_render=revoke
        with self.assertRaisesRegex(RuntimeError,'not-approved|approval-changed'):self.runtime.run(self.request())
        self.assertFalse((self.voice/'outbox').exists());self.assertEqual(list((self.voice/'.singing-work').iterdir()),[])
        self.config['approved']=True;self.save_config()
        def change():self.metadata['verified']=False;self.save_metadata()
        self.on_render=change
        with self.assertRaisesRegex(RuntimeError,'metadata-mismatch'):self.runtime.run(self.request())
        self.assertFalse((self.voice/'outbox').exists())

    def test_output_wrong_rate_or_timing_cannot_claim_success(self):
        self.output_rate=24000
        with self.assertRaisesRegex(RuntimeError,'generated-audio-invalid'):self.runtime.run(self.request())
        self.output_rate=44100;self.output_duration=2
        with self.assertRaisesRegex(RuntimeError,'generated-audio-invalid'):self.runtime.run(self.request())

    def test_approved_reference_change_during_loading_is_not_silently_used(self):
        def change():
            sf.write(self.voice/'other.wav',np.full(44100,.2),44100)
            self.config['referenceAudio']='voice/other.wav';self.save_config()
        self.on_factory=change
        with self.assertRaisesRegex(RuntimeError,'approval-changed'):self.runtime.run(self.request())
        self.assertEqual(self.calls,['factory']);self.assertFalse((self.voice/'outbox').exists())

    def test_nonfinite_and_silent_guides_are_rejected_before_backend(self):
        for value in (0,float('nan')):
            sf.write(self.guide_file,np.full(44100,value,dtype=np.float32),44100,subtype='FLOAT')
            self.metadata['guideSha256']=digest(self.guide_file);self.save_metadata()
            with self.subTest(value=str(value)),self.assertRaisesRegex(RuntimeError,'guide-audio-invalid|guide-silent'):self.runtime.run(self.request())
        self.assertEqual(self.calls,[])

    def test_long_result_segments_preserve_all_frames_and_each_is_at_most_55_seconds(self):
        self.duration=112;self.write_guide();result=self.runtime.run(self.request())
        self.assertGreater(result['segmentCount'],2);self.assertTrue(result['requiresSegments'])
        frames=0
        for part in result['segments']:
            info=sf.info(part['file']);self.assertLessEqual(info.duration,55);frames+=info.frames
        self.assertEqual(frames,112*44100)

    def test_protocol_redirects_banners_and_failure_does_not_leak_input(self):
        output=io.StringIO();main(self.runtime,io.StringIO(json.dumps(self.request())+'\n'+json.dumps({'id':'bad','op':'synthesize','text':'PRIVATE BODY'})+'\n'),output)
        rows=[json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(rows[0]['ok']);self.assertFalse(rows[1]['ok']);self.assertIn('code',rows[1]);self.assertNotIn('PRIVATE BODY',output.getvalue())

    def test_network_is_blocked_during_render(self):
        self.on_render=lambda:socket.create_connection(('example.org',443))
        with self.assertRaisesRegex(RuntimeError,'network-forbidden'):self.runtime.run(self.request())
        self.assertFalse((self.voice/'outbox').exists())


if __name__=='__main__':unittest.main()
