// Capture and scan scheduling only. The injected functions own their I/O and cancellation.
export function createNativeVoicePump({capture:takeCapture,scan,onSuccess=()=>{},onError=()=>{},
  isClosed=()=>false,isPaused=()=>false,now=Date.now,setTimer=setTimeout,clearTimer=clearTimeout,
  minCaptureMs=15000,retryMs=15000}) {
  if(typeof takeCapture!=='function'||typeof scan!=='function'||!Number.isFinite(minCaptureMs)
    ||minCaptureMs<0||!Number.isFinite(retryMs)||retryMs<=0||typeof isPaused!=='function')throw Error('native-voice-pump-config-invalid');
  let closed=false,inflight=null,wake=null,wakeAt=null,needScan=false,needCapture=false;
  let lastCapture=-Infinity,captureRetryAt=-Infinity,scanRetryAt=-Infinity;
  const stopped=()=>closed||isClosed();
  const paused=()=>{try{return !!isPaused();}catch{return true;}};
  const captureDue=()=>Math.max(lastCapture+minCaptureMs,captureRetryAt);
  const retryCapture=()=>{
    needCapture=true;needScan=true;
    captureRetryAt=now()+retryMs;
    scanRetryAt=Math.max(scanRetryAt,captureDue());
  };
  const notify=async(callback,...args)=>{try{await callback(...args);}catch{/* A reporter cannot stop the pump. */}};
  function cancelWake(){if(wake!==null)clearTimer(wake);wake=null;wakeAt=null;}
  function schedule(){
    if(stopped()||paused()){cancelWake();return;}
    const due=Math.min(needCapture?captureDue():Infinity,needScan?scanRetryAt:Infinity);
    if(due===Infinity){cancelWake();return;}
    if(wake!==null&&wakeAt===due)return;
    cancelWake();wakeAt=due;
    wake=setTimer(()=>{
      wake=null;wakeAt=null;
      if(!stopped())void start();
    },Math.max(0,due-now()));
    wake?.unref?.();
  }
  async function run(){
    const result={ok:true,captures:0,scans:0,deferred:false};
    try{
      while(!stopped()){
        // Pausing never cancels an owned capture already underway. Its result
        // and pending scan/capture intent remain intact, but no next I/O starts.
        if(paused()){result.deferred=needCapture||needScan;cancelWake();break;}
        if(needCapture&&now()>=captureDue()){
          needCapture=false;lastCapture=now();
          try{
            const value=await takeCapture();
            if(stopped())break;
            if(value?.ok===false)throw Error(typeof value.code==='string'?value.code:'native-voice-capture-failed');
            captureRetryAt=-Infinity;result.captures++;needScan=true;
          }catch(error){
            if(stopped())break;
            result.ok=false;retryCapture();
            await notify(onError,error,{phase:'capture'});
          }
          continue;
        }
        if(needScan&&now()>=scanRetryAt){
          needScan=false;
          try{
            const value=await scan();
            if(stopped())break;
            scanRetryAt=-Infinity;result.scans++;
            // A catalog may be authentic but its media DB has not caught up yet.
            // The processor reports individual queued failures without throwing.
            // Keep its diagnostics, but retry with a fresh capture, not a tight scan loop.
            if(Number.isFinite(value?.failed)&&value.failed>0){result.ok=false;retryCapture();}
            await notify(onSuccess,value);
          }catch(error){
            if(stopped())break;
            result.ok=false;retryCapture();
            await notify(onError,error,{phase:'scan'});
          }
          continue;
        }
        result.deferred=needCapture||needScan;
        schedule();
        break;
      }
      return result;
    }finally{
      // This release is INSIDE the run's last synchronous turn, not a later .finally
      // reaction. A request queued at completion either enters this loop or starts
      // its own run; it cannot attach to an already-finished run and lose its flags.
      inflight=null;
      if(stopped()||paused())cancelWake();
    }
  }
  function start(){
    if(stopped())return Promise.resolve({closed:true});
    if(paused()){cancelWake();return Promise.resolve({ok:true,captures:0,scans:0,deferred:true});}
    if(inflight)return inflight;
    // Starting on a microtask also coalesces simultaneous hints before capture starts.
    inflight=Promise.resolve().then(run);
    return inflight;
  }
  return {
    refresh({capture=false}={}){
      if(stopped())return Promise.resolve({closed:true});
      needScan=true;needCapture||=capture===true;
      return start();
    },
    close(){closed=true;needScan=false;needCapture=false;cancelWake();},
  };
}
