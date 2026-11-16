import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createOwnerTextBaselinePublisher} from './owner-text-baseline.mjs';
import {createNativeOwnerTextInbox,ownerTextScopeDigest} from './native-owner-text-inbox.mjs';

const digest=v=>createHash('sha256').update(JSON.stringify(v)).digest('hex');
function fixture(t){
  const root=fs.mkdtempSync(path.resolve('artifacts/owner-text-baseline-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const config={dataRoot:root,accountId:'fixture-account',nativeVoiceSources:{ownerNativeChatKey:'wechat:owner_fixture'}};
  const row=id=>({nativeId:'wechat-server:'+id,serverId:String(id),nativeChatKey:'wechat:owner_fixture',senderId:'owner_fixture',
    senderDisplayName:'Owner',rawText:'private fixture 中文',timestampSeconds:1000,localId:Number(id),snapshotSourceId:'row/'+id,direction:'incoming'});
  const catalog=(messages=[row(1)])=>({source:'authenticated-native-owner-text-catalog',accountId:config.accountId,nativeChatKey:'wechat:owner_fixture',
    scopeDigest:ownerTextScopeDigest(config.accountId,'wechat:owner_fixture','owner_fixture'),snapshotId:'c531a315-2bab-4c85-bf01-19ea8aedf608',complete:true,messages});
  const publisher=createOwnerTextBaselinePublisher({config});
  const file=path.join(root,'config/native-owner-text-inbox.json'),authority=path.join(root,'config/native-owner-text-authority.json');
  const stage=token=>path.join(root,'config/native-owner-text-cutover',token.attemptId,'stage/config/native-owner-text-inbox.json');
  const prepare=()=>publisher.prepare(catalog(),{cutoverAtMs:Date.now()-1000});
  return {root,config,row,catalog,publisher,file,authority,stage,prepare};
}

test('constructor inert; explicit real inbox staging records baseline proof and publishes once',t=>{
  const f=fixture(t);assert.deepEqual(fs.readdirSync(f.root),[]);assert.equal(f.publisher.exists(),false);
  const token=f.prepare();assert.equal(f.publisher.exists(),false);assert.ok(Object.isFrozen(token));
  assert.equal(JSON.stringify(token).includes('private fixture'),false);
  const staged=JSON.parse(fs.readFileSync(f.stage(token),'utf8'));
  assert.equal(staged.baselineSnapshotId,f.catalog().snapshotId);assert.match(staged.baselineCatalogDigest,/^[a-f0-9]{64}$/);
  assert.equal(staged.claims[0].state,'baseline');
  const result=f.publisher.publish(token);assert.equal(result.published,true);assert.equal(f.publisher.exists(),true);
  assert.equal(fs.existsSync(f.stage(token)),false);assert.equal(fs.existsSync(f.file+'.lock'),false);
  const inbox=createNativeOwnerTextInbox({dataRoot:f.root,accountId:f.config.accountId,ownerNativeChatKey:'wechat:owner_fixture',expectedPeerUsername:'owner_fixture'});
  assert.equal(inbox.status().baseline,1);assert.equal(inbox.status().pending,0);
  assert.throws(()=>f.publisher.publish(token),/attempt-state-invalid/);
  assert.throws(()=>f.publisher.publish({...token}),/token-invalid/);
});

test('multiattempt first publish wins and preexisting inbox is never adopted or overwritten',t=>{
  const f=fixture(t),one=f.prepare(),two=f.prepare();f.publisher.publish(one);const before=fs.readFileSync(f.file);
  assert.throws(()=>f.publisher.publish(two),/existing-state/);assert.deepEqual(fs.readFileSync(f.file),before);
  assert.throws(()=>f.prepare(),/existing-state/);assert.throws(()=>createOwnerTextBaselinePublisher({config:f.config}).publish(one),/token-invalid/);
});

test('discard archives exact untouched baseline only with explicit nonnative proof',t=>{
  const f=fixture(t),token=f.prepare();f.publisher.publish(token);
  for(const proof of [{},{workerMode:'native',commitNotActive:true},{workerMode:'preparing',commitNotActive:false}]){
    assert.throws(()=>f.publisher.discardPublished(token,proof),/discard-denied/);
  }
  const before=fs.readFileSync(f.file);assert.equal(f.publisher.discardPublished(token,{workerMode:'preparing',commitNotActive:true}).discarded,true);
  assert.equal(f.publisher.exists(),false);
  const archive=path.join(f.root,'config/native-owner-text-cutover',token.attemptId,'discarded-native-owner-text-inbox.json');
  assert.deepEqual(fs.readFileSync(archive),before);assert.throws(()=>f.publisher.discardPublished(token,{workerMode:'ui',commitNotActive:true}),/discard-denied/);
});

test('authority marker even partial or changed/queued baseline forbids rollback',t=>{
  const f=fixture(t),token=f.prepare();f.publisher.publish(token);fs.writeFileSync(f.authority,'{partial');
  assert.throws(()=>f.publisher.discardPublished(token,{workerMode:'ui',commitNotActive:true}),/authority-present/);
  fs.unlinkSync(f.authority);const value=JSON.parse(fs.readFileSync(f.file));value.revision=2;value.claims[0].state='queued';
  const {digest:_,...body}=value;fs.writeFileSync(f.file,JSON.stringify({...body,digest:digest(body)}));
  assert.throws(()=>f.publisher.discardPublished(token,{workerMode:'ui',commitNotActive:true}),/baseline-changed/);assert.equal(fs.existsSync(f.file),true);
});

test('corrupt stage, partial publish, lock contention and authority presence refuse safely',t=>{
  const f=fixture(t),one=f.prepare();fs.writeFileSync(f.stage(one),'{partial');assert.throws(()=>f.publisher.publish(one),/state-invalid/);
  assert.equal(f.publisher.exists(),false);
  const two=f.prepare();fs.writeFileSync(f.file+'.lock','occupied');assert.throws(()=>f.publisher.publish(two),/busy/);
  assert.equal(fs.readFileSync(f.file+'.lock','utf8'),'occupied');fs.unlinkSync(f.file+'.lock');
  fs.writeFileSync(f.authority,'{}');assert.throws(()=>f.publisher.publish(two),/existing-state/);fs.unlinkSync(f.authority);
  fs.writeFileSync(f.file,'{partial');assert.throws(()=>f.publisher.publish(two),/existing-state/);assert.equal(fs.readFileSync(f.file,'utf8'),'{partial');
});

test('stage body changes cannot publish even with a recomputed state digest',t=>{
  const f=fixture(t),token=f.prepare(),value=JSON.parse(fs.readFileSync(f.stage(token)));
  value.claims[0].rawText='different';const {digest:_,...body}=value;fs.writeFileSync(f.stage(token),JSON.stringify({...body,digest:digest(body)}));
  assert.throws(()=>f.publisher.publish(token),/baseline-changed/);assert.equal(f.publisher.exists(),false);
});

test('real hardlink stage is refused where supported',t=>{
  const f=fixture(t),token=f.prepare(),alias=path.join(f.root,'alias.json');
  try{fs.linkSync(f.stage(token),alias)}catch(e){if(['EPERM','EACCES','ENOTSUP'].includes(e.code)){t.skip('OS denies fixture hardlinks');return}throw e}
  assert.throws(()=>f.publisher.publish(token),/file-invalid/);assert.equal(f.publisher.exists(),false);
});

test('link-count and symbolic-link path guards also fail closed without OS link privilege',t=>{
  const f=fixture(t),token=f.prepare(),original=fs.lstatSync;
  for(const mode of ['hard','symbolic']){
    const mock=t.mock.method(fs,'lstatSync',function(file,...args){
      const value=original.call(fs,file,...args);
      if(String(file)!==f.stage(token))return value;
      return new Proxy(value,{get(target,key){
        if(mode==='hard'&&key==='nlink')return 2;
        if(mode==='symbolic'&&key==='isSymbolicLink')return ()=>true;
        const item=Reflect.get(target,key);return typeof item==='function'?item.bind(target):item;
      }});
    });
    assert.throws(()=>f.publisher.publish(token),/file-invalid|path-invalid/);mock.mock.restore();
  }
  assert.equal(f.publisher.exists(),false);
});

test('unknown rename/fsync publication preserves evidence and only explicit safe discard can archive',t=>{
  const f=fixture(t),token=f.prepare(),original=fs.fsyncSync;
  const mock=t.mock.method(fs,'fsyncSync',()=>{throw Error('fixture disk unknown')});
  assert.throws(()=>f.publisher.publish(token),/fixture disk unknown/);assert.equal(fs.existsSync(f.file),true);
  mock.mock.restore();assert.equal(fs.fsyncSync,original);
  assert.throws(()=>f.publisher.publish(token),/attempt-state-invalid/);
  assert.equal(f.publisher.discardPublished(token,{workerMode:'ui',commitNotActive:true}).discarded,true);
});
