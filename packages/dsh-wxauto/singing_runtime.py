"""Private JSONL singing conversion worker; no UI, sending, HTTP or TTS.

Trusted Node derives chatKey; the model-facing tool only supplies guideId.
Seed-VC source remains GPL-3.0 in its pinned official checkout; not copied here.
"""
import contextlib,hashlib,importlib.metadata,importlib.util,json,math,os,re,shutil,sys,time,uuid
from pathlib import Path
from urllib.parse import urlsplit
from attachment_policy import checked_file,local_path
from data_paths import absolute_data_path,resolve_data_paths
from emotional_voice_runtime import no_network

ROOT=Path(__import__('os').environ.get('WHALE_RUNTIME_ROOT') or Path(__file__).resolve().parents[2])
BACKEND='seed-vc-44k-svc'
COMMIT='51383efd921027683c89e5348211d93ff12ac2a8'
SOURCE_LOCK_SHA='4d2e7c93696efe1480683c04b538cd1a467761a402cad442a7b213943087705b'
MODEL_LOCK_SHA='3088b6ec3705616a3b16e5a51c5857932cd81179b77fc3026722dfddeac4778c'
MAX_GUIDE_SECONDS=600
MAX_BYTES=128*1024**2


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024*1024),b''):value.update(block)
    return value.hexdigest()


def asset_status(root,model_root,verify_hashes=False):
    here=local_path(root/'tools/seed-vc');source=local_path(root/'sources/seed-vc')
    source_lock=checked_file(here/'runtime-source.lock.json',here,1024**2)
    model_lock=checked_file(here/'models.lock.json',here,1024**2)
    if digest(source_lock)!=SOURCE_LOCK_SHA or digest(model_lock)!=MODEL_LOCK_SHA:raise RuntimeError('singing-asset-lock-mismatch')
    sources=json.loads(source_lock.read_text());models=json.loads(model_lock.read_text())
    if sources.get('commit')!=COMMIT or models.get('sourceCommit')!=COMMIT:raise RuntimeError('singing-source-version-mismatch')
    for name,expected in sources['files'].items():
        file=absolute_data_path(name,root=source)
        if file.is_file() and file.stat().st_size==0 and file.stat().st_nlink==1:
            pass  # Pinned empty __init__.py files are legitimate source assets.
        else:file=checked_file(file,source,16*1024**2)
        if digest(file)!=expected:raise RuntimeError('singing-source-hash-mismatch')
    count=0
    for model in models['models']:
        if model.get('missing'):raise RuntimeError('singing-model-lock-incomplete')
        for row in model['files']:
            file=checked_file(absolute_data_path(model['repo'].replace('/','--')+'/'+row['path'],root=model_root/'seed-vc'),model_root/'seed-vc',2*1024**3)
            if file.stat().st_size!=row['bytes']:raise RuntimeError('singing-model-size-mismatch')
            if verify_hashes and digest(file)!=row['sha256']:raise RuntimeError('singing-model-hash-mismatch')
            count+=1
    return {'modelsReady':True,'modelFiles':count,'modelHashesVerified':bool(verify_hashes),'sourceCommit':COMMIT}


def dependency_status(root):
    names=('torch','torchaudio','numpy','soundfile','yaml','transformers','librosa','dac')
    try:versions={name:importlib.metadata.version(name) for name in ('torch','torchaudio')}
    except importlib.metadata.PackageNotFoundError:versions={}
    dll=root/'.venv-seed-vc/Lib/site-packages/torch/lib/libomp140.x86_64.dll'
    dll_ready=dll.is_file() and digest(dll)=='81996853dc057e995b4f11682653699d5b62bcadea994cbce2ab90a820fc887f'
    return {'environmentReady':local_path(sys.executable)==local_path(root/'.venv-seed-vc/Scripts/python.exe'),
        'dependenciesReady':all(importlib.util.find_spec(name) is not None for name in names)
            and versions=={'torch':'2.4.0+cu124','torchaudio':'2.4.0+cu124'} and dll_ready,
        'localOpenMPRuntimeReady':dll_ready,'cudaRequired':True,'cudaChecked':False}


def backend_factory(root,models):
    file=checked_file(root/'tools/seed-vc/svc_backend.py',root/'tools/seed-vc',1024**2)
    spec=importlib.util.spec_from_file_location('_whale_pinned_svc_backend',file)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.SeedBackend(root,models)


class SingingRuntime:
    def __init__(self,*,data_root=None,asset_root=None,model_root=None,factory=backend_factory,
                 assets=asset_status,dependencies=dependency_status):
        self.root=local_path(asset_root or os.environ.get('WHALE_SINGING_ASSET_ROOT',ROOT))
        self.paths=resolve_data_paths(root=data_root or os.environ.get('WHALE_DATA_ROOT', os.path.join(os.path.expanduser('~'), 'WhaleData')))
        self.voice=local_path(self.paths['voice']);self.config_file=self.voice/'voice.local.json'
        self.models=local_path(model_root or os.environ.get('WHALE_MODEL_ROOT',self.root/'voice-data/models'))
        self.factory,self.assets,self.dependencies=factory,assets,dependencies

    def config(self):
        file=local_path(self.config_file)
        value=json.loads(checked_file(file,self.voice,65536).read_text(encoding='utf-8-sig')) if file.exists() else {}
        if not isinstance(value,dict):raise RuntimeError('singing-invalid-voice-config')
        selection=self.voice/'singing.local.json'
        if selection.exists():
            chosen=json.loads(checked_file(selection,self.voice,65536).read_text(encoding='utf-8-sig'))
            keys={'approved','parentReferenceSha256','referenceAudioBase','referenceAudio','referenceSha256','voiceId'}
            if (not isinstance(chosen,dict) or set(chosen)!=keys or chosen.get('approved') is not True
                or chosen.get('referenceAudioBase')!='dataRoot'
                or not isinstance(chosen.get('voiceId'),str) or not chosen['voiceId']
                or not re.fullmatch('[0-9a-f]{64}',str(chosen.get('referenceSha256','')))):
                raise RuntimeError('singing-invalid-selection')
            _,parent_sha=self.reference(value)
            if chosen['parentReferenceSha256']!=parent_sha:raise RuntimeError('singing-parent-reference-changed')
            value={**value,**{k:chosen[k] for k in ('referenceAudioBase','referenceAudio','referenceSha256','voiceId')}}
        return value

    def reference(self,config):
        value=config.get('referenceAudio')
        if not isinstance(value,str) or not value:raise RuntimeError('singing-reference-required')
        file=absolute_data_path(value,root=self.paths['root']) if config.get('referenceAudioBase')=='dataRoot' else local_path(value)
        allowed=[self.voice,local_path(Path.home()/'.dsh')]
        base=next((p for p in allowed if file!=p and file.is_relative_to(p)),None)
        if base is None:raise RuntimeError('singing-reference-path-denied')
        file=checked_file(file,base,32*1024**2)
        if file.suffix.lower() not in ('.wav','.flac','.ogg','.mp3','.m4a'):raise RuntimeError('singing-reference-file-denied')
        import soundfile as sf
        info=sf.info(str(file))
        if not 1<=info.duration<=30 or info.channels not in (1,2):raise RuntimeError('singing-reference-duration-invalid')
        sha=digest(file)
        if config.get('referenceSha256') and config['referenceSha256']!=sha:raise RuntimeError('singing-reference-hash-mismatch')
        return file,sha

    def approval(self):
        config=self.config()
        if config.get('approved') is not True:raise RuntimeError('singing-voice-not-approved')
        file,sha=self.reference(config)
        if config.get('referenceSha256') and config['referenceSha256']!=sha:raise RuntimeError('singing-reference-hash-mismatch')
        return {'reference':file,'sha256':sha,'identity':(str(file),sha,config.get('voiceId'))}

    def status(self):
        try:models=self.assets(self.root,self.models,False)
        except Exception as error:
            code=str(error) if re.fullmatch('[a-z][a-z0-9-]{2,100}',str(error)) else 'singing-assets-unavailable'
            models={'modelsReady':False,'modelHashesVerified':False,'modelErrorCode':code}
        try:config=self.config()
        except Exception:config={}
        try:self.reference(config);reference_ready=True
        except Exception:reference_ready=False
        dependencies=self.dependencies(self.root)
        approved=config.get('approved') is True
        return {'backend':BACKEND,**models,**dependencies,'referenceReady':reference_ready,
            'voiceApproved':approved,'voiceSelected':approved and reference_ready,
            'ready':bool(models.get('modelsReady') and dependencies.get('environmentReady') and dependencies.get('dependenciesReady') and approved and reference_ready),
            'maxGuideSeconds':MAX_GUIDE_SECONDS,'maxSegmentSeconds':55,'cpuFallback':False,'gpuLoadedByStatus':False}

    def guide(self,guide_id,chat_key):
        if not isinstance(guide_id,str) or not re.fullmatch('[0-9a-f]{32}',guide_id):raise RuntimeError('singing-invalid-guide-id')
        if not isinstance(chat_key,str) or not re.fullmatch('[0-9a-f]{16}',chat_key):raise RuntimeError('singing-invalid-chat-key')
        folder=local_path(self.voice/'song-guides'/chat_key/guide_id)
        metadata_file=checked_file(folder/'metadata.json',folder,65536)
        metadata=json.loads(metadata_file.read_text(encoding='utf-8-sig'))
        if not isinstance(metadata,dict) or any((type(metadata.get('schemaVersion')) is not int,metadata.get('schemaVersion')!=1,metadata.get('kind')!='whale-song-guide',
            metadata.get('guideId')!=guide_id,metadata.get('jobId')!=guide_id,metadata.get('chatKey')!=chat_key,
            metadata.get('guideFile')!='guide.wav',metadata.get('audioKind')!='isolated-vocal',metadata.get('verified') is not True)):
            raise RuntimeError('singing-guide-metadata-mismatch')
        for key in ('guideSha256','sourceAudioSha256'):
            if not isinstance(metadata.get(key),str) or not re.fullmatch('[0-9a-f]{64}',metadata[key]):raise RuntimeError('singing-guide-hash-required')
        try:
            url=urlsplit(metadata['sourceUrl'])
            if url.scheme!='https' or not url.hostname or url.username or url.password or url.fragment:raise ValueError()
            for key in ('sourceStartSeconds','sourceDurationSeconds','durationSeconds'):
                value=metadata[key]
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError()
            if not 0<=metadata['sourceStartSeconds']<=86400 or not 1<=metadata['sourceDurationSeconds']<=MAX_GUIDE_SECONDS:raise ValueError()
        except (KeyError,TypeError,ValueError):raise RuntimeError('singing-guide-source-invalid')
        file=checked_file(folder/'guide.wav',folder,MAX_BYTES)
        if digest(file)!=metadata['guideSha256']:raise RuntimeError('singing-guide-hash-mismatch')
        import soundfile as sf
        info=sf.info(str(file))
        if info.samplerate!=44100 or info.channels!=1 or not 1<=info.duration<=MAX_GUIDE_SECONDS or abs(info.duration-metadata['durationSeconds'])>.12:
            raise RuntimeError('singing-guide-audio-invalid')
        if metadata.get('sampleRate')!=44100 or metadata.get('channels')!=1:raise RuntimeError('singing-guide-metadata-mismatch')
        import numpy as np
        peak=0
        for block in sf.blocks(str(file),blocksize=44100,dtype='float32'):
            if not np.isfinite(block).all():raise RuntimeError('singing-guide-audio-invalid')
            peak=max(peak,float(np.abs(block).max()))
        if peak<.00001:raise RuntimeError('singing-guide-silent')
        return file,metadata

    @staticmethod
    def remove_directory(path,parent):
        parent=local_path(parent).resolve();path=local_path(path).resolve()
        if path==parent or not path.is_relative_to(parent):raise RuntimeError('singing-cleanup-path-denied')
        if path.exists():shutil.rmtree(path)

    def synthesize(self,args):
        if set(args)-{'id','op','guideId','chatKey'}:raise RuntimeError('singing-unexpected-argument')
        original=self.approval();guide,metadata=self.guide(args.get('guideId'),args.get('chatKey'))
        state=self.status()
        if not state['ready']:raise RuntimeError('singing-environment-or-models-unavailable')
        self.assets(self.root,self.models,True)
        work_root=local_path(self.voice/'.singing-work');work_root.mkdir(parents=True,exist_ok=True)
        job=uuid.uuid4().hex;work=local_path(work_root/job);work.mkdir()
        final_root=local_path(self.voice/'outbox'/'singing');final=None
        try:
            guide_copy=work/'guide.wav';reference_copy=work/('reference'+original['reference'].suffix.lower())
            shutil.copyfile(guide,guide_copy);shutil.copyfile(original['reference'],reference_copy)
            if digest(guide_copy)!=metadata['guideSha256'] or digest(reference_copy)!=original['sha256']:raise RuntimeError('singing-input-changed')
            with no_network(),contextlib.redirect_stdout(sys.stderr):
                backend=self.factory(self.root,self.models)
                if self.approval()['identity']!=original['identity']:raise RuntimeError('singing-voice-approval-changed')
                rendered=backend.render(guide_copy,reference_copy,work)
            if self.approval()['identity']!=original['identity']:raise RuntimeError('singing-voice-approval-changed')
            _,current=self.guide(args['guideId'],args['chatKey'])
            if current!=metadata:raise RuntimeError('singing-guide-changed')
            file=checked_file(rendered['file'],work,MAX_BYTES)
            import numpy as np,soundfile as sf
            from voice_audio import segments
            audio,rate=sf.read(str(file),dtype='float32',always_2d=True)
            if rate!=44100 or audio.shape[1]!=1 or not len(audio) or not np.isfinite(audio).all() or len(audio)/rate>MAX_GUIDE_SECONDS+1 or abs(len(audio)/rate-metadata['durationSeconds'])>.25:
                raise RuntimeError('singing-generated-audio-invalid')
            peak=float(np.abs(audio).max());gain=min(1,.95/max(peak,1e-12));audio*=gain
            publish=local_path(work/'publish');publish.mkdir();pieces=[];offset=0
            for number,chunk in enumerate(segments(audio,rate,max_seconds=55),1):
                name=f'segment-{number:03}.wav';output=publish/name;sf.write(str(output),chunk,rate,subtype='PCM_24')
                pieces.append({'name':name,'durationSeconds':len(chunk)/rate,'offsetSeconds':offset/rate,'sha256':digest(output)})
                offset+=len(chunk)
            result={'backend':BACKEND,'sampleRate':rate,'durationSeconds':len(audio)/rate,'segmentCount':len(pieces),'requiresSegments':len(pieces)>1,'segments':pieces,
                'metadata':{'guideId':args['guideId'],'chatKey':args['chatKey'],'sourceUrl':metadata['sourceUrl'],
                    'guideSha256':metadata['guideSha256'],'sourceAudioSha256':metadata['sourceAudioSha256'],
                    'sourceStartSeconds':metadata['sourceStartSeconds'],'sourceDurationSeconds':metadata['sourceDurationSeconds'],
                    'actorReferenceSha256':original['sha256'],'sourceCommit':COMMIT,'audioKind':'voice-converted-guide',
                    'musicalSourceVerified':metadata.get('musicalSourceVerified') is True,'listeningAccepted':False,
                    'vocalIsolation':metadata.get('vocalIsolation','model-estimated'),'dryVocalMethod':metadata.get('dryVocalMethod'),
                    'headroom':{'rawPeak':peak,'gain':gain,'method':'uniform-downward-only'},
                    'vocoderHeadroom':rendered.get('vocoderHeadroom'),
                    'limitations':'Conversion follows the provided guide; no claim that its song identity/melody or isolation was human verified.'},
                'checkpointAudit':rendered.get('checkpointAudit',{})}
            (publish/'metadata.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
            if self.approval()['identity']!=original['identity']:raise RuntimeError('singing-voice-approval-changed')
            final_root.mkdir(parents=True,exist_ok=True);final=local_path(final_root/job)
            os.replace(publish,final)
            if self.approval()['identity']!=original['identity']:raise RuntimeError('singing-voice-approval-changed')
            result['segments']=[{**p,'file':str(final/p['name'])} for p in pieces]
            result['file']=result['segments'][0]['file']
            return result
        except Exception:
            if final is not None:self.remove_directory(final,final_root)
            raise
        finally:self.remove_directory(work,work_root)

    def run(self,args):
        if not isinstance(args,dict):raise RuntimeError('singing-invalid-request')
        if args.get('op')=='status':
            if set(args)-{'op','id'}:raise RuntimeError('singing-unexpected-argument')
            return self.status()
        if args.get('op')=='synthesize':return self.synthesize(args)
        raise RuntimeError('singing-unsupported-operation')


def main(runtime=None,stdin=None,stdout=None):
    runtime=runtime or SingingRuntime();stdin=stdin or sys.stdin;stdout=stdout or sys.stdout
    while True:
        line=stdin.readline(65537)
        if not line:break
        request={}
        try:
            if len(line)>65536:
                while not line.endswith('\n'):
                    line=stdin.readline(65537)
                    if not line:break
                raise RuntimeError('singing-request-too-large')
            request=json.loads(line)
            if isinstance(request,dict) and request.get('op')=='stop':break
            with contextlib.redirect_stdout(sys.stderr):value=runtime.run(request)
            response={'id':request.get('id'),'ok':True,'value':value}
        except Exception as error:
            code=str(error) if isinstance(error,(RuntimeError,ValueError)) and re.fullmatch('[a-z][a-z0-9-]{2,100}',str(error)) else 'singing-internal-error'
            response={'id':request.get('id') if isinstance(request,dict) else None,'ok':False,'code':code}
        stdout.write(json.dumps(response,ensure_ascii=False)+'\n');stdout.flush()


if __name__=='__main__':main()
