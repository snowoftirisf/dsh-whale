// Read-only first-cutover quiet checks. Constructor performs no IO; never
// claims recovery inputs, initializes native state, captures, asks or sends.
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {nativeGroupTextPaths} from './group-text-contract.mjs';

const fail=code=>{throw Object.assign(Error(code),{safeCode:code})};
const sig=s=>JSON.stringify([s.dev,s.ino,s.size,s.mtimeMs,s.nlink]);
const finiteCount=v=>Number.isSafeInteger(v)&&v>=0;
const SAFE_CODES=new Set(['file-unverified','file-changed','identity-unverified','closed','not-ready','extra-busy','config-changed',
  'recovery-unverified','recovery-group-text-pending','queue-unverified','queue-busy','queue-paused','buffer-unverified',
  'buffer-pending','history-unverified','history-pending','worker-unverified','deferred-unverified','pending-deferred']);

function readChecked(file,maxBytes,{missing=false}={}){
  if(typeof file!=='string'||!path.isAbsolute(file))fail('file-unverified');
  let absent=false;
  for(let current=file;;current=path.dirname(current)){
    let s;try{s=fs.lstatSync(current)}catch(e){if(e.code==='ENOENT'&&current===file&&missing){absent=true;continue}if(e.code==='ENOENT'&&current!==file)continue;fail('file-unverified')}
    if(s.isSymbolicLink()||current!==file&&!s.isDirectory())fail('file-unverified');
    if(current===file&&(!s.isFile()||s.nlink!==1||s.size<1||s.size>maxBytes))fail('file-unverified');
    if(path.dirname(current)===current)break;
  }
  if(absent)return null;
  const info=fs.lstatSync(file),fd=fs.openSync(file,'r');let bytes;
  try{
    if(sig(fs.fstatSync(fd))!==sig(info))fail('file-changed');bytes=fs.readFileSync(fd);
    if(sig(fs.fstatSync(fd))!==sig(info)||sig(fs.lstatSync(file))!==sig(info))fail('file-changed');
  }finally{fs.closeSync(fd)}
  return bytes;
}

function deferredCount(dataRoot,nativeChatKey){
  const folder=path.join(nativeGroupTextPaths({dataRoot,nativeChatKey}).directory,'deferred');let absent=false;
  for(let current=folder;;current=path.dirname(current)){
    let info;try{info=fs.lstatSync(current)}catch(e){if(e.code==='ENOENT'){if(current===folder)absent=true;continue}fail('deferred-unverified')}
    if(info.isSymbolicLink()||!info.isDirectory())fail('deferred-unverified');
    if(path.dirname(current)===current)break;
  }
  if(absent)return 0;
  const before=fs.lstatSync(folder),entries=fs.readdirSync(folder);
  if(entries.length>10000)fail('deferred-unverified');
  for(const name of entries){
    if(!/^[a-f0-9]{32}\.json$/.test(name))fail('deferred-unverified');
    const info=fs.lstatSync(path.join(folder,name));
    if(!info.isFile()||info.isSymbolicLink()||info.nlink!==1)fail('deferred-unverified');
  }
  if(sig(fs.lstatSync(folder))!==sig(before))fail('deferred-unverified');
  return entries.length;
}

export function createGroupTextReadiness({config,nativeChatKey,target,routeForGroup,configPath,configSha256,recoveryFile,getSessionId,getRevision,
  queue,buffers,history,harness,isReady,dependenciesReady,getWorkerStatus,isClosed,extraBusy=()=>false}={}){
  if(!config||typeof config.target!=='string'||!config.target||typeof config.dataRoot!=='string'||!path.isAbsolute(config.dataRoot)||typeof configPath!=='string'||!path.isAbsolute(configPath)
    ||typeof recoveryFile!=='string'||!path.isAbsolute(recoveryFile)||typeof configSha256!=='string'||!(/^[a-f0-9]{64}$/.test(configSha256))
    ||[getSessionId,getRevision,isReady,dependenciesReady,getWorkerStatus,isClosed,extraBusy].some(f=>typeof f!=='function')
    ||typeof queue?.status!=='function'||typeof buffers?.get!=='function'||typeof history?.cutoverStatus!=='function'
    ||typeof history?.settleIncoming!=='function'||typeof harness?.isSessionRunning!=='function')throw Error('group-text-readiness-config-invalid');
  if(!/^wechat:[A-Za-z0-9_-]+@chatroom$/.test(nativeChatKey??'')||typeof target!=='string'||!target||typeof routeForGroup!=='function')throw Error('group-text-readiness-config-invalid');
  const owner=target;
  const identity=()=>JSON.stringify([config.accountId,config.dataRoot,nativeChatKey,routeForGroup(nativeChatKey)]);
  const originalIdentity=identity();
  const baseCounts=()=>({incomingPending:0,outgoingPending:0,evidencePending:0,activeInputs:0,contextPending:0,pending:0,active:false,paused:false,buffered:0,deferredPending:0});
  function activity({allowPaused=true}={}){
    let revision=null,sessionId=null;const counts=baseCounts();
    try{
      revision=getRevision();sessionId=getSessionId();
      if(!finiteCount(revision)||typeof sessionId!=='string'||!sessionId)fail('identity-unverified');
      if(isClosed()!==false)fail('closed');
      if(isReady()!==true||dependenciesReady()!==true)fail('not-ready');
      if(extraBusy()!==false)fail('extra-busy');
      if(identity()!==originalIdentity)fail('config-changed');
      const cfg=readChecked(configPath,4*1024*1024);
      if(createHash('sha256').update(cfg).digest('hex')!==configSha256)fail('config-changed');
      counts.deferredPending=deferredCount(config.dataRoot,nativeChatKey);
      if(counts.deferredPending)fail('pending-deferred');
      const recovery=readChecked(recoveryFile,4*1024*1024,{missing:true});
      if(recovery){
        let value;try{value=JSON.parse(recovery.toString('utf8'))}catch{fail('recovery-unverified')}
        if(value?.version!==1||!['pending','claimed'].includes(value.state)||!Array.isArray(value.messages)||value.messages.length>20)fail('recovery-unverified');
        for(const message of value.messages){
          if(!message||typeof message.target!=='string'||!['friend','group'].includes(message.kind)
            ||typeof message.id!=='string'||!message.id.startsWith('recovery-')||typeof message.text!=='string'||typeof message.sender!=='string')fail('recovery-unverified');
          if(value.state==='pending'&&message.target===owner&&message.kind==='group'&&(!message.mediaType||message.mediaType==='text'))fail('recovery-group-text-pending');
        }
      }
      const q=queue.status(owner);
      if(!q||!finiteCount(q.pending)||typeof q.active!=='boolean'||typeof q.paused!=='boolean')fail('queue-unverified');
      Object.assign(counts,{pending:q.pending,active:q.active,paused:q.paused});
      const buffer=buffers.get(owner);
      if(buffer!==undefined&&!Array.isArray(buffer))fail('buffer-unverified');
      counts.buffered=buffer?.length??0;
      const status=history.cutoverStatus();
      for(const key of ['incomingPending','outgoingPending','evidencePending','activeInputs','contextPending']){
        if(!status||!finiteCount(status[key]))fail('history-unverified');counts[key]=status[key];
      }
      if(q.pending||q.active)fail('queue-busy');
      if(q.paused&&!allowPaused)fail('queue-paused');
      if(counts.buffered)fail('buffer-pending');
      if(['incomingPending','outgoingPending','evidencePending','activeInputs','contextPending'].some(k=>counts[k]))fail('history-pending');
      const worker=getWorkerStatus();
      const r=routeForGroup(nativeChatKey);if(r?.target!==target||r.kind!=='group'||r.active===false||r.sessionId!==sessionId)fail('identity-unverified');
      if(worker?.version!==1||worker.readsStopped!==false||!['ui','preparing'].includes(worker.mode)
        ||worker.dirty!==false||worker.deferred!==0)fail('worker-unverified');
      return {quiet:true,revision,sessionId,code:'group-text-ready',counts};
    }catch(error){return {quiet:false,revision,sessionId,code:error?.safeCode==='pending-deferred'?'group-text-cutover-pending-deferred':'group-text-readiness-'+(SAFE_CODES.has(error?.safeCode)?error.safeCode:'unverified'),counts}}
  }
  return Object.freeze({activity,
    async inspect({allowPaused=false,settle=true}={}){
      const before=activity({allowPaused});
      if(!before.quiet&&!(settle&&before.code==='group-text-readiness-history-pending'))return before;
      try{
        if(settle)await history.settleIncoming();
        const running=await harness.isSessionRunning(before.sessionId);
        const after=activity({allowPaused});if(!after.quiet)return after;
        if(before.revision!==after.revision||before.sessionId!==after.sessionId)return {...after,quiet:false,code:'group-text-readiness-activity-changed'};
        if(running!==false)return {...after,quiet:false,code:running===true?'group-text-readiness-session-running':'group-text-readiness-session-unverified'};
        return after;
      }catch{return {...activity({allowPaused}),quiet:false,code:'group-text-readiness-inspection-failed'}}
    },
  });
}
