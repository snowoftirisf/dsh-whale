import {fileURLToPath as __testFile} from 'node:url';
import test from 'node:test';import assert from 'node:assert/strict';import path from 'node:path';import {EventEmitter} from 'node:events';
import {createGroupTextCatalogClient} from './group-text-client.mjs';import {groupTextScopeDigest} from './native-group-text-catalog.mjs';
import fs from 'node:fs';import {spawnSync} from 'node:child_process';
const accountId='account',nativeChatKey='wechat:fixture@chatroom',snapshotId='11111111-1111-4111-8111-111111111111';
const page=(over={})=>({source:'authenticated-native-group-text-catalog-page',accountId,nativeChatKey,snapshotId,scopeDigest:groupTextScopeDigest(accountId,nativeChatKey),scanScopeDigest:'b'.repeat(64),messages:[],unresolved:[],observedNativeIds:[],scanned:0,position:{descriptorIndex:1,rowid:0},complete:true,nextCursor:null,...over});
function fixture(respond,timeoutMs=200){const calls=[],children=[];const client=createGroupTextCatalogClient({root:path.dirname(__testFile(import.meta.url)),dataRoot:path.resolve('artifacts/group-fixture'),python:path.resolve('fixture-python.exe'),accountId,nativeChatKey,timeoutMs,
 spawnProcess:(exe,args,options)=>{const c=new EventEmitter();c.stdout=new EventEmitter();c.stderr={resume(){}};c.stdin=new EventEmitter();c.kills=0;c.kill=()=>{c.killed=true;c.kills++;};
 c.stdin.end=input=>{c.input=JSON.parse(input);respond?.(c,children.length-1);};children.push(c);calls.push({exe,args,options});return c;}});return {client,calls,children};}
const reply=(child,value)=>queueMicrotask(()=>{child.stdout.emit('data',Buffer.from(JSON.stringify({ok:true,value})));child.emit('close',0);});

test('inert constructor, fixed selector/path and all-page snapshot pin',async()=>{
 const f=fixture((c,n)=>reply(c,n===0?page({scanned:1,complete:false,nextCursor:'first',position:{descriptorIndex:0,rowid:1}}):page()));
 assert.equal(f.calls.length,0);const value=await f.client.catalog();assert.equal(value.complete,true);assert.equal(f.calls.length,2);
 assert.equal(f.children[1].input.snapshotId,snapshotId);assert.equal(f.children[1].input.nativeChatKey,nativeChatKey);
 assert.equal(f.children[1].input.scanKey,f.children[0].input.scanKey);assert.equal(f.calls[0].options.windowsHide,true);
 assert.equal(path.basename(f.calls[0].args[1]),'native_group_text_io.py');f.client.close();
});

test('close/abort/timeout kill only owned IO child without retry',async()=>{
 for(const mode of ['close','abort','timeout']){const f=fixture(null,5),controller=new AbortController();const work=f.client.catalog({signal:controller.signal});
 if(mode==='close')f.client.close();if(mode==='abort')controller.abort();await assert.rejects(work,/stopped|timeout/);assert.equal(f.children.length,1);assert.equal(f.children[0].kills,1);f.client.close();}
});

test('malformed subprocess or cross-snapshot page cannot publish partial',async()=>{
 for(const bad of ['json','scope']){const f=fixture((c,n)=>{if(bad==='json')queueMicrotask(()=>{c.stdout.emit('data',Buffer.from('invalid'));c.emit('close',0);});
 else reply(c,n===0?page({scanned:1,complete:false,nextCursor:'first',position:{descriptorIndex:0,rowid:1}}):page({snapshotId:'22222222-2222-4222-8222-222222222222'}));});
 await assert.rejects(f.client.catalog(),/response-invalid|page-invalid/);f.client.close();}
});

test('actual Python authenticated SQLite -> private IO -> Node full catalog, fixture only',async()=>{
 const root=fs.mkdtempSync(path.resolve('artifacts/group-text-io-fixture-'));
 const python=(process.env.WHALE_TEST_PYTHON||path.resolve('.venv-wxauto/Scripts/python.exe')),plugin=path.dirname(__testFile(import.meta.url));
 const script=`import json,sys,uuid\nfrom pathlib import Path\nfrom vault.test_snapshot_import import make_snapshot,OWNER,TALKER,SENDER\nr=Path(sys.argv[1]);(r/'config').mkdir();s=str(uuid.uuid4());p=r/'snapshots/windows-live/snapshots';p.mkdir(parents=True)\na=make_snapshot(p/s,rows=[(1,9001,1,1772323200,2,'hello',0),(2,9002,1,1772323201,9,'wxid_fake:\\nnot author',0),(3,0,1,1772323202,2,'pending server',0),(4,9004,1,1772323203,1,'own',0)],hash_manifest=True)\nf=a/'snapshot-manifest.json';v=json.loads(f.read_text());v['snapshot_id']=s;v['source_id']='fixture';f.write_text(json.dumps(v))\n(r/'config/wxauto.local.json').write_text(json.dumps({'dataRoot':str(r),'accountId':'account','target':'owner','capture':{'accountUsername':OWNER,'sourceId':'fixture'}}))\n(r/'config/group-routes.local.json').write_text(json.dumps({'version':1,'accountId':'account','groups':[{'accountId':'account','nativeChatKey':'wechat:'+TALKER,'target':'group','sessionId':'session-group','status':'active'}]}))\n(r/'snapshots/windows-live/latest.json').write_text(json.dumps({'snapshot_id':s,'import':{'account_id':'account'}}))\n`;
 let client;
 try{
  const built=spawnSync(python,['-B','-X','utf8','-c',script,root],{cwd:plugin,windowsHide:true,encoding:'utf8'});
  assert.equal(built.status,0,built.stderr);
  client=createGroupTextCatalogClient({root:plugin,dataRoot:root,python,accountId:'account',nativeChatKey:'wechat:group_fixture@chatroom'});
  const result=await client.catalog();assert.equal(result.messages.length,1);assert.equal(result.messages[0].senderNumericId,2);
  assert.equal(result.unresolved.length,2);assert.equal(result.unresolved[0].reason,'sender-unresolved');
  assert.equal(result.unresolved[1].nativeId,null);assert.equal(result.observedNativeIds.length,3);
 }finally{client?.close();fs.rmSync(root,{recursive:true,force:true});}
});

test('pending admission proof is forwarded to fixed private IO without target/session scope arguments',async()=>{
 const proof={claimId:'a'.repeat(32),claimHash:'c'.repeat(64)};
 const f=fixture(c=>reply(c,page({admission:{...proof,snapshotId,verified:true}})));
 const result=await f.client.catalog({snapshotId,admissionProof:proof});
 assert.deepEqual(f.children[0].input.admissionProof,proof);assert.deepEqual(result.admission,{...proof,snapshotId,verified:true});
 assert.equal(Object.hasOwn(f.children[0].input,'target'),false);assert.equal(Object.hasOwn(f.children[0].input,'sessionId'),false);f.client.close();
});

test('actual queued authenticated unread source authorizes only its pending snapshot through full client',async()=>{
 const root=fs.mkdtempSync(path.resolve('artifacts/group-text-pending-io-fixture-'));
 const python=(process.env.WHALE_TEST_PYTHON||path.resolve('.venv-wxauto/Scripts/python.exe')),plugin=path.dirname(__testFile(import.meta.url));let client;
 const script=`import json,sys,shutil\nfrom pathlib import Path\nfrom test_native_group_handoff import NativeHandoffTests\nf=NativeHandoffTests();f.setUp()\ntry:\n r=Path(sys.argv[1]);shutil.copytree(f.data,r,dirs_exist_ok=True);c=f.config;c['dataRoot']=str(r);c['target']='owner';key=f.claim['nativeChatKey']\n (r/'config/wxauto.local.json').write_text(json.dumps(c));(r/'config/group-routes.local.json').write_text(json.dumps({'version':1,'accountId':c['accountId'],'groups':[{'accountId':c['accountId'],'nativeChatKey':key,'target':f.claim['target'],'sessionId':f.claim['sessionId'],'chatId':'canonical-group','status':'pending'}]}))\n print(json.dumps({'accountId':c['accountId'],'key':key,'snapshotId':f.claim['snapshotId'],'claimId':f.claim['claimId'],'claimHash':f.claim['claimHash']}))\nfinally:f.doCleanups()\n`;
 try{
  const built=spawnSync(python,['-B','-X','utf8','-c',script,root],{cwd:plugin,windowsHide:true,encoding:'utf8'});assert.equal(built.status,0,built.stderr);
  const input=JSON.parse(built.stdout);client=createGroupTextCatalogClient({root:plugin,dataRoot:root,python,accountId:input.accountId,nativeChatKey:input.key});
  const proof={claimId:input.claimId,claimHash:input.claimHash};const result=await client.catalog({snapshotId:input.snapshotId,admissionProof:proof});
  assert.equal(result.messages.length,2);assert.deepEqual(result.admission,{...proof,snapshotId:input.snapshotId,verified:true});
  await assert.rejects(client.catalog({snapshotId:input.snapshotId}),/unavailable/);
 }finally{client?.close();fs.rmSync(root,{recursive:true,force:true});}
});
