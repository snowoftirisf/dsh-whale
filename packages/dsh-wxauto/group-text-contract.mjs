// Staged authenticated native group text contract. No IO or model authority.
import path from 'node:path';
import {createHash} from 'node:crypto';
export const groupTextHash=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
export const nativeGroupTextScopeDigest=(accountId,nativeChatKey)=>groupTextHash([accountId,nativeChatKey]);
export const nativeGroupTextClaimId=(accountId,nativeChatKey,serverId)=>groupTextHash([accountId,nativeChatKey,serverId]).slice(0,32);
export const groupTextString=(value,max=4000)=>typeof value==='string'&&value.length>0&&value.length<=max&&!value.includes('\0');
export const nativeGroupKey=value=>typeof value==='string'&&/^wechat:[A-Za-z0-9_-]+@chatroom$/.test(value);
export const groupTextHex=value=>typeof value==='string'&&/^[a-f0-9]{32}$/.test(value);
const fail=()=>{throw Error('native-group-text-contract-invalid');};
const fields=['nativeId','serverId','nativeChatKey','senderId','senderDisplayName','senderNumericId','rawText','renderedText','renderStatus',
  'timestampSeconds','localId','snapshotSourceId','direction'];
export const NATIVE_GROUP_TEXT_MESSAGE_FIELDS=Object.freeze(fields);
export const NATIVE_GROUP_TEXT_UNRESOLVED_FIELDS=Object.freeze(['nativeId','serverId','nativeChatKey','rawText','timestampSeconds','localId','snapshotSourceId','senderNumericId','reason','direction']);
export function nativeGroupTextPaths({dataRoot,nativeChatKey}={}){
  if(!groupTextString(dataRoot)||!path.isAbsolute(dataRoot)||!nativeGroupKey(nativeChatKey))fail();
  const suffix=createHash('sha256').update(nativeChatKey,'utf8').digest('hex').slice(0,32);
  const directory=path.join(dataRoot,'config','native-group-text',suffix),file=path.join(directory,'inbox.json');
  return {directory,file,inbox:file,authority:path.join(directory,'authority.json'),lock:file+'.lock'};
}
function common(value,key,nullable=false){
  if(!value||value.nativeChatKey!==key||!groupTextString(value.rawText,64000)||!value.rawText.trim()
    ||!Number.isSafeInteger(value.timestampSeconds)||value.timestampSeconds<=0||!Number.isSafeInteger(value.timestampSeconds*1000)||!Number.isSafeInteger(value.localId)||value.localId<=0
    ||!groupTextString(value.snapshotSourceId)||!(value.senderNumericId===null||Number.isSafeInteger(value.senderNumericId)&&value.senderNumericId>0))fail();
  if(nullable&&value.nativeId===null&&value.serverId===null)return;
  if(typeof value.serverId!=='string'||!/^[1-9][0-9]{0,29}$/.test(value.serverId)||value.nativeId!=='wechat-server:'+value.serverId)fail();
}
export function normalizeNativeGroupTextMessage(value,key){
  common(value,key);
  if(value.direction!=='incoming'||!groupTextString(value.senderId,512)||!groupTextString(value.senderDisplayName)
    ||value.senderNumericId===null||!groupTextString(value.renderedText,64000)||!value.renderedText.trim()
    ||!['raw-preserved','verified-sender-prefix-stripped'].includes(value.renderStatus)
    ||value.renderStatus==='raw-preserved'&&value.renderedText!==value.rawText)fail();
  return Object.fromEntries(fields.map(k=>[k,value[k]]));
}
export function normalizeNativeGroupTextUnresolved(value,key){
  common(value,key,true);
  if(value.direction!=='unknown'||(value.nativeId===null?value.reason!=='server-id-unresolved':value.reason!=='sender-unresolved'))fail();
  return Object.fromEntries(NATIVE_GROUP_TEXT_UNRESOLVED_FIELDS.map(k=>[k,value[k]]));
}
export const nativeGroupTextPhysicalIdentity=value=>[value.nativeChatKey,value.nativeId,value.serverId,value.rawText,value.timestampSeconds,value.localId,value.senderNumericId];
export const nativeGroupTextMessageIdentity=value=>[...nativeGroupTextPhysicalIdentity(value),value.senderId];
export const nativeGroupTextSourceRowId=(accountId,value)=>groupTextHash([accountId,value.nativeChatKey,value.snapshotSourceId,value.localId]);
const lexical=(a,b)=>a<b?-1:a>b?1:0;
const unresolvedKey=value=>value.nativeId??JSON.stringify([value.snapshotSourceId,value.localId]);
export function nativeGroupTextCatalogDigest(catalog){
  return groupTextHash([catalog.snapshotId,catalog.messages.map(m=>normalizeNativeGroupTextMessage(m,m.nativeChatKey)).sort((a,b)=>lexical(a.nativeId,b.nativeId)),
    catalog.unresolved.map(m=>normalizeNativeGroupTextUnresolved(m,m.nativeChatKey)).sort((a,b)=>lexical(unresolvedKey(a),unresolvedKey(b))),[...catalog.observedNativeIds].sort(lexical)]);
}
export function normalizeNativeGroupTextCatalog(value,{accountId,nativeChatKey,maxIdentities=100000}){
  if(value?.source!=='authenticated-native-group-text-catalog'||value.complete!==true||value.accountId!==accountId||value.nativeChatKey!==nativeChatKey
    ||value.scopeDigest!==nativeGroupTextScopeDigest(accountId,nativeChatKey)||!groupTextString(value.snapshotId)
    ||![value.messages,value.unresolved,value.observedNativeIds].every(Array.isArray))fail();
  if(value.messages.length+value.unresolved.length>maxIdentities||value.observedNativeIds.length>maxIdentities)fail();
  const messages=value.messages.map(v=>normalizeNativeGroupTextMessage(v,nativeChatKey));
  const unresolved=value.unresolved.map(v=>normalizeNativeGroupTextUnresolved(v,nativeChatKey));
  const observedNativeIds=[...value.observedNativeIds],observed=new Set(observedNativeIds),seen=new Set(),sourceRows=new Set();
  if(observed.size!==observedNativeIds.length||observedNativeIds.some(id=>typeof id!=='string'||!/^wechat-server:[1-9][0-9]{0,29}$/.test(id)))fail();
  for(const row of [...messages,...unresolved]){
    const id=nativeGroupTextSourceRowId(accountId,row);if(sourceRows.has(id))fail();sourceRows.add(id);
    if(row.nativeId!==null){if(seen.has(row.nativeId)||!observed.has(row.nativeId))fail();seen.add(row.nativeId);}
  }
  return {snapshotId:value.snapshotId,messages,unresolved,observedNativeIds};
}
