// backend: 'breezeblue' (cloud, voice.local.json breezeVoiceId) | 'indextts2.5' | 'qwen3-tts' (default)
export function selectedSpeech(settings,legacy,emotional,cloud=null) {
  if(settings.backend==='breezeblue')return cloud;
  if(settings.backend==='indextts2.5')return emotional;
  if(settings.backend===undefined||settings.backend==='qwen3-tts')return legacy;
  return null;
}

export function speechCapabilities(settings,{legacy,emotional,emotionalReady,nativeVoice,cloud=null}) {
  const selected=selectedSpeech(settings,legacy,emotional,cloud);
  const cloudVoice=settings.backend==='breezeblue';
  const emotion=settings.backend==='indextts2.5'||cloudVoice;
  const voice=!!(settings.approved===true&&nativeVoice===true&&selected?.available()===true
    &&(settings.backend!=='indextts2.5'||emotionalReady===true));
  return {voice,emotion:voice&&emotion&&settings.emotionDirectionsEnabled!==false};
}
