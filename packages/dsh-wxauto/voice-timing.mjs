// Numeric timing only. Never export speech text, file paths or reference audio.
export async function measureVoicePreparation({runGpu,synthesize,queuedAt,taskStartedAt,
  now=()=>performance.now(),observe=()=>{}}) {
  const requestedAt=now();
  let enteredAt;
  const audio=await runGpu(async()=>{
    enteredAt=now();
    return synthesize();
  });
  const completedAt=now();
  const ms=n=>Math.round(Math.max(0,n)*1000)/1000;
  const timing={replyQueueMs:ms(taskStartedAt-queuedAt),gpuQueueMs:ms(enteredAt-requestedAt),
    speechRpcMs:ms(completedAt-enteredAt)};
  if(Number.isFinite(audio?.durationSeconds))timing.audioSeconds=audio.durationSeconds;
  if(audio?.timings && typeof audio.timings==='object') {
    timing.backend={};
    for(const [key,value] of Object.entries(audio.timings)) {
      if(['preflightSeconds','hashVerifySeconds','modelLoadSeconds','inferenceSeconds',
          'postprocessSeconds','totalSeconds'].includes(key)&&Number.isFinite(value)&&value>=0)
        timing.backend[key]=value;
      if(key==='modelReloaded'&&typeof value==='boolean')timing.backend[key]=value;
    }
  }
  // Timing persistence must never turn successful synthesis into a resend.
  try{observe(timing)}catch{}
  return audio;
}
