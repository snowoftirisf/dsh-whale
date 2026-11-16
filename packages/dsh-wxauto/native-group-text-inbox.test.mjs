import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createNativeGroupTextInbox,nativeGroupTextPaths} from './native-group-text-inbox.mjs';
import {nativeGroupTextScopeDigest,nativeGroupTextClaimId,nativeGroupTextCatalogDigest} from './group-text-contract.mjs';

function fixture(t,extra={}){
  const root=fs.mkdtempSync(path.resolve('artifacts/group-text-inbox-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  let now=1000500;const config={dataRoot:root,accountId:'account',nativeChatKey:'wechat:g@chatroom',now:()=>now,...extra};
  const make=(id,changes={})=>({nativeId:'wechat-server:'+id,serverId:String(id),nativeChatKey:config.nativeChatKey,senderId:'peer-'+id,
    senderDisplayName:'Member '+id,senderNumericId:Number(id),rawText:'same fixture text',renderedText:'same fixture text',renderStatus:'raw-preserved',
    timestampSeconds:1001,localId:Number(id),snapshotSourceId:'db/'+id,direction:'incoming',...changes});
  const unresolved=(id,changes={})=>{const m=make(id);return {nativeId:m.nativeId,serverId:m.serverId,nativeChatKey:m.nativeChatKey,
    rawText:m.rawText,timestampSeconds:m.timestampSeconds,localId:m.localId,snapshotSourceId:m.snapshotSourceId,senderNumericId:m.senderNumericId,
    reason:'sender-unresolved',direction:'unknown',...changes};};
  const catalog=(messages=[],pending=[],observed=[...messages,...pending].filter(m=>m.nativeId!==null).map(m=>m.nativeId),snapshotId='snapshot-1')=>({
    source:'authenticated-native-group-text-catalog',accountId:config.accountId,nativeChatKey:config.nativeChatKey,
    scopeDigest:nativeGroupTextScopeDigest(config.accountId,config.nativeChatKey),snapshotId,complete:true,messages,unresolved:pending,observedNativeIds:observed});
  const inbox=createNativeGroupTextInbox(config),file=nativeGroupTextPaths(config).file;
  return {root,config,inbox,file,make,unresolved,catalog,time:v=>now=v,baseline(value=catalog(),extra={}){
    return inbox.baseline({catalog:value,authorityEpoch:'e'.repeat(32),cutoverAtMs:1000001,...extra});}};
}

test('inert complete baseline >1000 survives restart, outgoing observations and unresolved never silently become new',t=>{
  const f=fixture(t);assert.deepEqual(fs.readdirSync(f.root),[]);
  const rows=Array.from({length:1201},(_,i)=>f.make(i+1)),u=f.unresolved(1300);
  f.baseline(f.catalog(rows,[u],[...rows.map(m=>m.nativeId),u.nativeId,'wechat-server:1500']));
  const other=createNativeGroupTextInbox(f.config);
  assert.equal(other.status().baseline,1201);assert.equal(other.status().held,1);assert.equal(other.status().authorityEpoch,'e'.repeat(32));
  other.enqueue(f.catalog([f.make(1300),f.make(1500)]));
  assert.equal(other.pending().length,0);assert.equal(other.status().held,2);
  assert.throws(()=>f.baseline(),/already-initialized/);
});

test('baseline catalog digest normalizes property order and sorts native/source identities, never numeric server order',t=>{
  const f=fixture(t),first=f.catalog([f.make(10),f.make(2)],[f.unresolved(3)]);
  const reverse=value=>Object.fromEntries(Object.entries(value).reverse());
  const second={...first,messages:first.messages.map(reverse).reverse(),unresolved:first.unresolved.map(reverse),observedNativeIds:[...first.observedNativeIds].reverse()};
  assert.equal(nativeGroupTextCatalogDigest(first),nativeGroupTextCatalogDigest(second));
});

test('stable group+account+server claim IDs preserve distinct senders/same text/nonmonotonic IDs and submitted unknown never replays',t=>{
  const f=fixture(t);f.baseline(f.catalog([f.make(999)]));f.inbox.enqueue(f.catalog([f.make(2),f.make(1)]));
  const pending=f.inbox.pending();assert.equal(pending.length,2);assert.notEqual(pending[0].claimId,pending[1].claimId);
  assert.equal(pending[0].claimId,nativeGroupTextClaimId('account','wechat:g@chatroom','2'));
  assert.deepEqual(f.inbox.submit([pending[0].claimId],{requireAllQueued:true}),{submitted:1});
  const restarted=createNativeGroupTextInbox(f.config);
  restarted.enqueue(f.catalog([f.make(2,{senderDisplayName:'Renamed',snapshotSourceId:'new-db'})],[],['wechat-server:2'],'snapshot-2'));
  assert.equal(restarted.pending().length,1);assert.equal(restarted.status().submitted,1);
  assert.deepEqual(restarted.release([pending[0].claimId]),{released:0});
});

test('post-cutover unresolved may map once, while baseline/old/future and missing physical sender remain held; server0 only diagnostics',t=>{
  const f=fixture(t);f.baseline(f.catalog([],[f.unresolved(1)]));
  const zero=f.unresolved(9,{nativeId:null,serverId:null,reason:'server-id-unresolved'});
  f.inbox.enqueue(f.catalog([], [f.unresolved(2),f.unresolved(3,{timestampSeconds:1000}),f.unresolved(4,{timestampSeconds:1031}),f.unresolved(5,{senderNumericId:null}),zero]));
  const result=f.inbox.enqueue(f.catalog([f.make(1),f.make(2),f.make(3,{timestampSeconds:1000}),f.make(4,{timestampSeconds:1031}),f.make(5),f.make(6)]));
  assert.equal(result.queued,2);assert.deepEqual(f.inbox.pending().map(c=>c.serverId).sort(),['2','6']);
  assert.equal(f.inbox.status().unresolvedSourceRows,1);
  f.inbox.enqueue(f.catalog([f.make(2)]));assert.equal(f.inbox.pending().length,2);
});

test('catalog scope/budget/duplicates and immutable physical or sender changes reject the whole mutation',t=>{
  const f=fixture(t);assert.throws(()=>f.inbox.enqueue(f.catalog()),/initialize-required/);f.baseline();f.inbox.enqueue(f.catalog([f.make(1)]));
  const before=fs.readFileSync(f.file);
  for(const change of [{senderId:'other'},{senderNumericId:22},{rawText:'changed',renderedText:'changed'},{localId:22},{timestampSeconds:1002}]){
    assert.throws(()=>f.inbox.enqueue(f.catalog([f.make(2),f.make(1,change)])),/identity-conflict/);assert.deepEqual(fs.readFileSync(f.file),before);
  }
  for(const value of [{...f.catalog(),accountId:'other'},{...f.catalog(),complete:false},f.catalog([f.make(2),f.make(2)]),
    f.catalog([f.make(2,{nativeChatKey:'wechat:other@chatroom'})])])assert.throws(()=>f.inbox.enqueue(value));
  assert.deepEqual(fs.readFileSync(f.file),before);
});

test('explicit delegation is separate from quiet baseline and cannot delegate unresolved or absent IDs',t=>{
  const f=fixture(t);assert.throws(()=>f.baseline(f.catalog([],[f.unresolved(1)]),{delegatedUnreadNativeIds:['wechat-server:1']}),/baseline-invalid/);
  f.baseline(f.catalog([f.make(1),f.make(2)]),{delegatedUnreadNativeIds:['wechat-server:2']});
  assert.equal(f.inbox.status().baseline,1);assert.equal(f.inbox.pending()[0].firstSeenMode,'delegated-unread');
});

test('strict all-queued submit is atomic across two instances; mixed submitted claim never consumes the other',t=>{
  const f=fixture(t);f.baseline();f.inbox.enqueue(f.catalog([f.make(1),f.make(2)]));const ids=f.inbox.pending().map(c=>c.claimId);
  const other=createNativeGroupTextInbox(f.config);other.submit([ids[0]],{requireAllQueued:true});const before=fs.readFileSync(f.file);
  assert.throws(()=>f.inbox.submit(ids,{requireAllQueued:true}),/claim-already-submitted/);assert.deepEqual(fs.readFileSync(f.file),before);
  assert.equal(f.inbox.status().pending,1);assert.deepEqual(f.inbox.submit([ids[0]]),{submitted:0});
});

test('lock contention and corrupt/hash-invalid state refuse without silently rebuilding',t=>{
  const f=fixture(t);f.baseline();const paths=nativeGroupTextPaths(f.config);
  fs.writeFileSync(paths.lock,'fixture');assert.throws(()=>f.inbox.enqueue(f.catalog()),/busy/);fs.unlinkSync(paths.lock);
  const original=fs.readFileSync(f.file);fs.writeFileSync(f.file,'{}');assert.throws(()=>f.inbox.status(),/state-invalid/);fs.writeFileSync(f.file,original);
});

test('real hardlinked state is refused when OS permits creating a hardlink',t=>{
  const f=fixture(t);f.baseline();const other=f.file+'.hardlink';
  try{fs.linkSync(f.file,other);}catch(error){if(['EPERM','EACCES'].includes(error.code)){t.skip('OS sandbox denies hardlink creation');return;}throw error;}
  assert.throws(()=>f.inbox.status(),/file-invalid/);fs.unlinkSync(other);
});

test('rename lost-write does not return a successful durable submit or mutate existing state',t=>{
  const f=fixture(t);f.baseline();f.inbox.enqueue(f.catalog([f.make(1)]));const id=f.inbox.pending()[0].claimId;
  const original=fs.renameSync;fs.renameSync=()=>{};
  try{assert.throws(()=>f.inbox.submit([id],{requireAllQueued:true}),/write-not-durable/);}finally{fs.renameSync=original;}
  assert.equal(f.inbox.status().submitted,0);assert.equal(f.inbox.pending().length,1);
});

test('capacity and fsync failures leave the old durable contents intact',t=>{
  const f=fixture(t,{maxIdentities:2});f.baseline();const before=fs.readFileSync(f.file);
  assert.throws(()=>f.inbox.enqueue(f.catalog([f.make(1),f.make(2),f.make(3)])));assert.deepEqual(fs.readFileSync(f.file),before);
  const original=fs.fsyncSync;fs.fsyncSync=()=>{throw Error('fixture fsync');};
  try{assert.throws(()=>f.inbox.enqueue(f.catalog([f.make(1)])));}finally{fs.fsyncSync=original;}
  assert.deepEqual(fs.readFileSync(f.file),before);assert.equal(f.inbox.status().pending,0);
});
