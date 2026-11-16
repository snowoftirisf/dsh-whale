// Inert parent for one explicitly started helper. No UI, model, browser control
// or automatic restart. Transcripts go only to validated onSegment callbacks.
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';

const fail=code=>{throw Error(code);};
const hex=(value,size)=>typeof value==='string'&&new RegExp(`^[a-f0-9]{${size}}$`).test(value);
const finite=value=>typeof value==='number'&&Number.isFinite(value);
const integer=value=>Number.isSafeInteger(value)&&value>=0;
const exact=(value,fields)=>value&&typeof value==='object'&&!Array.isArray(value)
  &&fields.every(k=>Object.hasOwn(value,k))&&Object.keys(value).every(k=>fields.includes(k));

export function createContinuousDouyinClient({python,script,cacheRoot,modelRoot,spawnProcess=spawn,
  report=()=>{},onSegment=()=>{},onGap=()=>{},shutdownGraceMs=6000,platform=process.platform}={}){
  if([python,script,cacheRoot,modelRoot].some(v=>typeof v!=='string'||!v||v.includes('\0'))
      ||typeof spawnProcess!=='function'||!finite(shutdownGraceMs)||shutdownGraceMs<=0)
    fail('continuous-audio-client-config-invalid');
  let worker=null,lines=null,browserPid=null,sessionId=null,state='idle',exited=false,stopping=false;
  let shutdown=null,exitResolve,exitPromise=null,lastSequence=-1,segments=0,gaps=0,rejected=0;
  let exitCode=null,exitSignal=null,captureProof=null;
  const status=()=>({state,browserPid,workerPid:integer(worker?.pid)?worker.pid:null,sessionId,
    segments,gaps,rejected,exitCode,exitSignal,captureProof});
  const notify=code=>{try{Promise.resolve(report(code,status())).catch(()=>{});}catch{/* Diagnostics never echo payloads. */}};
  const reject=()=>{rejected++;notify('continuous-audio-protocol-rejected');};
  const dispatch=(callback,event)=>{try{Promise.resolve(callback(Object.freeze({...event})))
    .catch(()=>notify('continuous-audio-consumer-failed'));}catch{notify('continuous-audio-consumer-failed');}};
  function write(op){
    if(!worker||exited||worker.stdin.destroyed)return false;
    try{worker.stdin.write(JSON.stringify({op})+'\n',error=>{if(error)notify('continuous-audio-control-write-failed');});return true;}
    catch{notify('continuous-audio-control-write-failed');return false;}
  }
  function armShutdown(){
    if(shutdown!==null||exited||!worker)return;
    shutdown=setTimeout(()=>{
      shutdown=null;if(exited)return;
      state='stop-timeout';notify('continuous-audio-stop-timeout');
      // Only the actual process returned by THIS spawn is eligible. Never use
      // the dedicated browser PID, nor infer a process from helper payloads.
      if(!Number.isInteger(worker.pid)||worker.pid<=0||worker.pid===browserPid){
        notify('continuous-audio-worker-pid-unverified');return;
      }
      try{
        if(platform==='win32'){
          const killer=spawnProcess('taskkill.exe',['/PID',String(worker.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});
          killer.on('error',()=>notify('continuous-audio-worker-kill-failed'));
        }else worker.kill();
      }catch{notify('continuous-audio-worker-kill-failed');}
    },shutdownGraceMs);
    shutdown.unref?.();
  }
  function finish(code,signal){
    if(exited)return;
    exited=true;stopping=true;state='exited';exitCode=Number.isInteger(code)?code:null;
    exitSignal=typeof signal==='string'&&/^[A-Z0-9]+$/.test(signal)?signal:null;
    if(shutdown!==null){clearTimeout(shutdown);shutdown=null;}
    lines?.close();notify('continuous-audio-worker-exited');exitResolve?.(status());
  }
  function eventLine(line){
    if(stopping||exited)return;
    let event;
    try{if(line.length>200000)throw Error();event=JSON.parse(line);}catch{reject();return;}
    // main() can fail before constructing the helper/session (e.g. denied PID).
    // This exact safe diagnostic comes only from our own child's stdout and
    // never establishes a session or authorizes unbound audio.
    if(sessionId===null&&exact(event,['event','code'])&&event.event==='error'
        &&typeof event.code==='string'&&/^continuous-audio-[a-z-]{1,100}$/.test(event.code)){
      notify(event.code);return;
    }
    if(!event||event.pid!==browserPid||!hex(event.sessionId,32)
        ||(sessionId!==null&&sessionId!==event.sessionId)){reject();return;}
    if(event.event==='started'){
      if((sessionId!==null&&!['resuming','paused'].includes(state))||event.sampleRate!==48000||event.channels!==2||event.chunkFrames!==480000
          ||event.videoIdentity!=='unknown'||event.playingProof!==false){reject();return;}
      if(event.protocolVersion!==2||event.processSpecificCapture!==true
          ||event.captureScopeVersion!==1||event.callbackFrameNormalizationVersion!==1){
        reject();stopping=true;state='stopping';write('stop');
        try{worker.stdin.end();}catch{/* The bounded shutdown remains armed. */}
        armShutdown();notify('continuous-audio-capture-scope-unverified');return;
      }
      captureProof={protocolVersion:2,processSpecificCapture:true,captureScopeVersion:1,callbackFrameNormalizationVersion:1};
      sessionId=event.sessionId;state='running';notify('continuous-audio-running');return;
    }
    // Errors before started can explain a failed start but do not authorize any
    // audio. Bind their session only after checking the same worker/browser PID.
    if(event.event==='error'){
      if(typeof event.code==='string'&&/^continuous-audio-[a-z-]{1,100}$/.test(event.code))notify(event.code);
      else reject();
      return;
    }
    if(event.event==='stopped'){
      if(sessionId===null)sessionId=event.sessionId;
      stopping=true;state='stopping';armShutdown();notify('continuous-audio-helper-stopped');return;
    }
    if(sessionId===null){reject();return;}
    if(event.event==='paused'){
      if(event.captureStopped!==true){reject();return;}
      state='paused';notify('continuous-audio-paused');return;
    }
    if(event.event==='gap'){
      if(!integer(event.frames)||event.frames<=0||!finite(event.durationSeconds)
          ||Math.abs(event.durationSeconds-event.frames/48000)>1/48000
          ||!integer(event.droppedFrames)||event.droppedFrames<event.frames
          ||typeof event.reason!=='string'||!/^[a-z-]{1,80}$/.test(event.reason)
          ||(event.sequence!==undefined&&!integer(event.sequence))){reject();return;}
      gaps++;dispatch(onGap,{event:'gap',sessionId,pid:browserPid,frames:event.frames,durationSeconds:event.durationSeconds,
        droppedFrames:event.droppedFrames,reason:event.reason,...(event.sequence===undefined?{}:{sequence:event.sequence})});
      return;
    }
    const fields=['sessionId','pid','event','sequence','startedAt','endedAt','callbackObservedAt','timestampBasis',
      'durationSeconds','frames','sampleRate','channels','encoding','wavSha256','audioRetained','asrStatus',
      'transcript','backend','videoIdentity','playingProof'];
    if(!exact(event,fields)||event.event!=='segment'||!integer(event.sequence)||event.sequence<=lastSequence
        ||!integer(event.frames)||event.frames<=0||event.frames>480000||event.sampleRate!==48000||event.channels!==2||event.encoding!=='pcm-s16le'
        ||!finite(event.durationSeconds)||Math.abs(event.durationSeconds-event.frames/48000)>.000001
        ||![event.startedAt,event.endedAt,event.callbackObservedAt].every(v=>finite(v)&&v>=0)
        ||Math.abs(event.endedAt-event.startedAt-event.durationSeconds)>.001
        ||event.timestampBasis!=='callback-arrival-minus-audio-frames'||!hex(event.wavSha256,64)
        ||event.audioRetained!==false||event.backend!=='sensevoice-int8'||event.videoIdentity!=='unknown'||event.playingProof!==false
        ||!['speech','no-speech','no-transcript'].includes(event.asrStatus)||typeof event.transcript!=='string'
        ||event.transcript.length>64000||Array.from(event.transcript).length>32000||event.transcript.includes('\0')
        ||(event.asrStatus==='speech'?!event.transcript.trim():!!event.transcript.trim())){reject();return;}
    lastSequence=event.sequence;segments++;dispatch(onSegment,event);
  }
  return Object.freeze({
    start(pid){
      if(!Number.isInteger(pid)||pid<=0)fail('continuous-audio-browser-pid-invalid');
      if(worker&&!stopping&&!exited&&pid===browserPid)return status();
      if(worker||stopping||exited)fail('continuous-audio-client-already-started');
      browserPid=pid;state='starting';exitPromise=new Promise(resolve=>{exitResolve=resolve;});
      try{
        worker=spawnProcess(python,['-u',script],{windowsHide:true,stdio:['pipe','pipe','pipe'],
          env:{...process.env,CUDA_VISIBLE_DEVICES:'',HF_HUB_OFFLINE:'1',TRANSFORMERS_OFFLINE:'1',PYTHONIOENCODING:'utf-8'}});
        worker.stderr.on('data',()=>{});worker.stdin.on('error',()=>notify('continuous-audio-control-write-failed'));
        worker.on('exit',finish);
        worker.on('error',()=>{notify('continuous-audio-worker-error');if(!Number.isInteger(worker.pid))finish(null,null);});
        lines=createInterface({input:worker.stdout});lines.on('line',eventLine);
        worker.stdin.write(JSON.stringify({op:'start',pid,cacheRoot,modelRoot})+'\n',error=>{
          if(error)notify('continuous-audio-control-write-failed');});
      }catch{state='failed';stopping=true;if(!worker)finish(null,null);
        else{try{worker.stdin.end();}catch{/* The stop timer remains authoritative. */}armShutdown();}
        notify('continuous-audio-start-failed');}
      return status();
    },
    pause(){if(!worker||stopping||exited)return false;if(['paused','pausing'].includes(state))return true;
      const sent=write('pause');if(sent)state='pausing';return sent;},
    resume(){if(!worker||stopping||exited)return false;if(['running','resuming'].includes(state))return true;
      const sent=write('resume');if(sent)state='resuming';return sent;},
    stop(){
      if(!worker){stopping=true;state='stopped';return Promise.resolve(status());}
      if(exited)return exitPromise;
      if(!stopping){stopping=true;state='stopping';write('stop');
        try{worker.stdin.end();}catch{notify('continuous-audio-control-write-failed');}}
      armShutdown();return exitPromise;
    },
    status,
  });
}
