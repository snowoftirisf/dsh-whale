// Isolated real snapshot/Node inbox/Python bridge acceptance. No wxauto,
// production root, capture, model, message send or real Vault writer is used.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {EventEmitter} from 'node:events';
import {PassThrough,Writable} from 'node:stream';
import {fileURLToPath} from 'node:url';
import {createGroupTextCutover} from './group-text-cutover.mjs';
import {createGroupTextBaselinePublisher} from './group-text-baseline.mjs';
const {createNativeGroupTextRuntime}=await import(process.env.WHALE_GROUP_RUNTIME_TEST_CANDIDATE==='1'?'./group-text-runtime.candidate.mjs':'./group-text-runtime.mjs');
import {createGroupTextCatalogClient} from './group-text-client.mjs';
import {nativeGroupTextPaths} from './group-text-contract.mjs';
import {createGroupIncomingSubmissionGate} from './group-incoming-submit.mjs';
import {createNativeGroupTextInbox} from './native-group-text-inbox.mjs';
import {createSyncManager} from './sync-manager.mjs';

const here=path.dirname(fileURLToPath(import.meta.url));
const workspace=path.resolve(here,'../..');
const python=(process.env.WHALE_TEST_PYTHON||path.join(workspace,'.venv-wxauto/Scripts/python.exe'));
const PROGRAM=String.raw`
import sys,json,sqlite3,uuid,time
from pathlib import Path
from contextlib import closing
from types import SimpleNamespace
from vault.test_snapshot_import import make_snapshot,TABLE,TALKER,OWNER,SENDER
from vault.store import digest_file
from worker_group_text import WorkerGroupTextBridge
from message_watch import SessionGate
root=Path(sys.argv[1]);changed=sys.argv[2]=='changed';snapshots=[]
key='wechat:'+TALKER;target='fixture-group';account='fixture-account'
config={'dataRoot':str(root),'target':'owner-private','accountId':account,'capture':{'enabled':True,'accountUsername':OWNER,'sourceId':'fixture'}}
(root/'config').mkdir(parents=True)
(root/'config/wxauto.local.json').write_text(json.dumps(config),encoding='utf8')
route={'accountId':account,'nativeChatKey':key,'target':target,'sessionId':'original-group-session','chatId':'original-group-chat','status':'active'}
(root/'config/group-routes.local.json').write_text(json.dumps({'version':1,'accountId':account,'groups':[route]}),encoding='utf8')
oldtime=int(time.time())-100
old=(1,9001,1,oldtime,2,'fixture old group text',0)
def make(index,rows):
    snap=make_snapshot(root/('build-'+str(index)),rows=rows,hash_manifest=True)
    file=snap/'snapshot-manifest.json';v=json.loads(file.read_text('utf8'));v['source_id']='fixture'
    v['databases']=[{'database':p.relative_to(snap).as_posix(),'authenticated':True,'plaintext_sha256':digest_file(p)} for p in snap.rglob('*.db')]
    file.write_text(json.dumps(v),encoding='utf8');dest=root/'snapshots/windows-live/snapshots'/v['snapshot_id'];dest.parent.mkdir(parents=True,exist_ok=True);snap.rename(dest)
    return v['snapshot_id']
for index in range(2):snapshots.append(make(index,[old if not(index==1 and changed)else(*old[:5],'changed old text',0)]))
def latest(sid):
    p=root/'snapshots/windows-live/latest.json';p.write_text(json.dumps({'snapshot_id':sid,'import':{'account_id':account}}),encoding='utf8')
latest(snapshots[0])
def emit(v):print(json.dumps(v,ensure_ascii=False),flush=True)
published=[];bridge=WorkerGroupTextBridge(config,publish=published.append,emit=emit)
session_info={'isnew':False,'new_count':0,'preview':'handled stable fixture','time':'fixture'}
monitor=SimpleNamespace(routes={target:'group'},wx=SimpleNamespace(GetSession=lambda:[SimpleNamespace(name=target)]),sessions=lambda:{target:dict(session_info)},gate=SimpleNamespace(handled={target:SessionGate.fingerprint(session_info)}),current=lambda:target,controls=lambda:[],cursor=SimpleNamespace(pending=lambda _target,_controls:[]),startup_pending=set(),startup_uncertain=set(),deferred_direction={})
emit({'event':'fixture-ready','config':config,'snapshots':snapshots,'key':key,'route':route})
try:
    for line in sys.stdin:
        r=json.loads(line);op=r['op'];args=r.get('args',{})
        if op=='fixture-latest':latest(args['snapshotId']);emit({'id':r['id'],'ok':True,'value':{'published':True}})
        elif op=='fixture-append':
            at=int(time.time());sid=make(2,[old,(2,9002,1,at,2,'new ordinary group fixture',0),(3,9003,1,at,2,'a1234567',0)]);latest(sid);emit({'id':r['id'],'ok':True,'value':{'snapshotId':sid}})
        elif op=='fixture-dirty':
            bridge.route({'id':'exact-ui-fixture','target':target,'kind':'group','mediaType':'text','text':'deferred actual fixture','sender':'Member','images':[],'files':[]});emit({'id':r['id'],'ok':True,'value':{'dirty':True}})
        elif op=='fixture-published':emit({'id':r['id'],'ok':True,'value':published})
        else:bridge.command({'op':op,'id':r['id'],'target':target,**args},monitor=monitor)
finally:bridge.close()
`;

async function fixture(t,changed=false,dirtyPrepare=false){
  const root=fs.mkdtempSync(path.join(workspace,'artifacts/group-cutover-integration-'));
  const child=spawn(python,['-B','-u','-X','utf8','-c',PROGRAM,root,changed?'changed':'same'],{cwd:here,windowsHide:true,stdio:['pipe','pipe','pipe']});
  let next=0,resolveReady,rejectReady;const pending=new Map();
  const ready=new Promise((resolve,reject)=>{resolveReady=resolve;rejectReady=reject});
  const lines=createInterface({input:child.stdout});child.stderr.on('data',()=>{});
  lines.on('line',line=>{const v=JSON.parse(line);if(v.event==='fixture-ready'){resolveReady(v);return}
    const item=pending.get(v.id);if(!item)return;pending.delete(v.id);clearTimeout(item.timer);v.ok?item.resolve(v.value):item.reject(Error('fixture-rpc-failed'));});
  child.once('exit',()=>{rejectReady(Error('fixture-worker-exited'));for(const p of pending.values()){clearTimeout(p.timer);p.reject(Error('fixture-worker-exited'))}pending.clear()});
  t.after(async()=>{child.stdin.end();await new Promise(resolve=>{if(child.exitCode!==null)return resolve();child.once('exit',resolve);setTimeout(()=>{child.kill();resolve()},2000).unref()});lines.close();fs.rmSync(root,{recursive:true,force:true})});
  const initial=await ready;
  const rpc=(op,args={})=>new Promise((resolve,reject)=>{const id='fixture-'+(++next);const timer=setTimeout(()=>{pending.delete(id);reject(Error('fixture-rpc-timeout'))},10000);pending.set(id,{resolve,reject,timer});child.stdin.write(JSON.stringify({id,op,args})+'\n')});
  let prepareAckLost=false;
  const requestGroup=async(op,args={})=>{const value=await rpc(op,args);if(op==='group-text-prepare'&&dirtyPrepare&&!prepareAckLost){prepareAckLost=true;await rpc('fixture-dirty');}return value;};
  let captures=0;
  const fakeCapture=()=>{
    const index=captures++,sid=initial.snapshots[index];if(!sid)throw Error('unexpected fixture capture');
    const process=new EventEmitter();process.stdout=new PassThrough();process.stderr=new PassThrough();process.exitCode=null;
    process.kill=()=>{process.exitCode=0;process.stdout.end();process.emit('exit',0)};
    process.stdin=new Writable({write(chunk,_encoding,done){const r=JSON.parse(chunk);setImmediate(()=>{
      const value=r.op==='collect'?{jobId:'job-'+index,candidatePath:path.join(root,'snapshots/windows-live/snapshots',sid),cursorSource:'fixture-source',accountId:'fixture-account',captureReceipt:{snapshot_id:sid,keys_persisted:false}}:
        r.op==='commit'?{committed:true,jobId:'job-'+index}:{aborted:true};
      const finish=()=>process.stdout.write(JSON.stringify({id:r.id,ok:true,value})+'\n');if(r.op==='commit')void rpc('fixture-latest',{snapshotId:sid}).then(finish);else finish();});done()}});return process;
  };
  const sync=createSyncManager({python:'fake-collector',dataRoot:root,capture:{enabled:true,dbRoot:'fixture-only',sourceId:'fixture',accountId:'fixture-account'},
    spawn:fakeCapture,isReady:()=>true,vaultClient:{request:async(op,args)=>op==='status'?{cursors:{}}:op==='import_snapshot'?{complete:true,snapshot_id:path.basename(args.snapshotPath),scanned:1,inserted:0}:{}},timeoutMs:2000});
  t.after(()=>sync.stop());
  const catalogClient=createGroupTextCatalogClient({root:here,dataRoot:root,python,accountId:initial.config.accountId,nativeChatKey:initial.key});
  t.after(()=>catalogClient.close());
  const inputs=[],history=[];let paused=false,scans=0;
  const runtimeOptions={config:initial.config,root:here,requestGroup,clientFactory:()=>createGroupTextCatalogClient({root:here,dataRoot:root,python,accountId:initial.config.accountId,nativeChatKey:initial.key}),
    history:{incoming:async row=>{history.push(structuredClone(row));return {id:'durable-fixture-'+row.nativeId}}},
    routeForGroup:()=>({...initial.route,kind:'group',active:true}),groupKeys:()=>[initial.key],enqueue:async e=>inputs.push(e)};
  const runtime=createNativeGroupTextRuntime(runtimeOptions);t.after(()=>runtime.close());
  const publisher=createGroupTextBaselinePublisher({config:initial.config,nativeChatKey:initial.key});
  const cutover=createGroupTextCutover({config:initial.config,nativeChatKey:initial.key,requestGroup,sync,catalogClient,publisher,runtime,
    getQuiet:async()=>({quiet:true,revision:0,sessionId:initial.route.sessionId}),pauseGroup:()=>{paused=true;return ()=>{paused=false}},
    readActivity:()=>({quiet:true,revision:0}),ensureScanning:async()=>{scans++;await runtime.refresh()},monitorMs:10});
  return {root,rpc,sync,cutover,runtime,runtimeOptions,inputs,history,initial,captures:()=>captures,paused:()=>paused,scans:()=>scans};
}

async function activate(f){const job=f.cutover.activate();await f.cutover.wait(job.attemptId);return f.cutover.status();}
test('actual group SQLite catalog client, Node baseline and Python authority handshake activate without replay',async t=>{
 if(!fs.existsSync(python)){t.skip('Windows fixture Python unavailable');return;}const f=await fixture(t);assert.equal((await activate(f)).outcome,'active');
 assert.equal(f.runtime.active(f.initial.key),true);assert.equal(f.paused(),false);assert.equal(f.captures(),2);
 assert.equal((await f.rpc('group-text-status',{nativeChatKey:f.initial.key})).mode,'native');assert.equal(fs.existsSync(nativeGroupTextPaths({dataRoot:f.root,nativeChatKey:f.initial.key}).authority),true);assert.deepEqual(f.inputs,[]);assert.deepEqual(f.history,[]);
});
test('new authenticated group input reaches durable history, original buffer and real submit gate once, codes remain group text',async t=>{
 if(!fs.existsSync(python)){t.skip('Windows fixture Python unavailable');return;}const f=await fixture(t);assert.equal((await activate(f)).outcome,'active');
 await new Promise(r=>setTimeout(r,1100));await f.rpc('fixture-append');await f.runtime.refresh();assert.equal(f.inputs.length,2);assert.equal(f.history.length,2);
 assert.equal(f.inputs[0].text,'new ordinary group fixture');assert.equal(f.inputs[1].text,'a1234567');assert.ok(f.inputs.every(e=>e.kind==='group'&&e.target===f.initial.route.target));
 const processor=f.runtime.getProcessor(f.initial.key),noVoice={validateSubmitting:()=>[],markSubmitting:()=>({submitted:0})};
 const args={sessionId:f.initial.route.sessionId,target:f.initial.route.target,batch:f.inputs};const gate=createGroupIncomingSubmissionGate({dataRoot:f.root,accountId:f.initial.config.accountId,textProcessor:processor,voiceProcessor:noVoice});assert.equal((await gate.submit(args)).issued,true);
 const inbox=createNativeGroupTextInbox({dataRoot:f.root,accountId:f.initial.config.accountId,nativeChatKey:f.initial.key});assert.equal(inbox.status().submitted,2);
 await assert.rejects(()=>gate.submit(args));f.runtime.close();const restarted=createNativeGroupTextRuntime(f.runtimeOptions);t.after(()=>restarted.close());await restarted.restore();await restarted.refresh();assert.equal(f.inputs.length,2);assert.equal(f.history.length,2);
});
test('real changed catalog aborts to UI before baseline publication',async t=>{
 if(!fs.existsSync(python)){t.skip('Windows fixture Python unavailable');return;}const f=await fixture(t,true);assert.equal((await activate(f)).outcome,'aborted');assert.equal(f.paused(),false);assert.equal((await f.rpc('group-text-status',{nativeChatKey:f.initial.key})).mode,'ui');assert.equal(fs.existsSync(nativeGroupTextPaths({dataRoot:f.root,nativeChatKey:f.initial.key}).authority),false);assert.deepEqual(f.inputs,[]);
});
test('real preparing UI deferral dirties authority, abort returns exact envelope once',async t=>{
 if(!fs.existsSync(python)){t.skip('Windows fixture Python unavailable');return;}const f=await fixture(t,false,true);assert.equal((await activate(f)).outcome,'aborted');assert.equal((await f.rpc('group-text-status',{nativeChatKey:f.initial.key})).mode,'ui');const handed=await f.rpc('fixture-published');assert.equal(handed.length,1);assert.equal(handed[0].id,'exact-ui-fixture');assert.equal(handed[0].text,'deferred actual fixture');assert.deepEqual(f.inputs,[]);assert.deepEqual(await f.rpc('fixture-published'),handed);
});
