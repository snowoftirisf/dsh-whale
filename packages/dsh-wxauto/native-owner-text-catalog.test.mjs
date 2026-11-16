import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {collectNativeOwnerTextCatalog} from './native-owner-text-catalog.mjs';
import {createNativeOwnerTextInbox,ownerTextScopeDigest} from './native-owner-text-inbox.mjs';

const identity={accountId:'fixture',ownerNativeChatKey:'wechat:peer',expectedPeerUsername:'peer'};
const message=n=>({nativeId:'wechat-server:'+n,serverId:String(n),nativeChatKey:'wechat:peer',senderId:'peer',
  senderDisplayName:'Peer',direction:'incoming',rawText:'same text',timestampSeconds:100,localId:n,snapshotSourceId:'snapshot:row:'+n});
const page=(messages=[],extra={})=>({source:'authenticated-native-owner-text-catalog-page',accountId:'fixture',snapshotId:'snapshot',
  scopeDigest:ownerTextScopeDigest('fixture','wechat:peer','peer'),scanScopeDigest:'a'.repeat(64),nativeChatKey:'wechat:peer',
  messages,observedNativeIds:messages.map(v=>v.nativeId),scanned:messages.length,
  position:{descriptorIndex:1,rowid:0},complete:true,nextCursor:null,...extra});

test('all 1201 exact incoming identities form one complete baseline only after every page, retaining same-text messages',async t=>{
  const root=fs.mkdtempSync(path.resolve('artifacts/owner-text-full-baseline-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const all=Array.from({length:1201},(_,i)=>message(i+1)),calls=[];
  const catalog=await collectNativeOwnerTextCatalog({...identity,readPage:async args=>{
    calls.push(args);const offset=calls.length===1?0:Number(args.cursor),end=Math.min(offset+500,all.length),done=end===all.length;
    return page(all.slice(offset,end),{position:done?{descriptorIndex:1,rowid:0}:{descriptorIndex:0,rowid:end},
      complete:done,nextCursor:done?null:String(end)});
  }});
  assert.equal(catalog.messages.length,1201);assert.equal(calls.length,3);
  assert.match(calls[0].scanKey,/^[a-f0-9]{64}$/);assert.equal(calls[1].scanKey,calls[0].scanKey);
  assert.equal(calls[0].snapshotId,undefined);assert.equal(calls[1].snapshotId,'snapshot');
  const inbox=createNativeOwnerTextInbox({...identity,dataRoot:root,now:()=>200000});
  inbox.initialize(catalog,{cutoverAtMs:200000});assert.equal(inbox.pending().length,0);
  assert.equal(inbox.status().identities,1201);
});

test('outgoing server-zero-only page advances physical progress before any incoming text',async()=>{
  let count=0;
  const result=await collectNativeOwnerTextCatalog({...identity,readPage:async()=>++count===1
    ?page([],{scanned:500,position:{descriptorIndex:0,rowid:500},complete:false,nextCursor:'next'})
    :page([message(7)])});
  assert.equal(count,2);assert.equal(result.messages[0].serverId,'7');
});

test('changed snapshot, scope or native identity refuses the whole collection',async()=>{
  for(const change of [{snapshotId:'other'},{scanScopeDigest:'b'.repeat(64)},{scopeDigest:'b'.repeat(64)},
    {accountId:'other'},{nativeChatKey:'wechat:other'},{observedNativeIds:['wechat-server:1'],messages:[message(1)]}]){
    let n=0;
    await assert.rejects(collectNativeOwnerTextCatalog({...identity,readPage:async()=>++n===1
      ?page([message(1)],{position:{descriptorIndex:0,rowid:1},complete:false,nextCursor:'next'})
      :page([message(2)],change)}),/page-invalid|duplicate-native-id/);
  }
});

test('aborted, incomplete, nonadvancing or oversized scans never return a partial catalog',async()=>{
  const controller=new AbortController();
  await assert.rejects(collectNativeOwnerTextCatalog({...identity,signal:controller.signal,readPage:async()=>{
    controller.abort();return page([message(1)]);
  }}),/stopped/);
  await assert.rejects(collectNativeOwnerTextCatalog({...identity,maxPages:1,readPage:async()=>
    page([message(1)],{position:{descriptorIndex:0,rowid:1},complete:false,nextCursor:'next'})}),/page-limit/);
  await assert.rejects(collectNativeOwnerTextCatalog({...identity,readPage:async()=>
    page([],{position:{descriptorIndex:0,rowid:0},complete:false,nextCursor:'next'})}),/cursor-not-advancing/);
  await assert.rejects(collectNativeOwnerTextCatalog({...identity,maxBytes:20,readPage:async()=>page([message(1)])}),/byte-limit/);
});

test('sender, direction and message-to-physical-row binding cannot be forged by a page',async()=>{
  for(const changed of [{senderId:'someone-else'},{direction:'outgoing'},{rawText:' '},{nativeId:'wechat-server:other'},
    {serverId:'2'},{snapshotSourceId:''}]){
    await assert.rejects(collectNativeOwnerTextCatalog({...identity,readPage:async()=>
      page([{...message(1),...changed}],{observedNativeIds:['wechat-server:1']})}),/message-invalid/);
  }
});
