// Observe an existing dedicated browser only. This module never opens, plays,
// navigates or authorizes a video, and never calls an LLM or the WeChat worker.
export function createContinuousDouyinSupervisor({getSources,createClient,onTranscript,onUnboundTranscript,onGap=()=>{},report=()=>{},
  enabled=()=>true,now=Date.now,intervalMs=1000}={}) {
  if([getSources,createClient,onTranscript].some(fn=>typeof fn!=='function'))throw Error('continuous-supervisor-config-invalid');
  if(onUnboundTranscript!==undefined&&typeof onUnboundTranscript!=='function')throw Error('continuous-supervisor-config-invalid');
  let source=null,sourceGeneration=0,client=null,pid=null,timer=null,closed=false,busy=false;
  let observations=[],segments=0,unbound=0,privateSegments=0,privateFailures=0,lastState='waiting-for-source',lastReport=null;
  const status=()=>({state:lastState,browserPid:pid,segments,unbound,privateSegments,privateFailures,worker:client?.status()??null});
  const notify=state=>{lastState=state;const value=status(),key=JSON.stringify(value);if(key===lastReport)return;lastReport=key;try{report(value);}catch{}};
  function remember(value){
    const s=value?.state??value,video=s?.video;
    const valid=!!s&&!s.loginRequired&&!s.verificationRequired&&!s.privateChatVisible&&!s.unsupportedPage
      &&/^\d{10,30}$/.test(video?.id??'')&&typeof video.paused==='boolean'&&video.readyState>=2;
    const item={observedAtMs:now(),public:valid,...(valid?{videoId:video.id,playing:video.paused===false}:{})};
    const last=observations.at(-1);
    if(last&&item.observedAtMs<=last.observedAtMs){
      if(item.observedAtMs===last.observedAtMs&&JSON.stringify(item)===JSON.stringify(last))return last;
      observations=[{observedAtMs:item.observedAtMs,public:false}];return observations[0];
    }
    observations.push(item);observations=observations.slice(-200);
    return item;
  }
  async function receive(segment,owner){
    const active=()=>!closed&&client===owner&&enabled();
    if(!active())return;
    let captureProof;
    try{
      const proof=owner.status().captureProof;
      if(proof?.protocolVersion!==2||proof.processSpecificCapture!==true
          ||proof.captureScopeVersion!==1||proof.callbackFrameNormalizationVersion!==1)throw Error('scope-unverified');
      captureProof={protocolVersion:2,processSpecificCapture:true,captureScopeVersion:1,callbackFrameNormalizationVersion:1};
    }catch{unbound++;notify('audio-process-scope-unproven');return;}
    async function retain(reason){
      if(!active())return;
      unbound++;
      if(!onUnboundTranscript){notify('audio-'+reason);return;}
      try{
        await onUnboundTranscript({segment,captureProof:{...captureProof},reason});
        privateSegments++;notify('private-transcript-recorded');
      }catch{privateFailures++;notify('private-transcript-failed');}
    }
    const bound=source,generation=sourceGeneration;
    if(!bound)return retain('source-unavailable');
    let result;
    try{result=await bound();}
    catch{if(active())return retain(sourceGeneration!==generation||source!==bound
      ?(source?'context-unproven':'source-unavailable'):'context-unavailable');return;}
    if(!active())return;
    if(source!==bound||sourceGeneration!==generation)return retain(source?'context-unproven':'source-unavailable');
    let proof;
    try{
      remember(result);
      const start=segment.startedAt*1000,end=segment.endedAt*1000;
      const before=observations.filter(o=>o.observedAtMs<=start).at(-1);
      const after=observations.find(o=>o.observedAtMs>=end);
      const span=before&&after?observations.filter(o=>o.observedAtMs>=before.observedAtMs&&o.observedAtMs<=after.observedAtMs):[];
      // Missing or non-public context cannot publish a transcript to group access.
      if(!span.length||span.length>40||span.some(o=>!o.public)
          ||start-before.observedAtMs>2000||after.observedAtMs-end>2000)return retain('context-unproven');
      proof=span.map(({videoId,playing,observedAtMs})=>({videoId,playing,observedAtMs}));
    }catch{return retain('context-unavailable');}
    if(!active())return;
    // Publication can have committed before its promise rejects. Never route
    // that error to a second writer or relabel it as missing context.
    try{await onTranscript({segment,observations:proof,captureProof:{...captureProof}});segments++;notify('transcript-recorded');}
    catch{notify('public-transcript-failed');}
  }
  async function tick(){
    if(closed||busy)return;
    busy=true;
    try{
      if(!enabled()){
        const old=client;client=null;pid=null;observations=[];if(old)await old.stop();notify('disabled');return;
      }
      if(!source){client?.pause();notify('waiting-for-source');return;}
      const bound=source,generation=sourceGeneration;
      const current=()=>{
        if(closed)return false;
        if(!enabled()||source!==bound||sourceGeneration!==generation){client?.pause();return false;}
        return true;
      };
      const result=await getSources(),sources=Array.isArray(result)?result:result?.sources;
      if(!current())return;
      if(!Array.isArray(sources)||sources.length!==1){client?.pause();notify('waiting-for-dedicated-browser');return;}
      const target=sources[0].pid;
      if(!Number.isSafeInteger(target)||target<=0)throw Error('source-invalid');
      const page=await bound();if(!current())return;
      const sample=remember(page);
      if(!sample.public||!sample.playing){client?.pause();notify('paused');return;}
      if(client&&pid===target&&client.status().state==='exited'){notify('worker-exited-no-retry');return;}
      if(client&&pid!==target){
        const old=client;client=null;pid=null;await old.stop();if(!current())return;observations=[sample];
      }
      if(!client){
        let owned;
        owned=createClient({onSegment:event=>receive(event,owned),onGap:event=>{if(!closed&&client===owned)onGap(event);}});
        client=owned;pid=target;owned.start(target);notify('starting');
      }else if(['paused','pausing'].includes(client.status().state))client.resume();
    }catch{client?.pause();notify('state-unavailable');}
    finally{busy=false;}
  }
  return Object.freeze({
    status,tick,
    registerSource(fn){
      if(typeof fn!=='function')throw Error('continuous-supervisor-source-invalid');
      const generation=++sourceGeneration;
      source=fn;observations=[];client?.pause();
      return ()=>{if(source===fn&&sourceGeneration===generation){sourceGeneration++;source=null;observations=[];client?.pause();}};
    },
    start(){if(closed)throw Error('continuous-supervisor-closed');if(timer)return;timer=setInterval(()=>void tick(),intervalMs);timer.unref?.();void tick();},
    async stop(){closed=true;sourceGeneration++;if(timer){clearInterval(timer);timer=null;}source=null;const old=client;client=null;if(old)await old.stop();notify('stopped');},
  });
}
