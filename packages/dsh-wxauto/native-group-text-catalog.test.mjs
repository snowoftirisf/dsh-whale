import test from 'node:test';import assert from 'node:assert/strict';
import {collectNativeGroupTextCatalog,groupTextScopeDigest} from './native-group-text-catalog.mjs';
const accountId='account',nativeChatKey='wechat:fixture@chatroom',snapshotId='11111111-1111-4111-8111-111111111111';
const source=i=>JSON.stringify(['message/message_0.db','Msg_'+'a'.repeat(32),i]);
const message=i=>({nativeId:'wechat-server:'+i,serverId:String(i),nativeChatKey,senderId:'member',senderDisplayName:'成员',senderNumericId:2,
 rawText:'text '+i,renderedText:'text '+i,renderStatus:'raw-preserved',timestampSeconds:1700000000+i,localId:i,snapshotSourceId:source(i),direction:'incoming'});
const page=(over={})=>({source:'authenticated-native-group-text-catalog-page',accountId,nativeChatKey,snapshotId,scopeDigest:groupTextScopeDigest(accountId,nativeChatKey),scanScopeDigest:'b'.repeat(64),
 messages:[],unresolved:[],observedNativeIds:[],scanned:0,position:{descriptorIndex:1,rowid:0},complete:true,nextCursor:null,...over});
const collect=(readPage,rest={})=>collectNativeGroupTextCatalog({readPage,accountId,nativeChatKey,...rest});

test('all pages aggregate outgoing-only progress, resolved, held and null-ID diagnostics',async()=>{
 const calls=[];const result=await collect(async args=>{calls.push(args);return calls.length===1?page({scanned:2,observedNativeIds:['wechat-server:99'],complete:false,nextCursor:'opaque',position:{descriptorIndex:0,rowid:2}}):page({scanned:3,
   messages:[message(3)],observedNativeIds:['wechat-server:3','wechat-server:4'],unresolved:[
     {nativeId:'wechat-server:4',serverId:'4',nativeChatKey,rawText:'unmapped',timestampSeconds:1700000004,localId:4,snapshotSourceId:source(4),senderNumericId:9,reason:'sender-unresolved',direction:'unknown'},
     {nativeId:null,serverId:null,nativeChatKey,rawText:'pending server',timestampSeconds:1700000005,localId:5,snapshotSourceId:source(5),senderNumericId:2,reason:'server-id-unresolved',direction:'unknown'}]});});
 assert.equal(result.complete,true);assert.equal(result.messages.length,1);assert.equal(result.unresolved.length,2);assert.equal(result.observedNativeIds.length,3);
 assert.equal(calls[1].snapshotId,snapshotId);assert.equal(calls[1].scanKey,calls[0].scanKey);assert.equal(JSON.stringify(result).includes(calls[0].scanKey),false);
});

test('1001 resolved IDs do not truncate',async()=>{
 let count=0;const result=await collect(async()=>{const start=count;const take=Math.min(500,1001-count);count+=take;const rows=Array.from({length:take},(_,i)=>message(start+i+1));
 return page({messages:rows,observedNativeIds:rows.map(r=>r.nativeId),scanned:take,complete:count===1001,nextCursor:count===1001?null:'cursor'+count,position:{descriptorIndex:count===1001?1:0,rowid:count===1001?0:count}});});
 assert.equal(result.messages.length,1001);
});

test('cross-page duplicates, snapshot/scope/account change reject entire collection',async()=>{
 for(const over of [{snapshotId:'22222222-2222-4222-8222-222222222222'},{accountId:'foreign'},{nativeChatKey:'wechat:other@chatroom'},{scanScopeDigest:'c'.repeat(64)},{}]){
 let n=0;await assert.rejects(collect(async()=>++n===1?page({scanned:1,messages:[message(1)],observedNativeIds:['wechat-server:1'],complete:false,nextCursor:'first',position:{descriptorIndex:0,rowid:1}}):page({scanned:1,messages:[message(1)],observedNativeIds:['wechat-server:1'],...over})),/page-invalid|duplicate-native-id/);}
});

test('malformed render and identity envelope cannot become input',async()=>{
 for(const over of [{senderNumericId:null},{senderId:''},{renderedText:'different'},{nativeChatKey:'wechat:other@chatroom'},{direction:'outgoing'},{snapshotSourceId:'["../private","table",1]'}]){
 await assert.rejects(collect(async()=>page({scanned:1,messages:[{...message(1),...over}],observedNativeIds:['wechat-server:1']})),/message-invalid/);}
});

test('cursor loop/empty nonadvance, resource caps and mid-scan failure never return partial',async()=>{
 await assert.rejects(collect(async()=>page({complete:false,nextCursor:'loop',position:{descriptorIndex:0,rowid:0}})),/cursor-not-advancing/);
 await assert.rejects(collect(async()=>page(),{maxBytes:1}),/byte-limit/);
 await assert.rejects(collect(async()=>page({scanned:2}),{maxIdentities:1}),/identity-limit/);
 await assert.rejects(collect(async()=>page({scanned:1,complete:false,nextCursor:'more',position:{descriptorIndex:0,rowid:1}}),{maxPages:1}),/page-limit/);
 let n=0;await assert.rejects(collect(async()=>{if(++n===2)throw Error('fixture-mid-scan-failure');return page({scanned:1,complete:false,nextCursor:'more',position:{descriptorIndex:0,rowid:1}});}),/fixture-mid-scan/);
});

test('abort and non-group scope never read a page',async()=>{
 let count=0;const readPage=async()=>{count++;return page();};const signal=AbortSignal.abort();
 await assert.rejects(collect(readPage,{signal}),/stopped/);await assert.rejects(collect(readPage,{nativeChatKey:'wechat:owner'}),/config-invalid/);assert.equal(count,0);
});

test('pending proof is fixed to explicit original snapshot on every page and survives only authenticated metadata',async()=>{
 const proof={claimId:'a'.repeat(32),claimHash:'c'.repeat(64)},expected={...proof,snapshotId,verified:true};let calls=0;
 const result=await collect(async args=>{calls++;assert.deepEqual(args.admissionProof,{claimId:'a'.repeat(32),claimHash:'c'.repeat(64)});
   assert.equal(args.snapshotId,snapshotId);proof.claimId='d'.repeat(32);
   return page({admission:expected,...(calls===1?{scanned:1,complete:false,nextCursor:'pending-next',position:{descriptorIndex:0,rowid:1}}:{})});
 },{snapshotId,admissionProof:proof});
 assert.deepEqual(result.admission,expected);assert.equal(calls,2);
});

test('unrequested/missing/forged admission metadata and proof latest scope refuse',async()=>{
 const proof={claimId:'a'.repeat(32),claimHash:'c'.repeat(64)},expected={...proof,snapshotId,verified:true};
 for(const admission of [undefined,{...expected,verified:false},{...expected,claimHash:'d'.repeat(64)},
   {...expected,snapshotId:'22222222-2222-4222-8222-222222222222'},{...expected,extra:true}]){
   await assert.rejects(collect(async()=>page({...admission?{admission}:{}}),{snapshotId,admissionProof:proof}),/admission-source-invalid/);
 }
 await assert.rejects(collect(async()=>page({admission:expected})),/admission-source-unrequested/);
 let read=false;await assert.rejects(collect(async()=>{read=true;return page();},{admissionProof:proof}),/admission-invalid/);assert.equal(read,false);
});
