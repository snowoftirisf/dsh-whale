// Staged per-native-group inbox; construction is inert. No UI/model/cutover.
import fs from 'node:fs';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {groupTextHash as hash,groupTextString as string,nativeGroupKey,groupTextHex as hex,
 nativeGroupTextPaths,nativeGroupTextScopeDigest,normalizeNativeGroupTextCatalog as normalizeCatalog,
 normalizeNativeGroupTextMessage as message,normalizeNativeGroupTextUnresolved as unresolved,
 nativeGroupTextClaimId,nativeGroupTextMessageIdentity as identity,nativeGroupTextPhysicalIdentity as physical,
 nativeGroupTextSourceRowId,nativeGroupTextCatalogDigest} from './group-text-contract.mjs';
export {nativeGroupTextPaths} from './group-text-contract.mjs';
const fail=code=>{throw Error('native-group-text-inbox-'+code);};
const signature=s=>JSON.stringify([s.dev,s.ino,s.size,s.mtimeMs,s.ctimeMs,s.nlink]);
const MAX_BYTES=64*1024*1024;
export function createNativeGroupTextInbox({dataRoot,accountId,nativeChatKey,maxIdentities=100000,now=Date.now}={}){
 if(!string(accountId)||!nativeGroupKey(nativeChatKey)||!Number.isSafeInteger(maxIdentities)||maxIdentities<1||maxIdentities>100000||typeof now!=='function')fail('config-invalid');
 const {directory:folder,file,lock}=nativeGroupTextPaths({dataRoot,nativeChatKey});
 const scopeDigest=nativeGroupTextScopeDigest(accountId,nativeChatKey);
 const clock=()=>{const value=now();if(!Number.isSafeInteger(value)||value<=0)fail('clock-invalid');return value;};
 const catalog=value=>normalizeCatalog(value,{accountId,nativeChatKey,maxIdentities});
 const claimId=value=>nativeGroupTextClaimId(accountId,nativeChatKey,value.serverId);
  function parents(){
    for(let p=folder;;p=path.dirname(p)){
      let s;try{s=fs.lstatSync(p);}catch(e){if(e.code!=='ENOENT')throw e;}
      if(s&&(!s.isDirectory()||s.isSymbolicLink()))fail('link-invalid');
      if(path.dirname(p)===p)break;
    }
  }
  function read(){
    parents();let info;try{info=fs.lstatSync(file);}catch(e){if(e.code==='ENOENT')return null;throw e;}
    if(!info.isFile()||info.isSymbolicLink()||info.nlink!==1||info.size>MAX_BYTES)fail('file-invalid');
    const fd=fs.openSync(file,'r');let text;
    try{const opened=fs.fstatSync(fd);text=fs.readFileSync(fd,'utf8');
      if([opened,fs.fstatSync(fd),fs.lstatSync(file)].some(s=>signature(s)!==signature(info)))fail('file-changed');
    }finally{fs.closeSync(fd);}
    let value;try{value=JSON.parse(text);}catch{fail('state-invalid');}
    if(!value||typeof value!=='object'||Array.isArray(value))fail('state-invalid');
    const {digest,...body}=value;
    if(digest!==hash(body)||body.format!=='whale-native-group-text-inbox'||body.version!==1||body.accountId!==accountId
      ||body.nativeChatKey!==nativeChatKey||body.scopeDigest!==scopeDigest||!hex(body.authorityEpoch)
      ||!Number.isSafeInteger(body.cutoverAtMs)||body.cutoverAtMs<=0||body.cutoverFloorSeconds!==Math.ceil(body.cutoverAtMs/1000)
      ||!Number.isSafeInteger(body.revision)||body.revision<1||!string(body.baselineSnapshotId)
      ||typeof body.baselineCatalogDigest!=='string'||!/^[a-f0-9]{64}$/.test(body.baselineCatalogDigest)
      ||![body.claims,body.observedNativeIds,body.unresolvedSourceRows].every(Array.isArray)
      ||[body.claims,body.observedNativeIds,body.unresolvedSourceRows].some(a=>a.length>maxIdentities))fail('state-invalid');
    const observed=new Set(body.observedNativeIds),seen=new Set(),sources=new Set();
    if(observed.size!==body.observedNativeIds.length||body.observedNativeIds.some(id=>typeof id!=='string'||!/^wechat-server:[1-9][0-9]{0,29}$/.test(id)))fail('state-invalid');
    for(const c of body.claims){
      if(c.resolution==='resolved')message(c,nativeChatKey);else if(c.resolution==='unresolved')unresolved(c,nativeChatKey);else fail('state-invalid');
      if(c.nativeId===null||!observed.has(c.nativeId)||seen.has(c.nativeId)||c.claimId!==claimId(c)||!['baseline','held','queued','submitted'].includes(c.state)
        ||!['baseline','post-cutover','delegated-unread'].includes(c.firstSeenMode)||!string(c.snapshotId)||!Number.isSafeInteger(c.createdAt)||c.createdAt<=0
        ||c.resolution==='unresolved'&&c.state!=='held'||(c.state==='held'&&!['baseline-unresolved','late-history','future-timestamp','sender-unresolved','previously-observed'].includes(c.holdReason))
        ||c.state==='submitted'&&(!Number.isSafeInteger(c.submittedAt)||c.submittedAt<=0))fail('state-invalid');
      seen.add(c.nativeId);
    }
    for(const c of body.unresolvedSourceRows){
      unresolved(c,nativeChatKey);
      if(c.nativeId!==null||c.sourceRowId!==nativeGroupTextSourceRowId(accountId,c)||sources.has(c.sourceRowId)||!string(c.snapshotId)
        ||!Number.isSafeInteger(c.createdAt)||c.createdAt<=0||!['baseline','post-cutover'].includes(c.firstSeenMode))fail('state-invalid');
      sources.add(c.sourceRowId);
    }
    if(body.admission!==undefined){
      const a=body.admission;
      if(!a||a.version!==1||!hex(a.rawClaimId)||typeof a.rawClaimHash!=='string'||!/^[a-f0-9]{64}$/.test(a.rawClaimHash)
        ||a.snapshotId!==body.baselineSnapshotId||a.authorityEpoch!==body.authorityEpoch
        ||!Array.isArray(a.unreadNativeIds)||new Set(a.unreadNativeIds).size!==a.unreadNativeIds.length
        ||!Array.isArray(a.newTextClaimIds)||new Set(a.newTextClaimIds).size!==a.newTextClaimIds.length
        ||a.newTextClaimIds.length!==a.unreadNativeIds.length)fail('admission-invalid');
      for(const nativeId of a.unreadNativeIds){const c=body.claims.find(c=>c.nativeId===nativeId);
        if(!c||c.resolution!=='resolved'||c.firstSeenMode!=='delegated-unread'||!['queued','submitted'].includes(c.state)
          ||!a.newTextClaimIds.includes(c.claimId))fail('admission-invalid');}
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
      const committed=read();if(!committed||hash(committed.body)!==hash(body))fail('write-not-durable');
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
  const make=(row,snapshotId,state,createdAt,firstSeenMode,holdReason)=>({...row,
    resolution:row.direction==='incoming'?'resolved':'unresolved',snapshotId,claimId:claimId(row),state,createdAt,firstSeenMode,
    ...(holdReason?{holdReason}:{})});
  const sourceMake=(row,snapshotId,createdAt,firstSeenMode)=>({...row,sourceRowId:nativeGroupTextSourceRowId(accountId,row),snapshotId,createdAt,firstSeenMode});
  const checkCapacity=body=>{if([body.claims,body.observedNativeIds,body.unresolvedSourceRows].some(a=>a.length>maxIdentities))fail('capacity-exceeded');};
  function baseline({catalog:value,authorityEpoch,cutoverAtMs,delegatedUnreadNativeIds=[]}={},admission=null){
    const input=catalog(value);
    if(!hex(authorityEpoch)||!Number.isSafeInteger(cutoverAtMs)||cutoverAtMs<=0||cutoverAtMs>clock()
      ||!Array.isArray(delegatedUnreadNativeIds)||new Set(delegatedUnreadNativeIds).size!==delegatedUnreadNativeIds.length
      ||delegatedUnreadNativeIds.some(id=>!input.messages.some(m=>m.nativeId===id)))fail('baseline-invalid');
    return mutate(old=>{
      if(old)fail('already-initialized');const at=clock(),delegated=new Set(delegatedUnreadNativeIds),floor=Math.ceil(cutoverAtMs/1000);
      const claims=input.messages.map(m=>{
        const fresh=delegated.has(m.nativeId),future=m.timestampSeconds>Math.floor(at/1000)+30;
        return make(m,input.snapshotId,fresh?(future?'held':'queued'):'baseline',at,fresh?'delegated-unread':'baseline',fresh&&future?'future-timestamp':null);
      });
      claims.push(...input.unresolved.filter(m=>m.nativeId!==null).map(m=>make(m,input.snapshotId,'held',at,'baseline','baseline-unresolved')));
      const body={format:'whale-native-group-text-inbox',version:1,accountId,nativeChatKey,scopeDigest,authorityEpoch,
        cutoverAtMs,cutoverFloorSeconds:floor,revision:1,baselineSnapshotId:input.snapshotId,baselineCatalogDigest:nativeGroupTextCatalogDigest(input),
        observedNativeIds:input.observedNativeIds,claims,
        unresolvedSourceRows:input.unresolved.filter(m=>m.nativeId===null).map(m=>sourceMake(m,input.snapshotId,at,'baseline')),
        ...(admission?{admission}:{})};
      checkCapacity(body);write(body,null);
      return {initialized:true,authorityEpoch,baselined:claims.filter(c=>c.state==='baseline').length,held:claims.filter(c=>c.state==='held').length,
        queued:claims.filter(c=>c.state==='queued').length,unresolvedSourceRows:body.unresolvedSourceRows.length};
    });
  }
  const api={
    baseline:options=>baseline(options),
    admit({catalog:value,rawClaimId,rawClaimHash,unreadNativeIds,authorityEpoch,cutoverAtMs}={}){
      const input=catalog(value),proof=value?.admission;
      if(!hex(rawClaimId)||typeof rawClaimHash!=='string'||!/^[a-f0-9]{64}$/.test(rawClaimHash)
        ||proof?.verified!==true||proof.claimId!==rawClaimId||proof.claimHash!==rawClaimHash||proof.snapshotId!==input.snapshotId
        ||!Array.isArray(unreadNativeIds)||new Set(unreadNativeIds).size!==unreadNativeIds.length
        ||unreadNativeIds.some(id=>!input.messages.some(m=>m.nativeId===id)))fail('admission-invalid');
      const previous=read()?.body;
      if(previous){
        const a=previous.admission;
        if(a&&a.rawClaimId===rawClaimId&&a.rawClaimHash===rawClaimHash){
          if(a.snapshotId!==input.snapshotId||hash(a.unreadNativeIds)!==hash(unreadNativeIds)
            ||previous.baselineCatalogDigest!==nativeGroupTextCatalogDigest(input))fail('admission-binding-conflict');
          // Repairing the same authorized adoption cannot reset its state.
          return {authorityEpoch:previous.authorityEpoch,newTextClaimIds:[...a.newTextClaimIds],ownershipMode:'new-admission',reused:true};
        }
        // An existing owner is not a new authority. Normal enqueue respects
        // cutover/held/submitted semantics; callers MUST verify actual native
        // authority separately and may not use this receipt to create one.
        api.enqueue(value);
        return {authorityEpoch:previous.authorityEpoch,newTextClaimIds:[],ownershipMode:'existing-inbox',reused:true};
      }
      if(!hex(authorityEpoch))fail('admission-invalid');
      const nativeBoundary=Math.min(...unreadNativeIds.map(id=>input.messages.find(m=>m.nativeId===id).timestampSeconds*1000));
      if(!unreadNativeIds.length||cutoverAtMs!==nativeBoundary||!Number.isSafeInteger(nativeBoundary)||nativeBoundary<=0||nativeBoundary>clock())fail('admission-native-boundary-invalid');
      if(unreadNativeIds.some(id=>input.messages.find(m=>m.nativeId===id).timestampSeconds>Math.floor(clock()/1000)+30))fail('admission-future-source');
      const admission={version:1,rawClaimId,rawClaimHash,snapshotId:input.snapshotId,authorityEpoch,unreadNativeIds:[...unreadNativeIds],
        newTextClaimIds:unreadNativeIds.map(id=>claimId(input.messages.find(m=>m.nativeId===id)))};
      // Same single fsync/atomic inbox write owns both claims AND admission.
      baseline({catalog:value,authorityEpoch,cutoverAtMs,delegatedUnreadNativeIds:unreadNativeIds},admission);
      return {authorityEpoch,newTextClaimIds:[...admission.newTextClaimIds],ownershipMode:'new-admission',reused:false};
    },
    lookup(nativeIds){
      if(!Array.isArray(nativeIds)||new Set(nativeIds).size!==nativeIds.length||nativeIds.length>maxIdentities
        ||nativeIds.some(id=>typeof id!=='string'||!/^wechat-server:[1-9][0-9]{0,29}$/.test(id)))fail('lookup-invalid');
      const body=read()?.body;if(!body)fail('initialize-required');
      return nativeIds.map(id=>{
        const c=body.claims.find(c=>c.nativeId===id);
        if(!c||c.resolution!=='resolved')fail('lookup-missing');
        return structuredClone({...c,authorityEpoch:body.authorityEpoch});
      });
    },
    status(){const body=read()?.body;return {initialized:!!body,accountId,nativeChatKey,scopeDigest,revision:body?.revision??0,
      authorityEpoch:body?.authorityEpoch??null,cutoverAtMs:body?.cutoverAtMs??null,cutoverFloorSeconds:body?.cutoverFloorSeconds??null,
      baselineSnapshotId:body?.baselineSnapshotId??null,baselineCatalogDigest:body?.baselineCatalogDigest??null,stateDigest:body?hash(body):null,
      identities:body?.claims.length??0,observed:body?.observedNativeIds.length??0,
      baseline:body?.claims.filter(c=>c.state==='baseline').length??0,held:body?.claims.filter(c=>c.state==='held').length??0,
      pending:body?.claims.filter(c=>c.state==='queued').length??0,submitted:body?.claims.filter(c=>c.state==='submitted').length??0,
      unresolvedSourceRows:body?.unresolvedSourceRows.length??0};},
    enqueue(value){const input=catalog(value);return mutate(old=>{
      if(!old)fail('initialize-required');const body=structuredClone(old.body),known=new Map(body.claims.map(c=>[c.nativeId,c]));
      const observed=new Set(body.observedNativeIds),oldObserved=new Set(observed),sources=new Map(body.unresolvedSourceRows.map(c=>[c.sourceRowId,c]));
      const at=clock();let changed=false;const result={queued:0,held:0,resolved:0,refreshed:0,ignored:0,unresolvedSourceRows:0};
      for(const row of [...input.messages,...input.unresolved]){
        if(row.nativeId===null){
          const id=nativeGroupTextSourceRowId(accountId,row),previous=sources.get(id);
          if(previous){
            if(hash(physical(previous))!==hash(physical(row)))fail('identity-conflict');
            if(previous.snapshotId!==input.snapshotId){previous.snapshotId=input.snapshotId;changed=true;result.refreshed++;}
          }else{
            const entry=sourceMake(row,input.snapshotId,at,'post-cutover');body.unresolvedSourceRows.push(entry);sources.set(id,entry);changed=true;result.unresolvedSourceRows++;
          }
          continue;
        }
        const previous=known.get(row.nativeId);
        if(previous){
          if(hash(physical(previous))!==hash(physical(row))){
            // A previously absent physical sender cannot prove a later mapping.
            // Keep that exact held claim; do not block unrelated new inputs.
            const withoutNumeric=v=>physical(v).slice(0,-1);
            if(previous.resolution==='unresolved'&&previous.senderNumericId===null&&hash(withoutNumeric(previous))===hash(withoutNumeric(row))){result.ignored++;continue;}
            fail('identity-conflict');
          }
          if(previous.resolution==='resolved'&&row.direction==='incoming'&&hash(identity(previous))!==hash(identity(row)))fail('identity-conflict');
          if(previous.resolution==='unresolved'&&row.direction==='incoming'){
            const canAdmit=previous.firstSeenMode==='post-cutover'&&previous.holdReason==='sender-unresolved'
              &&previous.senderNumericId!==null&&row.timestampSeconds>=body.cutoverFloorSeconds&&row.timestampSeconds<=Math.floor(at/1000)+30;
            Object.assign(previous,message(row,nativeChatKey),{resolution:'resolved'});
            delete previous.reason;
            if(canAdmit){previous.state='queued';delete previous.holdReason;result.queued++;}
            result.resolved++;changed=true;
          }
          const mutable=['snapshotSourceId',...(previous.resolution==='resolved'&&row.direction==='incoming'?['senderDisplayName','renderedText','renderStatus']:[])];
          if(previous.snapshotId!==input.snapshotId||mutable.some(k=>previous[k]!==row[k])){
            previous.snapshotId=input.snapshotId;for(const k of mutable)previous[k]=row[k];changed=true;result.refreshed++;
          }else result.ignored++;
        }else{
          const reason=oldObserved.has(row.nativeId)?'previously-observed':row.timestampSeconds<body.cutoverFloorSeconds?'late-history':
            row.timestampSeconds>Math.floor(at/1000)+30?'future-timestamp':row.direction==='unknown'?'sender-unresolved':null;
          const entry=make(row,input.snapshotId,reason?'held':'queued',at,'post-cutover',reason);
          body.claims.push(entry);known.set(row.nativeId,entry);changed=true;reason?result.held++:result.queued++;
        }
      }
      for(const id of input.observedNativeIds)if(!observed.has(id)){body.observedNativeIds.push(id);observed.add(id);changed=true;}
      checkCapacity(body);if(changed){body.revision++;write(body,old.token);}return result;
    });},
    pending(){return structuredClone(read()?.body.claims.filter(c=>c.state==='queued'&&c.resolution==='resolved')??[]);},
    submit(claimIds,{requireAllQueued=false}={}){
      if(typeof requireAllQueued!=='boolean'||!Array.isArray(claimIds)||claimIds.length>maxIdentities
        ||new Set(claimIds).size!==claimIds.length||claimIds.some(id=>!hex(id)))fail('claims-invalid');
      return mutate(old=>{
        if(!old)fail('initialize-required');const body=structuredClone(old.body),known=new Map(body.claims.map(c=>[c.claimId,c]));
        for(const id of claimIds){const c=known.get(id);
          if(!c||c.resolution!=='resolved'||!['queued','submitted'].includes(c.state))fail('claim-not-queued');
          if(requireAllQueued&&c.state==='submitted')fail('claim-already-submitted');
        }
        let submitted=0;for(const id of claimIds){const c=known.get(id);if(c.state==='queued'){c.state='submitted';c.submittedAt=clock();submitted++;}}
        if(submitted){body.revision++;write(body,old.token);}return {submitted};
      });
    },
    // Does not roll durable state backwards. Only the processor owns ephemeral
    // prepared buffers; submitted/held/baseline claims can never be released.
    release(ids){if(!Array.isArray(ids)||ids.some(id=>!hex(id)))fail('claims-invalid');
      const pending=new Set((read()?.body.claims??[]).filter(c=>c.state==='queued').map(c=>c.claimId));
      return {released:ids.filter(id=>pending.has(id)).length};},
  };
  return Object.freeze(api);
}
