// Startup-only, in the same GPU queue as all real synthesis. No message sending.
export function createVoiceWarmup({runGpu,request,isClosed=()=>false,observe=()=>{}}) {
  let attempted=false;
  const emit=value=>{try{observe(value)}catch{}};
  return {
    consider(status) {
      if(attempted||isClosed()||status?.ready!==true)return null;
      attempted=true;
      emit({state:'queued'});
      return Promise.resolve().then(()=>runGpu(async()=>{
        if(isClosed())return {state:'cancelled'};
        emit({state:'loading'});
        const loaded=await request('warmup');
        if(loaded?.ttsLoaded!==true)throw Error('voice-warmup-not-confirmed');
        return {state:'ready'};
      })).catch(()=>({state:'failed',automaticRetry:false})).then(result=>{emit(result);return result});
    },
  };
}
