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
import {createOwnerTextCutover} from './owner-text-cutover.mjs';
import {createOwnerTextBaselinePublisher} from './owner-text-baseline.mjs';
import {createNativeOwnerTextRuntime} from './owner-text-runtime.mjs';
import {createSyncManager} from './sync-manager.mjs';

const here=path.dirname(fileURLToPath(import.meta.url));
const workspace=path.resolve(here,'../..');
const python=(process.env.WHALE_TEST_PYTHON||path.join(workspace,'.venv-wxauto/Scripts/python.exe'));
const PROGRAM=String.raw`
import sys,json,sqlite3,uuid
from pathlib import Path
from contextlib import closing
from vault.test_snapshot_import import make_snapshot,TABLE,OWNER,SENDER
from vault.snapshot_import import load_reader
from vault.store import digest_file
from native_owner_text_catalog import list_native_owner_texts
from worker_owner_text import WorkerOwnerTextBridge
root=Path(sys.argv[1]);change=sys.argv[2]=='changed';snapshots=[]
for index in range(2):
    base=root/('build-'+str(index));snap=make_snapshot(base,rows=[(1,9001,1,1772323200,2,'fixture content'+(' changed' if index==1 and change else ''),0)],hash_manifest=True)
    table=load_reader().message_table(SENDER)
    with closing(sqlite3.connect(snap/'message/message_0.db')) as db:
        db.execute(f'ALTER TABLE [{TABLE}] RENAME TO [{table}]');db.commit()
    file=snap/'snapshot-manifest.json';v=json.loads(file.read_text('utf8'));v['source_id']='fixture'
    v['databases']=[{'database':p.relative_to(snap).as_posix(),'authenticated':True,'plaintext_sha256':digest_file(p)} for p in snap.rglob('*.db')]
    file.write_text(json.dumps(v),encoding='utf8');dest=root/'snapshots/windows-live/snapshots'/v['snapshot_id'];dest.parent.mkdir(parents=True,exist_ok=True);snap.rename(dest);snapshots.append(v['snapshot_id'])
config={'dataRoot':str(root),'target':'owner-fixture','accountId':'fixture-account','capture':{'enabled':True,'accountUsername':OWNER,'sourceId':'fixture'},'nativeVoiceSources':{'ownerNativeChatKey':'wechat:'+SENDER}}
def emit(value):print(json.dumps(value,ensure_ascii=False),flush=True)
published=[];bridge=WorkerOwnerTextBridge(config,publish=published.append,emit=emit)
emit({'event':'fixture-ready','config':config,'snapshots':snapshots})
try:
    for line in sys.stdin:
        request=json.loads(line);op=request['op'];args=request.get('args',{})
        if op=='catalog':
            sid=args.get('snapshotId',snapshots[-1])
            value=list_native_owner_texts(root/'snapshots/windows-live/snapshots'/sid,expected_account_username=OWNER,expected_source_id='fixture',account_id=config['accountId'],owner_native_chat_key='wechat:'+SENDER,expected_peer_username=SENDER,expected_snapshot_id=sid)
            emit({'id':request['id'],'ok':True,'value':value})
        else:bridge.command({'op':op,'id':request['id'],'target':config['target'],**args},dispatcher=None)
finally:bridge.close()
`;

async function fixture(t,changed=false,losePrepareAck=false){
  const root=fs.mkdtempSync(path.join(workspace,'artifacts/owner-cutover-integration-'));
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
  const requestOwner=async(op,args={})=>{
    const value=await rpc(op,args);
    if(op==='owner-text-prepare'&&losePrepareAck&&!prepareAckLost){prepareAckLost=true;throw Error('fixture-prepare-ack-lost')}
    return value;
  };
  let captures=0;
  const fakeCapture=()=>{
    const index=captures++,sid=initial.snapshots[index];if(!sid)throw Error('unexpected fixture capture');
    const process=new EventEmitter();process.stdout=new PassThrough();process.stderr=new PassThrough();process.exitCode=null;
    process.kill=()=>{process.exitCode=0;process.stdout.end();process.emit('exit',0)};
    process.stdin=new Writable({write(chunk,_encoding,done){const r=JSON.parse(chunk);setImmediate(()=>{
      const value=r.op==='collect'?{jobId:'job-'+index,candidatePath:path.join(root,'snapshots/windows-live/snapshots',sid),cursorSource:'fixture-source',accountId:'fixture-account',captureReceipt:{snapshot_id:sid,keys_persisted:false}}:
        r.op==='commit'?{committed:true,jobId:'job-'+index}:{aborted:true};
      process.stdout.write(JSON.stringify({id:r.id,ok:true,value})+'\n');});done()}});return process;
  };
  const sync=createSyncManager({python:'fake-collector',dataRoot:root,capture:{enabled:true,dbRoot:'fixture-only',sourceId:'fixture',accountId:'fixture-account'},
    spawn:fakeCapture,isReady:()=>true,vaultClient:{request:async(op,args)=>op==='status'?{cursors:{}}:op==='import_snapshot'?{complete:true,snapshot_id:path.basename(args.snapshotPath),scanned:1,inserted:0}:{}},timeoutMs:2000});
  t.after(()=>sync.stop());
  const catalogClient={catalog:args=>rpc('catalog',args),close(){}};
  const inputs=[],history=[];let paused=false,scans=0;
  const runtime=createNativeOwnerTextRuntime({config:initial.config,root,requestOwner,catalogClient,
    history:{incoming:async row=>{history.push(row);return {id:'fixture'}}},routeForOwner:()=>({target:initial.config.target,kind:'friend',sessionId:'original-session',active:true}),enqueue:async e=>inputs.push(e)});
  t.after(()=>runtime.close());
  const publisher=createOwnerTextBaselinePublisher({config:initial.config});
  const cutover=createOwnerTextCutover({config:initial.config,requestOwner,sync,catalogClient,publisher,runtime,
    getQuiet:async()=>({quiet:true,revision:0,sessionId:'original-session'}),pauseOwner:()=>{paused=true;return ()=>{paused=false}},
    readActivity:()=>({quiet:true,revision:0}),ensureScanning:async()=>{scans++;await runtime.refresh()},monitorMs:10});
  return {root,rpc,sync,cutover,runtime,inputs,history,captures:()=>captures,paused:()=>paused,scans:()=>scans};
}

test('real authenticated snapshots + Python bridge + Node baseline commit activate without replay',async t=>{
  if(!fs.existsSync(python)){t.skip('existing Windows history Python unavailable');return}
  const f=await fixture(t),queued=f.cutover.activate();await f.cutover.wait(queued.attemptId);
  assert.equal(f.cutover.status().outcome,'active');assert.equal(f.runtime.active(),true);assert.equal(f.paused(),false);
  assert.equal((await f.rpc('owner-text-status')).mode,'native');assert.equal(f.captures(),2);assert.equal(f.scans(),1);
  assert.equal(fs.existsSync(path.join(f.root,'config/native-owner-text-authority.json')),true);
  assert.deepEqual(f.inputs,[]);assert.deepEqual(f.history,[]);
});

test('real catalog content changes abort preparing ticket before publication and retain UI authority',async t=>{
  if(!fs.existsSync(python)){t.skip('existing Windows history Python unavailable');return}
  const f=await fixture(t,true),queued=f.cutover.activate();await f.cutover.wait(queued.attemptId);
  assert.equal(f.cutover.status().outcome,'aborted');assert.equal(f.runtime.active(),false);assert.equal(f.paused(),false);
  assert.equal((await f.rpc('owner-text-status')).mode,'ui');assert.equal(f.captures(),2);
  assert.equal(fs.existsSync(path.join(f.root,'config/native-owner-text-inbox.json')),false);
  assert.equal(fs.existsSync(path.join(f.root,'config/native-owner-text-authority.json')),false);
  assert.deepEqual(f.inputs,[]);assert.deepEqual(f.history,[]);
});

test('lost real prepare acknowledgement recovers same-worker ticket and aborts without native initialization',async t=>{
  if(!fs.existsSync(python)){t.skip('existing Windows history Python unavailable');return}
  const f=await fixture(t,false,true),queued=f.cutover.activate();await f.cutover.wait(queued.attemptId);
  assert.equal(f.cutover.status().outcome,'aborted');assert.equal(f.paused(),false);assert.equal(f.runtime.active(),false);
  assert.equal((await f.rpc('owner-text-status')).mode,'ui');assert.equal(f.captures(),1);
  assert.equal(fs.existsSync(path.join(f.root,'config/native-owner-text-inbox.json')),false);
  assert.equal(fs.existsSync(path.join(f.root,'config/native-owner-text-authority.json')),false);
  assert.deepEqual(f.inputs,[]);
});
