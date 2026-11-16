// Trusted native voice -> durable history -> chat buffer. No UI, capture or model calls.
import path from 'node:path';
import {createHash} from 'node:crypto';

const hash=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const hex=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const file=value=>typeof value==='string'&&path.isAbsolute(value)&&!value.includes('\0');
const identity=c=>[c.claimId,c.nativeId,c.serverId,c.nativeChatKey,c.senderId,c.rawText,c.timestampSeconds,c.localId];
const routeIdentity=r=>r&&typeof r.target==='string'&&r.target&&['friend','group'].includes(r.kind)&&r.active!==false
  ?[r.target,r.kind,r.sessionId??null]:null;
const fail=code=>{throw Error(code);};

export function createNativeVoiceProcessor({inbox,readCatalog,extract,decode,transcribe,history,
  routeForNativeKey,enqueue,report=()=>{},isClosed=()=>false,now=Date.now}) {
  let closed=false,inflight=null;
  const dispatched=new Map();
  // Resource throttling only: queued/submitted durable identities remain owned by inbox.
  const retries=new Map(),maxRetries=1000;
  const stopped=()=>closed||isClosed();
  const notify=(code,claim)=>{try{report(code,claim?{claimId:claim.claimId}:{});}catch{/* Reporting cannot change delivery. */}};
  const routeFor=claim=>routeIdentity(routeForNativeKey(claim.nativeChatKey));
  const sameRoute=(claim,expected)=>!stopped()&&JSON.stringify(routeFor(claim))===JSON.stringify(expected);
  const pendingMap=()=>new Map(inbox.pending().map(c=>[c.claimId,c]));
  const voiceEvents=batch=>{
    if(!Array.isArray(batch))fail('native-voice-batch-invalid');
    const result=batch.filter(e=>e?.sourceKind==='wechat-native-voice'||e?.nativeVoiceClaimId!==undefined);
    if(new Set(result.map(e=>e.nativeVoiceClaimId)).size!==result.length)fail('native-voice-batch-invalid');
    return result;
  };
  function validateSubmitting(batch,{sessionId,target}={}){
    const events=voiceEvents(batch);
    if(stopped())fail('native-voice-processor-closed');
    const pending=pendingMap(),ids=[];
    for(const event of events){
      const record=dispatched.get(event.nativeVoiceClaimId),claim=pending.get(event.nativeVoiceClaimId);
      if(!record||!claim||event.sourceKind!=='wechat-native-voice'||hash(event)!==record.fingerprint
        ||hash(identity(claim))!==hash(identity(record.claim))||!sameRoute(claim,record.route)
        ||(sessionId!==undefined&&record.route[2]!==sessionId)||(target!==undefined&&record.route[0]!==target))
        fail('native-voice-submit-invalid');
      ids.push(claim.claimId);
    }
    return ids;
  }
  async function run(){
    if(stopped())return {closed:true,dispatched:0,failed:0};
    if(!inbox.status().initialized)fail('native-voice-baseline-required');
    const catalog=await readCatalog();
    if(stopped())return {closed:true,dispatched:0,failed:0};
    inbox.enqueue(catalog);
    const claims=inbox.pending().sort((a,b)=>a.timestampSeconds-b.timestampSeconds||a.localId-b.localId||a.claimId.localeCompare(b.claimId));
    const pendingIds=new Set(claims.map(c=>c.claimId));
    for(const id of retries.keys())if(!pendingIds.has(id))retries.delete(id);
    const result={dispatched:0,failed:0,skipped:0};
    for(const claim of claims){
      if(stopped())break;
      if(dispatched.has(claim.claimId)){result.skipped++;continue;}
      let stage='native-voice-route-failed';
      let retryKey;
      try{
        const route=routeFor(claim);
        if(!route){result.skipped++;continue;}
        retryKey=hash([identity(claim),route]);
        const retry=retries.get(claim.claimId);
        if(retry&&retry.key!==retryKey)retries.delete(claim.claimId);
        else if(retry&&now()<retry.nextRetryAt){
          result.deferred=(result.deferred??0)+1;
          result.nextRetryAt=Math.min(result.nextRetryAt??Infinity,retry.nextRetryAt);
          continue;
        }
        stage='native-voice-extract-failed';
        const source=await extract(structuredClone(claim)),m=source?.metadata;
        if(!file(source?.file)||!m||!hex(m.sha256)||m.serverId!==claim.serverId
          ||m.nativeChatKey!==claim.nativeChatKey||m.senderId!==claim.senderId
          ||m.timestampSeconds!==claim.timestampSeconds||m.localId!==claim.localId
          ||!Number.isSafeInteger(m.durationMilliseconds)||m.durationMilliseconds<1||m.durationMilliseconds>120000
          ||(m.snapshotId!==undefined&&m.snapshotId!==claim.snapshotId))fail('native-voice-source-identity-invalid');
        if(!sameRoute(claim,route))continue;
        stage='native-voice-decode-failed';
        const decoded=await decode(source);
        if(!file(decoded?.file)||!hex(decoded.sha256)||decoded.sourceSha256!==m.sha256||decoded.file===source.file)
          fail('native-voice-decoded-identity-invalid');
        if(!sameRoute(claim,route))continue;
        stage='native-voice-transcribe-failed';
        const asr=await transcribe(decoded);
        if(typeof asr?.text!=='string'||!asr.text.trim()||asr.text.length>64000
          ||typeof asr.backend!=='string'||!asr.backend.trim()||asr.backend.length>200)fail('native-voice-no-speech');
        retries.delete(claim.claimId);
        if(!sameRoute(claim,route))continue;
        const event={id:'native-voice:'+claim.claimId,target:route[0],kind:route[1],sender:claim.senderDisplayName,
          text:'[本地语音转文字，可能有误]\n'+asr.text.trim(),timestamp:claim.timestampSeconds*1000,
          mediaType:'voice',sourceKind:'wechat-native-voice',nativeId:claim.nativeId,nativeSenderId:claim.senderId,
          nativeSnapshotId:claim.snapshotId,nativeSourceText:claim.rawText,rawText:claim.rawText,
          transcript:asr.text.trim(),asrBackend:asr.backend,nativeVoiceClaimId:claim.claimId,
          audio:[{path:source.file,sha256:m.sha256,role:'source'},{path:decoded.file,sha256:decoded.sha256,role:'decoded'}],
          images:[],files:[]};
        stage='native-voice-history-failed';
        const saved=await history.incoming(structuredClone(event));
        if(!Array.isArray(saved?.messageIds)||saved.messageIds.length<2
          ||saved.messageIds.some(id=>typeof id!=='string'||!id.trim())
          ||new Set(saved.messageIds).size!==saved.messageIds.length)fail('native-voice-history-not-durable');
        if(!sameRoute(claim,route))continue;
        // Save the exact buffer envelope, not model-controllable claim identifiers.
        const record={claim:structuredClone(claim),route,fingerprint:hash(event)};
        dispatched.set(claim.claimId,record);
        stage='native-voice-buffer-failed';
        try{await enqueue(structuredClone(event));}catch(error){dispatched.delete(claim.claimId);throw error;}
        result.dispatched++;
      }catch(error){
        const codes=new Set(['native-voice-source-identity-invalid','native-voice-decoded-identity-invalid',
          'native-voice-no-speech','native-voice-history-not-durable']);
        const code=codes.has(error?.message)?error.message:stage;
        if(!stopped()&&['native-voice-extract-failed','native-voice-decode-failed','native-voice-transcribe-failed'].includes(stage)){
          const previous=retries.get(claim.claimId);
          const count=previous?.key===retryKey&&previous.code===code?previous.count+1:1;
          const long=['native-voice-source-identity-invalid','native-voice-decoded-identity-invalid','native-voice-no-speech'].includes(code);
          const delay=Math.min(long?300000:60000,(long?60000:15000)*2**Math.min(count-1,10));
          if(!retries.has(claim.claimId)&&retries.size>=maxRetries)retries.delete(retries.keys().next().value);
          retries.set(claim.claimId,{key:retryKey,code,count,nextRetryAt:now()+delay});
        }
        notify(code,claim);result.failed++;
      }
    }
    return result;
  }
  return {
    // Not async: concurrent callers get the very same Promise, including its rejection.
    refresh(){
      if(!inflight){inflight=run().finally(()=>{inflight=null;});}
      return inflight;
    },
    validateSubmitting,
    async markSubmitting(batch,scope){
      const ids=validateSubmitting(batch,scope);
      if(!ids.length)return {submitted:0};
      // No await between validation and the inbox's locked, atomic submit. A failure never authorizes ask.
      const value=inbox.submit(ids,{requireAllQueued:true});
      if(value?.submitted!==ids.length)fail('native-voice-submit-not-durable');
      for(const id of ids){dispatched.delete(id);retries.delete(id);}
      return value;
    },
    release(batch){
      const pending=pendingMap();let released=0;
      for(const event of voiceEvents(batch)){
        const record=dispatched.get(event.nativeVoiceClaimId);
        if(pending.has(event.nativeVoiceClaimId)&&record&&hash(event)===record.fingerprint){
          dispatched.delete(event.nativeVoiceClaimId);released++;
        }
      }
      return {released};
    },
    close(){closed=true;dispatched.clear();retries.clear();},
    status(){return {closed:stopped(),refreshing:!!inflight,dispatched:dispatched.size,...inbox.status()};},
  };
}
