// Inert per-group restore; a heartbeat never creates an inbox or changes authority.
import {createNativeGroupTextInbox} from './native-group-text-inbox.mjs';
import {createNativeGroupTextProcessor} from './native-group-text-processor.mjs';
import {createGroupTextCatalogClient} from './group-text-client.mjs';
const keyOk=v=>/^wechat:[A-Za-z0-9_-]+@chatroom$/.test(v??'');
const hex=v=>/^[a-f0-9]{32}$/.test(v??'');
export function createNativeGroupTextRuntime({config,root,requestGroup,history,routeForGroup,groupKeys,enqueue,
 isClosed=()=>false,report=()=>{},clientFactory=createGroupTextCatalogClient,inboxFactory=createNativeGroupTextInbox,processorFactory=createNativeGroupTextProcessor}={}){
 if(!config||[requestGroup,routeForGroup,groupKeys,enqueue].some(f=>typeof f!=='function'))throw Error('group-text-runtime-config-invalid');
 let closed=false,restoreWork=null;const entries=new Map();
 const route=key=>{try{const r=routeForGroup(key);return keyOk(key)&&r?.kind==='group'&&r.active!==false&&typeof r.target==='string'&&r.target&&typeof r.sessionId==='string'&&r.sessionId?r:null;}catch{return null;}};
 // reason is a short public code shown to the owner when a group is blocked.
 const revoke=(key,mode,reason=null)=>{const e=entries.get(key);e?.processor?.close();e?.client?.close();entries.set(key,{...e,mode,processor:null,client:null,epoch:e?.seenEpoch??null,reason:mode!=='blocked'?null:reason==='awaiting-restore'&&e?.mode==='blocked'&&e.reason?e.reason:reason??'unknown'});};
 const canonical=(e,r)=>!!r&&typeof r.chatId==='string'&&!!r.chatId&&e?.seenSession===r.sessionId&&e?.seenChat===r.chatId;
 const active=key=>{const e=entries.get(key),r=route(key);return !closed&&!isClosed()&&canonical(e,r)&&e?.mode==='native'&&!!e.processor;};
 const remember=(key,r,{sticky=false,epoch=null}={})=>{
  const old=entries.get(key),targets=new Set(old?.seenTargets??(old?.seenTarget?[old.seenTarget]:[]));
  if(r?.target)targets.add(r.target);
  entries.set(key,{...old,guarded:true,stickyGuard:!!old?.stickyGuard||sticky,seenTargets:[...targets],
   seenTarget:r?.target??old?.seenTarget,seenSession:old?.guarded?old.seenSession:r?.sessionId,seenChat:old?.guarded?old.seenChat:r?.chatId,
   ...(epoch?{seenEpoch:old?.seenEpoch??epoch}:{})});
 };
 function apply(key,v,allow=false){
  if(!keyOk(key))return false;
  if(closed||isClosed()){revoke(key,'closed');return false;}
  const old=entries.get(key),r=route(key);
  // Only a matching trusted worker envelope may establish a cold guard.
  // A missing RPC / foreign account is not evidence of native authority.
  const bound=!!r&&v?.version===1&&v.accountId===config.accountId&&v.nativeChatKey===key&&v.target===r.target;
  if(!bound){revoke(key,old?.guarded?'blocked':'unavailable','worker-status-unbound');return false;}
  if(v.readsStopped===true||v.mode==='blocked'){
    remember(key,r,{sticky:true,epoch:hex(v.authorityEpoch)?v.authorityEpoch:null});revoke(key,'blocked','worker-'+(/^[a-z0-9-]{1,80}$/.test(v.blockedReason??'')?v.blockedReason:'blocked'));return false;
  }
  if(!['ui','preparing','native'].includes(v.mode)){revoke(key,old?.guarded?'blocked':'unavailable','worker-mode-unknown');return false;}
  if(old?.stickyGuard&&!canonical(old,r)){
    remember(key,r,{sticky:true});revoke(key,'blocked','route-session-or-chat-changed');return false;
  }
  if(old?.seenEpoch&&(v.mode!=='native'||v.authorityEpoch!==old.seenEpoch)){
    revoke(key,'blocked',v.mode!=='native'?'native-state-regressed':'native-epoch-changed');return false;
  }
  if(v.mode!=='native'){
    if(old?.stickyGuard){remember(key,r,{sticky:true});revoke(key,'blocked','native-state-regressed');return false;}
    revoke(key,v.mode);const current=entries.get(key);
    current.guarded=v.mode==='preparing';if(current.guarded)remember(key,r);
    return false;
  }
  // Even malformed/missing epoch in an otherwise bound native reply is not a
  // license to replay old UI messages. Restore may later verify a real epoch.
  remember(key,r,{sticky:true,epoch:hex(v.authorityEpoch)?v.authorityEpoch:null});
  if(!hex(v.authorityEpoch)||typeof r.chatId!=='string'||!r.chatId){revoke(key,'blocked','native-authority-invalid');return false;}
  // Preserve evidence of an immutable worker authority even when parent restore
  // cannot verify its inbox yet. A later UI response cannot silently revoke it.
  if(!old?.processor&&!allow){revoke(key,'blocked','awaiting-restore');return false;}
  try{
   if(!old?.processor){
    const inbox=inboxFactory({dataRoot:config.dataRoot,accountId:config.accountId,nativeChatKey:key}),state=inbox.status();
    if(state.initialized!==true||state.authorityEpoch!==v.authorityEpoch)throw Error('baseline');
    const e={...entries.get(key),mode:'native',epoch:v.authorityEpoch,client:clientFactory({root,dataRoot:config.dataRoot,python:config.python,accountId:config.accountId,nativeChatKey:key})};
    entries.set(key,e);
    e.processor=processorFactory({inbox,readCatalog:options=>e.client.catalog(options),history,routeForGroup,enqueue,accountId:config.accountId,nativeChatKey:key,
     activeAuthority:()=>active(key)?{active:true,accountId:config.accountId,nativeChatKey:key,authorityEpoch:e.epoch}:null,isClosed:()=>closed||isClosed(),report});
   }
   entries.get(key).mode='native';return true;
  }catch{revoke(key,'blocked','inbox-unverified');try{report('group-text-runtime-restore-unverified');}catch{}return false;}
 }
 const runtime={active,anyActive:()=>[...entries.keys()].some(active),getProcessor:key=>active(key)?entries.get(key).processor:null,
  handleStatus:(key,value)=>apply(key,value),
  restore(){if(!restoreWork)restoreWork=(async()=>{const keys=groupKeys();if(!Array.isArray(keys)||keys.length>256||new Set(keys).size!==keys.length||keys.some(k=>!keyOk(k)))throw Error('group-text-routes-invalid');
    for(const key of entries.keys())if(!keys.includes(key))revoke(key,'blocked');
    for(const key of keys){try{apply(key,await requestGroup('group-text-status',{nativeChatKey:key}),true);}catch{revoke(key,entries.get(key)?.guarded?'blocked':'unavailable','worker-status-unavailable');}}
    return runtime.status();})().finally(()=>restoreWork=null);return restoreWork;},
  async refresh(){let buffered=0,failed=0;for(const [key,e]of entries){if(!active(key))continue;try{const r=await e.processor.refresh();buffered+=r.buffered??0;failed+=r.failed??0;}catch{failed++;}}return {buffered,failed};},
  acceptsHint:e=>e?.event==='native-group-text-observed'&&e.kind==='group'&&active(e.nativeChatKey)&&route(e.nativeChatKey)?.target===e.target&&entries.get(e.nativeChatKey).epoch===e.authorityEpoch,
  guardedTargets:()=>new Set([...entries].filter(([,e])=>e.guarded).flatMap(([key,e])=>[
    ...(e.seenTargets??(e.seenTarget?[e.seenTarget]:[])),route(key)?.target].filter(Boolean))),
  status:()=>({version:1,recoveryGuardVersion:2,active:[...entries.keys()].filter(active).length,groups:[...entries].map(([nativeChatKey,e])=>({nativeChatKey,mode:e.mode,active:active(nativeChatKey),authorityEpoch:e.epoch??null,...(e.mode==='blocked'?{blockedReason:e.reason??'unknown'}:{})}))}),
  blockedReason:key=>{const e=entries.get(key);return e?.mode==='blocked'?e.reason??'unknown':null;},
  // Owner reset only: the worker has already moved this group's native state aside.
  forget(key){if(entries.has(key)){revoke(key,'unavailable');entries.delete(key);}},
  close(){closed=true;for(const key of entries.keys())revoke(key,'closed');}};
 return Object.freeze(runtime);
}
