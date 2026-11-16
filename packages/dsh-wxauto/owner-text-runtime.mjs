// Restore existing explicit authority only. Never initializes an inbox, prepares
// a cutover, captures native data, calls a model, or sends a WeChat message.
import {createNativeOwnerTextInbox} from './native-owner-text-inbox.mjs';
import {createNativeOwnerTextProcessor} from './native-owner-text-processor.mjs';
import {createOwnerTextCatalogClient} from './owner-text-client.mjs';
const hex=v=>typeof v==='string'&&/^[a-f0-9]{32}$/.test(v);

export function createNativeOwnerTextRuntime({config,root,requestOwner,history,routeForOwner,enqueue,onControl,
  isClosed=()=>false,report=()=>{},catalogClient}={}){
  if(!config||typeof requestOwner!=='function'||typeof routeForOwner!=='function'||typeof enqueue!=='function')throw Error('owner-text-runtime-config-invalid');
  let closed=false,authority=null,processor=null,client=catalogClient??null,mode='unrestored',restoreWork=null;
  const notify=code=>{try{report(code);}catch{}};
  const revoke=next=>{authority=null;mode=next;processor?.close();processor=null;client?.close?.();client=null;};
  const route=()=>{try{const r=routeForOwner();return r?.target===config.target&&r.kind==='friend'&&r.active!==false&&typeof r.sessionId==='string'&&r.sessionId?r:null;}catch{return null;}};
  const active=()=>!closed&&!isClosed()&&!!authority&&!!processor&&!!route();
  function applyStatus(value,allowActivation=false){
    value=value?.ownerTextAuthority??value;
    if(closed||isClosed()){revoke('closed');return false;}
    if(value?.version!==1||value.readsStopped===true||!['ui','native'].includes(value.mode)){revoke('blocked');return false;}
    if(value.mode==='ui'){revoke('ui');return false;}
    if(!hex(value.authorityEpoch)){revoke('blocked');return false;}
    if(authority&&authority.authorityEpoch!==value.authorityEpoch){revoke('blocked');return false;}
    if(!processor&&!allowActivation)return false;
    try{
      const key=config.nativeVoiceSources?.ownerNativeChatKey;
      if(!route())throw Error('route');
      if(!processor){
        const inbox=createNativeOwnerTextInbox({dataRoot:config.dataRoot,accountId:config.accountId,
          ownerNativeChatKey:key,expectedPeerUsername:key?.slice(7)}),state=inbox.status();
        if(!state.initialized||state.authorityEpoch!==value.authorityEpoch)throw Error('epoch');
        client??=createOwnerTextCatalogClient({root,dataRoot:config.dataRoot,python:config.python,accountId:config.accountId,
          ownerNativeChatKey:key,expectedPeerUsername:key.slice(7)});
        authority={active:true,accountId:config.accountId,ownerNativeChatKey:key,authorityEpoch:value.authorityEpoch};
        processor=createNativeOwnerTextProcessor({inbox,readCatalog:options=>client.catalog(options),history,routeForOwner,enqueue,onControl,
          accountId:config.accountId,ownerNativeChatKey:key,expectedPeerUsername:key.slice(7),ownerTarget:config.target,
          activeAuthority:()=>!closed&&!isClosed()&&route()?authority:null,isClosed:()=>closed||isClosed(),report});
      }
      mode='native';return true;
    }catch{revoke('blocked');notify('owner-text-runtime-restore-unverified');return false;}
  }
  const handleStatus=value=>applyStatus(value);
  return Object.freeze({handleStatus,active,getProcessor:()=>active()?processor:null,
    restore(){if(!restoreWork)restoreWork=(async()=>{try{const value=await requestOwner('owner-text-status',{});applyStatus(value,true);return {mode,active:active()};}
      catch{revoke(closed?'closed':'blocked');notify('owner-text-runtime-status-unavailable');return {mode,active:false};}})().finally(()=>{restoreWork=null;});return restoreWork;},
    refresh(){return active()?processor.refresh():Promise.resolve({inactive:true,buffered:0,failed:0});},
    acceptsHint(event){return active()&&event?.event==='native-owner-text-observed'&&event.target===config.target&&event.kind==='friend'
      &&event.authorityEpoch===authority.authorityEpoch;},
    status:()=>({mode,active:active(),authorityEpoch:authority?.authorityEpoch??null}),
    close(){closed=true;revoke('closed');},
  });
}
