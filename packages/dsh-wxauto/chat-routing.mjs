import { createHash } from 'node:crypto';
import fs from 'node:fs';

export function claimRecoveryInbox(file, routes, {denyOwnerTextTarget,denyGroupTextTargets}={}) {
  let data;
  try { data=JSON.parse(fs.readFileSync(file,'utf8')); }
  catch(error) { if(error.code==='ENOENT')return [];throw error; }
  if(data.state==='claimed')return [];
  if(data.version!==1||data.state!=='pending'||!Array.isArray(data.messages)||data.messages.length>20)throw Error('invalid-recovery-inbox');
  for(const message of data.messages) {
    if(routes.get(message.target)?.kind!==message.kind||typeof message.id!=='string'||!message.id.startsWith('recovery-')||typeof message.text!=='string'||typeof message.sender!=='string')throw Error('invalid-recovery-message');
  }
  if(denyOwnerTextTarget&&data.messages.some(message=>message.target===denyOwnerTextTarget
    &&message.kind==='friend'&&(!message.mediaType||message.mediaType==='text')))
    throw Error('native-owner-text-recovery-held');
  if(denyGroupTextTargets!==undefined&&!(denyGroupTextTargets instanceof Set))throw Error('native-group-text-recovery-scope-invalid');
  if(denyGroupTextTargets&&data.messages.some(message=>denyGroupTextTargets.has(message.target)&&message.kind==='group'
    &&(!message.mediaType||message.mediaType==='text')&&!message.images?.length&&!message.files?.length&&!message.audio?.length))
    throw Error('native-group-text-recovery-held');
  // Claim before submission: an uncertain submitted request is never replayed
  // automatically after a crash. This is a one-time pending inbox, not history.
  fs.writeFileSync(file,JSON.stringify({...data,state:'claimed',claimedAt:new Date().toISOString()},null,2));
  return data.messages;
}

export function groupSessionId(target) {
  return 'whale-wx-group-' + createHash('sha256').update(target).digest('hex').slice(0, 24);
}

// One chain per conversation: an unfinished group must not hold other groups.
export function createChatQueue() {
  const tails = new Map(),states=new Map();
  const stateFor=key=>{if(!states.has(key))states.set(key,{pending:0,active:false,locks:0,barrier:null,release:null});return states.get(key);};
  const prune=(key,state)=>{if(!state.pending&&!state.locks&&states.get(key)===state)states.delete(key);};
  const enqueue=(key, work) => {
    const state=stateFor(key);state.pending++;
    const task = (tails.get(key) || Promise.resolve()).then(async()=>{
      // Check again after each released barrier: a second pause may have been
      // installed before this queued continuation regained execution.
      while(state.locks)await state.barrier;
      state.active=true;
      try{return await work();}finally{state.active=false;state.pending--;prune(key,state);}
    });
    const settled = task.catch(() => {});
    tails.set(key, settled);
    settled.then(() => {if (tails.get(key) === settled) tails.delete(key);prune(key,state);});
    return task;
  };
  enqueue.status=key=>{const state=states.get(key);return {pending:state?.pending??0,active:state?.active??false,paused:(state?.locks??0)>0};};
  enqueue.pause=key=>{
    const state=stateFor(key);if(!state.locks)state.barrier=new Promise(resolve=>state.release=resolve);state.locks++;
    let released=false;
    return ()=>{if(released)return;released=true;state.locks--;if(!state.locks){const resolve=state.release;state.release=null;state.barrier=null;resolve();prune(key,state);}};
  };
  // Do not await idle while retaining a pause with queued work: that is an
  // intentional barrier. The cutover orchestrator must inspect/recheck status.
  enqueue.idle=key=>tails.get(key)??Promise.resolve();
  return enqueue;
}

