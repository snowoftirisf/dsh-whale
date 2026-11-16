// Optional background adapter. Existing synchronous song tools remain unchanged.
import fs from 'node:fs';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {absoluteDataPath,dataPaths,relativeDataPath} from './data-paths.mjs';
import {assertSongActive,selectedSongVoice,songChatKey} from './song-delivery.mjs';
import {songToolDefinitions,validateSongSourceArgs} from './song-tools.mjs';
import {findRepertoireSong,recordSongJob,songTitle} from './song-repertoire.mjs';

const ID=/^[0-9a-f]{32}$/,CHAT=/^[0-9a-f]{16}$/;
const ACTIVE=new Set(['queued','running']);
const STATES=new Set(['queued','running','sent','failed','unknown','interrupted','not-dispatched','already-sent']);
const fail=code=>{throw Error(code)};
const codeOf=error=>/^song-[a-z0-9-]+$/.test(error?.message)?error.message:'song-job-operation-failed';

function readJob(file,root) {
  relativeDataPath(file,{dataRoot:root});
  const stat=fs.lstatSync(file);
  if(!stat.isFile()||stat.nlink!==1||stat.size<1||stat.size>65536)fail('song-job-record-invalid');
  const job=JSON.parse(fs.readFileSync(file,'utf8'));
  if(job?.format!=='whale-song-job'||job.version!==1||!ID.test(job.jobId||'')||!CHAT.test(job.chatKey||'')
      ||typeof job.target!=='string'||!job.target||songChatKey(job.target)!==job.chatKey
      ||!['friend','group'].includes(job.kind)||typeof job.sessionId!=='string'||!job.sessionId
      ||!STATES.has(job.status))fail('song-job-record-invalid');
  if(path.basename(file)!==job.jobId+'.json'||path.basename(path.dirname(file))!==job.chatKey)fail('song-job-record-scope-invalid');
  return job;
}
function writeJob(file,root,job,{exclusive=false}={}) {
  relativeDataPath(file,{dataRoot:root});
  const destination=exclusive?file:file+'.'+randomUUID()+'.tmp';
  let fd;
  try {
    fd=fs.openSync(destination,'wx',0o600);
    fs.writeFileSync(fd,JSON.stringify(job));fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;
    if(!exclusive)fs.renameSync(destination,file);
  } finally {
    if(fd!==undefined)fs.closeSync(fd);
    if(!exclusive&&fs.existsSync(destination))fs.unlinkSync(destination);
  }
}
function summary(job) {
  return {jobId:job.jobId,status:job.status,phase:job.phase,sessionId:job.sessionId,
    target:job.target,kind:job.kind,chatKey:job.chatKey,createdAt:job.createdAt,
    ...(job.finishedAt?{finishedAt:job.finishedAt}:{}),...(job.guideId?{guideId:job.guideId}:{}),
    ...(job.code?{code:job.code}:{}),...(job.reportStatus?{reportStatus:job.reportStatus}:{}),
    ...(job.deliveryStatus?{deliveryStatus:job.deliveryStatus}:{}),
    untrusted_song_data:true,sourceUrl:job.parameters.sourceUrl,listeningAccepted:false,
    limitations:'仅按原导唱转换已选音色；未听验不保证纯人声。中断或结果未知不自动重跑或重发。'};
}

// Scoped read-only continuity data. This never constructs a controller, changes
// recovery states, invokes a model or retries a song/notification.
export function readRecentSongReceipts(dataRoot,route,limit=3) {
  if(!route||!['friend','group'].includes(route.kind)||typeof route.target!=='string'||!route.target)fail('song-job-session-denied');
  if(!Number.isInteger(limit)||limit<1||limit>5)fail('song-job-history-limit-invalid');
  const root=dataPaths({dataRoot}).root,chatKey=songChatKey(route.target);
  const folder=absoluteDataPath(`voice/song-jobs/${chatKey}`,{dataRoot:root});
  const result={source:'persisted-song-job-ledger',untrusted_song_data:true,items:[],unavailableRecords:0,automaticRetry:false};
  if(!fs.existsSync(folder))return result;
  if(!fs.lstatSync(folder).isDirectory())fail('song-job-history-directory-invalid');
  const files=fs.readdirSync(folder).filter(name=>/^[0-9a-f]{32}\.json$/.test(name));
  if(files.length>500)fail('song-job-history-scan-limit');
  const jobs=[];
  for(const name of files)try{
    const job=readJob(path.join(folder,name),root);validateSongSourceArgs(job.parameters);
    if(job.target!==route.target||job.kind!==route.kind||!Number.isFinite(Date.parse(job.createdAt)))fail('song-job-record-scope-invalid');
    jobs.push(job);
  }catch{result.unavailableRecords++;}
  result.items=jobs.sort((a,b)=>Date.parse(b.createdAt)-Date.parse(a.createdAt)||b.jobId.localeCompare(a.jobId)).slice(0,limit).map(summary);
  return result;
}

export function createSongJobs(options) {
  const {routeForSession,voiceSettings,nativeVoiceReady,onResult,signal}=options;
  const root=dataPaths({dataRoot:options.dataRoot}).root;
  const directory=absoluteDataPath('voice/song-jobs',{dataRoot:root});
  const active=new Map();let closed=false,onAbort=null,recoveryIssues=0;
  const context=exec=>{
    const sessionId=exec?.agent?.session?.id,route=routeForSession(sessionId);
    if(typeof sessionId!=='string'||!sessionId||!route||!['friend','group'].includes(route.kind)
        ||typeof route.target!=='string'||!route.target)fail('song-job-session-denied');
    return {sessionId,target:route.target,kind:route.kind,chatKey:songChatKey(route.target)};
  };
  const filename=(chatKey,jobId)=>absoluteDataPath(`voice/song-jobs/${chatKey}/${jobId}.json`,{dataRoot:root});
  const requireLive=()=>{if(closed)fail('song-job-closed');assertSongActive(signal)};

  // A new controller may inspect prior receipts, never replay external work.
  if(fs.existsSync(directory)) {
    for(const entry of fs.readdirSync(directory,{withFileTypes:true})) {
      if(!CHAT.test(entry.name))continue;
      const folder=absoluteDataPath(`voice/song-jobs/${entry.name}`,{dataRoot:root});
      if(!fs.lstatSync(folder).isDirectory())continue;
      for(const name of fs.readdirSync(folder)) {
        if(!/^[0-9a-f]{32}\.json$/.test(name))continue;
        try {
          const file=filename(entry.name,name.slice(0,32)),job=readJob(file,root);
          validateSongSourceArgs(job.parameters);
          if(ACTIVE.has(job.status)) {
            writeJob(file,root,{...job,status:'interrupted',phase:'complete',code:'song-job-restarted',
              finishedAt:new Date().toISOString(),automaticRetry:false});
          }
        }catch {
          // A partial/corrupt crash record is not permission to replay it and
          // must not take down the existing WeChat adapter on plugin startup.
          recoveryIssues++;
        }
        // Existing reportStatus=claimed/unknown is deliberately never replayed.
      }
    }
  }

  async function report(job,file) {
    if(closed||signal?.aborted||typeof onResult!=='function')return;
    const stored=readJob(file,root);
    if(stored.reportStatus)return;
    job={...stored,reportStatus:'claimed',reportClaimedAt:new Date().toISOString()};writeJob(file,root,job);
    try {
      await onResult(summary(job));
      job={...job,reportStatus:'delivered'};writeJob(file,root,job);
    }catch {
      // The callback may have stored the result already. Do not call it twice.
      try{writeJob(file,root,{...job,reportStatus:'unknown'})}catch{}
    }
  }

  async function run(job,file,controller) {
    const jobSignal=signal?AbortSignal.any([signal,controller.signal]):controller.signal;
    const alive=()=>{requireLive();assertSongActive(jobSignal)};
    try {
      alive();job={...job,status:'running',phase:'preparing'};writeJob(file,root,job);
      const guardedVoice=async()=>{
        alive();const settings=await voiceSettings();
        const selected=await selectedSongVoice(()=>settings,root);
        if(selected.identity!==job.voiceIdentity)fail('song-job-voice-changed');
        alive();return settings;
      };
      const tools=songToolDefinitions({...options,dataRoot:root,signal:jobSignal,voiceSettings:guardedVoice,
        // Captured trusted context only; never create a synthetic user message.
        routeForSession:id=>id===job.sessionId?{target:job.target,kind:job.kind}:null});
      const exec={agent:{session:{id:job.sessionId}},signal:jobSignal};
      await guardedVoice();
      const prepared=await tools[0].execute(job.parameters,exec);alive();
      job={...job,guideId:prepared.guideId,phase:'singing'};writeJob(file,root,job);
      await guardedVoice();
      const delivered=await tools[1].execute({guideId:job.guideId},exec);
      // A native action already acknowledged sent cannot be unsent on shutdown.
      if(delivered.status!=='sent'&&delivered.status!=='already-sent')alive();
      job={...job,status:delivered.status,deliveryStatus:delivered.status,phase:'complete',
        ...(delivered.code?{code:delivered.code}:{}),finishedAt:new Date().toISOString(),automaticRetry:false};
      writeJob(file,root,job);
    }catch(error) {
      job={...job,status:closed||jobSignal.aborted?'interrupted':'failed',phase:'complete',
        code:codeOf(error),finishedAt:new Date().toISOString(),automaticRetry:false};
      try{writeJob(file,root,job)}catch{}
    }
    try{recordSongJob(root,job)}catch{}
    try{await report(job,file)}finally{active.delete(job.jobId)}
  }

  const service={
    async submit(args,exec) {
      requireLive();assertSongActive(exec?.signal);
      if(!args||typeof args!=='object'||Array.isArray(args))fail('song-tool-arguments-invalid');
      // title names the song in the shared repertoire; a title alone re-sings
      // the remembered source and slice.
      const {title:rawTitle,...source}=args,title=songTitle(rawTitle);
      if(title&&source.sourceUrl===undefined){
        const known=findRepertoireSong(root,title);
        if(!known)fail('song-not-in-repertoire');
        Object.assign(source,{sourceUrl:known.sourceUrl,
          startSeconds:source.startSeconds??known.startSeconds,durationSeconds:source.durationSeconds??known.durationSeconds});
      }
      const parameters=validateSongSourceArgs(source),captured=context(exec);
      const selected=await selectedSongVoice(voiceSettings,root);
      if(await nativeVoiceReady(captured)!==true)fail('song-native-transport-unavailable');
      requireLive();assertSongActive(exec?.signal);
      const jobId=randomUUID().replaceAll('-',''),file=filename(captured.chatKey,jobId);
      fs.mkdirSync(path.dirname(file),{recursive:true});
      const job={format:'whale-song-job',version:1,jobId,...captured,parameters,...(title?{title}:{}),
        voiceIdentity:selected.identity,voiceSignature:selected.signature,
        status:'queued',phase:'queued',createdAt:new Date().toISOString(),automaticRetry:false};
      writeJob(file,root,job,{exclusive:true});
      const controller=new AbortController();let finish;
      const completion=new Promise(resolve=>{finish=resolve});
      active.set(jobId,{controller,file,completion});
      // Return before CPU preparation starts. Background lifetime is independent
      // of the submit tool's signal after this durable acceptance point.
      setImmediate(()=>{void run(job,file,controller).catch(()=>{}).finally(finish)});
      return {status:'started',jobId};
    },
    status(jobId,exec) {
      if(typeof jobId!=='string'||!ID.test(jobId))fail('song-job-id-invalid');
      const captured=context(exec),file=filename(captured.chatKey,jobId);
      if(!fs.existsSync(file))fail('song-job-not-found');
      const job=readJob(file,root);
      if(job.target!==captured.target||job.kind!==captured.kind||job.chatKey!==captured.chatKey)fail('song-job-scope-denied');
      validateSongSourceArgs(job.parameters);return summary(job);
    },
    close() {
      if(closed)return;closed=true;
      if(onAbort)signal?.removeEventListener('abort',onAbort);
      for(const {controller,file} of active.values()) {
        controller.abort();
        try {
          const job=readJob(file,root);
          if(ACTIVE.has(job.status))writeJob(file,root,{...job,status:'interrupted',phase:'complete',
            code:'song-job-closed',finishedAt:new Date().toISOString(),automaticRetry:false});
        }catch{}
      }
    },
    whenIdle(){return Promise.allSettled([...active.values()].map(job=>job.completion))},
    diagnostics(){return {closed,activeJobs:active.size,recoveryIssues}},
  };
  onAbort=()=>service.close();
  if(signal?.aborted)service.close();else signal?.addEventListener('abort',onAbort,{once:true});
  return service;
}

export function songJobToolDefinitions(options) {
  const jobs=options.jobs??createSongJobs(options);
  const output={schema:{type:'object',additionalProperties:true},render:(_args,value)=>[{type:'text',text:JSON.stringify(value)}]};
  return [{name:'whale_start_song',
    description:'后台从明确原曲演唱链接截取1至55秒，准备导唱并用已选同一音色，通过本聊天原生微信语音发送。务必带上title（歌名，可加原唱），结果会记进所有聊天共用的歌单；歌单里已有的歌只传title即可按记下的链接和片段重唱。提交后可继续聊天，不轮询等待；结果只记录供本聊天后续使用，不自动另发状态消息。不是凭歌词或歌名生成原曲旋律，未听验不保证纯人声。',
    parameters:{type:'object',properties:{title:{type:'string',description:'歌名（可加原唱），如“雨爱 - 杨丞琳”'},sourceUrl:{type:'string'},
      startSeconds:{type:'number',minimum:0,maximum:86400},durationSeconds:{type:'number',minimum:1,maximum:55}},additionalProperties:false},output,
    execute:(args,exec)=>jobs.submit(args,exec)},
  {name:'whale_song_status',description:'查看本聊天后台演唱任务状态；不会重跑、重发或发送状态消息，不能查询其他聊天任务。',
    parameters:{type:'object',properties:{jobId:{type:'string',pattern:'^[0-9a-f]{32}$'}},required:['jobId'],additionalProperties:false},output,
    execute(args,exec){
      if(!args||typeof args!=='object'||Object.keys(args).length!==1||typeof args.jobId!=='string')fail('song-job-arguments-invalid');
      return jobs.status(args.jobId,exec);
    }}];
}

export function registerSongJobTools(scope,options) {
  const own=!options.jobs,jobs=options.jobs??createSongJobs(options);
  const removes=songJobToolDefinitions({...options,jobs}).map(tool=>scope.tools.register(tool));
  return ()=>{for(const remove of removes)if(typeof remove==='function')remove();if(own)jobs.close()};
}
