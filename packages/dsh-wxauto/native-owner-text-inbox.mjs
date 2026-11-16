// Staged owner-text authority. Import/construction do no IO; initialization is
// explicit and is NOT a production cutover. No UI/text similarity matching.
import fs from 'node:fs';
import path from 'node:path';
import {createHash,randomUUID} from 'node:crypto';

const hash=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const fail=code=>{throw Error('native-owner-text-inbox-'+code);};
const string=(v,max=4000)=>typeof v==='string'&&v.length>0&&v.length<=max&&!v.includes('\0');
const key=v=>typeof v==='string'&&/^wechat:[A-Za-z0-9_-]+$/.test(v);
const hex=v=>typeof v==='string'&&/^[a-f0-9]{32}$/.test(v);
const signature=s=>JSON.stringify([s.dev,s.ino,s.size,s.mtimeMs,s.ctimeMs,s.nlink]);
const MAX_BYTES=64*1024*1024;
export const ownerTextScopeDigest=(accountId,nativeChatKey,peer)=>hash([accountId,nativeChatKey,peer]);

export function createNativeOwnerTextInbox({dataRoot,accountId,ownerNativeChatKey,expectedPeerUsername,
  maxIdentities=100000,now=Date.now}={}){
  if(!string(dataRoot)||!path.isAbsolute(dataRoot)||!string(accountId)||!key(ownerNativeChatKey)
    ||!string(expectedPeerUsername)||ownerNativeChatKey!=='wechat:'+expectedPeerUsername
    ||!Number.isSafeInteger(maxIdentities)||maxIdentities<1||maxIdentities>100000||typeof now!=='function')fail('config-invalid');
  const scopeDigest=ownerTextScopeDigest(accountId,ownerNativeChatKey,expectedPeerUsername);
  const folder=path.join(dataRoot,'config'),file=path.join(folder,'native-owner-text-inbox.json'),lock=file+'.lock';
  const clock=()=>{const v=now();if(!Number.isSafeInteger(v)||v<=0)fail('clock-invalid');return v;};
  function parents(){
    for(let p=folder;;p=path.dirname(p)){
      let s;try{s=fs.lstatSync(p);}catch(e){if(e.code!=='ENOENT')throw e;}
      if(s&&(!s.isDirectory()||s.isSymbolicLink()))fail('link-invalid');
      if(path.dirname(p)===p)break;
    }
  }
  function message(v){
    if(!v||v.nativeChatKey!==ownerNativeChatKey||v.senderId!==expectedPeerUsername||v.direction!=='incoming'
      ||typeof v.serverId!=='string'||!(/^[1-9][0-9]{0,29}$/.test(v.serverId))||v.nativeId!=='wechat-server:'+v.serverId
      ||!string(v.senderDisplayName)||typeof v.rawText!=='string'||!v.rawText.trim()||v.rawText.length>64000||v.rawText.includes('\0')
      ||!Number.isSafeInteger(v.timestampSeconds)||v.timestampSeconds<=0||!Number.isSafeInteger(v.localId)||v.localId<=0
      ||!string(v.snapshotSourceId))fail('message-invalid');
    return Object.fromEntries(['nativeId','serverId','nativeChatKey','senderId','senderDisplayName','rawText','timestampSeconds',
      'localId','snapshotSourceId','direction'].map(k=>[k,v[k]]));
  }
  // Display names and snapshot lineage may change; authenticated source identity,
  // text, timestamp and local identity may not change for an existing native ID.
  const identity=v=>[accountId,v.nativeChatKey,v.nativeId,v.serverId,v.senderId,v.rawText,v.timestampSeconds,v.localId];
  const claimId=v=>hash(identity(v)).slice(0,32);
  function catalog(v){
    if(v?.source!=='authenticated-native-owner-text-catalog'||v.complete!==true||v.accountId!==accountId
      ||v.nativeChatKey!==ownerNativeChatKey||v.scopeDigest!==scopeDigest||!string(v.snapshotId)
      ||!Array.isArray(v.messages))fail('catalog-invalid');
    if(v.messages.length>maxIdentities)fail('capacity-exceeded');
    const messages=v.messages.map(message),seen=new Set();
    for(const m of messages){if(seen.has(m.nativeId))fail('duplicate-native-id');seen.add(m.nativeId);}
    return {snapshotId:v.snapshotId,messages};
  }
  function read(){
    parents();let info;try{info=fs.lstatSync(file);}catch(e){if(e.code==='ENOENT')return null;throw e;}
    if(!info.isFile()||info.isSymbolicLink()||info.nlink!==1||info.size>MAX_BYTES)fail('file-invalid');
    const fd=fs.openSync(file,'r');let text;
    try{
      const opened=fs.fstatSync(fd);text=fs.readFileSync(fd,'utf8');
      if([opened,fs.fstatSync(fd),fs.lstatSync(file)].some(s=>signature(s)!==signature(info)))fail('file-changed');
    }finally{fs.closeSync(fd);}
    let v;try{v=JSON.parse(text);}catch{fail('state-invalid');}
    if(!v||typeof v!=='object'||Array.isArray(v))fail('state-invalid');
    const {digest,...body}=v;
    if(digest!==hash(body)||body.format!=='whale-native-owner-text-inbox'||body.version!==1||body.accountId!==accountId
      ||body.ownerNativeChatKey!==ownerNativeChatKey||body.expectedPeerUsername!==expectedPeerUsername||body.scopeDigest!==scopeDigest
      ||!hex(body.authorityEpoch)||!Number.isSafeInteger(body.cutoverAtMs)||body.cutoverAtMs<=0
      ||body.cutoverFloorSeconds!==Math.ceil(body.cutoverAtMs/1000)||!Number.isSafeInteger(body.revision)||body.revision<1
      ||!Array.isArray(body.claims)||body.claims.length>maxIdentities)fail('state-invalid');
    if(body.baselineSnapshotId!==undefined||body.baselineCatalogDigest!==undefined){
      if(!string(body.baselineSnapshotId)||typeof body.baselineCatalogDigest!=='string'||!/^[a-f0-9]{64}$/.test(body.baselineCatalogDigest))fail('state-invalid');
    }
    const seen=new Set();
    for(const c of body.claims){
      message(c);
      if(seen.has(c.nativeId)||c.claimId!==claimId(c)||!['baseline','held','queued','submitted'].includes(c.state)
        ||!string(c.snapshotId)||!Number.isSafeInteger(c.createdAt)||c.createdAt<=0
        ||(c.state==='held'&&!['late-history','future-timestamp'].includes(c.holdReason))
        ||(c.state==='submitted'&&(!Number.isSafeInteger(c.submittedAt)||c.submittedAt<=0)))fail('state-invalid');
      seen.add(c.nativeId);
    }
    return {body,token:hash([signature(info),text])};
  }
  function write(body,expected){
    parents();const text=JSON.stringify({...body,digest:hash(body)});
    if(Buffer.byteLength(text)>MAX_BYTES)fail('capacity-exceeded');
    const temp=file+'.'+randomUUID()+'.tmp';let fd;
    try{
      fd=fs.openSync(temp,'wx',0o600);fs.writeFileSync(fd,text);fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;
      if((read()?.token??null)!==expected)fail('state-changed');
      fs.renameSync(temp,file);
      // Windows does not expose directory fsync consistently; file fsync above
      // is mandatory and POSIX directory durability is additionally requested.
      if(process.platform!=='win32'){const dir=fs.openSync(folder,'r');try{fs.fsyncSync(dir);}finally{fs.closeSync(dir);}}
    }finally{if(fd!==undefined)fs.closeSync(fd);if(fs.existsSync(temp))fs.unlinkSync(temp);}
  }
  function mutate(work){
    parents();fs.mkdirSync(folder,{recursive:true});parents();let fd,lockInfo;
    try{
      try{fd=fs.openSync(lock,'wx',0o600);}catch(e){if(e.code==='EEXIST')fail('busy');throw e;}
      lockInfo=fs.fstatSync(fd);return work(read());
    }finally{if(fd!==undefined){fs.closeSync(fd);const current=fs.lstatSync(lock);
      if(current.isSymbolicLink()||current.nlink!==1||signature(current)!==signature(lockInfo))fail('lock-changed');fs.unlinkSync(lock);}}
  }
  const make=(m,snapshotId,state,createdAt,holdReason)=>({...m,snapshotId,claimId:claimId(m),state,createdAt,...(holdReason?{holdReason}:{})});
  return Object.freeze({
    status(){const b=read()?.body;return {initialized:!!b,revision:b?.revision??0,authorityEpoch:b?.authorityEpoch??null,
      cutoverAtMs:b?.cutoverAtMs??null,cutoverFloorSeconds:b?.cutoverFloorSeconds??null,identities:b?.claims.length??0,
      baseline:b?.claims.filter(c=>c.state==='baseline').length??0,held:b?.claims.filter(c=>c.state==='held').length??0,
      pending:b?.claims.filter(c=>c.state==='queued').length??0,submitted:b?.claims.filter(c=>c.state==='submitted').length??0};},
    initialize(value,{cutoverAtMs}={}){
      const input=catalog(value);if(!Number.isSafeInteger(cutoverAtMs)||cutoverAtMs<=0||cutoverAtMs>clock())fail('cutover-invalid');
      return mutate(old=>{if(old)fail('already-initialized');const at=clock(),authorityEpoch=randomUUID().replaceAll('-','');
        const body={format:'whale-native-owner-text-inbox',version:1,accountId,ownerNativeChatKey,expectedPeerUsername,scopeDigest,
          authorityEpoch,cutoverAtMs,cutoverFloorSeconds:Math.ceil(cutoverAtMs/1000),revision:1,
          baselineSnapshotId:input.snapshotId,baselineCatalogDigest:hash([input.snapshotId,[...input.messages].sort((a,b)=>a.nativeId<b.nativeId?-1:a.nativeId>b.nativeId?1:0)]),
          claims:input.messages.map(m=>make(m,input.snapshotId,'baseline',at))};
        write(body,null);return {initialized:true,baselined:body.claims.length,authorityEpoch};});
    },
    enqueue(value){const input=catalog(value);return mutate(old=>{
      if(!old)fail('initialize-required');const b=structuredClone(old.body),known=new Map(b.claims.map(c=>[c.nativeId,c]));
      let queued=0,held=0,refreshed=0,ignored=0;const at=clock();
      for(const m of input.messages){
        const previous=known.get(m.nativeId);
        if(previous){
          if(hash(identity(previous))!==hash(identity(m)))fail('identity-conflict');
          if(previous.snapshotId!==input.snapshotId||previous.snapshotSourceId!==m.snapshotSourceId){
            previous.snapshotId=input.snapshotId;previous.snapshotSourceId=m.snapshotSourceId;refreshed++;
          }else ignored++;
        }else{
          const reason=m.timestampSeconds<b.cutoverFloorSeconds?'late-history':m.timestampSeconds>Math.floor(at/1000)+30?'future-timestamp':null;
          const c=make(m,input.snapshotId,reason?'held':'queued',at,reason);b.claims.push(c);known.set(m.nativeId,c);reason?held++:queued++;
          if(b.claims.length>maxIdentities)fail('capacity-exceeded');
        }
      }
      if(queued||held||refreshed){b.revision++;write(b,old.token);}return {queued,held,refreshed,ignored};
    });},
    pending(){return structuredClone(read()?.body.claims.filter(c=>c.state==='queued')??[]);},
    submit(claimIds,{requireAllQueued=false}={}){
      if(typeof requireAllQueued!=='boolean')fail('claims-invalid');
      if(!Array.isArray(claimIds)||claimIds.length>maxIdentities||new Set(claimIds).size!==claimIds.length||claimIds.some(id=>!hex(id)))fail('claims-invalid');
      return mutate(old=>{if(!old)fail('initialize-required');const b=structuredClone(old.body),known=new Map(b.claims.map(c=>[c.claimId,c]));let submitted=0;
        if(requireAllQueued)for(const id of claimIds){
          const c=known.get(id);if(!c||!['queued','submitted'].includes(c.state))fail('claim-not-queued');
          if(c.state==='submitted')fail('claim-already-submitted');
        }
        for(const id of claimIds){const c=known.get(id);if(!c||!['queued','submitted'].includes(c.state))fail('claim-not-queued');
          if(c.state==='queued'){c.state='submitted';c.submittedAt=clock();submitted++;}}
        if(submitted){b.revision++;write(b,old.token);}return {submitted};
      });
    },
  });
}
