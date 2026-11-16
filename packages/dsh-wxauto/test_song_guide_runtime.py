"""Offline bounded guide contracts; no real media fetch or separation here."""
import io
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import types
import unittest
from unittest import mock

import numpy as np
import soundfile as sf
import song_guide_runtime as guide

URL='https://www.youtube.com/watch?v=oec9R5ypf-o'


class SongGuideTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        self.phases=[];self.fail=False;self.no_vocals=False;self.silent=False
        self.runtime=guide.SongGuide(root=self.root,data_root=self.root/'data',phase_runner=self.phase)
        self.request={'op':'prepare','sourceUrl':URL,'startSeconds':80,'durationSeconds':2,'chatKey':'a'*16}

    def phase(self,name,job,args):
        self.phases.append(name)
        if name=='extract':
            x=np.sin(np.arange(88200)*2*np.pi*220/44100)*.2
            sf.write(job/'source.wav',np.column_stack([x,x]),44100)
            return {'ok':True,'extractorVersion':'fixture','mediaId':'oec9R5ypf-o'}
        if self.fail:raise guide.GuideError('separator-fixture-failed')
        if not self.no_vocals:
            sf.write(job/'guide.wav',np.zeros(88200) if self.silent else np.sin(np.arange(88200)*.02)*.1,44100,subtype='FLOAT')
        return {'ok':True,'method':'demucs-htdemucs','modelSha256':guide.MODEL_SHA,'gain':1.}

    def job(self):
        return next((self.root/'data/voice/song-guides'/('a'*16)).iterdir())

    def test_success_metadata_hashes_chat_scoping_and_no_absolute_result(self):
        result=self.runtime.run(self.request);job=self.root/'data/voice/song-guides'/('a'*16)/result['guideId']
        metadata=json.loads((job/'metadata.json').read_text('utf8'))
        self.assertEqual(self.phases,['extract','separate'])
        self.assertEqual(metadata['schemaVersion'],1);self.assertEqual(metadata['kind'],'whale-song-guide')
        self.assertEqual(metadata['guideFile'],'guide.wav');self.assertEqual(metadata['audioKind'],'isolated-vocal')
        self.assertEqual(metadata['guideSha256'],guide.sha(job/'guide.wav'))
        self.assertEqual(metadata['sourceAudioSha256'],guide.sha(job/'source.wav'))
        self.assertEqual(metadata['separator']['modelUrl'],guide.MODEL_URL)
        self.assertEqual(metadata['sourceExtractor']['ffmpegBinarySha256'],guide.FFMPEG_SHA)
        self.assertFalse(metadata['listeningAccepted']);self.assertFalse(metadata['musicalSourceVerified'])
        self.assertEqual(metadata['vocalIsolation'],'model-estimated');self.assertTrue(metadata['verified'])
        self.assertEqual(metadata['channels'],1);self.assertEqual(metadata['sampleRate'],44100)
        self.assertEqual(metadata['chatKey'],'a'*16);self.assertEqual(metadata['jobId'],result['guideId'])
        self.assertNotIn(str(self.root),json.dumps(result));self.assertNotIn('target',result)

    def test_separation_failure_never_publishes_mix_as_guide(self):
        self.fail=True
        with self.assertRaisesRegex(guide.GuideError,'separator-fixture-failed'):self.runtime.run(self.request)
        self.assertTrue((self.job()/'source.wav').is_file())
        self.assertFalse((self.job()/'metadata.json').exists());self.assertFalse((self.job()/'guide.wav').exists())
        self.assertFalse(json.loads((self.job()/'failure.json').read_text())['verified'])

    def test_missing_or_silent_vocals_are_not_success(self):
        for option in ('no_vocals','silent'):
            with self.subTest(option=option):
                setattr(self,option,True)
                with self.assertRaises(Exception):self.runtime.run(self.request)
                setattr(self,option,False)
        for job in (self.root/'data/voice/song-guides'/('a'*16)).iterdir():
            self.assertFalse((job/'metadata.json').exists())

    def test_unknown_target_or_bad_chat_is_rejected_before_pipeline(self):
        for extra in ({'target':'another person'},{'chatKey':'../someone'},{'chatKey':'a'*32}):
            with self.assertRaises(guide.GuideError):self.runtime.run({**self.request,**extra})
        self.assertEqual(self.phases,[])

    def test_no_external_url_or_local_input_paths(self):
        for value in ('file:///C:/secret.wav','C:/secret.wav','https://127.0.0.1/x','http://youtube.com/watch?v=oec9R5ypf-o',
                      'https://youtube.com.evil/watch?v=oec9R5ypf-o','https://u:p@youtube.com/watch?v=oec9R5ypf-o',
                      'https://@youtube.com/watch?v=oec9R5ypf-o','https://youtu.be/../../x','https://www.youtube.com/playlist?list=x'):
            with self.subTest(value=value),self.assertRaises(guide.GuideError):guide.source_url(value)
        self.assertEqual(guide.source_url('https://youtu.be/oec9R5ypf-o?t=80'),URL)
        self.assertEqual(guide.source_url('https://www.bilibili.com/video/BV1xx411c7mD/?p=2&spm=x'),
                         'https://www.bilibili.com/video/BV1xx411c7mD/?p=2')

    def test_time_bounds_and_nonfinite(self):
        for start,duration in [(-1,2),(0,0),(0,56),(0,float('nan')),(float('inf'),2),(True,2),(0,True)]:
            with self.assertRaises(guide.GuideError):guide.slice_args(start,duration)
        self.assertEqual(guide.slice_args(0,55),(0.,55.))

    def test_cdn_allowlist_and_public_dns(self):
        public=lambda *a,**k:[(None,None,None,None,('8.8.8.8',443))]
        private=lambda *a,**k:[(None,None,None,None,('127.0.0.1',443))]
        uri='https://rr1.googlevideo.com/videoplayback?public-signature=x'
        self.assertEqual(guide.public_media_url(uri,'youtube',public),uri)
        for url,resolver in [(uri,private),('https://googlevideo.com.evil/x',public),('file:///x',public),
                             ('https://rr1.googlevideo.com:8443/x',public),('https://x.local/x',public)]:
            with self.assertRaises(guide.GuideError):guide.public_media_url(url,'youtube',resolver)

    def test_bad_rate_size_duration_and_symlink(self):
        file=self.root/'bad.wav';sf.write(file,np.ones(100)*.1,100)
        with self.assertRaises(guide.GuideError):guide.audio_info(file)
        sf.write(file,np.ones(44100)*.1,44100)
        with self.assertRaisesRegex(guide.GuideError,'duration-mismatch'):guide.audio_info(file,duration=2)
        with file.open('wb') as stream:stream.truncate(guide.MAX_FILE+1)
        with self.assertRaises(RuntimeError):guide.audio_info(file)
        link=self.root/'linked.wav';outside=self.root/'other.wav';outside.write_bytes(b'x')
        try:link.symlink_to(outside)
        except OSError:return
        with self.assertRaises(RuntimeError):guide.audio_info(link)

    def test_stdio_failure_is_sanitized_and_keeps_correlations(self):
        class Bad:
            def run(self,request):raise RuntimeError('secret-key https://signed.example/?token=secret')
        out=io.StringIO();guide.main(io.StringIO('{"id":"one","op":"prepare"}\n[]\n{"op":"stop"}\n'),out,Bad())
        rows=[json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual(rows[0]['id'],'one');self.assertFalse(rows[0]['ok'])
        self.assertNotIn('secret',out.getvalue());self.assertEqual(len(rows),2)

    def test_timeout_kills_only_own_worker_tree(self):
        python=self.root/'.venv-seed-vc/Scripts/python.exe';python.parent.mkdir(parents=True);python.write_bytes(b'fake')
        job=self.root/'job';job.mkdir();process=mock.Mock(pid=12345)
        process.wait.side_effect=[subprocess.TimeoutExpired('private argv',210),0]
        with mock.patch.object(guide.subprocess,'Popen',return_value=process) as popen,\
             mock.patch.object(guide.subprocess,'run') as kill:
            with self.assertRaisesRegex(guide.GuideError,'extract-timeout'):
                self.runtime.run_phase('extract',job,{'sourceUrl':URL,'startSeconds':80,'durationSeconds':2})
        self.assertIs(popen.call_args.kwargs['shell'],False)
        process.kill.assert_called_once()
        if guide.os.name=='nt':self.assertEqual(kill.call_args.args[0],['taskkill','/PID','12345','/T','/F'])

    def test_separation_wrong_method_or_source_mismatch_fails(self):
        original=self.phase
        def wrong(name,job,args):
            result=original(name,job,args)
            if name=='separate':result['method']='copy-original-mix'
            return result
        self.runtime.phase_runner=wrong
        with self.assertRaisesRegex(guide.GuideError,'unverified'):self.runtime.run(self.request)
        self.assertFalse((self.job()/'metadata.json').exists())

    def extractor_fixture(self,media=None,error=None):
        outer=self
        class FakeYDL:
            def __init__(self,options):outer.extractor_options=options
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def extract_info(self,*args,**kwargs):
                if error:raise error
                return media
        module=types.ModuleType('yt_dlp');module.YoutubeDL=FakeYDL
        version=types.ModuleType('yt_dlp.version');version.__version__='2026.10.26'
        binary=self.root/'portable-ffmpeg.exe';binary.write_bytes(b'fixture-executable-not-run')
        lock=self.root/'tools/seed-vc/ffmpeg.lock.json';lock.parent.mkdir(parents=True)
        lock.write_text(json.dumps({'binaryPath':str(binary)}))
        job=self.root/'stage';job.mkdir()
        return {'yt_dlp':module,'yt_dlp.version':version},binary,job

    def test_extract_uses_argv_bounded_slice_and_no_login_options(self):
        media={'id':'oec9R5ypf-o','formats':[{'vcodec':'none','acodec':'aac','protocol':'https',
                    'url':'https://rr1.googlevideo.com/videoplayback?token=private','abr':128}]}
        modules,binary,job=self.extractor_fixture(media)
        with mock.patch.dict(guide.sys.modules,modules),mock.patch.object(guide,'FFMPEG_SHA',guide.sha(binary)),\
             mock.patch.object(guide,'public_media_url',side_effect=lambda value,*args:value),\
             mock.patch.object(guide.subprocess,'run',return_value=types.SimpleNamespace(returncode=0)) as call:
            result=guide.extract_stage(self.root,job,URL,80,2)
        self.assertTrue(result['ok']);command=call.call_args.args[0]
        self.assertEqual(command[command.index('-ss')+1],'80.0')
        self.assertEqual(command[command.index('-t')+1],'2.0')
        self.assertEqual(command[command.index('-fs')+1],str(guide.MAX_FILE))
        self.assertIs(call.call_args.kwargs['shell'],False)
        self.assertEqual(call.call_args.kwargs['stderr'],subprocess.DEVNULL)
        for key in ('cookiefile','cookiesfrombrowser','username','password','usenetrc'):
            self.assertNotIn(key,self.extractor_options)

    def test_login_or_captcha_error_is_not_retried_or_leaked(self):
        modules,binary,job=self.extractor_fixture(error=RuntimeError('login needed secret-key signed-url-token'))
        with mock.patch.dict(guide.sys.modules,modules),mock.patch.object(guide,'FFMPEG_SHA',guide.sha(binary)):
            with self.assertRaisesRegex(guide.GuideError,'public-source-unavailable-or-auth-required') as caught:
                guide.extract_stage(self.root,job,URL,80,2)
        self.assertNotIn('secret',str(caught.exception))
        self.assertEqual(self.extractor_options['retries'],0)

    def test_nonpublic_extracted_stream_never_starts_ffmpeg(self):
        media={'id':'oec9R5ypf-o','formats':[{'vcodec':'none','acodec':'aac','protocol':'https',
                                         'url':'https://127.0.0.1/private'}]}
        modules,binary,job=self.extractor_fixture(media)
        with mock.patch.dict(guide.sys.modules,modules),mock.patch.object(guide,'FFMPEG_SHA',guide.sha(binary)),\
             mock.patch.object(guide.subprocess,'run') as call:
            with self.assertRaisesRegex(guide.GuideError,'nonpublic-or-unsupported-media-url'):
                guide.extract_stage(self.root,job,URL,80,2)
        call.assert_not_called()


if __name__=='__main__':unittest.main()
