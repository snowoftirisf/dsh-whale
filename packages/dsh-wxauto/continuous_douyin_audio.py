"""Unconnected continuous dedicated-Douyin process-audio prototype.

Import does nothing. Only start validates the live dedicated-browser PID and
opens a scope-verified process wrapper. No desktop/system loopback, TTS, GPU,
network or WeChat controls.
Temporary WAVs are removed after ASR; parent must persist the emitted evidence.
"""
import array
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import stat
import sys
import threading
import time
import uuid
import wave


def _path(value):
    if not isinstance(value,str) or '\x00' in value:raise RuntimeError('continuous-audio-path-invalid')
    p=Path(value)
    if not p.is_absolute() or '..' in p.parts or value.startswith(('\\\\','//')):
        raise RuntimeError('continuous-audio-path-invalid')
    for part in (*reversed(p.parents),p):
        try:s=part.lstat()
        except FileNotFoundError:continue
        if stat.S_ISLNK(s.st_mode) or getattr(s,'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise RuntimeError('continuous-audio-linked-path')
    return p


class ContinuousDouyinAudio:
    def __init__(self,config,*,capture_factory,speech_factory,source_provider,emit,
                 clock=time.time,chunk_frames=480000,stop_timeout=2):
        allowed={'op','pid','cacheRoot','modelRoot','queueSegments','id'}
        if (not isinstance(config,dict) or not {'op','pid','cacheRoot','modelRoot'}<=set(config) or set(config)-allowed or config.get('op')!='start'
                or type(config.get('pid')) is not int or config['pid']<=0
                or type(config.get('queueSegments',3)) is not int or not 1<=config.get('queueSegments',3)<=8
                or type(chunk_frames)is not int or not 0<chunk_frames<=480000 or not 0<stop_timeout<=5):
            raise RuntimeError('continuous-audio-config-invalid')
        self.pid=config['pid'];self.root=_path(config['cacheRoot']);self.models=_path(config['modelRoot'])
        self.capture_factory,self.speech_factory,self.sources=capture_factory,speech_factory,source_provider
        self.emit,self.clock=emit,clock;self.chunk_frames=chunk_frames;self.stop_timeout=stop_timeout
        self.session_id=uuid.uuid4().hex;self.lock=threading.RLock();self.output_lock=threading.Lock()
        self.jobs=queue.Queue(maxsize=config.get('queueSegments',3));self.pending={}
        self.buffer=bytearray();self.buffer_start=None;self.capture=None;self.generation=0
        self.accepting=False;self.closed=False;self.started=False;self.sequence=0;self.worker=None
        self.stop_threads=[];self.folder=None;self.dropped_frames=0
        self.capture_stop_confirmed=True;self.stop_reported=False;self.hard_exit=False
        self.pending_gaps={};self.callback_errors=0
        self.last_callback_observed_at=None

    def _emit(self,event):
        with self.output_lock:
            try:self.emit({'sessionId':self.session_id,'pid':self.pid,**event})
            except Exception:self.closed=True;self.accepting=False

    def _permitted(self):
        return any(type(x.get('pid')) is int and x['pid']==self.pid for x in self.sources())

    def _gap(self,item,reason):
        # Never perform output from a capture callback. Repeated gaps coalesce
        # into at most 16 tiny records, including the reserved overflow bucket.
        with self.lock:
            self.dropped_frames+=item['frames']
            if reason not in self.pending_gaps and len(self.pending_gaps)>=15:reason='coalesced-drops'
            event=self.pending_gaps.get(reason)
            if event is None:
                event={'event':'gap','frames':0,'reason':reason,'gapCount':0}
                if item.get('sequence') is not None:event['sequence']=item['sequence']
                self.pending_gaps[reason]=event
            event['frames']+=item['frames'];event['gapCount']+=1
            event['durationSeconds']=event['frames']/48000;event['droppedFrames']=self.dropped_frames

    def _drain_notices(self):
        with self.lock:
            events=sorted(self.pending_gaps.values(),key=lambda event:event['droppedFrames'])
            self.pending_gaps={};errors=self.callback_errors;self.callback_errors=0
        # Output may block here, but no lock needed by capture is held.
        for event in events:self._emit(event)
        if errors:self._emit({'event':'error','code':'continuous-audio-callback-invalid','occurrences':errors})

    def _tail(self,reason):
        if self.buffer:
            self._gap({'frames':len(self.buffer)//8,'sequence':self.sequence},reason);self.sequence+=1
            self.buffer.clear();self.buffer_start=None

    def _flush_pause_tail(self):
        if not self.buffer:return
        frames=len(self.buffer)//8
        if (len(self.buffer)%8 or not 0<frames<=self.chunk_frames or self.buffer_start is None
                or self.last_callback_observed_at is None):
            self._tail('pause-tail-invalid');return
        item={'sequence':self.sequence,'frames':frames,'startedAt':self.buffer_start,
            'endedAt':self.buffer_start+frames/48000,'callbackObservedAt':self.last_callback_observed_at,'pcm':bytes(self.buffer)}
        self.sequence+=1;self.buffer.clear();self.buffer_start=None
        try:
            self.jobs.put_nowait(item);self.pending[item['sequence']]=item
        except queue.Full:self._gap(item,'asr-backpressure')

    @staticmethod
    def _verified_capture(capture):
        return (capture.scope_verified is True and type(capture.scope_version)is int and capture.scope_version==1
            and type(capture.normalization_version)is int and capture.normalization_version==1
            and capture.check() is True)

    def _on_audio(self,generation,pcm,count):
        with self.lock:
            if self.closed or not self.accepting or generation!=self.generation:return
            if type(count) is not int or count<=0 or count>48000*30:
                self._tail('invalid-callback');self.callback_errors+=1;return
            try:data=bytes(pcm)
            except Exception:
                self._tail('invalid-callback');self._gap({'frames':count},'invalid-callback');return
            if len(data)!=count*8:
                self._tail('invalid-callback');self._gap({'frames':count},'invalid-callback');return
            # These wall times estimate delivered audio from callback arrival;
            # they are NOT a player position or proof of a stable video ID.
            arrival=self.clock()
            self.last_callback_observed_at=arrival
            if not self.buffer:self.buffer_start=arrival-count/48000
            self.buffer.extend(data)
            size=self.chunk_frames*8
            while len(self.buffer)>=size:
                item={'sequence':self.sequence,'frames':self.chunk_frames,
                    'startedAt':self.buffer_start,'endedAt':self.buffer_start+self.chunk_frames/48000,
                    'callbackObservedAt':arrival,'pcm':bytes(self.buffer[:size])}
                self.sequence+=1;del self.buffer[:size];self.buffer_start=item['endedAt']
                try:
                    self.jobs.put_nowait(item);self.pending[item['sequence']]=item
                except queue.Full:self._gap(item,'asr-backpressure')
            if not self.buffer:self.buffer_start=None

    def _stop_capture(self):
        with self.lock:
            capture=self.capture;self.capture=None;self.accepting=False;self.generation+=1
        if capture is None:return
        errors=[]
        def stop():
            try:capture.stop()
            except Exception:errors.append(True)
        thread=threading.Thread(target=stop,daemon=True);self.stop_threads.append(thread);thread.start()
        thread.join(self.stop_timeout)
        if thread.is_alive() or errors:
            self.closed=True
            self.capture_stop_confirmed=False
            self._emit({'event':'error','code':'continuous-audio-capture-stop-unconfirmed'})
        else:self.capture_stop_confirmed=True

    def start(self):
        if self.closed:raise RuntimeError('continuous-audio-closed')
        if self.accepting:return
        if not self._permitted():raise RuntimeError('continuous-audio-process-denied')
        if not self.started:
            self.folder=_path(str(self.root/'voice'/'continuous-douyin'/self.session_id))
            self.folder.mkdir(parents=True,exist_ok=True)
            self.worker=threading.Thread(target=self._work,daemon=True);self.worker.start();self.started=True
        try:
            with self.lock:
                self.generation+=1;generation=self.generation;self.accepting=False
                self.capture=self.capture_factory(self.pid,lambda pcm,count:self._on_audio(generation,pcm,count))
                capture=self.capture;self.capture_stop_confirmed=False
            capture.start()
            if not self._verified_capture(capture):raise RuntimeError('continuous-audio-scope-unverified')
            with self.lock:self.accepting=True
        except Exception:
            self.stop(reason='capture-start-failed');raise RuntimeError('continuous-audio-capture-start-failed') from None
        self._emit({'event':'started','sampleRate':48000,'channels':2,'chunkFrames':self.chunk_frames,
            'videoIdentity':'unknown','playingProof':False,'protocolVersion':2,'processSpecificCapture':True,
            'captureScopeVersion':1,'callbackFrameNormalizationVersion':1})

    def pause(self):
        self._stop_capture()
        with self.lock:
            if self.capture_stop_confirmed and not self.closed:self._flush_pause_tail()
            else:self._tail('pause-tail-unconfirmed')
        self._drain_notices()
        self._emit({'event':'paused','queuedSegments':len(self.pending),'captureStopped':self.capture_stop_confirmed})

    def check_target(self):
        if self.closed:return
        if not self._permitted():self.stop(reason='target-unavailable');return
        with self.lock:capture=self.capture
        if capture is not None:
            try:
                if not self._verified_capture(capture):raise RuntimeError('scope-unverified')
            except Exception:
                self._emit({'event':'error','code':'continuous-audio-process-scope-unverified'})
                self.stop(reason='process-scope-unverified')

    def _write(self,item,file):
        file=_path(str(file))
        values=array.array('f');values.frombytes(item['pcm'])
        if sys.byteorder!='little':values.byteswap()
        if any(not math.isfinite(x) for x in values):raise RuntimeError('continuous-audio-nonfinite-samples')
        pcm=array.array('h',(round(max(-1,min(1,x))*32767) for x in values))
        if sys.byteorder!='little':pcm.byteswap()
        with file.open('xb') as target:
            with wave.open(target,'wb') as out:
                out.setnchannels(2);out.setsampwidth(2);out.setframerate(48000);out.writeframes(pcm.tobytes())
        with wave.open(str(file),'rb') as stream:
            if stream.getnframes()!=item['frames']:raise RuntimeError('continuous-audio-frame-mismatch')

    def _work(self):
        speech=None
        while not self.closed:
            self._drain_notices()
            try:item=self.jobs.get(timeout=.1)
            except queue.Empty:continue
            with self.lock:
                if self.closed or item['sequence'] not in self.pending:continue
            file=self.folder/(str(item['sequence']).zfill(8)+'-'+uuid.uuid4().hex+'.wav')
            try:
                self._write(item,file)
                if speech is None:speech=self.speech_factory()
                with contextlib.redirect_stdout(sys.stderr):result=speech.transcribe(str(file))
                if (not isinstance(result,dict) or not isinstance(result.get('text'),str)
                        or len(result['text'])>32000 or result.get('backend')!='sensevoice-int8'):
                    raise RuntimeError('continuous-audio-asr-invalid')
                transcript=result['text'].strip()
                if transcript and result.get('audioStatus')=='no-detected-speech':
                    raise RuntimeError('continuous-audio-asr-invalid')
                event={'event':'segment','sequence':item['sequence'],'startedAt':item['startedAt'],
                    'endedAt':item['endedAt'],'callbackObservedAt':item['callbackObservedAt'],
                    'timestampBasis':'callback-arrival-minus-audio-frames','durationSeconds':item['frames']/48000,
                    'frames':item['frames'],'sampleRate':48000,'channels':2,'encoding':'pcm-s16le',
                    'wavSha256':hashlib.sha256(file.read_bytes()).hexdigest(),'audioRetained':False,
                    'asrStatus':'speech' if transcript else 'no-speech' if result.get('audioStatus')=='no-detected-speech' else 'no-transcript',
                    'transcript':transcript,'backend':'sensevoice-int8','videoIdentity':'unknown','playingProof':False}
                with self.lock:
                    dispatch=not self.closed and self.pending.pop(item['sequence'],None) is not None
                if dispatch:self._emit(event)
            except Exception:
                with self.lock:
                    if self.pending.pop(item['sequence'],None) is not None:self._gap(item,'asr-unavailable')
            finally:
                try:file.unlink(missing_ok=True)
                except OSError:self._emit({'event':'error','code':'continuous-audio-temp-cleanup-failed'})

    def stop(self,reason='stop'):
        with self.lock:
            if self.stop_reported:return self.hard_exit
            self.stop_reported=True
            self.closed=True;self.accepting=False
        self._stop_capture()
        with self.lock:
            self._tail(reason+'-tail')
            for item in self.pending.values():self._gap(item,reason+'-unprocessed')
            self.pending.clear()
            while True:
                try:self.jobs.get_nowait()
                except queue.Empty:break
        if self.worker is not None:self.worker.join(self.stop_timeout)
        self._drain_notices()
        alive=bool(self.worker and self.worker.is_alive()) or any(t.is_alive() for t in self.stop_threads) or not self.capture_stop_confirmed
        self.hard_exit=alive
        self._emit({'event':'stopped','reason':reason,'captureStopped':self.capture_stop_confirmed,
            'boundedExitRequired':alive,'droppedFrames':self.dropped_frames})
        return alive


def main():
    instance=None;hard_exit=False
    output=sys.stdout
    def emit(value):output.write(json.dumps(value,ensure_ascii=False)+'\n');output.flush()
    try:
        line=sys.stdin.readline(16385)
        if not line or len(line)>16384:raise RuntimeError('continuous-audio-config-invalid')
        config=json.loads(line)
        cache=_path(config['cacheRoot']);models=_path(config['modelRoot'])
        os.environ.update({'WHALE_DATA_ROOT':str(cache),'WHALE_MODEL_ROOT':str(models),
            'CUDA_VISIBLE_DEVICES':'','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'})
        from voice_runtime import Speech
        from dedicated_process_audio import DedicatedProcessAudio
        speech=Speech()
        instance=ContinuousDouyinAudio(config,capture_factory=lambda pid,callback:DedicatedProcessAudio(pid,callback),
            speech_factory=lambda:speech,source_provider=speech.video_sources,emit=emit)
        instance.start();commands=queue.Queue()
        def reader():
            while True:
                line=sys.stdin.readline(16385)
                if not line:commands.put({'op':'stop','reason':'eof'});return
                try:
                    value=json.loads(line)
                    if len(line)>16384 or type(value)is not dict or set(value)!={'op'} or value['op'] not in ('pause','resume','stop'):
                        raise ValueError()
                    commands.put(value)
                except Exception:commands.put({'op':'invalid'})
        threading.Thread(target=reader,daemon=True).start();last_check=0
        while not instance.closed:
            try:command=commands.get(timeout=.2)
            except queue.Empty:command=None
            if command:
                if command['op']=='stop':break
                if command['op']=='pause':instance.pause()
                elif command['op']=='resume':instance.start()
                else:emit({'event':'error','code':'continuous-audio-command-invalid'})
            if time.monotonic()-last_check>=1:
                instance.check_target();last_check=time.monotonic()
    except Exception as error:
        code=str(error) if isinstance(error,RuntimeError) and str(error).startswith('continuous-audio-') else 'continuous-audio-unavailable'
        emit({'event':'error','code':code})
    finally:
        if instance is not None:hard_exit=instance.stop()
    if hard_exit:output.flush();os._exit(124)


if __name__=='__main__':main()
