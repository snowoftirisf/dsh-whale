// Complete authenticated page collection only; no initialization or reply authority.
import {randomBytes} from 'node:crypto';
import {ownerTextScopeDigest} from './native-owner-text-inbox.mjs';

const fail=code=>{throw Error('native-owner-text-collector-'+code);};
const string=(v,max=4000)=>typeof v==='string'&&v.length>0&&v.length<=max&&!v.includes('\0');
const digest=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const native=v=>typeof v==='string'&&/^wechat-server:[1-9][0-9]{0,29}$/.test(v);

export async function collectNativeOwnerTextCatalog({readPage,accountId,ownerNativeChatKey,expectedPeerUsername,
  snapshotId,signal,pageSize=500,maxPages=2048,maxBytes=64*1024*1024,maxIdentities=100000}={}){
  if(typeof readPage!=='function'||!string(accountId)||!string(expectedPeerUsername)
    ||!/^wechat:[A-Za-z0-9_-]+$/.test(ownerNativeChatKey??'')||ownerNativeChatKey!=='wechat:'+expectedPeerUsername
    ||(snapshotId!==undefined&&!string(snapshotId))||!Number.isInteger(pageSize)||pageSize<1||pageSize>1000
    ||!Number.isInteger(maxPages)||maxPages<1||maxPages>2048||!Number.isInteger(maxBytes)||maxBytes<1||maxBytes>64*1024*1024
    ||!Number.isInteger(maxIdentities)||maxIdentities<1||maxIdentities>100000)fail('config-invalid');
  const scopeDigest=ownerTextScopeDigest(accountId,ownerNativeChatKey,expectedPeerUsername);
  const scanKey=randomBytes(32).toString('hex'),seen=new Set(),tokens=new Set(),messages=[];
  let fixedSnapshot=snapshotId,scanScope=null,cursor,previous={descriptorIndex:0,rowid:0},bytes=0;
  for(let pageNumber=0;pageNumber<maxPages;pageNumber++){
    if(signal?.aborted)fail('stopped');
    const page=await readPage({op:'catalog-page',scanKey,pageSize,...(fixedSnapshot?{snapshotId:fixedSnapshot}:{}),...(cursor?{cursor}:{})},signal);
    if(signal?.aborted)fail('stopped');
    bytes+=Buffer.byteLength(JSON.stringify(page));if(bytes>maxBytes)fail('byte-limit');
    if(page?.source!=='authenticated-native-owner-text-catalog-page'||page.accountId!==accountId
      ||page.nativeChatKey!==ownerNativeChatKey||page.scopeDigest!==scopeDigest||!digest(page.scanScopeDigest)
      ||!string(page.snapshotId)||(fixedSnapshot&&page.snapshotId!==fixedSnapshot)||(scanScope&&page.scanScopeDigest!==scanScope)
      ||!Array.isArray(page.messages)||!Array.isArray(page.observedNativeIds)||!Number.isSafeInteger(page.scanned)
      ||page.scanned<0||page.scanned>pageSize||page.observedNativeIds.length>page.scanned
      ||typeof page.complete!=='boolean'||(page.complete?page.nextCursor!==null:!string(page.nextCursor,4096)))fail('page-invalid');
    fixedSnapshot=page.snapshotId;scanScope=page.scanScopeDigest;
    const pos=page.position;
    if(!pos||!Number.isSafeInteger(pos.descriptorIndex)||pos.descriptorIndex<0||!Number.isSafeInteger(pos.rowid)||pos.rowid<0
      ||pos.descriptorIndex<previous.descriptorIndex||(pos.descriptorIndex===previous.descriptorIndex&&pos.rowid<previous.rowid)
      ||(!page.complete&&pos.descriptorIndex===previous.descriptorIndex&&pos.rowid<=previous.rowid))fail('cursor-not-advancing');
    const pageIds=new Set();
    for(const id of page.observedNativeIds){
      if(!native(id)||seen.has(id)||pageIds.has(id))fail('duplicate-native-id');
      pageIds.add(id);seen.add(id);if(seen.size>maxIdentities)fail('identity-limit');
    }
    const incoming=new Set();
    for(const m of page.messages){
      if(!m||!pageIds.has(m.nativeId)||incoming.has(m.nativeId)||m.nativeId!=='wechat-server:'+m.serverId
        ||m.nativeChatKey!==ownerNativeChatKey||m.senderId!==expectedPeerUsername||m.direction!=='incoming'
        ||!string(m.senderDisplayName)||!string(m.rawText,64000)||!m.rawText.trim()
        ||!Number.isSafeInteger(m.timestampSeconds)||m.timestampSeconds<=0||!Number.isSafeInteger(m.localId)||m.localId<=0
        ||!string(m.snapshotSourceId))fail('message-invalid');
      incoming.add(m.nativeId);messages.push(structuredClone(m));
    }
    if(page.complete)return {source:'authenticated-native-owner-text-catalog',accountId,snapshotId:fixedSnapshot,
      scopeDigest,nativeChatKey:ownerNativeChatKey,complete:true,messages:messages.sort((a,b)=>
        a.timestampSeconds-b.timestampSeconds||a.localId-b.localId||a.nativeId.localeCompare(b.nativeId))};
    if(tokens.has(page.nextCursor)||page.nextCursor===cursor)fail('cursor-loop');
    tokens.add(page.nextCursor);cursor=page.nextCursor;previous={...pos};
  }
  fail('page-limit');
}
