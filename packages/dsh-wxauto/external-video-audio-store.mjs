// External partial-clip evidence only. No UI/model/network/Vault work or legacy import.
// Capabilities protect this API boundary, not a plugin with general filesystem access.
import fs from 'node:fs';
import path from 'node:path';
import {createHash,randomUUID} from 'node:crypto';

const digest=s=>createHash('sha256').update(s).digest('hex');
const plain=x=>x!==null&&typeof x==='object'&&Object.getPrototypeOf(x)===Object.prototype;
const canonical=x=>JSON.stringify(x,(_k,v)=>plain(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
const fail=code=>{throw Error(code);};
const sha=x=>typeof x==='string'&&/^[a-f0-9]{64}$/.test(x);
const text=(x,max)=>typeof x==='string'&&x.length<=max&&!/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(x);
const id=x=>typeof x==='string'&&/^[A-Za-z0-9_-]{1,120}$/.test(x);
const ms=x=>Number.isSafeInteger(x)&&x>=0&&x<=8640000000000000;
const platform=x=>['douyin','bilibili'].includes(x);
const scopes=['owner-private','public-autonomous-video'];
const filePattern=/^\d{16}-[a-f0-9]{64}\.entry$/;
const exact=(x,required,optional=[])=>plain(x)&&required.every(k=>Object.hasOwn(x,k))&&Object.keys(x).every(k=>required.includes(k)||optional.includes(k));
const authority=x=>(typeof x==='object'&&x!==null)||typeof x==='symbol';
function processCaptureProof(value){
  if(!exact(value,['protocolVersion','processSpecificCapture','captureScopeVersion','callbackFrameNormalizationVersion'])
      ||value.protocolVersion!==2||value.processSpecificCapture!==true||value.captureScopeVersion!==1
      ||value.callbackFrameNormalizationVersion!==1)fail('video-audio-process-scope-unverified');
  return {...value};
}
const chars=x=>Array.from(x);
const safe=(code,callback)=>{try{return callback();}catch(error){
  if(typeof error?.message==='string'&&/^video-audio-[a-z-]{1,80}$/.test(error.message))throw error;
  fail(code);
}};

function checkParents(directory){
  for(let p=directory;;p=path.dirname(p)){
    try{if(fs.lstatSync(p).isSymbolicLink())fail('video-audio-linked-path');}catch(e){if(e.code!=='ENOENT')throw e;}
    if(path.dirname(p)===p)break;
  }
}
function verifiedRead(file){
  checkParents(path.dirname(file));
  const a=fs.lstatSync(file);
  if(!a.isFile()||a.isSymbolicLink()||a.nlink!==1||a.size>150000)fail('video-audio-entry-corrupt');
  let fd;
  const sig=s=>[s.dev,s.ino,s.size,s.mtimeMs,s.nlink].join(':');
  try{
    fd=fs.openSync(file,'r');
    if(sig(fs.fstatSync(fd))!==sig(a))fail('video-audio-entry-changed');
    const bytes=fs.readFileSync(fd);
    if(sig(fs.fstatSync(fd))!==sig(a)||sig(fs.lstatSync(file))!==sig(a))fail('video-audio-entry-changed');
    return bytes;
  }finally{if(fd!==undefined)fs.closeSync(fd);}
}
function captureValue(input,now){
  if(!exact(input,['platform','videoId','before','after','durationSeconds','asr'],['samplePosition','audioSha256','captureProof'])
      ||!platform(input.platform)||!id(input.videoId)||!Number.isFinite(input.durationSeconds)
      ||input.durationSeconds<=0||input.durationSeconds>30)fail('video-audio-capture-invalid');
  for(const edge of [input.before,input.after]){
    if(!exact(edge,['videoId','playing','observedAtMs'])||edge.videoId!==input.videoId||edge.playing!==true
        ||!ms(edge.observedAtMs))fail('video-audio-playing-proof-required');
  }
  if(input.after.observedAtMs<input.before.observedAtMs||input.after.observedAtMs>now+30000)
    fail('video-audio-capture-time-invalid');
  const asr=input.asr;
  if(!exact(asr,['status','text','backend'])||!['speech','no-speech','no-transcript'].includes(asr.status)
      ||!text(asr.text,32000)||chars(asr.text).length>16000
      ||typeof asr.backend!=='string'||!/^[A-Za-z0-9_.:-]{1,100}$/.test(asr.backend)
      ||(asr.status==='speech'?!asr.text.trim():!!asr.text.trim()))fail('video-audio-asr-invalid');
  let position={status:'unknown'};
  if(input.samplePosition!==undefined&&input.samplePosition!==null){
    const p=input.samplePosition;
    if(!exact(p,['startSeconds','endSeconds'])||![p.startSeconds,p.endSeconds].every(x=>Number.isFinite(x)&&x>=0)
        ||p.endSeconds<=p.startSeconds||p.endSeconds>864000)fail('video-audio-position-invalid');
    position={status:'observed',startSeconds:p.startSeconds,endSeconds:p.endSeconds};
  }
  if(input.audioSha256!==undefined&&!sha(input.audioSha256))fail('video-audio-audio-hash-invalid');
  const captureProof=input.captureProof===undefined?null:processCaptureProof(input.captureProof);
  return {platform:input.platform,videoId:input.videoId,...(captureProof?{captureProof}:{}),
    before:{...input.before},after:{...input.after},durationSeconds:input.durationSeconds,samplePosition:position,
    asr:{status:asr.status,text:asr.text.trim(),backend:asr.backend},
    audioSha256:input.audioSha256??null,
    audioHashStatus:input.audioSha256!==undefined?'producer-supplied-unverified':'unavailable'};
}
function filtersValue(args){
  const filters={};
  if(args.platform!==undefined){if(!platform(args.platform))fail('video-audio-filter-invalid');filters.platform=args.platform;}
  if(args.videoId!==undefined){if(!id(args.videoId))fail('video-audio-filter-invalid');filters.videoId=args.videoId;}
  for(const key of ['dateFrom','dateTo'])if(args[key]!==undefined){
    if(typeof args[key]!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(args[key])
        ||!Number.isFinite(Date.parse(args[key]+'T00:00:00Z'))
        ||new Date(Date.parse(args[key]+'T00:00:00Z')).toISOString().slice(0,10)!==args[key])fail('video-audio-filter-invalid');
    filters[key]=args[key];
  }
  if(filters.dateFrom&&filters.dateTo&&filters.dateFrom>filters.dateTo)fail('video-audio-filter-invalid');
  if(args.query!==undefined){if(!text(args.query,200)||!args.query.trim())fail('video-audio-filter-invalid');filters.query=args.query.trim().toLowerCase();}
  return filters;
}
function continuousSegmentValue(s,now){
  const fields=['sessionId','pid','event','sequence','startedAt','endedAt','callbackObservedAt','timestampBasis',
    'durationSeconds','frames','sampleRate','channels','encoding','wavSha256','audioRetained','asrStatus','transcript','backend','videoIdentity','playingProof'];
  if(!exact(s,fields)||s.event!=='segment'||!Number.isSafeInteger(s.pid)||s.pid<=0
      ||typeof s.sessionId!=='string'||!/^[a-f0-9]{32}$/.test(s.sessionId)
      ||!Number.isSafeInteger(s.sequence)||s.sequence<0||!Number.isSafeInteger(s.frames)||s.frames<=0||s.frames>480000||s.sampleRate!==48000||s.channels!==2
      ||s.encoding!=='pcm-s16le'||typeof s.durationSeconds!=='number'||!Number.isFinite(s.durationSeconds)
      ||Math.abs(s.durationSeconds-s.frames/48000)>.000001||s.audioRetained!==false||!sha(s.wavSha256)
      ||s.timestampBasis!=='callback-arrival-minus-audio-frames'||s.videoIdentity!=='unknown'||s.playingProof!==false
      ||![s.startedAt,s.endedAt,s.callbackObservedAt].every(x=>typeof x==='number'&&Number.isFinite(x)&&x>=0&&ms(Math.round(x*1000)))
      ||Math.abs(s.endedAt-s.startedAt-s.durationSeconds)>0.000001||s.callbackObservedAt+0.000001<s.endedAt
      ||s.callbackObservedAt*1000>now+30000)fail('video-audio-continuous-segment-invalid');
  if(!['speech','no-speech','no-transcript'].includes(s.asrStatus)||!text(s.transcript,32000)
      ||chars(s.transcript).length>16000||s.backend!=='sensevoice-int8'
      ||(s.asrStatus==='speech'?!s.transcript.trim():!!s.transcript.trim()))fail('video-audio-asr-invalid');
  return {platform:'douyin',segment:structuredClone(s),estimatedEndedAtMs:Math.round(s.endedAt*1000),
    timeBasis:s.timestampBasis,playingProof:false,durationSeconds:s.frames/s.sampleRate,samplePosition:{status:'unknown'},
    asr:{status:s.asrStatus,text:s.transcript.trim(),backend:s.backend},audioSha256:s.wavSha256,
    audioHashStatus:'producer-supplied-unverified'};
}
function continuousValue(input,now){
  if(!exact(input,['segment','observations'],['captureProof']))fail('video-audio-continuous-invalid');
  const s=input.segment,observations=input.observations,base=continuousSegmentValue(s,now);
  if(!Array.isArray(observations)||observations.length<2||observations.length>40)fail('video-audio-observations-invalid');
  for(let n=0;n<observations.length;n++){
    const o=observations[n];
    if(!exact(o,['videoId','playing','observedAtMs'])||typeof o.videoId!=='string'||!/^\d{10,30}$/.test(o.videoId)
        ||typeof o.playing!=='boolean'||!ms(o.observedAtMs)||o.observedAtMs>now+30000
        ||n&&o.observedAtMs<=observations[n-1].observedAtMs)fail('video-audio-observations-invalid');
  }
  if(observations[0].observedAtMs>s.startedAt*1000||observations.at(-1).observedAtMs<s.endedAt*1000)
    fail('video-audio-observations-coverage-required');
  const stable=observations.every(o=>o.videoId===observations[0].videoId);
  const captureProof=input.captureProof===undefined?null:processCaptureProof(input.captureProof);
  return {...base,videoId:stable?observations[0].videoId:null,...(captureProof?{captureProof}:{}),
    videoIdentityStatus:stable?'state-associated':'unattributed-or-mixed',observations:structuredClone(observations)};
}
function unboundContinuousValue(input,now){
  if(!exact(input,['segment','captureProof','reason'])
      ||!['context-unproven','context-unavailable','source-unavailable'].includes(input.reason))fail('video-audio-unbound-invalid');
  return {...continuousSegmentValue(input.segment,now),captureProof:processCaptureProof(input.captureProof),
    reason:input.reason,videoId:null,videoIdentityStatus:'unattributed'};
}
function makeCursor(access,filters,lastScanned){
  const body={version:1,access,filtersSha256:digest(canonical(filters)),lastScanned};
  return Buffer.from(JSON.stringify({...body,checksum:digest(canonical(body))})).toString('base64url');
}
function readCursor(cursor,access,filters){
  if(cursor===undefined||cursor===null)return null;
  try{
    if(typeof cursor!=='string'||cursor.length>4096||!/^[A-Za-z0-9_-]+$/.test(cursor))throw Error();
    const parsed=JSON.parse(Buffer.from(cursor,'base64url').toString('utf8'));
    if(!exact(parsed,['version','access','filtersSha256','lastScanned','checksum']))throw Error();
    const {checksum,...body}=parsed;
    if(body.version!==1||body.access!==access||body.filtersSha256!==digest(canonical(filters))
        ||checksum!==digest(canonical(body))||typeof body.lastScanned!=='string'
        ||!/^\d{16}-[a-f0-9]{64}\.entry:(owner-private|public-autonomous-video)$/.test(body.lastScanned))throw Error();
    return body.lastScanned;
  }catch{fail('video-audio-cursor-invalid');}
}

export function createExternalVideoAudioStore({dataRoot,resolveAccess,privateProducer,trustedProducer,
  now=Date.now,scanBudget=200}={}){
  // v1 record requires before/after live-playback observations. Public-only v2
  // continuous process audio explicitly has unknown video identity/no playback
  // proof; bounded DOM snapshots provide at most a state association.
  // writerFor(privateProducer) is private; only the separate autonomous capability
  // can produce public entries. Do not expose either writer to model tools.
  // Cursors have a corruption checksum, not an authorization MAC: current access
  // is independently derived on every query/getEntry, never from the cursor.
  // Inert constructor: no filesystem calls, capability invocation or directory creation.
  if(typeof dataRoot!=='string'||!path.isAbsolute(dataRoot)||dataRoot.includes('\0')
      ||dataRoot.startsWith('\\\\')||dataRoot.startsWith('//')||dataRoot.split(/[\\/]/).includes('..')
      ||typeof resolveAccess!=='function'||typeof now!=='function'||!Number.isInteger(scanBudget)||scanBudget<1||scanBudget>1000
      ||(privateProducer!==undefined&&!authority(privateProducer))||(trustedProducer!==undefined&&!authority(trustedProducer))
      ||(privateProducer!==undefined&&privateProducer===trustedProducer))fail('video-audio-config-invalid');
  const root=path.resolve(dataRoot,'voice','video-audio-evidence');
  const accessFor=sessionId=>{
    if(typeof sessionId!=='string'||!sessionId)fail('video-audio-session-denied');
    const value=resolveAccess(sessionId),kind=typeof value==='string'?value:value?.kind;
    if(!['owner','group'].includes(kind))fail('video-audio-session-denied');
    return kind==='owner'?'owner':'public';
  };
  const allowed=access=>access==='owner'?scopes:[scopes[1]];
  function list(access){
    checkParents(root);const result=[];
    for(const scope of allowed(access)){
      const folder=path.join(root,scope);checkParents(folder);
      let entries;try{entries=fs.readdirSync(folder,{withFileTypes:true});}catch(e){if(e.code==='ENOENT')continue;throw e;}
      for(const item of entries)if(filePattern.test(item.name))result.push({scope,name:item.name,
        file:path.join(folder,item.name,'entry.json'),key:item.name+':'+scope});
    }
    return result.sort((a,b)=>a.key.localeCompare(b.key));
  }
  function load(item){
    const value=JSON.parse(verifiedRead(item.file).toString('utf8'));
    if(value.version===3){
      if(!exact(value,['version','id','scope','materialKind','coverage','captureOrigin','capture'])
          ||!sha(value.id)||value.scope!==scopes[0]||item.scope!==scopes[0]||value.materialKind!=='external-material'
          ||value.coverage!=='partial-clip'||value.captureOrigin!=='owner-private-unbound-process-audio')fail('video-audio-entry-corrupt');
      const c=value.capture;
      if(!plain(c))fail('video-audio-entry-corrupt');
      const checked=unboundContinuousValue({segment:c.segment,captureProof:c.captureProof,reason:c.reason},8640000000000000);
      const {id:entryId,...body}=value;
      if(canonical(c)!==canonical(checked)||digest(canonical(body))!==entryId
          ||item.name!==String(c.estimatedEndedAtMs).padStart(16,'0')+'-'+entryId+'.entry')fail('video-audio-entry-corrupt');
      return value;
    }
    if(value.version===2){
      if(!exact(value,['version','id','scope','materialKind','coverage','captureOrigin','capture'])
          ||!sha(value.id)||value.scope!==scopes[1]||item.scope!==scopes[1]||value.materialKind!=='external-material'
          ||value.coverage!=='partial-clip'||value.captureOrigin!=='autonomous-continuous-process-audio')fail('video-audio-entry-corrupt');
      const c=value.capture;
      if(!plain(c))fail('video-audio-entry-corrupt');
      const checked=continuousValue({segment:c.segment,observations:c.observations,
        ...(Object.hasOwn(c,'captureProof')?{captureProof:c.captureProof}:{})},8640000000000000);
      const {id:entryId,...body}=value;
      if(canonical(c)!==canonical(checked)||digest(canonical(body))!==entryId
          ||item.name!==String(c.estimatedEndedAtMs).padStart(16,'0')+'-'+entryId+'.entry')fail('video-audio-entry-corrupt');
      return value;
    }
    if(!exact(value,['version','id','scope','materialKind','coverage','captureOrigin','capture'])||value.version!==1
        ||!sha(value.id)||value.scope!==item.scope||value.materialKind!=='external-material'||value.coverage!=='partial-clip'
        ||value.captureOrigin!==(item.scope===scopes[1]?'autonomous-video-watch':'owner-private-video-audio'))fail('video-audio-entry-corrupt');
    const c=value.capture;
    if(!plain(c)||!exact(c,['platform','videoId','before','after','durationSeconds','samplePosition','asr','audioSha256','audioHashStatus'],['captureProof'])
        ||!plain(c.samplePosition)||!['unknown','observed'].includes(c.samplePosition.status)
        ||c.audioHashStatus!==(c.audioSha256!==null?'producer-supplied-unverified':'unavailable'))fail('video-audio-entry-corrupt');
    const position=c.samplePosition.status==='unknown'?null:{startSeconds:c.samplePosition.startSeconds,endSeconds:c.samplePosition.endSeconds};
    if(!exact(c.samplePosition,c.samplePosition.status==='unknown'?['status']:['status','startSeconds','endSeconds']))fail('video-audio-entry-corrupt');
    const checked=captureValue({platform:c.platform,videoId:c.videoId,before:c.before,after:c.after,durationSeconds:c.durationSeconds,
      samplePosition:position,asr:c.asr,...(c.audioSha256!==null?{audioSha256:c.audioSha256}:{}),
      ...(Object.hasOwn(c,'captureProof')?{captureProof:c.captureProof}:{})},8640000000000000);
    if(canonical(checked)!==canonical(c))fail('video-audio-entry-corrupt');
    const {id:entryId,...body}=value;
    if(digest(canonical(body))!==entryId||item.name!==String(c.after.observedAtMs).padStart(16,'0')+'-'+entryId+'.entry')fail('video-audio-entry-corrupt');
    return value;
  }
  function record(scope,input){
    const capture=captureValue(input,now());
    const body={version:1,scope,materialKind:'external-material',coverage:'partial-clip',
      captureOrigin:scope===scopes[1]?'autonomous-video-watch':'owner-private-video-audio',capture};
    return publish(scope,body,capture.after.observedAtMs);
  }
  function recordContinuous(scope,input){
    if(scope!==scopes[1])fail('video-audio-continuous-public-required');
    const capture=continuousValue(input,now());
    const saved=publish(scope,{version:2,scope,materialKind:'external-material',coverage:'partial-clip',
      captureOrigin:'autonomous-continuous-process-audio',capture},capture.estimatedEndedAtMs);
    return {...saved,videoId:capture.videoId,videoIdentityStatus:capture.videoIdentityStatus};
  }
  function recordUnboundContinuous(scope,input){
    if(scope!==scopes[0])fail('video-audio-unbound-private-required');
    const capture=unboundContinuousValue(input,now());
    const saved=publish(scope,{version:3,scope,materialKind:'external-material',coverage:'partial-clip',
      captureOrigin:'owner-private-unbound-process-audio',capture},capture.estimatedEndedAtMs);
    return {...saved,videoId:null,videoIdentityStatus:'unattributed'};
  }
  function publish(scope,body,endedAtMs){
    const entryId=digest(canonical(body)),name=String(endedAtMs).padStart(16,'0')+'-'+entryId+'.entry';
    const folder=path.join(root,scope),destination=path.join(folder,name),file=path.join(destination,'entry.json'),item={scope,file,name};
    checkParents(folder);fs.mkdirSync(folder,{recursive:true});checkParents(folder);
    if(fs.existsSync(destination)){load(item);return {entryId,scope,duplicate:true};}
    const temporary=path.join(folder,'.pending-'+randomUUID()+'.tmp');let fd,duplicate=false;
    try{
      fs.mkdirSync(temporary);
      fd=fs.openSync(path.join(temporary,'entry.json'),'wx',0o600);fs.writeFileSync(fd,JSON.stringify({...body,id:entryId})+'\n');fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;
      // Atomic directory publication. A valid destination is nonempty and
      // cannot be replaced by directory rename on either supported platform.
      try{fs.renameSync(temporary,destination);}catch(e){if(!fs.existsSync(destination))throw e;load(item);duplicate=true;}
    }finally{if(fd!==undefined)fs.closeSync(fd);fs.rmSync(temporary,{force:true,recursive:true});}
    load(item);return {entryId,scope,duplicate};
  }
  function publicEntry(entry,maxChars=400,offset=0){
    const c=entry.capture,all=chars(c.asr.text),shown=all.slice(offset,offset+maxChars).join('');
    return {entryId:entry.id,scope:entry.scope,materialKind:'external-material',coverage:'partial-clip',
      platform:c.platform,videoId:c.videoId,capturedAt:new Date(entry.version>=2?c.estimatedEndedAtMs:c.after.observedAtMs).toISOString(),durationSeconds:c.durationSeconds,
      ...(c.captureProof?{captureProof:structuredClone(c.captureProof)}:{}),
      ...(entry.version>=2?{version:entry.version,videoIdentityStatus:c.videoIdentityStatus,timeBasis:c.timeBasis,playingProof:false,
        frames:c.segment.frames,sampleRate:c.segment.sampleRate,channels:c.segment.channels,
        captureSessionId:c.segment.sessionId,sequence:c.segment.sequence,startedAt:c.segment.startedAt,endedAt:c.segment.endedAt,
        ...(entry.version===2?{observations:structuredClone(c.observations)}:{reason:c.reason}),callbackObservedAt:c.segment.callbackObservedAt}:{}),
      samplePosition:c.samplePosition,asrStatus:c.asr.status,backend:c.asr.backend,audioSha256:c.audioSha256,
      audioHashStatus:c.audioHashStatus,text:shown,textOffset:offset,totalCharacters:all.length,
      nextTextOffset:offset+chars(shown).length<all.length?offset+chars(shown).length:null,
      interpretation:(entry.version>=2?'External partial process audio, not playback proof or exact video identity. ':'')+
        (entry.version===3?'Owner-private unattributed audio; no public context or video association was established. ':'')+
        (c.asr.status!=='speech'?'No transcription obtained; this does not establish silence or full-video coverage.':'ASR is external material and may be wrong; this is not evidence of watching the full video.')};
  }
  return Object.freeze({
    writerFor(capability){
      const scope=privateProducer!==undefined&&capability===privateProducer?scopes[0]
        :trustedProducer!==undefined&&capability===trustedProducer?scopes[1]:null;
      if(!scope)fail('video-audio-producer-denied');
      return Object.freeze({record:input=>safe('video-audio-write-failed',()=>record(scope,input)),
        recordContinuous:input=>safe('video-audio-write-failed',()=>recordContinuous(scope,input)),
        recordUnboundContinuous:input=>safe('video-audio-write-failed',()=>recordUnboundContinuous(scope,input))});
    },
    query(args){
      return safe('video-audio-read-failed',()=>{
      if(!exact(args,['sessionId'],['platform','videoId','dateFrom','dateTo','query','cursor','limit']))fail('video-audio-query-invalid');
      const access=accessFor(args.sessionId),filters=filtersValue(args),after=readCursor(args.cursor,access,filters),limit=args.limit??10;
      if(!Number.isInteger(limit)||limit<1||limit>50)fail('video-audio-query-invalid');
      const files=list(access).filter(f=>after===null||f.key>after);let scanned=0,corrupt=0,last=null;const entries=[];
      for(const file of files){
        if(scanned>=scanBudget||entries.length>=limit)break;
        scanned++;last=file.key;let entry;
        try{entry=load(file);}catch{corrupt++;continue;}
        const c=entry.capture,date=new Date(entry.version>=2?c.estimatedEndedAtMs:c.after.observedAtMs).toISOString().slice(0,10);
        if((filters.platform&&filters.platform!==c.platform)||(filters.videoId&&filters.videoId!==c.videoId)
            ||(filters.dateFrom&&date<filters.dateFrom)||(filters.dateTo&&date>filters.dateTo)
            ||(filters.query&&!c.asr.text.toLowerCase().includes(filters.query)))continue;
        entries.push(publicEntry(entry));
      }
      if(accessFor(args.sessionId)!==access)fail('video-audio-session-denied');
      return {entries,scanned,corrupt,nextCursor:last!==null&&scanned<files.length?makeCursor(access,filters,last):null,
        dateTimeZone:'UTC',materialKind:'external-material',coverage:'partial-clip'};
      });
    },
    getEntry(args){
      return safe('video-audio-read-failed',()=>{
      if(!exact(args,['sessionId','entryId'],['offset','maxChars'])||!sha(args.entryId))fail('video-audio-entry-request-invalid');
      const access=accessFor(args.sessionId),offset=args.offset??0,maxChars=args.maxChars??2000;
      if(!Number.isSafeInteger(offset)||offset<0||!Number.isInteger(maxChars)||maxChars<1||maxChars>6000)fail('video-audio-entry-request-invalid');
      const matches=list(access).filter(f=>f.name.endsWith('-'+args.entryId+'.entry'));
      if(matches.length!==1)fail('video-audio-entry-unavailable');
      let value;try{value=load(matches[0]);}catch{fail('video-audio-entry-corrupt');}
      if(accessFor(args.sessionId)!==access)fail('video-audio-session-denied');
      const length=chars(value.capture.asr.text).length;
      if(offset>length)fail('video-audio-offset-invalid');
      return publicEntry(value,maxChars,offset);
      });
    },
  });
}
