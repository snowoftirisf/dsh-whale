// Explicit first activation only. No constructor IO, model calls or WeChat sends.
import {createHash,randomUUID} from 'node:crypto';
import {normalizeNativeGroupTextCatalog} from './group-text-contract.mjs';
const hash=v=>createHash('sha256').update(JSON.stringify(v)).digest('hex');
const uuid=v=>typeof v==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(v);
const fail=code=>{throw Error('group-text-cutover-'+code);};

export function createGroupTextCutover({config,nativeChatKey,requestGroup,sync,catalogClient,publisher,runtime,
  getQuiet,pauseGroup,readActivity,ensureScanning,isClosed=()=>false,report=()=>{},now=Date.now,monitorMs=250}={}){
  if(!config||!/^wechat:[A-Za-z0-9_-]+@chatroom$/.test(nativeChatKey??'')||[requestGroup,getQuiet,pauseGroup,readActivity,ensureScanning,isClosed,report,now].some(f=>typeof f!=='function')
    ||typeof sync?.exclusiveRefresh!=='function'||typeof catalogClient?.catalog!=='function'
    ||typeof publisher?.prepare!=='function'||typeof publisher?.publish!=='function'||typeof publisher?.discardPublished!=='function'
    ||typeof runtime?.restore!=='function'||typeof runtime?.active!=='function'
    ||!Number.isSafeInteger(monitorMs)||monitorMs<1||monitorMs>5000)fail('config-invalid');
  let state={phase:'idle',authorityMode:'ui',nativeActive:false,canActivate:false},running=null,release=null,closed=false;
  const stopped=()=>closed||isClosed();
  const emit=patch=>{state={...state,...patch};try{report({...state});}catch{}};
  const status=()=>({...state,nativeActive:runtime.active(nativeChatKey)});
  const role=async()=>{
    const value=await requestGroup('group-text-status',{nativeChatKey});
    if(value?.version!==1||value.accountId!==config.accountId||value.nativeChatKey!==nativeChatKey||value.readsStopped===true||!['ui','preparing','native'].includes(value.mode))fail('worker-state-unverified');
    emit({authorityMode:value.mode==='native'?'native':'ui'});return value;
  };
  function catalogIdentity(c){
    normalizeNativeGroupTextCatalog(c,{accountId:config.accountId,nativeChatKey});
    if(!uuid(c.snapshotId))fail('catalog-unverified');
    const rows=c.messages.map(m=>[m.nativeId,m.serverId,m.nativeChatKey,m.senderId,m.senderNumericId,m.rawText,m.timestampSeconds,m.localId]);
    rows.sort((a,b)=>a[0]<b[0]?-1:a[0]>b[0]?1:0);
    const unresolved=[...c.unresolved].map(m=>[m.nativeId,m.serverId,m.nativeChatKey,m.senderNumericId,m.rawText,m.timestampSeconds,m.localId,m.snapshotSourceId,m.reason]).sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b)));
    return hash([c.accountId,c.nativeChatKey,rows,unresolved,[...c.observedNativeIds].sort()]);
  }
  const check=async original=>{
    if(stopped())fail('closed');
    const quiet=await getQuiet({allowPaused:!!release});
    if(quiet?.quiet!==true||typeof quiet.sessionId!=='string'||!quiet.sessionId||!Number.isSafeInteger(quiet.revision))fail('not-quiet');
    if(original&&(quiet.revision!==original.revision||quiet.sessionId!==original.sessionId))fail('activity-changed');
    return quiet;
  };
  async function capture(lease,original){
    const work=lease.refresh();let interrupted=false;
    const timer=setInterval(()=>{
      try{const a=readActivity();if(stopped()||a?.revision!==original.revision||a?.quiet!==true){interrupted=true;work.abort?.();}}
      catch{interrupted=true;work.abort?.();}
    },monitorMs);timer.unref?.();
    try{const value=await work;if(interrupted||value?.ok!==true||!uuid(value.snapshotId))fail('capture-unverified');return value.snapshotId;}
    finally{clearInterval(timer);}
  }
  async function resume(){
    if(stopped())fail('closed');
    emit({phase:'restoring-parent',authorityMode:'native'});
    await runtime.restore();const restored={active:runtime.active(nativeChatKey)};
    if(stopped()||restored?.active!==true||!runtime.active(nativeChatKey))fail('parent-restore-unverified');
    await ensureScanning();
    if(stopped()||!runtime.active(nativeChatKey))fail('parent-restore-unverified');
    release?.();release=null;emit({phase:'active',outcome:'active',authorityMode:'native',nativeActive:true,canActivate:false});
  }
  async function run(){
    let ticket=null,token=null,publishAttempted=false,commitAttempted=false,prepareAttempted=false;
    try{
      emit({phase:'checking'});const initialRole=await role();
      if(initialRole.mode==='native'){await resume();return;}
      if(initialRole.mode!=='ui'||publisher.exists())fail('existing-state');
      const original=await check();release=pauseGroup();await check(original);
      const result=await sync.exclusiveRefresh(async lease=>{
        await check(original);emit({phase:'capturing-before'});
        const firstId=await capture(lease,original),first=await catalogClient.catalog({snapshotId:firstId});
        if(first.snapshotId!==firstId)fail('snapshot-mismatch');const before=catalogIdentity(first);
        await check(original);emit({phase:'quiescing'});
        const prepareStartedAt=now();prepareAttempted=true;
        const prepared=await requestGroup('group-text-prepare',{nativeChatKey});
        if(prepared?.version!==1||prepared.mode!=='preparing'||prepared.dirty||prepared.deferred!==0
          ||typeof prepared.ticket!=='string'||!/^[a-f0-9]{32}$/.test(prepared.ticket)
          ||!Number.isSafeInteger(prepared.preparedAtMs)||prepared.preparedAtMs<=0
          ||prepared.preparedAtMs<prepareStartedAt-1000||prepared.preparedAtMs>now()+1000)fail('prepare-unverified');
        ticket=prepared.ticket;await check(original);emit({phase:'capturing-after'});
        const secondId=await capture(lease,original);
        if(secondId===firstId)fail('capture-not-fresh');
        const second=await catalogClient.catalog({snapshotId:secondId});
        if(second.snapshotId!==secondId||catalogIdentity(second)!==before)fail('catalog-changed');
        await check(original);const current=await role();
        if(current.mode!=='preparing'||current.ticket!==ticket||current.dirty||current.deferred!==0)fail('worker-not-quiet');
        emit({phase:'preparing-baseline'});token=publisher.prepare(second,{cutoverAtMs:prepared.preparedAtMs});
        await check(original);emit({phase:'publishing-baseline'});publishAttempted=true;publisher.publish(token);
        await check(original);const last=await role();
        if(last.mode!=='preparing'||last.ticket!==ticket||last.dirty||last.deferred!==0)fail('worker-not-quiet');
        emit({phase:'committing'});commitAttempted=true;
        const committed=await requestGroup('group-text-commit',{nativeChatKey,ticket,authorityEpoch:token.authorityEpoch});
        if(committed?.version!==1||committed.mode!=='native'||committed.authorityEpoch!==token.authorityEpoch)fail('commit-unverified');
        return {committed:true};
      });
      if(result?.committed!==true)fail('capture-lease-unavailable');
      await resume();
    }catch{
      // Status is queued AFTER any uncertain commit on the same worker pipe.
      // Native is recovered by restore, never by repeating commit or rollback.
      try{
        const current=await role();
        if(current.mode==='native'){
          if(token&&current.authorityEpoch!==token.authorityEpoch)fail('epoch-changed');
          await resume();return;
        }
        if(!['ui','preparing'].includes(current.mode))fail('worker-state-unverified');
        if(current.mode==='preparing'&&!ticket&&prepareAttempted&&/^[a-f0-9]{32}$/.test(current.ticket??''))ticket=current.ticket;
        if(current.mode==='preparing'&&(!ticket||current.ticket!==ticket))fail('ticket-changed');
        if(publishAttempted)publisher.discardPublished(token,{workerMode:current.mode,commitNotActive:true});
        if(ticket&&current.mode==='preparing'){
          const aborted=await requestGroup('group-text-abort',{nativeChatKey,ticket});
          if(aborted?.version!==1||aborted.mode!=='ui')fail('abort-unverified');
        }
        release?.();release=null;
        emit({phase:'aborted',outcome:ticket||publishAttempted?'aborted':'refused',authorityMode:'ui',nativeActive:false,
          canActivate:false,code:commitAttempted?'group-text-cutover-commit-not-active':'group-text-cutover-not-quiet'});
      }catch{emit({phase:'blocked',outcome:'unknown',authorityMode:'blocked',nativeActive:false,canActivate:false,
        code:'group-text-cutover-outcome-unknown'});}
    }
  }
  return Object.freeze({status,
    activate(){
      if(stopped())return {...status(),outcome:'refused',canActivate:false};
      if(running||state.outcome==='unknown')return status();
      if(runtime.active(nativeChatKey))return {...status(),outcome:'active',authorityMode:'native'};
      state={phase:'queued',outcome:'working',attemptId:randomUUID().replaceAll('-',''),authorityMode:'ui',nativeActive:false,canActivate:false};
      running=Promise.resolve().then(run).finally(()=>{running=null;});return status();
    },
    wait(attemptId){if(attemptId!==state.attemptId)fail('attempt-invalid');return running??Promise.resolve();},
    close(){closed=true;catalogClient.close?.();},
  });
}
