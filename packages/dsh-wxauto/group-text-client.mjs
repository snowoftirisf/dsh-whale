import path from 'node:path';
import {spawn} from 'node:child_process';
import {collectNativeGroupTextCatalog} from './native-group-text-catalog.mjs';
export function createGroupTextCatalogClient({root,dataRoot,python,accountId,nativeChatKey,
  spawnProcess=spawn,timeoutMs=120000}={}){
  if([root,dataRoot,python].some(v=>typeof v!=='string'||!path.isAbsolute(v))||typeof spawnProcess!=='function'
    ||typeof accountId!=='string'||!accountId||accountId.length>256
    ||!/^wechat:[A-Za-z0-9_-]{1,128}@chatroom$/.test(nativeChatKey??'')
    ||!Number.isSafeInteger(timeoutMs)||timeoutMs<1)throw Error('group-text-catalog-config-invalid');
  let closed=false;const active=new Set();
  function readPage(args,signal){
    if(closed||signal?.aborted)return Promise.reject(Error('group-text-catalog-stopped'));
    return new Promise((resolve,reject)=>{
      let child,done=false,timer,bytes=0,chunks=[];
      const kill=()=>{if(child&&!child.killed)try{child.kill();}catch{}};
      const finish=(code,value)=>{if(done)return;done=true;clearTimeout(timer);signal?.removeEventListener('abort',cancel);active.delete(cancel);
        if(code){kill();reject(Error(code));}else resolve(value);};
      const cancel=()=>finish('group-text-catalog-stopped');
      try{
        child=spawnProcess(python,['-B',path.join(root,'native_group_text_io.py'),'--root',dataRoot],
          {cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8'}});
        active.add(cancel);signal?.addEventListener('abort',cancel,{once:true});
        child.stderr?.resume?.();
        child.on('error',()=>finish('group-text-catalog-process-failed'));
        child.stdout.on('data',data=>{if(done)return;const chunk=Buffer.from(data);bytes+=chunk.length;
          if(bytes>64*1024*1024)return finish('group-text-catalog-output-limit');chunks.push(chunk);});
        child.on('close',code=>{if(done)return;
          try{const value=JSON.parse(Buffer.concat(chunks).toString('utf8'));
            if(code!==0||value.ok!==true||!value.value)return finish('group-text-catalog-unavailable');finish(null,value.value);
          }catch{finish('group-text-catalog-response-invalid');}});
        child.stdin.on?.('error',()=>finish('group-text-catalog-write-failed'));
        timer=setTimeout(()=>finish('group-text-catalog-timeout'),timeoutMs);
        child.stdin.end(JSON.stringify(args));
        if(closed||signal?.aborted)cancel();
      }catch{finish('group-text-catalog-process-failed');}
    });
  }
  return Object.freeze({catalog({signal,snapshotId,admissionProof}={}){
    if(closed)return Promise.reject(Error('group-text-catalog-stopped'));
    return collectNativeGroupTextCatalog({readPage,accountId,nativeChatKey,signal,snapshotId,admissionProof});
  },close(){closed=true;for(const cancel of [...active])cancel();}});
}
