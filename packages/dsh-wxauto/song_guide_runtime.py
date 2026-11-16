"""Private bounded public-media -> CPU Demucs guide preparation, never delivery."""
import argparse
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import uuid
from datetime import datetime, timezone

from attachment_policy import checked_file, local_path
from data_paths import resolve_data_paths

ROOT=Path(__import__('os').environ.get('WHALE_RUNTIME_ROOT') or Path(__file__).resolve().parents[2])
MODEL_COMMIT='e976d93ecc3865e5757426930257e200846a520a'
MODEL_SHA='8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4'
MODEL_REPO='https://github.com/facebookresearch/demucs'
MODEL_URL='https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/955717e8-8726e21a.th'
FFMPEG_SHA='2ce797a0f88d7f067180338fb227f7b1928ea727bd9a4d7a1d022f7c52af71a3'
MAX_FILE=32*1024*1024
MAX_DURATION=55


class GuideError(RuntimeError):pass


def sha(file):
    h=hashlib.sha256()
    with file.open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024*1024),b''):h.update(block)
    return h.hexdigest()


def source_url(value):
    if not isinstance(value,str) or len(value)>2048 or any(ord(c)<33 for c in value):
        raise GuideError('invalid-source-url')
    try:
        u=urllib.parse.urlsplit(value)
        if u.scheme!='https' or u.username is not None or u.password is not None or u.port not in (None,443):raise ValueError()
        host=(u.hostname or '').lower();q=urllib.parse.parse_qs(u.query)
        if host in ('youtube.com','www.youtube.com','m.youtube.com'):
            video=(q.get('v') or [''])[0] if u.path=='/watch' else u.path.split('/')[-1] if u.path.startswith(('/shorts/','/embed/')) else ''
            if len(q.get('v',[]))>1 or not re.fullmatch(r'[A-Za-z0-9_-]{11}',video):raise ValueError()
            return 'https://www.youtube.com/watch?v='+video
        if host=='youtu.be' and re.fullmatch(r'/[A-Za-z0-9_-]{11}/?',u.path):
            return 'https://www.youtube.com/watch?v='+u.path.strip('/')
        if host in ('bilibili.com','www.bilibili.com','m.bilibili.com'):
            match=re.fullmatch(r'/video/(BV[A-Za-z0-9]{10})/?',u.path)
            part=q.get('p',['1'])
            if not match or len(part)!=1 or not re.fullmatch(r'[1-9][0-9]{0,2}',part[0]):raise ValueError()
            return 'https://www.bilibili.com/video/'+match[1]+'/' + ('?p='+part[0] if part[0]!='1' else '')
    except (ValueError,TypeError):pass
    raise GuideError('unsupported-source-url')


def slice_args(start,duration):
    if (isinstance(start,bool) or isinstance(duration,bool) or not isinstance(start,(int,float))
            or not isinstance(duration,(int,float)) or not math.isfinite(start) or not math.isfinite(duration)
            or not 0<=start<=86400 or not 1<=duration<=MAX_DURATION):
        raise GuideError('invalid-source-slice')
    return float(start),float(duration)


def public_media_url(url,platform,resolver=socket.getaddrinfo):
    try:
        u=urllib.parse.urlsplit(url);host=(u.hostname or '').lower()
        domains=('googlevideo.com',) if platform=='youtube' else ('bilivideo.com','bilivideo.cn','akamaized.net')
        if (u.scheme!='https' or u.username is not None or u.password is not None or u.port not in (None,443)
                or not any(host==d or host.endswith('.'+d) for d in domains)):
            raise ValueError()
        addresses=resolver(host,443,type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):raise ValueError()
    except (ValueError,TypeError,OSError):raise GuideError('nonpublic-or-unsupported-media-url') from None
    return url


def atomic_json(path,value):
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('x',encoding='utf8') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)


def clean_env():
    env=dict(os.environ)
    for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):env[key]=''
    env.update(CUDA_VISIBLE_DEVICES='',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',PYTHONUTF8='1')
    return env


def audio_info(path,*,duration=None,mono=False):
    import numpy as np,soundfile as sf
    checked_file(path,path.parent,MAX_FILE)
    with sf.SoundFile(path) as stream:
        rate=stream.samplerate;channels=stream.channels;seconds=len(stream)/rate
        if rate!=44100 or channels not in ((1,) if mono else (1,2)) or not 0.8<=seconds<=55.15:
            raise GuideError('invalid-guide-audio')
        peak=0.;energy=0.;count=0
        for block in stream.blocks(blocksize=65536,dtype='float32',always_2d=True):
            if not np.isfinite(block).all():raise GuideError('nonfinite-guide-audio')
            peak=max(peak,float(np.abs(block).max()));energy+=float(np.sum(block.astype(np.float64)**2));count+=block.size
    if duration is not None and abs(seconds-duration)>.15:raise GuideError('source-slice-duration-mismatch')
    if not count or energy/count<1e-12:raise GuideError('empty-or-silent-guide')
    return {'sampleRate':rate,'channels':channels,'durationSeconds':seconds,'peak':peak,'rms':math.sqrt(energy/count),'bytes':path.stat().st_size}


class SongGuide:
    def __init__(self,*,root=ROOT,data_root=None,phase_runner=None):
        self.root=local_path(root)
        self.data_root=resolve_data_paths(root=data_root or os.environ.get('WHALE_DATA_ROOT', os.path.join(os.path.expanduser('~'), 'WhaleData')))['root']
        self.guides=local_path(self.data_root/'voice/song-guides')
        self.phase_runner=phase_runner or self.run_phase

    def environment(self):
        return {'downloadPython':self.root/'.venv-seed-vc/Scripts/python.exe',
                'separatorPython':self.root/'.venv-vocal-separator/Scripts/python.exe',
                'model':self.root/'voice-data/models/demucs/955717e8-8726e21a.th',
                'source':self.root/'sources/demucs',
                'ffmpegLock':self.root/'tools/seed-vc/ffmpeg.lock.json'}

    def status(self):
        return {'backend':'song-guide-cpu','ready':all(p.exists() for p in self.environment().values()),
                'maxDurationSeconds':MAX_DURATION,'maxFileBytes':MAX_FILE,'requiresListeningApproval':False}

    def run_phase(self,name,job,args):
        env=self.environment();python=env['downloadPython' if name=='extract' else 'separatorPython']
        if not python.is_file():raise GuideError('song-guide-environment-unavailable')
        command=[str(python),str(Path(__file__).resolve()),'--stage',name,'--job-dir',str(job),'--workspace',str(self.root)]
        if name=='extract':command+=['--url',args['sourceUrl'],'--start',str(args['startSeconds']),'--duration',str(args['durationSeconds'])]
        try:
            options={'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}
            process=subprocess.Popen(command,env=clean_env(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,shell=False,**options)
            try:returncode=process.wait(timeout=210 if name=='extract' else 360)
            except subprocess.TimeoutExpired:
                # Kill only this worker's process tree, including a bounded
                # ffmpeg child, rather than leave an orphan after timeout.
                try:
                    if os.name=='nt':
                        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,shell=False)
                    else:
                        import signal
                        os.killpg(process.pid,signal.SIGKILL)
                except (OSError,subprocess.TimeoutExpired):pass
                try:process.kill();process.wait(timeout=10)
                except (OSError,subprocess.TimeoutExpired):pass
                raise GuideError('song-guide-'+name+'-timeout') from None
        except OSError:raise GuideError('song-guide-stage-unavailable') from None
        receipt=local_path(job/(name+'-result.json'))
        if not receipt.is_file():raise GuideError('song-guide-'+name+'-failed')
        info=json.loads(receipt.read_text('utf8'))
        if returncode or info.get('ok') is not True:
            code=info.get('code','')
            raise GuideError(code if re.fullmatch(r'[a-z0-9-]{1,80}',code) else 'song-guide-'+name+'-failed')
        return info

    def prepare(self,args):
        if set(args)-{'id','op','sourceUrl','startSeconds','durationSeconds','chatKey'}:raise GuideError('unsupported-guide-parameters')
        url=source_url(args.get('sourceUrl'));start,duration=slice_args(args.get('startSeconds'),args.get('durationSeconds'))
        chat=args.get('chatKey')
        if not isinstance(chat,str) or not re.fullmatch(r'[a-f0-9]{16}',chat):raise GuideError('invalid-chat-key')
        guide_id=uuid.uuid4().hex
        job=local_path(self.guides/chat/guide_id);job.mkdir(parents=True,exist_ok=False)
        request={'sourceUrl':url,'startSeconds':start,'durationSeconds':duration}
        try:
            extraction=self.phase_runner('extract',job,request)
            mixed=local_path(job/'source.wav');source_info=audio_info(mixed,duration=duration)
            separation=self.phase_runner('separate',job,request)
            if separation.get('method')!='demucs-htdemucs' or separation.get('modelSha256')!=MODEL_SHA:
                raise GuideError('guide-separation-unverified')
            guide=local_path(job/'guide.wav');guide_info=audio_info(guide,duration=source_info['durationSeconds'],mono=True)
            metadata={'schemaVersion':1,'kind':'whale-song-guide','guideId':guide_id,'jobId':guide_id,
                'chatKey':chat,'sourceUrl':url,'sourceAudioSha256':sha(mixed),
                'sourceStartSeconds':start,'sourceDurationSeconds':duration,
                'slice':{'startSeconds':start,'durationSeconds':duration},
                'guideFile':'guide.wav','guideSha256':sha(guide),'audioKind':'isolated-vocal',
                'verified':True,'musicalSourceVerified':False,'dryVocalMethod':'demucs-htdemucs',
                'vocalIsolation':'model-estimated','listeningAccepted':False,
                'createdAt':datetime.now(timezone.utc).isoformat(),**guide_info,
                'separator':{'sourceRepo':MODEL_REPO,'sourceCommit':MODEL_COMMIT,'modelUrl':MODEL_URL,'modelSha256':MODEL_SHA,'device':'cpu'},
                'sourceExtractor':{'name':'yt-dlp','version':extraction.get('extractorVersion'),
                                   'mediaId':extraction.get('mediaId'),'ffmpegBinarySha256':FFMPEG_SHA},
                'separationGain':separation.get('gain',1.0)}
            atomic_json(local_path(job/'metadata.json'),metadata)
            return {k:metadata[k] for k in ('guideId','jobId','chatKey','sourceUrl','sourceStartSeconds','sourceDurationSeconds',
                'sampleRate','durationSeconds','guideSha256','dryVocalMethod','listeningAccepted','musicalSourceVerified','vocalIsolation')}
        except Exception:
            (job/'metadata.json').unlink(missing_ok=True)
            atomic_json(job/'failure.json',{'schemaVersion':1,'guideId':guide_id,'verified':False})
            raise

    def run(self,request):
        if request.get('op')=='status':return self.status()
        if request.get('op')=='prepare':return self.prepare(request)
        raise GuideError('unsupported-operation')


def extract_stage(root,job,url,start,duration):
    import yt_dlp
    from yt_dlp.version import __version__
    class Quiet:
        def debug(self,*a,**k):pass
        def warning(self,*a,**k):pass
        def error(self,*a,**k):pass
    url=source_url(url);start,duration=slice_args(start,duration)
    try:
        lock=json.loads((root/'tools/seed-vc/ffmpeg.lock.json').read_text('utf8'))
        ffmpeg=local_path(lock['binaryPath'])
        if not ffmpeg.is_file():raise ValueError()
    except (OSError,ValueError,KeyError,RuntimeError):raise GuideError('ffmpeg-environment-unavailable') from None
    if not ffmpeg.is_relative_to(root) or sha(ffmpeg)!=FFMPEG_SHA:raise GuideError('ffmpeg-hash-mismatch')
    if __version__!='2026.10.26':raise GuideError('source-extractor-version-mismatch')
    with yt_dlp.YoutubeDL({'skip_download':True,'noplaylist':True,'cachedir':False,'proxy':'',
         'socket_timeout':15,'retries':0,'extractor_retries':0,'quiet':True,'no_warnings':True,'logger':Quiet()}) as downloader:
        try:media=downloader.extract_info(url,download=False)
        except Exception:raise GuideError('public-source-unavailable-or-auth-required') from None
    if not isinstance(media,dict) or media.get('_type') in ('playlist','multi_video') or media.get('is_live'):
        raise GuideError('unsupported-or-live-source')
    if media.get('availability') in ('private','premium_only','subscriber_only','needs_auth'):
        raise GuideError('public-source-unavailable-or-auth-required')
    formats=[f for f in media.get('formats',[]) if f.get('vcodec')=='none' and f.get('acodec') not in ('none',None)
             and f.get('protocol') in ('https','http') and isinstance(f.get('url'),str)]
    if not formats:raise GuideError('no-supported-direct-audio')
    platform='youtube' if 'youtube.com' in urllib.parse.urlsplit(url).hostname else 'bilibili'
    selected=None
    for candidate in sorted(formats,key=lambda f:f.get('abr') or f.get('tbr') or 0,reverse=True):
        try:public_media_url(candidate['url'],platform)
        except GuideError:continue
        selected=candidate;break
    if selected is None:raise GuideError('nonpublic-or-unsupported-media-url')
    headers=selected.get('http_headers') or media.get('http_headers') or {}
    safe_headers={k:str(headers.get(k,default)) for k,default in [('Referer',url),('User-Agent','Mozilla/5.0')]}
    if any('\r' in v or '\n' in v or len(v)>2048 for v in safe_headers.values()):raise GuideError('invalid-media-headers')
    command=[str(ffmpeg),'-nostdin','-hide_banner','-loglevel','error','-y','-protocol_whitelist','https,tls,tcp',
             '-rw_timeout','15000000','-headers',''.join(f'{k}: {v}\r\n' for k,v in safe_headers.items()),
             '-ss',str(start),'-i',selected['url'],'-t',str(duration),'-map','0:a:0','-vn','-ar','44100','-ac','2',
             '-c:a','pcm_s16le','-fs',str(MAX_FILE),str(job/'source.wav')]
    try:result=subprocess.run(command,env=clean_env(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=150,shell=False)
    except subprocess.TimeoutExpired:raise GuideError('bounded-source-download-timeout') from None
    if result.returncode:raise GuideError('bounded-source-download-failed')
    return {'ok':True,'mediaId':str(media.get('id',''))[:80],'extractorVersion':__version__}


def separate_stage(root,job):
    import numpy as np,soundfile as sf,torch
    source=root/'sources/demucs';weight=root/'voice-data/models/demucs/955717e8-8726e21a.th'
    if not weight.is_file():raise GuideError('separator-model-unavailable')
    if sha(weight)!=MODEL_SHA:raise GuideError('separator-weight-hash-mismatch')
    try:commit=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True,timeout=10).strip()
    except Exception:raise GuideError('separator-source-unavailable') from None
    if commit!=MODEL_COMMIT:raise GuideError('separator-source-commit-mismatch')
    audio_info(job/'source.wav')
    audio,rate=sf.read(job/'source.wav',dtype='float32',always_2d=True)
    torch.set_num_threads(4);torch.set_grad_enabled(False);torch.manual_seed(42)
    sys.path.insert(0,str(source))
    # Model repository is local; any unexpected download is rejected.
    from emotional_voice_runtime import no_network
    with no_network():
        from demucs.api import Separator
        separator=Separator(model='955717e8',repo=weight.parent,device='cpu',shifts=1,overlap=.25,
                            segment=7,split=True,jobs=0,progress=False)
        _,stems=separator.separate_tensor(torch.from_numpy(audio.T.copy()),sr=rate)
    if 'vocals' not in stems:raise GuideError('separator-returned-no-vocals')
    vocals=stems['vocals'].detach().cpu().numpy().mean(axis=0)
    if not len(vocals) or not np.isfinite(vocals).all():raise GuideError('invalid-separated-vocals')
    peak=float(np.max(np.abs(vocals)));gain=min(1.,.95/peak) if peak else 1.
    sf.write(job/'guide.wav',vocals*gain,separator.samplerate,subtype='FLOAT')
    return {'ok':True,'method':'demucs-htdemucs','modelSha256':MODEL_SHA,'gain':gain}


def main(input_stream=None,output_stream=None,runtime=None):
    input_stream=input_stream or sys.stdin;output_stream=output_stream or sys.stdout;runtime=runtime or SongGuide()
    for line in input_stream:
        request={}
        try:
            if len(line)>16384:raise GuideError('request-too-large')
            request=json.loads(line)
            if not isinstance(request,dict):raise GuideError('invalid-request')
            if request.get('op')=='stop':break
            started=time.monotonic();value=runtime.run(request)
            result={'id':request.get('id'),'ok':True,'value':value,'wallSeconds':round(time.monotonic()-started,2)}
        except Exception as error:
            result={'id':request.get('id') if isinstance(request,dict) else None,'ok':False,
                    'code':str(error) if isinstance(error,GuideError) else 'song-guide-preparation-failed'}
        output_stream.write(json.dumps(result,ensure_ascii=False)+'\n');output_stream.flush()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--stage',choices=['extract','separate']);parser.add_argument('--job-dir')
    parser.add_argument('--workspace');parser.add_argument('--url');parser.add_argument('--start',type=float);parser.add_argument('--duration',type=float)
    args=parser.parse_args()
    if args.stage:
        job=local_path(args.job_dir);root=local_path(args.workspace)
        try:
            value=extract_stage(root,job,args.url,args.start,args.duration) if args.stage=='extract' else separate_stage(root,job)
        except Exception as error:
            value={'ok':False,'code':str(error) if isinstance(error,GuideError) else 'song-stage-failed'}
        atomic_json(job/(args.stage+'-result.json'),value);sys.exit(0 if value['ok'] else 1)
    main()
