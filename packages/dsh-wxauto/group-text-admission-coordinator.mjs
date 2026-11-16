// New-group source handoff coordination only. Construction is inert; no UI/model calls.
import fs from 'node:fs';import path from 'node:path';import {createHash,randomUUID} from 'node:crypto';
import {nativeGroupTextPaths} from './group-text-contract.mjs';
const hash=v=>createHash('sha256').update(JSON.stringify(v)).digest('hex');
const fail=code=>{throw Error('native-group-text-admission-'+code)};
const binding=r=>[r.accountId,r.nativeChatKey,r.target,r.sessionId,r.chatId];
export function createGroupTextAdmissionCoordinator({dataRoot,accountId,groupInbox,textAdmission,getRoute,runtime,requestAdmit,requestStatus,pump,readTextStatus=()=>null,consumeNotIssued=()=>null,wait=ms=>new Promise(resolve=>setTimeout(resolve,ms)),retryWaitMs=30000,isClosed=()=>false,now=Date.now}={}){
 if(!path.isAbsolute(dataRoot??'')||!accountId||[getRoute,requestAdmit,requestStatus,pump,readTextStatus,consumeNotIssued,wait,isClosed,now].some(f=>typeof f!=='function')||typeof groupInbox?.read!=='function'||typeof groupInbox?.events!=='function'||typeof textAdmission?.prepare!=='function'||typeof runtime?.restore!=='function'||typeof runtime?.active!=='function')fail('config-invalid');
 if(!Number.isSafeInteger(retryWaitMs)||retryWaitMs<1||retryWaitMs>60000)fail('config-invalid');
 const grants=new Map(),running=new Set(),unissuedPermissions=new Map();let closed=false;
 const check=record=>{if(closed||isClosed())fail('closed');if(!record||record.accountId!==accountId||!/^wechat:[A-Za-z0-9_-]+@chatroom$/.test(record.nativeChatKey??'')||['target','sessionId','chatId'].some(k=>typeof record[k]!=='string'||!record[k]))fail('record-invalid');return record;};
 const fileFor=(r,claimId)=>{if(!/^[a-f0-9]{32}$/.test(claimId??''))fail('claim-invalid');return path.join(nativeGroupTextPaths({dataRoot,nativeChatKey:r.nativeChatKey}).directory,'admission-attempts',claimId+'.json');};
 function parents(file){for(let p=path.dirname(file);;p=path.dirname(p)){let s;try{s=fs.lstatSync(p)}catch(e){if(e.code!=='ENOENT')throw e;}if(s&&(!s.isDirectory()||s.isSymbolicLink()))fail('path-invalid');if(path.dirname(p)===p)break;}}
 function readAttempt(record,claimId=groupInbox.read(record)?.claimId){if(!claimId)return null;const file=fileFor(record,claimId);parents(file);let st;try{st=fs.lstatSync(file)}catch(e){if(e.code==='ENOENT')return null;throw e;}if(!st.isFile()||st.isSymbolicLink()||st.nlink!==1||st.size>32768)fail('attempt-invalid');const bytes=fs.readFileSync(file),after=fs.lstatSync(file);if(st.ino!==after.ino||st.size!==after.size||st.mtimeMs!==after.mtimeMs)fail('attempt-changed');const v=JSON.parse(bytes);const{digest,...body}=v;if(digest!==hash(body)||body.format!=='whale-native-group-text-admission-attempt'||body.version!==1||JSON.stringify(body.binding)!==JSON.stringify(binding(record))||!['requested','unissued','native-confirmed','complete'].includes(body.phase)||![0,1].includes(body.retryCount??0)||body.phase==='unissued'&&(!/^[a-f0-9]{32}$/.test(body.notIssued?.workerInstanceId??'')||!/^[a-f0-9]{64}$/.test(body.notIssued?.requestFingerprint??'')||typeof body.notIssued?.requestId!=='string')||!['new-admission','existing-inbox'].includes(body.ownershipMode)||body.rawClaimId!==claimId)fail('attempt-invalid');return v;}
 function write(record,value,exclusive=false){const file=fileFor(record,value.rawClaimId);parents(file);fs.mkdirSync(path.dirname(file),{recursive:true});parents(file);const {digest,...body}=value;const data=JSON.stringify({...body,digest:hash(body)}),target=exclusive?file:file+'.'+randomUUID()+'.tmp';let fd;
  try{fd=fs.openSync(target,'wx',0o600);fs.writeFileSync(fd,data);fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;if(!exclusive)fs.renameSync(target,file);const read=readAttempt(record,value.rawClaimId);if(read.digest!==hash(body))fail('attempt-write-unknown');return read;}finally{if(fd!==undefined)fs.closeSync(fd);}}
 function current(record,claimId){check(record);const q=groupInbox.read(record);if(!q||q.claimId!==claimId||!['queued','submitted'].includes(q.state))fail('claim-changed');return q;}
 function receipt(record,q){const t=q.textDelegation;if(!t||!['new-admission','existing-inbox'].includes(t.ownershipMode)||!/^[a-f0-9]{32}$/.test(t.authorityEpoch??'')||t.claimId!==q.claimId||t.claimHash!==q.claimHash||t.accountId!==accountId||t.nativeChatKey!==record.nativeChatKey||!Array.isArray(t.texts)||!t.texts.length)fail('delegation-required');return t;}
 function argsFor(record,a){return {nativeChatKey:record.nativeChatKey,claimId:a.rawClaimId,claimHash:a.rawClaimHash,authorityEpoch:a.authorityEpoch};}
 function grant(record,a,admitAllowed){const q=current(record,a.rawClaimId);if(q.claimHash!==a.rawClaimHash)fail('claim-changed');const t=receipt(record,q);if(t.authorityEpoch!==a.authorityEpoch)fail('claim-changed');grants.set(record.nativeChatKey,{record:structuredClone(record),attempt:a,admitAllowed});}
 function nativeResult(record,a,value){if(value?.version!==1||value.accountId!==accountId||value.nativeChatKey!==record.nativeChatKey||value.target!==record.target||value.mode!=='native'||value.authorityEpoch!==a.authorityEpoch||a.ownershipMode==='new-admission'&&(value.admission?.rawClaimId!==a.rawClaimId||value.admission?.rawClaimHash!==a.rawClaimHash))fail('native-outcome-unverified');}
 async function finish(record,a,{activateRoute,deliverLegacy,enableRoute,restoreUiBinding}={},needsBinding=false){
  if([activateRoute,deliverLegacy,enableRoute].some(f=>typeof f!=='function'))fail('callbacks-invalid');check(record);
  if(needsBinding){if(typeof restoreUiBinding!=='function')fail('ui-binding-restore-pending');const v=await restoreUiBinding(record,a.authorityEpoch);if(v?.nativeMonitorRestored!==true||v.actualUiGroupConfirmed!==true||v.accountId!==accountId||v.nativeChatKey!==record.nativeChatKey||v.nativeTextAuthorityEpoch!==a.authorityEpoch)fail('ui-binding-restore-unverified');}await activateRoute(record);check(record);
  await deliverLegacy(record);await enableRoute(record);check(record);await runtime.restore();if(!runtime.active(record.nativeChatKey))fail('parent-restore-unverified');await pump();
  if(a.rawClaimId)a=write(record,{...a,phase:'complete',completedAt:now()});return {native:true,authorityEpoch:a.authorityEpoch,resumed:true};
 }
 function validateHandoff(q,h){if(h?.ok!==true||h.actualUiGroupConfirmed!==true||h.checkedThisCall!==true||h.originalChatRestored!==true||h.nativeQueuedHandoff!==true||h.nativeClaimId!==q.claimId||h.nativeClaimHash!==q.claimHash||!Array.isArray(h.nativeRenderedMessages)||h.nativeRenderedMessages.length!==q.messages.length||q.messages.some((m,i)=>h.nativeRenderedMessages[i]?.nativeId!==m.nativeId||typeof h.nativeRenderedMessages[i]?.text!=='string'))fail('handoff-unverified');}
 function checkUiStatus(record,value){if(value?.version!==1||value.accountId!==accountId||value.nativeChatKey!==record.nativeChatKey||value.target!==record.target||value.mode!=='ui'||!/^[a-f0-9]{32}$/.test(value.workerInstanceId??''))fail('unissued-status-unverified');}
 async function retryUnissued(record,a,callbacks){
  if((a.retryCount??0)>=1)fail('retry-exhausted');
  grant(record,a,false);let value=await requestStatus(argsFor(record,a));
  if(value?.mode==='native'){nativeResult(record,a,value);return finish(record,a,callbacks,getRoute(record)?.active===false);}
  checkUiStatus(record,value);
  let permission=unissuedPermissions.get(a.rawClaimId);
  if(permission?.workerInstanceId!==value.workerInstanceId||permission?.requestFingerprint!==a.notIssued.requestFingerprint){
   if(typeof callbacks?.reconfirmUiHandoff!=='function')fail('reconfirmation-required');
   if(a.reconfirmationWorker===value.workerInstanceId)fail('reconfirmation-outcome-unknown');
   a=write(record,{...a,reconfirmationWorker:value.workerInstanceId});
   const q=current(record,a.rawClaimId);if(q.claimHash!==a.rawClaimHash)fail('claim-changed');
   validateHandoff(q,await callbacks.reconfirmUiHandoff(record,a.rawClaimId));
   permission={workerInstanceId:value.workerInstanceId,requestFingerprint:a.notIssued.requestFingerprint};unissuedPermissions.set(a.rawClaimId,permission);
  }
  const started=now();
  while(value.admitReady!==true){
   check(record);if(now()-started>=retryWaitMs)fail('unissued-wait-pending');
   await wait(Math.min(250,retryWaitMs));grant(record,a,false);value=await requestStatus(argsFor(record,a));
   if(value?.mode==='native'){nativeResult(record,a,value);return finish(record,a,callbacks,getRoute(record)?.active===false);}
   checkUiStatus(record,value);if(value.workerInstanceId!==permission.workerInstanceId)fail('worker-changed-reconfirmation-required');
  }
  if(value.workerInstanceId!==permission.workerInstanceId)fail('worker-changed-reconfirmation-required');
  a=write(record,{...a,phase:'requested',retryCount:1,retryRequestedAt:now()});grant(record,a,true);
  let result;
  try{result=await requestAdmit(argsFor(record,a));}catch(error){
   const proof=consumeNotIssued(error,argsFor(record,a));
   if(proof){write(record,{...a,phase:'unissued',notIssued:proof,retryExhausted:true});fail('retry-exhausted');}throw error;
  }
  nativeResult(record,a,result);a=write(record,{...a,phase:'native-confirmed'});grants.delete(record.nativeChatKey);unissuedPermissions.delete(a.rawClaimId);return finish(record,a,callbacks,false);
 }
 async function admitOnce(record,a,callbacks){
  let value;
  try{value=await requestAdmit(argsFor(record,a));}catch(error){
   const proof=consumeNotIssued(error,argsFor(record,a));if(!proof)throw error;
   a=write(record,{...a,phase:'unissued',notIssued:proof,retryCount:0});unissuedPermissions.set(a.rawClaimId,proof);
   return retryUnissued(record,a,callbacks);
  }
  nativeResult(record,a,value);a=write(record,{...a,phase:'native-confirmed'});grants.delete(record.nativeChatKey);return finish(record,a,callbacks,false);
 }
 async function withKey(record,work){check(record);const key=record.nativeChatKey;if(running.has(key))fail('busy');running.add(key);try{return await work();}finally{running.delete(key);grants.delete(key);}}
 return Object.freeze({
  pendingRoute(args){const g=grants.get(args?.nativeChatKey);if(!g)return null;try{check(g.record);const a=g.attempt;if(args.claimId!==a.rawClaimId||args.claimHash!==a.rawClaimHash||args.authorityEpoch!==a.authorityEpoch)return null;const q=a.rawClaimId?current(g.record,a.rawClaimId):null,t=q?receipt(g.record,q):null,r=getRoute(g.record);if(q&&(q.claimHash!==a.rawClaimHash||t.authorityEpoch!==a.authorityEpoch)||!r||g.admitAllowed&&r.active!==false||r.kind!=='group'||r.target!==g.record.target||r.sessionId!==g.record.sessionId||r.chatId!==g.record.chatId)return null;return {...g.record,...args,kind:'group',active:r.active!==false,admitAllowed:g.admitAllowed};}catch{return null;}},
  async prepare(record,claimId){return withKey(record,async()=>{const a=readAttempt(record,claimId);if(a)return a.phase==='complete'?{complete:true,queued:current(record,claimId)}:{resume:true,attempt:a};const q=current(record,claimId);if(q.state!=='queued')fail('claim-not-queued');if(!q.messages.some(m=>m.type==='text'&&!(q.handledServerIds??[]).includes(m.serverId)))return {legacy:true,queued:q};const delegated=await textAdmission.prepare(record,claimId);const fresh=current(record,claimId);receipt(record,fresh);return {delegated:true,queued:fresh};});},
  async commit(record,claimId,handoff,callbacks){return withKey(record,async()=>{if(readAttempt(record,claimId))fail('existing-attempt-resume-required');const q=current(record,claimId);if(q.state!=='queued')fail('claim-not-queued');const t=receipt(record,q);if(t.ownershipMode==='new-admission')validateHandoff(q,handoff);groupInbox.events(record,q,handoff);
    let a=write(record,{format:'whale-native-group-text-admission-attempt',version:1,binding:binding(record),phase:'requested',rawClaimId:q.claimId,rawClaimHash:q.claimHash,authorityEpoch:t.authorityEpoch,ownershipMode:t.ownershipMode,requestedAt:now()},true);grant(record,a,t.ownershipMode==='new-admission');
    if(t.ownershipMode==='new-admission')return admitOnce(record,a,callbacks);const value=await requestStatus(argsFor(record,a));nativeResult(record,a,value);a=write(record,{...a,phase:'native-confirmed'});grants.delete(record.nativeChatKey);return finish(record,a,callbacks,getRoute(record)?.active===false);
  });},
  async resume(record,callbacks){return withKey(record,async()=>{const a=readAttempt(record);if(!a)fail('resume-proof-missing');if(a.phase==='unissued')return retryUnissued(record,a,callbacks);grant(record,a,false);const value=await requestStatus(argsFor(record,a));nativeResult(record,a,value);grants.delete(record.nativeChatKey);return finish(record,a,callbacks,getRoute(record)?.active===false);});},
  async restoreMonitor(record,callbacks){return withKey(record,async()=>{
    if(typeof readTextStatus!=='function')fail('existing-inbox-status-unavailable');const state=readTextStatus(record);
    if(state?.initialized!==true||state.accountId!==accountId||state.nativeChatKey!==record.nativeChatKey||!/^[a-f0-9]{32}$/.test(state.authorityEpoch??''))fail('existing-inbox-status-invalid');
    const a={authorityEpoch:state.authorityEpoch,ownershipMode:'existing-inbox'};
    grants.set(record.nativeChatKey,{record:structuredClone(record),attempt:a,admitAllowed:false});
    nativeResult(record,a,await requestStatus({nativeChatKey:record.nativeChatKey,authorityEpoch:a.authorityEpoch}));grants.delete(record.nativeChatKey);
    return finish(record,a,callbacks,true);
  });},
  status(record){check(record);const a=readAttempt(record);return a?{phase:a.phase,rawClaimId:a.rawClaimId,authorityEpoch:a.authorityEpoch,resumeRequired:a.phase!=='complete'}:{phase:'unissued',resumeRequired:false};},
  close(){closed=true;grants.clear();unissuedPermissions.clear();}
 });
}
