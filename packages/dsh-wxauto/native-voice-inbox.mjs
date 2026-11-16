// Private durable native voice identities. No model/UI calls or source matching by text.
import fs from 'node:fs';
import path from 'node:path';
import {createHash,randomUUID} from 'node:crypto';

const sha=x=>createHash('sha256').update(JSON.stringify(x)).digest('hex');
const MAX_BYTES=64*1024*1024;
const validString=(s,max=1000)=>typeof s==='string'&&s.length>0&&s.length<=max&&!s.includes('\0');
const nativeKey=k=>typeof k==='string'&&/^wechat:[A-Za-z0-9_-]+(?:@chatroom)?$/.test(k);
const identity=(account,v)=>[account,v.nativeChatKey,v.nativeId,v.serverId,v.senderId,v.rawText,v.timestampSeconds,v.localId];
const claimId=(account,v)=>sha(identity(account,v)).slice(0,32);
const immutableEqual=(account,a,b)=>sha(identity(account,a))===sha(identity(account,b));

export function createNativeVoiceInbox({dataRoot,accountId,maxIdentities=100000}) {
  if(!path.isAbsolute(dataRoot)||!validString(accountId)||!Number.isSafeInteger(maxIdentities)||maxIdentities<1||maxIdentities>100000)throw Error('native-voice-inbox-config-invalid');
  const folder=path.join(dataRoot,'config'),file=path.join(folder,'native-voice-inbox.json'),lock=file+'.lock';
  function checkParents(){
    for(let p=folder;;p=path.dirname(p)){
      if(fs.existsSync(p)&&fs.lstatSync(p).isSymbolicLink())throw Error('native-voice-inbox-link-invalid');
      if(path.dirname(p)===p)break;
    }
  }
  function validateVoice(v){
    if(!v||!nativeKey(v.nativeChatKey)
      ||!(/^[1-9][0-9]*$/.test(v.serverId??''))||typeof v.serverId!=='string'||v.serverId.length>30
      ||v.nativeId!=='wechat-server:'+v.serverId||v.direction!=='incoming'
      ||!validString(v.senderId)||!validString(v.senderDisplayName)
      ||typeof v.rawText!=='string'||v.rawText.length>64000
      ||!Number.isSafeInteger(v.timestampSeconds)||v.timestampSeconds<=0
      ||!Number.isSafeInteger(v.localId)||v.localId<=0||!validString(v.snapshotSourceId,4000))throw Error('native-voice-inbox-voice-invalid');
    return {nativeId:v.nativeId,serverId:v.serverId,nativeChatKey:v.nativeChatKey,senderId:v.senderId,
      senderDisplayName:v.senderDisplayName,rawText:v.rawText,timestampSeconds:v.timestampSeconds,
      localId:v.localId,snapshotSourceId:v.snapshotSourceId,direction:'incoming'};
  }
  function catalog(value){
    if(value?.source!=='authenticated-native-voice-catalog'||value.accountId!==accountId
      ||!validString(value.snapshotId)||!Array.isArray(value.voices)||value.voices.length>maxIdentities)throw Error('native-voice-inbox-catalog-invalid');
    const voices=value.voices.map(validateVoice),ids=new Set();
    for(const v of voices){if(ids.has(v.serverId))throw Error('native-voice-inbox-duplicate-native-id');ids.add(v.serverId);}
    return {snapshotId:value.snapshotId,voices};
  }
  function read(){
    checkParents();let info;
    try{info=fs.lstatSync(file);}catch(e){if(e.code==='ENOENT')return null;throw e;}
    if(!info.isFile()||info.isSymbolicLink()||info.nlink!==1||info.size>MAX_BYTES)throw Error('native-voice-inbox-file-invalid');
    let fd;
    try{
      fd=fs.openSync(file,'r');const opened=fs.fstatSync(fd),text=fs.readFileSync(fd,'utf8'),after=fs.fstatSync(fd),current=fs.lstatSync(file);
      const sig=s=>[s.dev,s.ino,s.size,s.mtimeMs,s.nlink];
      if([opened,after,current].some(s=>JSON.stringify(sig(s))!==JSON.stringify(sig(info)))||current.isSymbolicLink())throw Error('native-voice-inbox-file-changed');
      let value;
      try{value=JSON.parse(text);}catch{throw Error('native-voice-inbox-state-invalid');}
      if(!value||typeof value!=='object'||Array.isArray(value))throw Error('native-voice-inbox-state-invalid');
      const {digest,...body}=value;
      if(body.format!=='whale-native-voice-inbox'||body.version!==1||body.accountId!==accountId
        ||!Number.isSafeInteger(body.revision)||body.revision<1||!Array.isArray(body.claims)||body.claims.length>maxIdentities
        ||digest!==sha(body))throw Error('native-voice-inbox-state-invalid');
      const ids=new Set();
      for(const c of body.claims){
        validateVoice(c);
        if(!validString(c.snapshotId)||!['baseline','queued','submitted'].includes(c.state)||c.claimId!==claimId(accountId,c)||ids.has(c.serverId)
          ||!Number.isSafeInteger(c.createdAt)||c.createdAt<=0
          ||(c.state==='submitted'&&(!Number.isSafeInteger(c.submittedAt)||c.submittedAt<=0)))throw Error('native-voice-inbox-state-invalid');
        ids.add(c.serverId);
      }
      // Old v1 files retain their original digest. Infer only chats with actual
      // recorded identities; zero-voice routes require an explicit adoption.
      if(body.chatBaselines===undefined)body.chatBaselines=[...new Set(body.claims.map(c=>c.nativeChatKey))].sort();
      if(!Array.isArray(body.chatBaselines)||body.chatBaselines.length>1000
        ||body.chatBaselines.some(k=>!nativeKey(k))||new Set(body.chatBaselines).size!==body.chatBaselines.length
        ||body.claims.some(c=>!body.chatBaselines.includes(c.nativeChatKey)))throw Error('native-voice-inbox-state-invalid');
      return body;
    }finally{if(fd!==undefined)fs.closeSync(fd);}
  }
  function write(value){
    checkParents();const text=JSON.stringify({...value,digest:sha(value)});
    if(Buffer.byteLength(text)>MAX_BYTES)throw Error('native-voice-inbox-capacity-exceeded');
    const temp=file+'.'+randomUUID()+'.tmp';let fd;
    try{
      fd=fs.openSync(temp,'wx',0o600);fs.writeFileSync(fd,text);fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;
      // Last-moment validation prevents replacing an unexpected link/state.
      read();fs.renameSync(temp,file);
    }finally{if(fd!==undefined)fs.closeSync(fd);if(fs.existsSync(temp))fs.unlinkSync(temp);}
  }
  function mutate(work){
    checkParents();fs.mkdirSync(folder,{recursive:true});checkParents();let fd;
    try{
      try{fd=fs.openSync(lock,'wx',0o600);}catch(e){if(e.code==='EEXIST')throw Error('native-voice-inbox-busy');throw e;}
      return work(read());
    }finally{if(fd!==undefined){fs.closeSync(fd);fs.unlinkSync(lock);}}
  }
  const create=(v,snapshotId,state)=>({...v,snapshotId,claimId:claimId(accountId,v),state,createdAt:Date.now()});
  return {
    status(){const value=read();return {initialized:!!value,revision:value?.revision??0,identities:value?.claims.length??0,pending:value?.claims.filter(c=>c.state==='queued').length??0,chatBaselines:structuredClone(value?.chatBaselines??[])};},
    hasChat(nativeChatKey){if(!nativeKey(nativeChatKey))throw Error('native-voice-inbox-chat-invalid');return read()?.chatBaselines.includes(nativeChatKey)??false;},
    lookup(nativeChatKey,serverIds){
      if(!nativeKey(nativeChatKey)||!Array.isArray(serverIds)||new Set(serverIds).size!==serverIds.length
        ||serverIds.some(id=>typeof id!=='string'||!/^[1-9][0-9]*$/.test(id)))throw Error('native-voice-inbox-lookup-invalid');
      const claims=new Map((read()?.claims??[]).filter(c=>c.nativeChatKey===nativeChatKey).map(c=>[c.serverId,c]));
      return serverIds.map(id=>{
        const c=claims.get(id);if(!c)throw Error('native-voice-inbox-lookup-missing');
        return {claimId:c.claimId,serverId:c.serverId,nativeId:c.nativeId,nativeChatKey:c.nativeChatKey,state:c.state};
      });
    },
    initialize(value,{nativeChatKeys}={}){const input=catalog(value);
      const keys=nativeChatKeys??[...new Set(input.voices.map(v=>v.nativeChatKey))];
      if(!Array.isArray(keys)||keys.length>1000||keys.some(k=>!nativeKey(k))||new Set(keys).size!==keys.length
        ||input.voices.some(v=>!keys.includes(v.nativeChatKey)))throw Error('native-voice-inbox-baseline-chats-invalid');
      return mutate(old=>{
      if(old)throw Error('native-voice-inbox-already-initialized');
      const state={format:'whale-native-voice-inbox',version:1,accountId,revision:1,chatBaselines:[...keys].sort(),claims:input.voices.map(v=>create(v,input.snapshotId,'baseline'))};
      write(state);return {initialized:true,baselined:state.claims.length};
    });},
    enqueue(value){const input=catalog(value);return mutate(old=>{
      if(!old)throw Error('native-voice-inbox-initialize-required');
      const state=structuredClone(old),byId=new Map(state.claims.map(c=>[c.serverId,c]));let queued=0,refreshed=0,ignored=0;
      for(const v of input.voices){
        const previous=byId.get(v.serverId);
        if(previous&&!immutableEqual(accountId,previous,v))throw Error('native-voice-inbox-native-identity-conflict');
        if(!state.chatBaselines.includes(v.nativeChatKey))throw Error('native-voice-inbox-chat-not-adopted');
        if(previous){
          if(previous.state==='queued'&&(previous.snapshotId!==input.snapshotId||previous.snapshotSourceId!==v.snapshotSourceId)){
            previous.snapshotId=input.snapshotId;previous.snapshotSourceId=v.snapshotSourceId;refreshed++;
          }else ignored++;
        }else{
          const claim=create(v,input.snapshotId,'queued');state.claims.push(claim);byId.set(v.serverId,claim);queued++;
          if(state.claims.length>maxIdentities)throw Error('native-voice-inbox-capacity-exceeded');
        }
      }
      if(queued||refreshed){state.revision++;write(state);}
      return {queued,refreshed,ignored};
    });},
    adoptChat(value,{nativeChatKey,unreadServerIds}={}){
      const input=catalog(value);
      if(!nativeKey(nativeChatKey)||!Array.isArray(unreadServerIds)||unreadServerIds.length>maxIdentities
        ||unreadServerIds.some(id=>typeof id!=='string'||!/^[1-9][0-9]*$/.test(id)||id.length>30)
        ||new Set(unreadServerIds).size!==unreadServerIds.length
        ||input.voices.some(v=>v.nativeChatKey!==nativeChatKey))throw Error('native-voice-inbox-adoption-invalid');
      const present=new Set(input.voices.map(v=>v.serverId)),unread=new Set(unreadServerIds);
      if(unreadServerIds.some(id=>!present.has(id)))throw Error('native-voice-inbox-unread-source-missing');
      return mutate(old=>{
        if(!old)throw Error('native-voice-inbox-initialize-required');
        const state=structuredClone(old),byId=new Map(state.claims.map(c=>[c.serverId,c]));let baselined=0,queued=0,preserved=0;
        const adopted=!state.chatBaselines.includes(nativeChatKey);
        if(adopted){state.chatBaselines.push(nativeChatKey);state.chatBaselines.sort();if(state.chatBaselines.length>1000)throw Error('native-voice-inbox-capacity-exceeded');}
        for(const v of input.voices){
          const previous=byId.get(v.serverId);
          if(previous){
            if(!immutableEqual(accountId,previous,v))throw Error('native-voice-inbox-native-identity-conflict');
            // Adoption never resets or refreshes an existing identity/state.
            preserved++;
          }else{
            const stateName=unread.has(v.serverId)?'queued':'baseline';
            const claim=create(v,input.snapshotId,stateName);state.claims.push(claim);byId.set(v.serverId,claim);
            if(state.claims.length>maxIdentities)throw Error('native-voice-inbox-capacity-exceeded');
            if(stateName==='queued')queued++;else baselined++;
          }
        }
        if(adopted||queued||baselined){state.revision++;write(state);}
        return {adopted,baselined,queued,preserved};
      });
    },
    pending(){return structuredClone(read()?.claims.filter(c=>c.state==='queued')??[]);},
    submit(claimIds,{requireAllQueued=false}={}){
      if(typeof requireAllQueued!=='boolean')throw Error('native-voice-inbox-claims-invalid');
      if(!Array.isArray(claimIds)||claimIds.length>maxIdentities||new Set(claimIds).size!==claimIds.length||claimIds.some(id=>!/^[a-f0-9]{32}$/.test(id)))throw Error('native-voice-inbox-claims-invalid');
      return mutate(old=>{
        if(!old)throw Error('native-voice-inbox-initialize-required');
        const state=structuredClone(old),claims=new Map(state.claims.map(c=>[c.claimId,c]));let submitted=0;
        if(requireAllQueued)for(const id of claimIds){const c=claims.get(id);
          if(!c||c.state==='baseline')throw Error('native-voice-inbox-claim-not-queued');
          if(c.state==='submitted')throw Error('native-voice-inbox-claim-already-submitted');}
        for(const id of claimIds){const c=claims.get(id);if(!c||c.state==='baseline')throw Error('native-voice-inbox-claim-not-queued');
          if(c.state==='queued'){c.state='submitted';c.submittedAt=Date.now();submitted++;}}
        // Persist BEFORE the caller asks Harness. Unknown outcomes remain submitted forever.
        if(submitted){state.revision++;write(state);}return {submitted};
      });
    },
  };
}
