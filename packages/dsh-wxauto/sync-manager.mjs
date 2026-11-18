// Private snapshot producer + existing sole vault writer. No WeChat UI input,
// network listener, message sending, second SQLite writer, or hidden auto-enable.
import {spawn as spawnProcess} from 'node:child_process';
import {createInterface} from 'node:readline';
import {randomUUID} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const rootDirectory=path.dirname(fileURLToPath(import.meta.url));
const coded=code=>Object.assign(new Error(code),{code});
const codeLike=value=>typeof value==='string'&&/^[a-z][a-z0-9:_-]{0,100}$/.test(value);

function channel(child,timeoutMs) {
  const pending=new Map();let terminal=null;
  const fail=code=>{
    if(terminal)return;terminal=coded(code);
    for(const item of pending.values()){clearTimeout(item.timer);item.reject(terminal)}pending.clear();
  };
  const lines=createInterface({input:child.stdout});
  lines.on('line',line=>{
    let value;try{value=JSON.parse(line)}catch{return}
    const item=pending.get(value.id);if(!item)return;
    clearTimeout(item.timer);pending.delete(value.id);
    value.ok===true?item.resolve(value.value):item.reject(coded(String(value.error||'capture-worker-failed').slice(0,120)));
  });
  child.once('error',()=>fail('capture-worker-start-failed'));
  child.once('exit',()=>fail('capture-worker-exited'));
  child.stdin.on('error',()=>fail('capture-worker-write-failed'));
  child.stderr?.on('data',()=>{}); // Never echo acquisition diagnostics/locals.
  return {
    alive(){return terminal===null;},
    request(op,args={},deadline=timeoutMs) {
      if(terminal)return Promise.reject(terminal);
      return new Promise((resolve,reject)=>{
        const id=randomUUID();
        const timer=setTimeout(()=>{pending.delete(id);reject(coded('capture-worker-timeout'))},deadline);
        pending.set(id,{resolve,reject,timer});
        child.stdin.write(JSON.stringify({id,op,args})+'\n',error=>{
          if(error){const item=pending.get(id);if(item){pending.delete(id);clearTimeout(item.timer);item.reject(coded('capture-worker-write-failed'))}}
        });
      });
    },
    async close(jobId) {
      if(!terminal)await this.request('abort',jobId?{jobId}:{},2000).catch(()=>{});
      child.stdin.end();
      if(!terminal){
        // Only our dedicated collector is terminated if it cannot service abort
        // during a bounded native scan. Never kill/restart the WeChat process.
        child.kill();
      }
      lines.close();fail('capture-worker-closed');
    },
  };
}

export function createSyncManager({python,dataRoot,vaultClient,capture={},isReady=()=>false,
  onState=()=>{},spawn=spawnProcess,intervalMs=300000,timeoutMs=420000}={}) {
  let timer=null,active=null,stopped=false,current=null,revision=0,exclusive=null;
  // One long-lived collector after a successful commit: a fresh Python process
  // per capture re-verified the vendor and re-scanned WeChat memory for keys
  // every time (~15s of each ~18s capture). Any failure still discards it.
  let worker=null;
  const workerFor=()=>{
    if(worker?.ipc.alive())return worker;
    const child=spawn(python,['-u','-X','utf8','-m','vault.capture_worker','--root',dataRoot],{
      cwd:rootDirectory,windowsHide:true,stdio:['pipe','pipe','pipe'],
      env:{...process.env,PYTHONIOENCODING:'utf-8',PYTHONUTF8:'1'},
    });
    worker={child,ipc:channel(child,timeoutMs)};
    return worker;
  };
  const dropWorker=async jobId=>{const w=worker;worker=null;if(w)await w.ipc.close(jobId);};
  let state={enabled:capture.enabled===true,state:'idle',busy:false};
  const report=(name,fields={})=>{
    // Select fields deliberately: no candidate path, raw receipt, chat names,
    // message text, account IDs, memory bytes or keys enter status callbacks.
    state={enabled:capture.enabled===true,state:name,busy:!!active,at:new Date().toISOString(),...fields};
    try{onState({...state})}catch{}
  };
  const waitOrStop=(promise,signal)=>new Promise((resolve,reject)=>{
    if(signal.aborted){reject(coded('sync-stopped'));return}
    const abort=()=>reject(coded('sync-stopped'));
    signal.addEventListener('abort',abort,{once:true});
    Promise.resolve(promise).then(resolve,reject).finally(()=>signal.removeEventListener('abort',abort));
  });
  const assertCandidate=value=>{
    if(!value||typeof value.jobId!=='string'||typeof value.candidatePath!=='string'||typeof value.cursorSource!=='string'
      ||value.accountId!==capture.accountId||value.captureReceipt?.keys_persisted!==false)throw coded('capture-candidate-invalid');
    const relative=path.relative(path.resolve(dataRoot,'snapshots','windows-live'),path.resolve(value.candidatePath));
    if(!relative||relative==='..'||relative.startsWith('..'+path.sep)||path.isAbsolute(relative))throw coded('capture-candidate-path-denied');
    return value;
  };
  const confirmedCommit=async candidate=>{
    if(!candidate)return false;
    try{
      const latest=JSON.parse(await readFile(path.join(dataRoot,'snapshots','windows-live','latest.json'),'utf8'));
      return latest.sync_job_id===candidate.jobId&&latest.snapshot_id===candidate.captureReceipt?.snapshot_id
        &&latest.import?.complete===true&&latest.import?.snapshot_id===latest.snapshot_id;
    }catch{return false}
  };

  async function run(signal) {
    let ipc=null,candidate=null,oldCursor,hadCursor=false,importAttempted=false,commitAttempted=false,committed=false,imported=null;
    try {
      report('collecting',{busy:true});
      ipc=workerFor().ipc;current={ipc,signal};
      const args={db_root:capture.dbRoot,source_id:capture.sourceId,account_id:capture.accountId,
        ...(capture.accountUsername?{account_username:capture.accountUsername}:{}),
        ...(capture.pids?{pids:capture.pids}:{}),scope:capture.scope||'chat',seconds:capture.seconds??90,max_db_mib:capture.maxDbMiB??512,
        ...(capture.includeAudio===true?{include_audio:true}:{})};
      candidate=assertCandidate(await waitOrStop(ipc.request('collect',args),signal));
      if(signal.aborted)throw coded('sync-stopped');
      report('importing');
      const before=await waitOrStop(vaultClient.request('status'),signal);
      hadCursor=Object.hasOwn(before.cursors||{},candidate.cursorSource);oldCursor=before.cursors?.[candidate.cursorSource];
      importAttempted=true;
      imported=await waitOrStop(vaultClient.request('import_snapshot',{snapshotPath:candidate.candidatePath,account_id:candidate.accountId}),signal);
      if(signal.aborted)throw coded('sync-stopped');
      if(imported?.complete!==true||imported.snapshot_id!==candidate.captureReceipt.snapshot_id)throw coded('snapshot-import-not-confirmed');
      report('committing');commitAttempted=true;
      // Once commit is accepted, it may already be published. Await its terminal
      // result instead of treating stop as permission to undo a completed sync.
      const result=await ipc.request('commit',{jobId:candidate.jobId,importResult:imported});
      if(result?.committed!==true||result.jobId!==candidate.jobId)throw coded('capture-commit-not-confirmed');
      committed=true;
      report('complete',{busy:false,scanned:imported.scanned??0,inserted:imported.inserted??0});
      return {ok:true,snapshotId:imported.snapshot_id,scanned:imported.scanned??0,inserted:imported.inserted??0};
    } catch(error) {
      // A pipe loss after publication is not proof of failure. Check our own
      // job's durable commit record before rolling back a verified import.
      if(commitAttempted&&await confirmedCommit(candidate)){
        committed=true;report('complete',{busy:false,ackMissing:true,scanned:imported?.scanned??0,inserted:imported?.inserted??0});
        return {ok:true,snapshotId:imported.snapshot_id,ackMissing:true};
      }
      if(importAttempted&&!committed){
        try{
          if(hadCursor)await vaultClient.request('set_cursor',{source:candidate.cursorSource,cursor:oldCursor});
          else await vaultClient.request('clear_cursor',{source:candidate.cursorSource});
        }catch{
          report('failed',{busy:false,code:'sync-cursor-rollback-failed'});
          return {ok:false,code:'sync-cursor-rollback-failed'};
        }
      }
      // Vault and other helpers reject with a code-shaped message but no .code.
      // Keep that code (never free text, which may carry paths or names).
      const code=signal.aborted?'sync-stopped':(error?.code?String(error.code):codeLike(error?.message)?'sync-failed:'+error.message:'sync-failed').slice(0,120);
      report(signal.aborted?'stopped':'failed',{busy:false,code});return {ok:false,code};
    } finally {
      // Keep the collector only after a clean commit; otherwise abort and discard it.
      if(ipc&&(!committed||stopped||signal.aborted))await dropWorker(committed?undefined:candidate?.jobId);
      current=null;
    }
  }

  const refreshFor=owner=>{
    if(stopped)return Promise.resolve({ok:false,code:'sync-stopped'});
    if(exclusive&&exclusive!==owner)return Promise.resolve({ok:false,code:'sync-exclusive-busy'});
    if(owner&&exclusive!==owner)return Promise.resolve({ok:false,code:'sync-exclusive-expired'});
    if(capture.enabled!==true)return Promise.resolve({ok:false,code:'sync-disabled'});
    if(capture.includeAudio!==undefined&&typeof capture.includeAudio!=='boolean')return Promise.resolve({ok:false,code:'capture-audio-option-invalid'});
    if(active)return active;
    try{if(!isReady())return Promise.resolve({ok:false,code:'wechat-not-ready'})}
    catch{report('failed',{code:'wechat-ready-check-failed'});return Promise.resolve({ok:false,code:'wechat-ready-check-failed'})}
    if(!python||!dataRoot||!capture.dbRoot||!capture.sourceId||!capture.accountId||!vaultClient?.request){
      report('failed',{code:'capture-config-incomplete'});return Promise.resolve({ok:false,code:'capture-config-incomplete'});
    }
    const controller=new AbortController();const own=++revision;
    const promise=run(controller.signal);
    active=promise.finally(()=>{if(own===revision){active=null;state={...state,busy:false}}});
    active.abort=()=>controller.abort();return active;
  };
  const refresh=()=>refreshFor(null);
  const status=()=>({...state,busy:!!active,exclusive:exclusive!==null});
  return {
    start(){
      if(timer||stopped)return;
      if(capture.enabled!==true){report('disabled',{busy:false});return}
      const tick=()=>{void refresh().catch(()=>report('failed',{code:'unexpected-sync-error'}))};
      tick();timer=setInterval(tick,intervalMs);timer.unref?.();
    },
    refresh,
    async exclusiveRefresh(work){
      if(stopped)return {ok:false,code:'sync-stopped'};
      if(exclusive)return {ok:false,code:'sync-exclusive-busy'};
      if(typeof work!=='function')return {ok:false,code:'sync-exclusive-work-invalid'};
      // Acquire before awaiting an existing capture: external ticks cannot join
      // it or slip a new capture between caller preparation and its fresh scan.
      const own={};exclusive=own;
      try{
        if(active)await active;
        if(stopped)return {ok:false,code:'sync-stopped'};
        const result=await work(Object.freeze({refresh:()=>refreshFor(own),status}));
        return stopped?{ok:false,code:'sync-stopped'}:result;
      }finally{
        // Even a callback that forgot to await its refresh cannot leak a running
        // owned capture into the next lease/public refresh.
        try{if(active)await active}finally{if(exclusive===own)exclusive=null}
      }
    },
    async stop(){
      stopped=true;if(timer){clearInterval(timer);timer=null}
      active?.abort?.();
      if(active)await active;
      await dropWorker();
      report('stopped',{busy:false});
    },
    status,
  };
}
