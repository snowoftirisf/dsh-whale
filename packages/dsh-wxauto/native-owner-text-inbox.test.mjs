import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createNativeOwnerTextInbox,ownerTextScopeDigest} from './native-owner-text-inbox.mjs';

function fixture(t,extra={}){
  const root=fs.mkdtempSync(path.resolve('artifacts/owner-text-inbox-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  let now=1000500;const config={dataRoot:root,accountId:'account',ownerNativeChatKey:'wechat:owner',expectedPeerUsername:'owner',now:()=>now,...extra};
  const make=(id,changes={})=>({nativeId:'wechat-server:'+id,serverId:String(id),nativeChatKey:'wechat:owner',senderId:'owner',senderDisplayName:'Owner',
    rawText:'same synthetic text',timestampSeconds:1001,localId:Number(id),snapshotSourceId:'db/row/'+id,direction:'incoming',...changes});
  const catalog=(messages=[],snapshotId='snapshot-1')=>({source:'authenticated-native-owner-text-catalog',accountId:'account',nativeChatKey:'wechat:owner',
    scopeDigest:ownerTextScopeDigest('account','wechat:owner','owner'),snapshotId,complete:true,messages});
  return {config,root,make,catalog,inbox:createNativeOwnerTextInbox(config),file:path.join(root,'config/native-owner-text-inbox.json'),
    time:v=>now=v,initialize(messages=[]){return this.inbox.initialize(catalog(messages),{cutoverAtMs:1000001});}};
}

test('constructor inert, explicit complete >1000 baseline persists fixed authority and never replays',t=>{
  const f=fixture(t);assert.deepEqual(fs.readdirSync(f.root),[]);
  const messages=Array.from({length:1201},(_,i)=>f.make(String(i+1)));
  const initial=f.initialize(messages);assert.equal(initial.baselined,1201);assert.match(initial.authorityEpoch,/^[a-f0-9]{32}$/);
  const restarted=createNativeOwnerTextInbox(f.config);assert.equal(restarted.status().authorityEpoch,initial.authorityEpoch);
  restarted.enqueue(f.catalog(messages,'snapshot-2'));assert.equal(restarted.pending().length,0);
  assert.equal(restarted.status().cutoverAtMs,1000001);assert.equal(restarted.status().baseline,1201);
  assert.throws(()=>f.initialize(),/already-initialized/);assert.equal(JSON.stringify(restarted.status()).includes('synthetic'),false);
});

test('nonmonotonic server identities and identical text are independent, queued then durably submitted once',t=>{
  const f=fixture(t);f.initialize([f.make('999')]);
  assert.equal(f.inbox.enqueue(f.catalog([f.make('1'),f.make('2')])).queued,2);
  const queued=f.inbox.pending();assert.equal(queued.length,2);assert.notEqual(queued[0].claimId,queued[1].claimId);
  const other=createNativeOwnerTextInbox(f.config);assert.equal(other.submit([queued[0].claimId]).submitted,1);
  assert.equal(f.inbox.submit([queued[0].claimId]).submitted,0);
  assert.equal(createNativeOwnerTextInbox(f.config).pending().length,1);
  f.inbox.enqueue(f.catalog([f.make('1'),f.make('2')],'snapshot-3'));assert.equal(f.inbox.pending().length,1);
  assert.equal(f.inbox.status().submitted,1);
});

test('late history and future timestamps held permanently, cutover ceil boundary precise',t=>{
  const f=fixture(t);f.initialize();
  const result=f.inbox.enqueue(f.catalog([f.make('1',{timestampSeconds:1000}),f.make('2',{timestampSeconds:1031}),f.make('3')]));
  assert.deepEqual(result,{queued:1,held:2,refreshed:0,ignored:0});
  f.time(1100000);f.inbox.enqueue(f.catalog([f.make('1',{timestampSeconds:1000}),f.make('2',{timestampSeconds:1031})],'new'));
  assert.equal(f.inbox.pending().length,1);assert.equal(f.inbox.status().held,2);
  const state=JSON.parse(fs.readFileSync(f.file));const held=state.claims.filter(c=>c.state==='held');
  for(const c of held)assert.throws(()=>f.inbox.submit([c.claimId]),/claim-not-queued/);
});

test('wrong account/chat/peer, incomplete catalog, duplicates and changed known identity reject whole batch',t=>{
  const f=fixture(t);f.initialize([f.make('1')]);const before=fs.readFileSync(f.file,'utf8');
  for(const bad of [
    {...f.catalog(),accountId:'other'}, {...f.catalog(),nativeChatKey:'wechat:group@chatroom'},
    {...f.catalog(),scopeDigest:'a'.repeat(64)}, {...f.catalog(),complete:false},
    f.catalog([f.make('2',{senderId:'other'})]),f.catalog([f.make('2'),f.make('2')]),
    f.catalog([f.make('2'),f.make('1',{rawText:'changed'})]),
    f.catalog([f.make('1',{localId:9})]),
  ])assert.throws(()=>f.inbox.enqueue(bad));
  assert.equal(fs.readFileSync(f.file,'utf8'),before);
  f.inbox.enqueue(f.catalog([f.make('1',{senderDisplayName:'Renamed',snapshotSourceId:'new/lineage'})],'new'));
  assert.equal(f.inbox.status().baseline,1);assert.equal(f.inbox.pending().length,0);
});

test('capacity and exclusive lock reject atomically; unknown submission never replayed on restart',t=>{
  const f=fixture(t,{maxIdentities:2});f.initialize([f.make('1')]);
  const before=fs.readFileSync(f.file,'utf8');assert.throws(()=>f.inbox.enqueue(f.catalog([f.make('2'),f.make('3')])),/capacity/);
  assert.equal(fs.readFileSync(f.file,'utf8'),before);
  fs.writeFileSync(f.file+'.lock','exclusive');assert.throws(()=>f.inbox.enqueue(f.catalog([f.make('2')])),/busy/);fs.unlinkSync(f.file+'.lock');
  f.inbox.enqueue(f.catalog([f.make('2')]));const [claim]=f.inbox.pending();f.inbox.submit([claim.claimId]);
  assert.equal(createNativeOwnerTextInbox(f.config).pending().length,0);
  assert.equal(f.inbox.submit([claim.claimId]).submitted,0);
});

test('failed rename leaves prior state and retry possible; outside state change cannot be overwritten',t=>{
  const f=fixture(t);f.initialize();const before=fs.readFileSync(f.file,'utf8');
  const rename=fs.renameSync;try{fs.renameSync=()=>{throw Error('fixture-lost-write');};assert.throws(()=>f.inbox.enqueue(f.catalog([f.make('1')])),/lost-write/);}
  finally{fs.renameSync=rename;}
  assert.equal(fs.readFileSync(f.file,'utf8'),before);assert.equal(f.inbox.pending().length,0);
  const sync=fs.fsyncSync;let changed=false;
  try{fs.fsyncSync=fd=>{sync(fd);if(!changed){changed=true;fs.writeFileSync(f.file,before+' ');}};
    assert.throws(()=>f.inbox.enqueue(f.catalog([f.make('1')])),/state-changed/);
  }finally{fs.fsyncSync=sync;}
  assert.equal(fs.readFileSync(f.file,'utf8'),before+' ');assert.equal(f.inbox.pending().length,0);
  assert.equal(f.inbox.enqueue(f.catalog([f.make('1')])).queued,1);
});

test('corrupt hash and cross-config state rejected without mutation',t=>{
  const f=fixture(t);f.initialize();
  const other=createNativeOwnerTextInbox({...f.config,accountId:'other'});assert.throws(()=>other.status(),/state-invalid/);
  const v=JSON.parse(fs.readFileSync(f.file));v.revision++;fs.writeFileSync(f.file,JSON.stringify(v));
  assert.throws(()=>f.inbox.status(),/state-invalid/);assert.throws(()=>f.inbox.enqueue(f.catalog()),/state-invalid/);
});

test('linked state files rejected rather than followed',t=>{
  const f=fixture(t);f.initialize();const copy=f.file+'.source';fs.renameSync(f.file,copy);
  try{fs.linkSync(copy,f.file);}catch(e){if(['EPERM','EACCES','ENOTSUP'].includes(e.code)){t.skip('OS denies hardlink fixture');return;}throw e;}
  assert.throws(()=>f.inbox.status(),/file-invalid/);assert.equal(fs.statSync(copy).nlink,2);
});

test('link/hardlink checks fail closed even on systems that forbid creating link fixtures',t=>{
  const f=fixture(t);f.initialize();const stat=fs.lstatSync;
  for(const kind of ['hardlink','symlink']){
    try{
      fs.lstatSync=(p,...args)=>{const value=stat(p,...args);if(p!==f.file)return value;
        const wrapped=Object.create(value);Object.defineProperty(wrapped,'nlink',{value:kind==='hardlink'?2:1});
        wrapped.isSymbolicLink=()=>kind==='symlink';return wrapped;};
      assert.throws(()=>f.inbox.status(),/file-invalid/);
    }finally{fs.lstatSync=stat;}
  }
});

test('baseline cannot join submit batch and invalid owner scope cannot be constructed',t=>{
  const f=fixture(t);f.initialize([f.make('1')]);f.inbox.enqueue(f.catalog([f.make('2')]));
  const baseline=JSON.parse(fs.readFileSync(f.file)).claims.find(c=>c.state==='baseline');
  const queued=f.inbox.pending()[0];assert.throws(()=>f.inbox.submit([queued.claimId,baseline.claimId]),/claim-not-queued/);
  assert.equal(f.inbox.pending().length,1);assert.equal(f.inbox.status().submitted,0);
  for(const bad of [{ownerNativeChatKey:'wechat:owner@chatroom'},{expectedPeerUsername:'other'}])
    assert.throws(()=>createNativeOwnerTextInbox({...f.config,...bad}),/config-invalid/);
});

test('strict submit rejects mixed submitted and fresh claims without consuming any fresh claim',t=>{
  const f=fixture(t);f.initialize();f.inbox.enqueue(f.catalog([f.make('1'),f.make('2')]));
  const [first,second]=f.inbox.pending();f.inbox.submit([first.claimId]);
  const before=fs.readFileSync(f.file,'utf8'),revision=f.inbox.status().revision;
  assert.throws(()=>f.inbox.submit([second.claimId,first.claimId],{requireAllQueued:true}),/claim-already-submitted/);
  assert.equal(fs.readFileSync(f.file,'utf8'),before);assert.equal(f.inbox.status().revision,revision);
  assert.deepEqual(f.inbox.pending().map(c=>c.claimId),[second.claimId]);
  assert.deepEqual(f.inbox.submit([first.claimId]),{submitted:0}); // legacy idempotent contract
});

test('competing strict consumers of the same batch have one durable winner',t=>{
  const f=fixture(t);f.initialize();f.inbox.enqueue(f.catalog([f.make('1'),f.make('2')]));
  const other=createNativeOwnerTextInbox(f.config),ids=f.inbox.pending().map(c=>c.claimId);
  assert.deepEqual(other.submit(ids,{requireAllQueued:true}),{submitted:2});
  const before=fs.readFileSync(f.file,'utf8');
  assert.throws(()=>f.inbox.submit(ids,{requireAllQueued:true}),/claim-already-submitted/);
  assert.equal(fs.readFileSync(f.file,'utf8'),before);assert.equal(f.inbox.pending().length,0);
  assert.equal(createNativeOwnerTextInbox(f.config).status().submitted,2);
});

test('baseline proof fields bind exact lexically sorted normalized catalog; paired legacy compatibility',t=>{
  const f=fixture(t),messages=[f.make('2'),f.make('10')];f.initialize(messages);
  const value=JSON.parse(fs.readFileSync(f.file,'utf8'));
  assert.equal(value.baselineSnapshotId,'snapshot-1');
  const sorted=[messages[1],messages[0]];assert.equal(value.baselineCatalogDigest,
    createHash('sha256').update(JSON.stringify(['snapshot-1',sorted])).digest('hex'));
  const save=v=>{delete v.digest;v.digest=createHash('sha256').update(JSON.stringify(v)).digest('hex');fs.writeFileSync(f.file,JSON.stringify(v));};
  delete value.baselineSnapshotId;save(value);assert.throws(()=>f.inbox.status(),/state-invalid/);
  delete value.baselineCatalogDigest;save(value);assert.equal(f.inbox.status().baseline,2); // legacy readable, never authority activation
});
