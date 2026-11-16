import path from 'node:path';
import {spawn} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {collectNativeOwnerTextCatalog} from './native-owner-text-catalog.mjs';
const hex=v=>typeof v==='string'&&/^[a-f0-9]{32}$/.test(v);
const fields={'owner-text-status':[],'owner-text-prepare':[],'owner-text-abort':['ticket'],
  'owner-text-commit':['ticket','authorityEpoch'],'owner-text-decision':['claimId','authorityEpoch']};

export function ownerTextRpcClient({ownerTarget,write,isReady=()=>true,timeoutMs=45000}={}){
  if(typeof ownerTarget!=='string'||!ownerTarget||typeof write!=='function'||typeof isReady!=='function'
    ||!Number.isSafeInteger(timeoutMs)||timeoutMs<1)throw Error('owner-text-client-config-invalid');
  let closed=false;const pending=new Map();
  return Object.freeze({request(op,args={}){
    if(closed||!isReady())return Promise.reject(Error('owner-text-worker-not-ready'));
    if(!Object.hasOwn(fields,op)||!args||typeof args!=='object'||Array.isArray(args)
      ||Object.keys(args).length!==fields[op].length||fields[op].some(k=>!Object.hasOwn(args,k)||!hex(args[k])))
      return Promise.reject(Error('owner-text-request-invalid'));
    return new Promise((resolve,reject)=>{
      const id='owner-text:'+randomUUID();
      const timer=setTimeout(()=>{pending.delete(id);reject(Error('owner-text-outcome-unknown'));},timeoutMs);
      pending.set(id,{resolve,reject,timer});
      const failure=error=>{if(!error)return;const item=pending.get(id);if(item){clearTimeout(item.timer);pending.delete(id);reject(Error('owner-text-write-failed'));}};
      try{write(JSON.stringify({op,id,target:ownerTarget,...args})+'\n',failure);}catch{failure(true);}
    });
  },handle(event){
    if(event?.event!=='owner-text-result')return false;const item=pending.get(event.id);if(!item)return false;
    clearTimeout(item.timer);pending.delete(event.id);
    if(event.ok===true)item.resolve(event.value);
    else item.reject(Error(typeof event.code==='string'&&/^owner-text-[a-z-]{1,100}$/.test(event.code)?event.code:'owner-text-operation-failed'));
    return true;
  },close(){closed=true;for(const item of pending.values()){clearTimeout(item.timer);item.reject(Error('owner-text-worker-exited'));}pending.clear();}});
}

export function createOwnerTextCatalogClient({root,dataRoot,python,accountId,ownerNativeChatKey,expectedPeerUsername,
  spawnProcess=spawn,timeoutMs=120000}={}){
  if([root,dataRoot,python].some(v=>typeof v!=='string'||!path.isAbsolute(v))||typeof spawnProcess!=='function'
    ||!Number.isSafeInteger(timeoutMs)||timeoutMs<1)throw Error('owner-text-catalog-config-invalid');
  let closed=false;const active=new Set();
  function readPage(args,signal){
    if(closed||signal?.aborted)return Promise.reject(Error('owner-text-catalog-stopped'));
    return new Promise((resolve,reject)=>{
      let child,done=false,timer,bytes=0,chunks=[];
      const kill=()=>{if(child&&!child.killed)try{child.kill();}catch{}};
      const finish=(code,value)=>{if(done)return;done=true;clearTimeout(timer);signal?.removeEventListener('abort',cancel);active.delete(cancel);
        if(code){kill();reject(Error(code));}else resolve(value);};
      const cancel=()=>finish('owner-text-catalog-stopped');
      try{
        child=spawnProcess(python,['-B',path.join(root,'native_owner_text_io.py'),'--root',dataRoot],
          {cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8'}});
        active.add(cancel);signal?.addEventListener('abort',cancel,{once:true});
        child.stderr?.resume?.();
        child.on('error',()=>finish('owner-text-catalog-process-failed'));
        child.stdout.on('data',data=>{if(done)return;const chunk=Buffer.from(data);bytes+=chunk.length;
          if(bytes>64*1024*1024)return finish('owner-text-catalog-output-limit');chunks.push(chunk);});
        child.on('close',code=>{if(done)return;
          try{const value=JSON.parse(Buffer.concat(chunks).toString('utf8'));
            if(code!==0||value.ok!==true||!value.value)return finish('owner-text-catalog-unavailable');finish(null,value.value);
          }catch{finish('owner-text-catalog-response-invalid');}});
        child.stdin.on?.('error',()=>finish('owner-text-catalog-write-failed'));
        timer=setTimeout(()=>finish('owner-text-catalog-timeout'),timeoutMs);
        child.stdin.end(JSON.stringify(args));
        if(closed||signal?.aborted)cancel();
      }catch{finish('owner-text-catalog-process-failed');}
    });
  }
  return Object.freeze({catalog({signal,snapshotId}={}){
    if(closed)return Promise.reject(Error('owner-text-catalog-stopped'));
    return collectNativeOwnerTextCatalog({readPage,accountId,ownerNativeChatKey,expectedPeerUsername,signal,snapshotId});
  },close(){closed=true;for(const cancel of [...active])cancel();}});
}
