// Staged exact native unread text delegation. No UI/model/authority activation.
import {randomUUID,createHash} from 'node:crypto';
import {normalizeNativeGroupTextCatalog} from './group-text-contract.mjs';
const hash=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const fail=code=>{const error=Error('native-group-text-admission-'+code);error.code='native-group-text-admission-'+code;throw error;};
const sourceView=q=>[q.claimId,q.claimHash,q.snapshotId,q.handledServerIds??[],q.messages.map(m=>[
  m.nativeId,m.serverId,m.senderId,m.timestampSeconds,m.text,m.localId??null,m.snapshotSourceId])];

export function createNativeGroupTextAdmission({groupInbox,textInbox,readCatalog,now=Date.now}={}){
 if(typeof groupInbox?.read!=='function'||typeof groupInbox?.delegateTexts!=='function'||typeof textInbox?.admit!=='function'
  ||typeof textInbox?.lookup!=='function'||typeof readCatalog!=='function'||typeof now!=='function')fail('config-invalid');
 return Object.freeze({
  async prepare(record,claimId){
   const queued=groupInbox.read(record);
   if(!queued||queued.claimId!==claimId)fail('claim-mismatch');
   if(queued.state==='submitted')return queued;
   const texts=queued.messages.filter(m=>m.type==='text'),handled=new Set(queued.handledServerIds??[]);
   const unread=texts.filter(m=>!handled.has(m.serverId));
   // Voice-only/system-only/all-handled use the original admission path. A
   // separate, later quiet cutover is required; no native-text claim is made.
   if(!unread.length)return queued;
   const catalog=await readCatalog(record.nativeChatKey,{snapshotId:queued.snapshotId,
    admissionProof:{claimId:queued.claimId,claimHash:queued.claimHash}});
   if(catalog?.admission?.verified!==true||catalog.admission.claimId!==queued.claimId
    ||catalog.admission.claimHash!==queued.claimHash||catalog.admission.snapshotId!==queued.snapshotId)fail('proof-invalid');
   const input=normalizeNativeGroupTextCatalog(catalog,{accountId:queued.accountId,nativeChatKey:queued.nativeChatKey});
   if(input.snapshotId!==queued.snapshotId)fail('catalog-invalid');
   const byId=new Map(input.messages.map(m=>[m.nativeId,m]));
   for(const m of texts){
    const actual=byId.get(m.nativeId);
    if(!actual||actual.serverId!==m.serverId||actual.nativeChatKey!==queued.nativeChatKey||actual.senderId!==m.senderId
     ||actual.timestampSeconds!==m.timestampSeconds||actual.snapshotSourceId!==m.snapshotSourceId
     ||m.localId!==undefined&&actual.localId!==m.localId)fail('source-conflict');
   }
   const current=groupInbox.read(record);
   if(!current||current.state!=='queued'||hash(sourceView(current))!==hash(sourceView(queued)))fail('claim-changed');
   const observedAt=now();if(!Number.isSafeInteger(observedAt)||observedAt<=0)fail('clock-invalid');
   // The authenticated unread boundary predates UI registration. Wall-clock
   // creation time would silently hold unseen arrivals during that handoff.
   const cutoverAtMs=Math.min(...unread.map(m=>m.timestampSeconds*1000));
   if(!Number.isSafeInteger(cutoverAtMs)||cutoverAtMs<=0)fail('source-boundary-invalid');
   let adopted;
   try{adopted=textInbox.admit({catalog,rawClaimId:queued.claimId,rawClaimHash:queued.claimHash,
     unreadNativeIds:unread.map(m=>m.nativeId),authorityEpoch:randomUUID().replaceAll('-',''),cutoverAtMs});}
   catch(error){if(String(error?.message).endsWith('admission-binding-conflict'))fail('ownership-binding-pending');throw error;}
   const owners=textInbox.lookup(texts.map(m=>m.nativeId)).map(c=>{
    const original=byId.get(c.nativeId);
    if(!original||c.serverId!==original.serverId||c.senderId!==original.senderId||c.rawText!==original.rawText
      ||c.timestampSeconds!==original.timestampSeconds||c.localId!==original.localId||c.senderNumericId!==original.senderNumericId)fail('ownership-conflict');
    // Lineage is read from the authenticated original snapshot, while the
    // durable claim/state may already have advanced to a newer snapshot.
    return {...c,snapshotSourceId:original.snapshotSourceId};
   });
   if(owners.some(c=>unread.some(m=>m.nativeId===c.nativeId)&&c.state==='held'))fail('held-source-pending');
   return groupInbox.delegateTexts(record,claimId,{authorityEpoch:adopted.authorityEpoch,ownershipMode:adopted.ownershipMode,
    texts:owners,newTextClaimIds:adopted.newTextClaimIds});
  },
 });
}
