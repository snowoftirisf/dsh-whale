import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {randomUUID} from 'node:crypto';

// One private writer at a time. Only a confirmed exit (or failed spawn without
// a PID) permits replacement. Timeouts never cause a parallel writer or replay.
export function createVaultProcess({python,cwd,dataRoot,onError=()=>{},onState=()=>{},
  spawnWorker=spawn,timeoutMs=120000,restartDelayMs=5000,now=Date.now}) {
  let closed=false,current=null,generation=0,retryAfter=0;
  const notify=code=>{try{onError(code);}catch{}};
  const announce=(state,label)=>{try{onState({state:label,generation:state?.generation??generation});}catch{}};
  const rejectPending=(state,code)=>{
    for(const pending of state.pending.values()){clearTimeout(pending.timer);pending.reject(Error(code));}
    state.pending.clear();
  };
  function start() {
    if(closed)throw Error('vault-client-closed');
    if(current&&!current.terminal){if(current.failed)throw Error('vault-worker-unavailable');return current;}
    if(now()<retryAfter)throw Error('vault-worker-restart-backoff');
    const state={child:null,lines:null,pending:new Map(),terminal:false,failed:false,generation:++generation};
    current=state;
    const terminal=()=>{
      if(state.terminal)return;
      state.terminal=true;state.failed=true;retryAfter=now()+restartDelayMs;
      rejectPending(state,'vault-worker-unavailable-outcome-unknown');state.lines?.close();
      announce(state,'exited');
      if(!closed)notify('vault-worker-exited');
    };
    try {
      const child=spawnWorker(python,['-X','utf8','-u','-m','vault.cli','--root',dataRoot,'worker'],
        {cwd,windowsHide:true,stdio:['pipe','pipe','pipe']});
      state.child=child;
      announce(state,'starting');
      const unavailable=()=>{
        state.failed=true;rejectPending(state,'vault-worker-unavailable-outcome-unknown');
        announce(state,'unavailable');
        if(!closed&&!state.terminal)notify('vault-worker-unavailable');
      };
      child.stdin.on('error',unavailable);
      child.stdout.on('error',unavailable);
      child.stderr.on('error',()=>{});
      child.on('error',()=>{
        state.failed=true;rejectPending(state,'vault-worker-unavailable-outcome-unknown');
        announce(state,'unavailable');
        // Node's spawn failure has no process to wait for. An error on a live
        // child does not prove its exit and must not permit a second writer.
        if(!Number.isInteger(child.pid)||child.pid<=0)terminal();
        else notify('vault-worker-unavailable');
      });
      child.on('exit',terminal);
      child.stderr.on('data',()=>{});
      const lines=createInterface({input:child.stdout});state.lines=lines;
      lines.on('error',unavailable);
      lines.on('line',line=>{
        let result;try{result=JSON.parse(line);}catch{return;}
        const pending=state.pending.get(result.id);if(!pending)return;
        clearTimeout(pending.timer);state.pending.delete(result.id);
        if(!state.ready&&!state.failed){state.ready=true;announce(state,'ready');}
        result.ok?pending.resolve(result.value):pending.reject(Error('vault-'+result.error));
      });
      lines.on('close',()=>{
        if(state.terminal||closed)return;
        state.failed=true;rejectPending(state,'vault-worker-unavailable-outcome-unknown');
        announce(state,'unavailable');
        notify('vault-worker-output-closed');
      });
      return state;
    } catch(error) {
      terminal();throw error;
    }
  }
  // Preserve the existing eager startup, with recovery driven by a later call.
  try{start();}catch{notify('vault-worker-unavailable');}
  return {
    request(op,args={}) {
      let state;try{state=start();}catch(error){return Promise.reject(error);}
      return new Promise((resolve,reject)=>{
        const id=randomUUID();
        const timer=setTimeout(()=>{state.pending.delete(id);reject(Error('vault-request-timeout-outcome-unknown'));},timeoutMs);
        state.pending.set(id,{resolve,reject,timer});
        const fail=error=>{const pending=state.pending.get(id);if(!pending)return;
          clearTimeout(timer);state.pending.delete(id);reject(error);};
        try {
          state.child.stdin.write(JSON.stringify({id,op,args})+'\n',error=>{
            if(error)fail(Error('vault-request-write-outcome-unknown'));
          });
        } catch {fail(Error('vault-request-write-outcome-unknown'));}
      });
    },
    status(){return {closed,generation,terminal:current?.terminal??true,unavailable:current?.failed??true};},
    close() {
      if(closed)return;closed=true;
      announce(current,'closed');
      if(!current)return;
      rejectPending(current,'vault-client-closed-outcome-unknown');
      if(!current.terminal)try{current.child.stdin.end(JSON.stringify({op:'stop'})+'\n');}catch{}
      current.lines?.close();
    },
  };
}
