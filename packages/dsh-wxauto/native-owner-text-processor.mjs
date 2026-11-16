// Staged native text -> history -> original chat buffer. No model or UI calls.
import {createHash} from 'node:crypto';

const fingerprint=v=>createHash('sha256').update(JSON.stringify(v)).digest('hex');
const hex=v=>typeof v==='string'&&/^[a-f0-9]{32}$/.test(v);
const potentialControl=text=>/^[0-9a-f]{8}$/i.test(text.trim())||/^(批准操作|拒绝操作)\s/i.test(text.trim());
const fail=code=>{throw Error('native-owner-text-processor-'+code);};
const identity=c=>[c.claimId,c.nativeId,c.serverId,c.nativeChatKey,c.senderId,c.rawText,c.timestampSeconds,c.localId];

export function createNativeOwnerTextProcessor({inbox,readCatalog,history,routeForOwner,enqueue,
  accountId,ownerNativeChatKey,expectedPeerUsername,ownerTarget,activeAuthority=()=>null,
  isControlText=potentialControl,onControl,report=()=>{},isClosed=()=>false}={}){
  if(!inbox||typeof inbox.status!=='function'||typeof inbox.pending!=='function'||typeof inbox.submit!=='function'
    ||typeof readCatalog!=='function'||typeof history?.incoming!=='function'||typeof routeForOwner!=='function'
    ||typeof enqueue!=='function'||typeof activeAuthority!=='function'||typeof isControlText!=='function'
    ||typeof isClosed!=='function'||typeof report!=='function'
    ||(onControl!==undefined&&typeof onControl!=='function')||typeof accountId!=='string'||!accountId
    ||typeof ownerTarget!=='string'||!ownerTarget||!/^wechat:[A-Za-z0-9_-]+$/.test(ownerNativeChatKey??'')
    ||ownerNativeChatKey!=='wechat:'+expectedPeerUsername)fail('config-invalid');
  let closed=false,inflight=null;const prepared=new Map();
  const stopped=()=>closed||isClosed();
  const notify=(code,claim)=>{try{report('native-owner-text-'+code,claim?{claimId:claim.claimId}:{});}catch{}};
  function context(){
    if(stopped())return null;
    const a=activeAuthority(),state=inbox.status(),route=routeForOwner();
    if(a?.active!==true||!hex(a.authorityEpoch)||a.accountId!==accountId||a.ownerNativeChatKey!==ownerNativeChatKey
      ||state.initialized!==true||state.authorityEpoch!==a.authorityEpoch||route?.kind!=='friend'
      ||route.target!==ownerTarget||route.active===false||typeof route.sessionId!=='string'||!route.sessionId)return null;
    return {epoch:a.authorityEpoch,route:[route.target,route.kind,route.sessionId]};
  }
  const same=c=>JSON.stringify(context())===JSON.stringify(c);
  const nativeEvents=batch=>{
    if(!Array.isArray(batch))fail('batch-invalid');
    const events=batch.filter(e=>e?.sourceKind==='wechat-native-text'||e?.nativeOwnerTextClaimId!==undefined);
    if(new Set(events.map(e=>e.nativeOwnerTextClaimId)).size!==events.length)fail('batch-invalid');
    return events;
  };
  function validateSubmitting(batch,{sessionId,target}={}){
    const events=nativeEvents(batch);
    if(!events.length)return [];
    const current=context();if(!current)fail('authority-inactive');
    const pending=new Map(inbox.pending().map(c=>[c.claimId,c])),ids=[];
    for(const e of events){
      const record=prepared.get(e.nativeOwnerTextClaimId),claim=pending.get(e.nativeOwnerTextClaimId);
      if(!record||!claim||e.sourceKind!=='wechat-native-text'||e.authorityEpoch!==current.epoch
        ||fingerprint(e)!==record.eventHash||fingerprint(identity(claim))!==record.claimHash
        ||JSON.stringify(record.context)!==JSON.stringify(current)
        ||(sessionId!==undefined&&current.route[2]!==sessionId)||(target!==undefined&&current.route[0]!==target))fail('submit-invalid');
      ids.push(claim.claimId);
    }
    return ids;
  }
  function markSubmitting(batch,scope){
    const ids=validateSubmitting(batch,scope);
    if(!ids.length)return {submitted:0};
    // No async boundary between revalidation, durable submission and return.
    const result=inbox.submit(ids,{requireAllQueued:true});
    if(result?.submitted!==ids.length)fail('submit-not-durable');
    for(const id of ids)prepared.delete(id);
    return result;
  }
  async function run(){
    const initial=context();if(!initial)return {inactive:true,buffered:0,failed:0};
    const catalog=await readCatalog();if(!same(initial))return {inactive:true,buffered:0,failed:0};
    inbox.enqueue(catalog);
    const result={buffered:0,controls:0,deferredControls:0,failed:0,skipped:0};
    const claims=inbox.pending().sort((a,b)=>a.timestampSeconds-b.timestampSeconds||a.localId-b.localId||a.claimId.localeCompare(b.claimId));
    for(const claim of claims){
      if(!same(initial))break;
      if(prepared.has(claim.claimId)){result.skipped++;continue;}
      try{
        if(claim.nativeChatKey!==ownerNativeChatKey||claim.senderId!==expectedPeerUsername)fail('identity-invalid');
        const event={event:'message',id:'native-owner-text:'+claim.claimId,target:ownerTarget,kind:'friend',direction:'incoming',
          sender:claim.senderDisplayName,text:claim.rawText,rawText:claim.rawText,nativeSourceText:claim.rawText,
          timestamp:claim.timestampSeconds*1000,mediaType:'text',sourceKind:'wechat-native-text',
          nativeId:claim.nativeId,nativeSenderId:claim.senderId,nativeChatKey:ownerNativeChatKey,nativeAccountId:accountId,
          nativeSnapshotId:claim.snapshotId,nativeOwnerTextClaimId:claim.claimId,authorityEpoch:initial.epoch,images:[],files:[]};
        const control=isControlText(event.text);
        if(typeof control!=='boolean')fail('control-classification-invalid');
        if(control&&!onControl){result.deferredControls++;notify('control-admission-required',claim);continue;}
        const saved=await history.incoming(structuredClone(event));
        const ids=Array.isArray(saved?.messageIds)?saved.messageIds:typeof saved?.id==='string'?[saved.id]:[];
        if(!ids.length||ids.some(id=>typeof id!=='string'||!id)||new Set(ids).size!==ids.length)fail('history-not-durable');
        if(!same(initial))continue;
        prepared.set(claim.claimId,{context:initial,claimHash:fingerprint(identity(claim)),eventHash:fingerprint(event)});
        if(control){
          markSubmitting([event]);
          // A rejected/unknown handler is still consumed; never forward it to LLM.
          await onControl(structuredClone(event));result.controls++;
        }else{
          // Keep the exact envelope even if enqueue committed then rejected.
          // Explicit release or a process restart may resolve an unasked buffer.
          await enqueue(structuredClone(event));result.buffered++;
        }
      }catch{result.failed++;notify('dispatch-failed-no-automatic-replay',claim);}
    }
    return result;
  }
  return Object.freeze({
    refresh(){if(!inflight)inflight=run().finally(()=>{inflight=null;});return inflight;},
    markSubmitting,validateSubmitting,
    release(batch){let released=0;const pending=new Set(inbox.pending().map(c=>c.claimId));
      for(const e of nativeEvents(batch)){const record=prepared.get(e.nativeOwnerTextClaimId);
        if(record&&pending.has(e.nativeOwnerTextClaimId)&&fingerprint(e)===record.eventHash){prepared.delete(e.nativeOwnerTextClaimId);released++;}}
      return {released};},
    close(){closed=true;prepared.clear();},
    status(){return {closed:stopped(),refreshing:!!inflight,prepared:prepared.size,...inbox.status()};},
  });
}
