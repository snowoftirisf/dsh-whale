// Durable one-time submission of authenticated native unread messages.
// UI observations are not merged by text/time; native server IDs retain identity.
import fs from 'node:fs';
import path from 'node:path';
import {createHash,randomUUID} from 'node:crypto';

const hash=x=>createHash('sha256').update(x).digest('hex');
const messageIdentity=m=>[m.nativeId,m.serverId,m.senderId,m.senderDisplayName,m.type,m.text,m.timestampSeconds,m.contextOnly];
export const nativeGroupClaimHash=value=>hash(JSON.stringify([value.accountId,value.nativeChatKey,value.target,
  value.sessionId,value.claimId,value.snapshotId,value.serverIds,value.messages.map(messageIdentity)]));
const delegationHash=(value,voices)=>hash(JSON.stringify([value.accountId,value.nativeChatKey,value.claimId,value.claimHash,voices]));
function validateDelegation(value,{required=false}={}){
  const voices=value.messages.filter(m=>m.type==='voice'),receipt=value.voiceDelegation;
  if(!receipt){if(required&&voices.length)throw Error('native-group-voice-delegation-required');return;}
  if(receipt.format!=='whale-native-group-voice-delegation'||receipt.version!==1
    ||receipt.claimId!==value.claimId||receipt.claimHash!==value.claimHash||!Array.isArray(receipt.voices)
    ||receipt.voices.length!==voices.length||receipt.digest!==delegationHash(value,receipt.voices))throw Error('native-group-voice-delegation-invalid');
  const ids=new Set(),claims=new Set();
  for(const item of receipt.voices){
    const original=voices.find(m=>m.serverId===item.serverId);
    if(!original||item.nativeId!==original.nativeId||item.nativeChatKey!==value.nativeChatKey
      ||!/^([a-f0-9]{32})$/.test(item.claimId??'')||ids.has(item.serverId)||claims.has(item.claimId)
      ||!['queued','submitted','baseline'].includes(item.state))throw Error('native-group-voice-delegation-invalid');
    ids.add(item.serverId);claims.add(item.claimId);
  }
}

export function createNativeGroupInbox({dataRoot,accountId}) {
  const folder=path.join(dataRoot,'config','auto-group-inbox');
  const fileFor=key=>path.join(folder,hash(accountId+'\0'+key)+'.json');
  const write=(file,value)=>{
    fs.mkdirSync(folder,{recursive:true});
    const temporary=file+'.'+randomUUID()+'.tmp';let fd;
    try{fd=fs.openSync(temporary,'wx');fs.writeFileSync(fd,JSON.stringify(value,null,2));fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;fs.renameSync(temporary,file);}
    finally{if(fd!==undefined)fs.closeSync(fd);}
  };
  const read=record=>{
    const file=fileFor(record.nativeChatKey);let stat;
    try{stat=fs.lstatSync(file);}catch(error){if(error.code==='ENOENT')return null;throw error;}
    if(!stat.isFile()||stat.isSymbolicLink()||stat.nlink!==1||stat.size>1024*1024)throw Error('native-group-inbox-file-invalid');
    const value=JSON.parse(fs.readFileSync(file,'utf8'));
    if(value.format!=='whale-native-group-inbox'||value.version!==1||value.accountId!==accountId
      ||value.nativeChatKey!==record.nativeChatKey||value.target!==record.target||value.sessionId!==record.sessionId
      ||!['queued','submitted'].includes(value.state)||!Array.isArray(value.messages)
      ||(value.handledServerIds!==undefined&&(!Array.isArray(value.handledServerIds)||value.handledServerIds.some(id=>!/^[1-9][0-9]*$/.test(id))))
      ||value.claimHash!==nativeGroupClaimHash(value))throw Error('native-group-inbox-invalid');
    validateDelegation(value);
    return value;
  };
  return {
    read,
    enqueue(record,unread) {
      if(unread?.state!=='ready'||unread.source!=='authenticated-wechat-native-unread'||unread.accountId!==accountId
        ||unread.nativeChatKey!==record.nativeChatKey||!Array.isArray(unread.messages)||unread.messages.length<1
        ||unread.messages.length>20||unread.unreadCount!==unread.messages.length
        ||!Array.isArray(unread.serverIds)||unread.serverIds.length!==unread.messages.length)throw Error('native-group-inbox-source-invalid');
      const ids=new Set();
      for(const m of unread.messages){
        if(!/^[1-9][0-9]*$/.test(m.serverId)||m.nativeId!=='wechat-server:'+m.serverId||ids.has(m.serverId)
          ||!['text','system','voice'].includes(m.type)||m.direction!=='incoming'||typeof m.text!=='string'
          ||!Number.isSafeInteger(m.timestampSeconds)||m.timestampSeconds<=0||typeof m.senderId!=='string'||!m.senderId
          ||typeof m.senderDisplayName!=='string'||!m.senderDisplayName||m.contextOnly!==(m.type==='system'))throw Error('native-group-inbox-message-invalid');
        ids.add(m.serverId);
      }
      if(unread.serverIds.some(id=>!ids.has(id))||new Set(unread.serverIds).size!==ids.size)throw Error('native-group-inbox-id-conflict');
      const old=read(record);
      if(old?.state==='queued'&&old.serverIds.every(id=>ids.has(id))){
        for(const m of old.messages){
          const current=unread.messages.find(x=>x.serverId===m.serverId);
          if(!current||hash(JSON.stringify(messageIdentity(current)))!==hash(JSON.stringify(messageIdentity(m))))throw Error('native-group-inbox-source-changed');
        }
        if(old.serverIds.length===ids.size)return old;
      }else if(old?.state==='queued')throw Error('native-group-inbox-unread-set-changed');
      const value={format:'whale-native-group-inbox',version:1,accountId,nativeChatKey:record.nativeChatKey,
        target:record.target,sessionId:record.sessionId,claimId:randomUUID().replaceAll('-',''),state:'queued',
        snapshotId:unread.snapshotId,unreadCount:unread.unreadCount,serverIds:unread.serverIds,messages:unread.messages,
        handledServerIds:old?.handledServerIds??[]};
      value.claimHash=nativeGroupClaimHash(value);write(fileFor(record.nativeChatKey),value);return value;
    },
    submit(record,claimId) {
      const value=read(record);
      if(!value||value.state!=='queued'||value.claimId!==claimId)return null;
      validateDelegation(value,{required:true});
      // Claim BEFORE Harness submission. A crash/unknown result never replays.
      write(fileFor(record.nativeChatKey),{...value,state:'submitted',submittedAt:Date.now(),
        handledServerIds:[...new Set([...(value.handledServerIds??[]),...value.serverIds])]});
      return value;
    },
    delegateVoices(record,claimId,claims){
      const value=read(record);
      if(!value||value.state!=='queued'||value.claimId!==claimId)throw Error('native-group-voice-claim-not-queued');
      if(!Array.isArray(claims)||claims.some(c=>!c||typeof c!=='object'))throw Error('native-group-voice-delegation-invalid');
      const voices=claims.map(c=>({claimId:c.claimId,serverId:c.serverId,nativeId:c.nativeId,nativeChatKey:c.nativeChatKey,state:c.state}));
      const receipt={format:'whale-native-group-voice-delegation',version:1,claimId,claimHash:value.claimHash,voices,digest:delegationHash(value,voices)};
      const next={...value,voiceDelegation:receipt};validateDelegation(next,{required:true});
      // Receipt proves durable ownership only; queued/submitted are not delivery receipts.
      if(JSON.stringify(value.voiceDelegation)!==JSON.stringify(receipt))write(fileFor(record.nativeChatKey),next);
      return next;
    },
    events(record,value,proof) {
      if(value.accountId!==accountId||value.nativeChatKey!==record.nativeChatKey||value.target!==record.target
        ||value.sessionId!==record.sessionId||value.claimHash!==nativeGroupClaimHash(value))throw Error('native-group-inbox-invalid');
      validateDelegation(value,{required:true});
      if(proof?.nativeClaimId!==value.claimId||proof?.nativeClaimHash!==value.claimHash
        ||!Array.isArray(proof.nativeRenderedMessages)||proof.nativeRenderedMessages.length!==value.messages.length)
        throw Error('native-group-render-proof-invalid');
      const rendered=new Map();
      for(const message of proof.nativeRenderedMessages){
        if(typeof message?.text!=='string'||message.text.length>32000||rendered.has(message.nativeId)
          ||!value.messages.some(m=>m.nativeId===message.nativeId))throw Error('native-group-render-proof-invalid');
        rendered.set(message.nativeId,message.text);
      }
      return value.messages.filter(m=>m.type!=='voice'&&!(value.handledServerIds??[]).includes(m.serverId)).map(m=>({id:'native-unread:'+hash(accountId+'\0'+record.nativeChatKey)+':'+m.serverId,
        target:record.target,kind:'group',sender:m.senderDisplayName,text:rendered.get(m.nativeId),nativeSourceText:m.text,
        timestamp:m.timestampSeconds*1000,mediaType:m.type,sourceKind:'wechat-native-unread',nativeId:m.nativeId,
        nativeSenderId:m.senderId,nativeSnapshotId:value.snapshotId,contextOnly:m.contextOnly,images:[],files:[]}));
    },
  };
}
