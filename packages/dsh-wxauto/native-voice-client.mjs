import {spawn} from 'node:child_process';
import path from 'node:path';
import {randomBytes} from 'node:crypto';

export function nativeVoiceClient({root,dataRoot,python,decoderPython,spawnProcess=spawn,timeoutMs=30000,maxCatalogPages=2048,maxCatalogBytes=64*1024*1024}) {
  const running=new Set();let closed=false;
  const call=(executable,args,input,signal)=>new Promise((resolve,reject)=>{
    if(closed||signal?.aborted){reject(Error('native-voice-client-stopped'));return;}
    let child;try{child=spawnProcess(executable,args,{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe'],
      env:{...process.env,PYTHONIOENCODING:'utf-8',CUDA_VISIBLE_DEVICES:''}});}catch{reject(Error('native-voice-client-start-failed'));return;}
    let settled=false,size=0,timer;const chunks=[];
    const stop=()=>{
      if(child.exitCode!==null&&child.exitCode!==undefined)return;
      if(process.platform==='win32'&&Number.isSafeInteger(child.pid)){
        const killer=spawnProcess('taskkill.exe',['/PID',String(child.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});
        killer.on('error',()=>child.kill());
      }else child.kill();
    };
    const finish=(error,value)=>{
      if(settled)return;settled=true;clearTimeout(timer);running.delete(cancel);signal?.removeEventListener('abort',cancel);
      error?reject(error):resolve(value);
    };
    const cancel=()=>{finish(Error('native-voice-client-stopped'));stop();};
    running.add(cancel);signal?.addEventListener('abort',cancel,{once:true});
    timer=setTimeout(()=>{finish(Error('native-voice-client-timeout'));stop();},timeoutMs);
    child.stderr.on('data',()=>{});
    child.stdout.on('data',chunk=>{
      if(settled)return;size+=chunk.length;
      if(size>4*1024*1024){finish(Error('native-voice-client-output-limit'));stop();return;}
      chunks.push(Buffer.from(chunk));
    });
    child.once('error',()=>finish(Error('native-voice-client-start-failed')));
    child.stdin.on('error',()=>{finish(Error('native-voice-client-write-failed'));stop();});
    child.once('close',code=>{
      if(settled)return;let value;
      try{value=JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch{finish(Error('native-voice-client-invalid-output'));return;}
      if(code!==0||value.ok!==true){
        const fixed=typeof value.code==='string'&&/^[a-z][a-z0-9-]{1,100}$/.test(value.code)?value.code:'native-voice-client-failed';
        finish(Error(fixed));return;
      }
      finish(null,value.value);
    });
    try{child.stdin.end(input===undefined?'':JSON.stringify(input));}
    catch{finish(Error('native-voice-client-write-failed'));stop();}
  });
  const io=(args,signal)=>call(python,['-u','-X','utf8',path.join(root,'native_voice_io.py'),'--root',dataRoot],args,signal);
  return {
    status({signal}={}){return call(decoderPython,['-u','-X','utf8',path.join(root,'native_voice_decode.py'),'--status'],undefined,signal);},
    async catalog(allowedChatKeys,{snapshotId,signal}={}){
      if(!Array.isArray(allowedChatKeys)||allowedChatKeys.length>1000||new Set(allowedChatKeys).size!==allowedChatKeys.length
        ||allowedChatKeys.some(k=>typeof k!=='string'||!/^wechat:[^\s/\\\0]{1,256}$/.test(k)))throw Error('native-voice-catalog-allowlist-invalid');
      const keys=[...allowedChatKeys].sort(),scanKey=randomBytes(32).toString('hex'),seen=new Set(),cursors=new Set(),voices=[];
      let cursor=null,fixedSnapshot=snapshotId,account=null,scope=null,bytes=0,previous={descriptorIndex:0,rowid:0};
      for(let count=0;count<maxCatalogPages;count++){
        const value=await io({op:'catalog-page',allowedChatKeys:keys,scanKey,pageSize:500,
          ...(fixedSnapshot?{snapshotId:fixedSnapshot}:{}),...(cursor?{cursor}:{})},signal);
        if(!value||typeof value!=='object'||Array.isArray(value))throw Error('native-voice-catalog-page-invalid');
        bytes+=Buffer.byteLength(JSON.stringify(value));if(bytes>maxCatalogBytes)throw Error('native-voice-catalog-total-byte-limit');
        if(value?.source!=='authenticated-native-voice-catalog-page'||typeof value.accountId!=='string'||!value.accountId
          ||typeof value.snapshotId!=='string'||!value.snapshotId||!(/^[a-f0-9]{64}$/.test(value.scopeDigest??''))
          ||(fixedSnapshot&&fixedSnapshot!==value.snapshotId)||(account&&account!==value.accountId)||(scope&&scope!==value.scopeDigest)
          ||!Array.isArray(value.voices)||!Array.isArray(value.observedServerIds)||!Number.isSafeInteger(value.scanned)||value.scanned<0||value.scanned>500
          ||value.observedServerIds.length!==value.scanned||typeof value.complete!=='boolean'
          ||(value.complete?value.nextCursor!==null:typeof value.nextCursor!=='string'||!value.nextCursor||value.nextCursor.length>4096))throw Error('native-voice-catalog-page-invalid');
        fixedSnapshot=value.snapshotId;account=value.accountId;scope=value.scopeDigest;
        const position=value.position;
        if(!position||!Number.isSafeInteger(position.descriptorIndex)||position.descriptorIndex<0||!Number.isSafeInteger(position.rowid)||position.rowid<0
          ||(cursor&&(position.descriptorIndex<previous.descriptorIndex||(position.descriptorIndex===previous.descriptorIndex&&position.rowid<=previous.rowid)))
          ||(!value.complete&&position.descriptorIndex===previous.descriptorIndex&&position.rowid<=previous.rowid))throw Error('native-voice-catalog-cursor-not-advancing');
        const pageIds=new Set();
        for(const native of value.observedServerIds){
          if(typeof native!=='string'||!/^wechat-server:[1-9][0-9]*$/.test(native)||seen.has(native)||pageIds.has(native))throw Error('native-voice-catalog-native-id-ambiguous');
          pageIds.add(native);seen.add(native);
        }
        const inputIds=new Set();
        for(const v of value.voices){
          if(!v||!pageIds.has(v.nativeId)||inputIds.has(v.nativeId)||v.nativeId!=='wechat-server:'+v.serverId||!keys.includes(v.nativeChatKey)||v.direction!=='incoming')throw Error('native-voice-catalog-voice-invalid');
          inputIds.add(v.nativeId);voices.push(v);if(voices.length>100000)throw Error('native-voice-catalog-identity-limit');
        }
        if(value.complete)return {source:'authenticated-native-voice-catalog',accountId:account,snapshotId:fixedSnapshot,
          voices:voices.sort((a,b)=>a.timestampSeconds-b.timestampSeconds||a.nativeChatKey.localeCompare(b.nativeChatKey)||a.localId-b.localId)};
        if(cursors.has(value.nextCursor)||value.nextCursor===cursor)throw Error('native-voice-catalog-cursor-loop');
        cursors.add(value.nextCursor);cursor=value.nextCursor;previous=position;
      }
      throw Error('native-voice-catalog-page-limit');
    },
    extract(claim,allowedChatKeys,{signal}={}){return io({op:'extract',allowedChatKeys,snapshotId:claim.snapshotId,
      nativeChatKey:claim.nativeChatKey,serverId:claim.serverId},signal);},
    decode(extracted,{signal}={}){
      return call(decoderPython,['-u','-X','utf8',path.join(root,'native_voice_decode.py'),'--data-root',dataRoot,
        '--source',extracted.file,'--expected-sha256',extracted.metadata.sha256,
        '--duration-ms',String(extracted.metadata.durationMilliseconds)],undefined,signal);
    },
    close(){closed=true;for(const cancel of [...running])cancel();},
  };
}
