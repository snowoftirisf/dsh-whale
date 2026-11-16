import fs from 'node:fs';
export const nativeSongDurationBoundsVersion=1;
import path from 'node:path';
import os from 'node:os';
import {createHash,randomUUID} from 'node:crypto';
import {absoluteDataPath,dataPaths,relativeDataPath} from './data-paths.mjs';

const ID=/^[0-9a-f]{32}$/, HASH=/^[0-9a-f]{64}$/, CHAT=/^[0-9a-f]{16}$/;
const sha=value=>createHash('sha256').update(value).digest('hex');
const fail=code=>{throw Error(code)};
export const songChatKey=target=>sha(target).slice(0,16);
export function assertSongActive(signal) {if(signal?.aborted)fail('song-request-aborted')}

function checkedFile(file,root,maxBytes=128*1024*1024) {
  relativeDataPath(file,{dataRoot:root});
  const stat=fs.lstatSync(file);
  if(!stat.isFile()||stat.nlink!==1||stat.size<1||stat.size>maxBytes)fail('song-file-invalid');
  return fs.readFileSync(file);
}
function jsonFile(file,root) {
  return JSON.parse(checkedFile(file,root,65536).toString('utf8').replace(/^\uFEFF/,''));
}
function finite(value,min,max) {return typeof value==='number'&&Number.isFinite(value)&&value>=min&&value<=max}
export function wavInfo(bytes) {
  if(bytes.length<44||bytes.toString('ascii',0,4)!=='RIFF'||bytes.toString('ascii',8,12)!=='WAVE')fail('song-wave-invalid');
  let format=null,data=null;
  for(let at=12;at+8<=bytes.length;) {
    const name=bytes.toString('ascii',at,at+4),size=bytes.readUInt32LE(at+4),start=at+8,end=start+size;
    if(end>bytes.length)fail('song-wave-truncated');
    if(name==='fmt ') {
      if(size<16)fail('song-wave-invalid');
      format={encoding:bytes.readUInt16LE(start),channels:bytes.readUInt16LE(start+2),sampleRate:bytes.readUInt32LE(start+4),
        blockAlign:bytes.readUInt16LE(start+12),bits:bytes.readUInt16LE(start+14)};
    }
    if(name==='data'){if(data!==null)fail('song-wave-invalid');data=size}
    at=end+(size%2);
  }
  if(!format||data===null||![1,3].includes(format.encoding)||format.channels!==1||format.sampleRate!==44100
      ||![16,24,32].includes(format.bits)||format.blockAlign!==format.bits/8||data%format.blockAlign)fail('song-wave-invalid');
  return {...format,durationSeconds:data/format.blockAlign/format.sampleRate};
}

export function readSongGuide(dataRoot,chatKey,guideId) {
  if(!CHAT.test(chatKey)||!ID.test(guideId))fail('song-guide-id-invalid');
  const folder=absoluteDataPath(`voice/song-guides/${chatKey}/${guideId}`,{dataRoot});
  const metadata=jsonFile(path.join(folder,'metadata.json'),folder);
  if(metadata.schemaVersion!==1||metadata.kind!=='whale-song-guide'||metadata.guideId!==guideId
      ||metadata.jobId!==guideId||metadata.chatKey!==chatKey||metadata.guideFile!=='guide.wav'
      ||metadata.verified!==true||metadata.audioKind!=='isolated-vocal'||!HASH.test(metadata.guideSha256||'')
      ||!HASH.test(metadata.sourceAudioSha256||'')||!finite(metadata.sourceStartSeconds,0,86400)
      ||!finite(metadata.sourceDurationSeconds,1,55)||!finite(metadata.durationSeconds,1,55)
      ||metadata.sampleRate!==44100||metadata.channels!==1)fail('song-guide-metadata-invalid');
  let url;try{url=new URL(metadata.sourceUrl)}catch{fail('song-guide-source-invalid')}
  if(url.protocol!=='https:'||url.username||url.password||url.hash||url.port
      ||!['youtube.com','www.youtube.com','m.youtube.com','youtu.be','bilibili.com','www.bilibili.com','m.bilibili.com'].includes(url.hostname))fail('song-guide-source-invalid');
  const bytes=checkedFile(path.join(folder,'guide.wav'),folder);
  if(sha(bytes)!==metadata.guideSha256)fail('song-guide-hash-mismatch');
  const info=wavInfo(bytes);
  if(Math.abs(info.durationSeconds-metadata.durationSeconds)>.12)fail('song-guide-duration-mismatch');
  return {folder,metadata};
}

export async function selectedSongVoice(voiceSettings,dataRoot) {
  const config=await voiceSettings();
  if(!config||config.approved!==true)fail('song-voice-not-approved');
  if(typeof config.referenceAudio!=='string'||!config.referenceAudio)fail('song-reference-required');
  const file=config.referenceAudioBase==='dataRoot'?absoluteDataPath(config.referenceAudio,{dataRoot}):path.resolve(config.referenceAudio);
  const roots=[dataPaths({dataRoot}).voice,path.join(os.homedir(),'.dsh')];
  const root=roots.find(root=>{try{relativeDataPath(file,{dataRoot:root});return true}catch{return false}});
  if(!root)fail('song-reference-path-denied');
  const parentHash=sha(checkedFile(file,root));
  if(config.referenceSha256!==undefined&&config.referenceSha256!==parentHash)fail('song-reference-hash-mismatch');
  if(config.voiceId!==undefined&&typeof config.voiceId!=='string')fail('song-voice-id-invalid');
  // singing_runtime.py renders with voice/singing.local.json when it was derived
  // from this exact speaking reference; validate against the same actor here,
  // or every render fails song-render-source-mismatch.
  let actorFile=file,hash=parentHash,voiceId=config.voiceId??null;
  const voiceRoot=dataPaths({dataRoot}).voice,selectionFile=path.join(voiceRoot,'singing.local.json');
  if(fs.existsSync(selectionFile)){
    const chosen=jsonFile(selectionFile,voiceRoot);
    if(chosen?.approved===true&&chosen.parentReferenceSha256===parentHash&&chosen.referenceAudioBase==='dataRoot'
        &&typeof chosen.referenceAudio==='string'&&HASH.test(chosen.referenceSha256??'')){
      actorFile=absoluteDataPath(chosen.referenceAudio,{dataRoot});
      hash=sha(checkedFile(actorFile,voiceRoot));
      if(hash!==chosen.referenceSha256)fail('song-reference-hash-mismatch');
      voiceId=typeof chosen.voiceId==='string'?chosen.voiceId:voiceId;
    }
  }
  const identity=sha(JSON.stringify({reference:path.normalize(actorFile),sha256:hash,voiceId}));
  // Renaming/copying identical reference bytes must not create a second send.
  const signature=sha(JSON.stringify({referenceSha256:hash}));
  return {signature,identity,hash};
}

function validatedRender(rendered,guide,voice,dataRoot,chatKey,guideId) {
  if(!rendered||rendered.backend!=='seed-vc-44k-svc'||rendered.sampleRate!==44100
      ||!finite(rendered.durationSeconds,1,56)||!Array.isArray(rendered.segments)
      ||rendered.segments.length<1||rendered.segments.length>2||rendered.segmentCount!==rendered.segments.length)fail('song-render-invalid');
  const metadata=rendered.metadata;
  if(!metadata||metadata.guideId!==guideId||metadata.chatKey!==chatKey||metadata.actorReferenceSha256!==voice.hash
      ||metadata.guideSha256!==guide.metadata.guideSha256||metadata.sourceAudioSha256!==guide.metadata.sourceAudioSha256
      ||metadata.sourceUrl!==guide.metadata.sourceUrl||metadata.sourceStartSeconds!==guide.metadata.sourceStartSeconds
      ||metadata.sourceDurationSeconds!==guide.metadata.sourceDurationSeconds||metadata.audioKind!=='voice-converted-guide')fail('song-render-source-mismatch');
  if(Math.abs(rendered.durationSeconds-guide.metadata.durationSeconds)>.25)fail('song-render-duration-mismatch');
  const job=typeof rendered.file==='string'?path.basename(path.dirname(rendered.file)):'';
  if(!ID.test(job))fail('song-render-path-invalid');
  const folder=absoluteDataPath(`voice/outbox/singing/${job}`,{dataRoot});
  let duration=0;
  const segments=rendered.segments.map((segment,index)=>{
    const name=`segment-${String(index+1).padStart(3,'0')}.wav`,file=path.join(folder,name);
    if(segment.name!==name||typeof segment.file!=='string'||path.resolve(segment.file)!==file||!HASH.test(segment.sha256||'')
        ||!finite(segment.durationSeconds,.5,55)||!finite(segment.offsetSeconds,0,56)
        ||Math.abs(segment.offsetSeconds-duration)>.02)fail('song-segment-invalid');
    const bytes=checkedFile(file,folder);if(sha(bytes)!==segment.sha256)fail('song-segment-hash-mismatch');
    const info=wavInfo(bytes);
    if(info.durationSeconds<.5||info.durationSeconds>55||Math.abs(info.durationSeconds-segment.durationSeconds)>.02)fail('song-segment-duration-mismatch');
    duration+=info.durationSeconds;
    return {file,durationSeconds:info.durationSeconds,sha256:segment.sha256};
  });
  if(path.resolve(rendered.file)!==segments[0].file||Math.abs(duration-rendered.durationSeconds)>.02)fail('song-segments-total-mismatch');
  return segments;
}

function saveReceipt(file,record) {
  // Preserve the exclusive claim on failures; never unlink/recreate it to retry.
  relativeDataPath(file,{dataRoot:path.dirname(file)});
  const temp=file+'.'+randomUUID()+'.tmp',fd=fs.openSync(temp,'wx',0o600);
  try{fs.writeFileSync(fd,JSON.stringify(record));fs.fsyncSync(fd)}finally{fs.closeSync(fd)}
  fs.renameSync(temp,file);
}

export function createSongDelivery({dataRoot,voiceSettings,nativeVoiceReady,svcRequest,sendAudio}) {
  dataRoot=dataPaths({dataRoot}).root;
  return async function sing(route,guideId,{signal}={}) {
    assertSongActive(signal);
    if(!route||!['friend','group'].includes(route.kind)||typeof route.target!=='string'||!route.target)fail('song-route-denied');
    const target=route.target,chatKey=songChatKey(target),voice=await selectedSongVoice(voiceSettings,dataRoot);
    if(await nativeVoiceReady(route)!==true)fail('song-native-transport-unavailable');
    assertSongActive(signal);
    const guide=readSongGuide(dataRoot,chatKey,guideId);
    const file=absoluteDataPath(`voice/song-guides/${chatKey}/${guideId}/delivery-${voice.signature}.json`,{dataRoot});
    const base={format:'whale-song-delivery',version:1,guideId,chatKey,voiceSignature:voice.signature,
      referenceSha256:voice.hash,operationId:'song:'+guideId+':'+voice.signature,createdAt:new Date().toISOString()};
    let fd;
    try{fd=fs.openSync(file,'wx',0o600)}catch(error){
      if(error.code!=='EEXIST')throw error;
      const existing=jsonFile(file,guide.folder);
      if(existing.format!==base.format||existing.version!==1||existing.guideId!==guideId||existing.chatKey!==chatKey
          ||existing.voiceSignature!==voice.signature||existing.referenceSha256!==voice.hash
          ||!['claimed','sent','unknown','failed'].includes(existing.state))fail('song-delivery-record-invalid');
      return {status:existing.state==='sent'?'already-sent':'not-dispatched',guideId,
        previousState:existing.state,automaticRetry:false};
    }
    let record={...base,state:'claimed',phase:'rendering'},sending=false;
    try{
      try{fs.writeFileSync(fd,JSON.stringify(record));fs.fsyncSync(fd)}finally{fs.closeSync(fd)}
      assertSongActive(signal);
      const rendered=await svcRequest('synthesize',{guideId,chatKey}); // Outside UI send queue.
      assertSongActive(signal);
      let segments=validatedRender(rendered,guide,voice,dataRoot,chatKey,guideId);
      const currentGuide=readSongGuide(dataRoot,chatKey,guideId);
      if(JSON.stringify(currentGuide.metadata)!==JSON.stringify(guide.metadata))fail('song-guide-changed');
      if((await selectedSongVoice(voiceSettings,dataRoot)).identity!==voice.identity)fail('song-voice-changed');
      if(await nativeVoiceReady(route)!==true)fail('song-native-transport-unavailable');
      // Recheck every output immediately before the native transport callback.
      segments=validatedRender(rendered,guide,voice,dataRoot,chatKey,guideId);
      record={...record,phase:'sending',segmentCount:segments.length};saveReceipt(file,record);
      const beforeSend=async()=>{
        assertSongActive(signal);
        if((await selectedSongVoice(voiceSettings,dataRoot)).identity!==voice.identity)fail('song-voice-changed');
        if(await nativeVoiceReady(route)!==true)fail('song-native-transport-unavailable');
        assertSongActive(signal);
        if(JSON.stringify(readSongGuide(dataRoot,chatKey,guideId).metadata)!==JSON.stringify(guide.metadata))fail('song-guide-changed');
        validatedRender(rendered,guide,voice,dataRoot,chatKey,guideId);
      };
      await beforeSend();
      sending=true;
      // Native callback MUST call beforeSend again INSIDE its UI queue, just
      // before the first recording, to catch changes while waiting for that queue.
      const ack=await sendAudio({target,chatKey,guideId,segments,rendered,operationId:base.operationId,beforeSend});
      if(ack?.outcome!=='sent')fail('song-send-outcome-unknown');
      record={...record,state:'sent',phase:'complete',finishedAt:new Date().toISOString()};saveReceipt(file,record);
      return {status:'sent',guideId,segmentCount:segments.length,durationSeconds:rendered.durationSeconds,
        untrusted_song_data:true,sourceUrl:guide.metadata.sourceUrl,listeningAccepted:false,
        limitations:'按原始导唱换同一选定音色；未凭歌名或歌词创作旋律，未听验不保证纯人声。'};
    }catch(error){
      record={...record,state:sending?'unknown':'failed',phase:'complete',finishedAt:new Date().toISOString(),
        code:/^song-[a-z0-9-]+$/.test(error?.message)?error.message:'song-operation-failed'};
      try{saveReceipt(file,record)}catch{/* An uncertain durable claim still prevents retries. */}
      return {status:record.state,guideId,code:record.code,automaticRetry:false};
    }
  };
}
