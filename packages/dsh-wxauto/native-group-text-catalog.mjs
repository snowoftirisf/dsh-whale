// Full authenticated group text scan only; no inbox initialization or execution.
import {createHash,randomBytes} from 'node:crypto';
const fail=c=>{throw Error('native-group-text-collector-'+c);};
const string=(v,max=65536)=>typeof v==='string'&&v.length>0&&Buffer.byteLength(v,'utf8')<=max&&!v.includes('\0');
const digest=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const native=v=>typeof v==='string'&&/^wechat-server:[1-9][0-9]{0,29}$/.test(v);
const positive=v=>Number.isSafeInteger(v)&&v>0;
export const groupTextScopeDigest=(accountId,key)=>createHash('sha256').update(JSON.stringify([accountId,key])).digest('hex');

export async function collectNativeGroupTextCatalog({readPage,accountId,nativeChatKey,snapshotId,signal,admissionProof,
  pageSize=500,maxPages=2048,maxBytes=64*1024*1024,maxIdentities=100000}={}){
  if(typeof readPage!=='function'||!string(accountId,256)||!/^wechat:[A-Za-z0-9_-]{1,128}@chatroom$/.test(nativeChatKey??'')
    ||(snapshotId!==undefined&&!/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/.test(snapshotId))
    ||!Number.isInteger(pageSize)||pageSize<1||pageSize>1000||!Number.isInteger(maxPages)||maxPages<1||maxPages>2048
    ||!Number.isInteger(maxBytes)||maxBytes<1||maxBytes>64*1024*1024||!Number.isInteger(maxIdentities)||maxIdentities<1||maxIdentities>100000)fail('config-invalid');
  if(admissionProof!==undefined&&(!admissionProof||typeof admissionProof!=='object'||Array.isArray(admissionProof)
    ||Object.keys(admissionProof).length!==2||!Object.hasOwn(admissionProof,'claimId')||!Object.hasOwn(admissionProof,'claimHash')
    ||typeof admissionProof.claimId!=='string'||!/^[a-f0-9]{32}$/.test(admissionProof.claimId)||!digest(admissionProof.claimHash)||snapshotId===undefined))fail('admission-invalid');
  const admission=admissionProof===undefined?undefined:structuredClone(admissionProof);
  const scopeDigest=groupTextScopeDigest(accountId,nativeChatKey),scanKey=randomBytes(32).toString('hex');
  const seen=new Set(),tokens=new Set(),sourceRows=new Set(),messages=[],unresolved=[];
  let fixed=snapshotId,scanScope=null,cursor,previous={descriptorIndex:0,rowid:0},bytes=0,physical=0;
  for(let n=0;n<maxPages;n++){
    if(signal?.aborted)fail('stopped');
    const page=await readPage({op:'catalog-page',nativeChatKey,scanKey,pageSize,...(fixed?{snapshotId:fixed}:{}),...(cursor?{cursor}:{}),
      ...(admission?{admissionProof:structuredClone(admission)}:{})},signal);
    if(signal?.aborted)fail('stopped');
    bytes+=Buffer.byteLength(JSON.stringify(page));if(bytes>maxBytes)fail('byte-limit');
    if(page?.source!=='authenticated-native-group-text-catalog-page'||page.accountId!==accountId||page.nativeChatKey!==nativeChatKey
      ||page.scopeDigest!==scopeDigest||!digest(page.scanScopeDigest)||!string(page.snapshotId,36)
      ||!/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/.test(page.snapshotId)||(fixed&&page.snapshotId!==fixed)||(scanScope&&page.scanScopeDigest!==scanScope)
      ||!Array.isArray(page.messages)||!Array.isArray(page.unresolved)||!Array.isArray(page.observedNativeIds)
      ||!Number.isSafeInteger(page.scanned)||page.scanned<0||page.scanned>pageSize||page.observedNativeIds.length>page.scanned
      ||page.messages.length+page.unresolved.length>page.scanned||typeof page.complete!=='boolean'
      ||(page.complete?page.nextCursor!==null:!string(page.nextCursor,4096)))fail('page-invalid');
    if(admission){
      const proof=page.admission;
      if(!proof||typeof proof!=='object'||Array.isArray(proof)||Object.keys(proof).length!==4||proof.verified!==true
        ||proof.claimId!==admission.claimId||proof.claimHash!==admission.claimHash||proof.snapshotId!==snapshotId)fail('admission-source-invalid');
    }else if(page.admission!==undefined)fail('admission-source-unrequested');
    fixed=page.snapshotId;scanScope=page.scanScopeDigest;physical+=page.scanned;
    if(physical>maxIdentities)fail('identity-limit'); // Includes zero-ID diagnostic rows; never grow unbounded.
    const pos=page.position;
    if(!pos||!Number.isSafeInteger(pos.descriptorIndex)||pos.descriptorIndex<0||!Number.isSafeInteger(pos.rowid)||pos.rowid<0
      ||pos.descriptorIndex<previous.descriptorIndex||(pos.descriptorIndex===previous.descriptorIndex&&pos.rowid<previous.rowid)
      ||(!page.complete&&pos.descriptorIndex===previous.descriptorIndex&&pos.rowid<=previous.rowid))fail('cursor-not-advancing');
    const pageIds=new Set();
    for(const id of page.observedNativeIds){if(!native(id)||seen.has(id)||pageIds.has(id))fail('duplicate-native-id');pageIds.add(id);seen.add(id);}
    const classified=new Set();
    for(const [items,isUnresolved] of [[page.messages,false],[page.unresolved,true]])for(const m of items){
      const noId=isUnresolved&&m?.reason==='server-id-unresolved'&&m.nativeId===null&&m.serverId===null;
      if(!m||(!noId&&(!pageIds.has(m.nativeId)||classified.has(m.nativeId)||m.nativeId!=='wechat-server:'+m.serverId))
        ||m.nativeChatKey!==nativeChatKey||!positive(m.localId)||!positive(m.timestampSeconds)||!string(m.rawText)||m.rawText.length>64000||!m.rawText.trim()
        ||!string(m.snapshotSourceId,4000)||(m.senderNumericId!==null&&!positive(m.senderNumericId)))fail('message-invalid');
      let source;try{source=JSON.parse(m.snapshotSourceId);}catch{fail('message-invalid');}
      if(!Array.isArray(source)||source.length!==3||!/^message\/message_[0-9]+\.db$/.test(source[0])||!/^Msg_[a-f0-9]{32}$/.test(source[1])||!positive(source[2]))fail('message-invalid');
      if(sourceRows.has(m.snapshotSourceId))fail('duplicate-source-row');sourceRows.add(m.snapshotSourceId);
      if(!noId)classified.add(m.nativeId);
      if(isUnresolved){
        if(m.direction!=='unknown'||(!noId&&m.reason!=='sender-unresolved')||Object.hasOwn(m,'senderId'))fail('unresolved-invalid');
        unresolved.push(structuredClone(m));
      }else{
        if(!string(m.senderId,256)||!string(m.senderDisplayName,4096)||m.senderDisplayName.length>4000||!positive(m.senderNumericId)||m.direction!=='incoming'
          ||!string(m.renderedText)||m.renderedText.length>64000||!m.renderedText.trim()||!['raw-preserved','verified-sender-prefix-stripped'].includes(m.renderStatus)
          ||(m.renderStatus==='raw-preserved'&&m.renderedText!==m.rawText)
          ||(m.renderStatus==='verified-sender-prefix-stripped'&&(!/^([^:\r\n]{1,128}):\r?\n/.test(m.rawText)||m.rawText.replace(/^([^:\r\n]{1,128}):\r?\n/,'')!==m.renderedText)))fail('message-invalid');
        messages.push(structuredClone(m));
      }
    }
    if(page.complete)return {source:'authenticated-native-group-text-catalog',accountId,nativeChatKey,snapshotId:fixed,scopeDigest,complete:true,
      messages:messages.sort((a,b)=>a.timestampSeconds-b.timestampSeconds||a.localId-b.localId||a.nativeId.localeCompare(b.nativeId)),
      observedNativeIds:[...seen].sort(),unresolved,
      ...(admission?{admission:{claimId:admission.claimId,claimHash:admission.claimHash,snapshotId:fixed,verified:true}}:{})};
    if(tokens.has(page.nextCursor)||page.nextCursor===cursor)fail('cursor-loop');tokens.add(page.nextCursor);cursor=page.nextCursor;previous={...pos};
  }
  fail('page-limit');
}
