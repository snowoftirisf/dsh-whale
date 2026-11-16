// Private new-group handoff RPC. Never broadens ordinary active-group RPC.
import {randomUUID,createHash} from 'node:crypto';
const hex=(v,n)=>typeof v==='string'&&new RegExp('^[a-f0-9]{'+n+'}$').test(v);
const signature=r=>JSON.stringify([r.accountId,r.nativeChatKey,r.target,r.sessionId,r.chatId,r.claimId,r.claimHash,r.authorityEpoch]);
export function groupTextAdmissionRpcClient({write,pendingRoute,isReady=()=>true,workerInstanceId=()=>null,timeoutMs=45000}={}){
 if(typeof write!=='function'||typeof pendingRoute!=='function'||typeof isReady!=='function'||typeof workerInstanceId!=='function'||!Number.isSafeInteger(timeoutMs)||timeoutMs<1)throw Error('group-text-admission-client-invalid');
 let closed=false;const pending=new Map(),notIssued=new WeakMap();
 const argsSignature=a=>JSON.stringify([a.nativeChatKey,a.claimId,a.claimHash,a.authorityEpoch]);
 const fingerprint=w=>createHash('sha256').update(JSON.stringify([w.op,w.id,w.target,w.nativeChatKey,w.claimId,w.claimHash,w.authorityEpoch])).digest('hex');
 function request(args,readOnly=false){
  if(closed||!isReady())return Promise.reject(Error('group-text-admission-worker-unavailable'));
  if(!args||typeof args!=='object'||Array.isArray(args)||!['authorityEpoch,claimHash,claimId,nativeChatKey',...(readOnly?['authorityEpoch,nativeChatKey']:[])].includes(Object.keys(args).sort().join(','))
   ||!/^wechat:[A-Za-z0-9_-]+@chatroom$/.test(args.nativeChatKey??'')||(!readOnly||args.claimId!==undefined||args.claimHash!==undefined)&&(!hex(args.claimId,32)||!hex(args.claimHash,64))||!hex(args.authorityEpoch,32))return Promise.reject(Error('group-text-admission-request-invalid'));
  let route;try{route=pendingRoute(args);}catch{}
  if(!route||route.kind!=='group'||!readOnly&&route.active!==false||!route.target||!route.sessionId||!route.chatId||route.nativeChatKey!==args.nativeChatKey||route.claimId!==args.claimId||route.claimHash!==args.claimHash||route.authorityEpoch!==args.authorityEpoch||!readOnly&&route.admitAllowed!==true)return Promise.reject(Error('group-text-admission-route-unverified'));
  return new Promise((resolve,reject)=>{const id='group-text-admit:'+randomUUID(),timer=setTimeout(()=>{pending.delete(id);reject(Error('group-text-admission-outcome-unknown'));},timeoutMs);const wire=readOnly?{op:'group-text-status',id,target:route.target,nativeChatKey:args.nativeChatKey}:{op:'group-text-admit',id,target:route.target,...args};pending.set(id,{resolve,reject,timer,args:structuredClone(args),binding:signature(route),active:route.active,wire,workerInstanceId:workerInstanceId()});
   const fail=err=>{if(!err)return;const p=pending.get(id);if(p){clearTimeout(p.timer);pending.delete(id);reject(Error('group-text-admission-outcome-unknown'));}};
   try{write(JSON.stringify(wire)+'\n',fail);}catch{fail(true);}
  });
 }
 return Object.freeze({request:args=>request(args),status:args=>request(args,true),handle(e){if(e?.event!=='group-text-result')return false;const p=pending.get(e.id);if(!p)return false;clearTimeout(p.timer);pending.delete(e.id);
  let route;try{route=pendingRoute(p.args);}catch{}
  if(!route||route.kind!=='group'||route.active!==p.active||signature(route)!==p.binding)p.reject(Error('group-text-admission-route-changed'));
  else if(e.ok===true)p.resolve(e.value);else {
   const error=Error(/^group-text-[a-z-]{1,100}$/.test(e.code??'')?e.code:'group-text-admission-denied');
   if(p.wire.op==='group-text-admit'&&e.code==='group-text-worker-busy'&&e.issuance==='not-issued'&&e.phase==='before-admit'
    &&hex(p.workerInstanceId,32)&&workerInstanceId()===p.workerInstanceId&&e.workerInstanceId===p.workerInstanceId&&e.requestFingerprint===fingerprint(p.wire))
    notIssued.set(error,{args:argsSignature(p.args),workerInstanceId:p.workerInstanceId,requestId:p.wire.id,requestFingerprint:e.requestFingerprint});
   p.reject(error);
  }return true;
 },consumeNotIssued(error,args){const proof=notIssued.get(error);if(!proof||proof.args!==argsSignature(args)||proof.workerInstanceId!==workerInstanceId())return null;notIssued.delete(error);return Object.freeze({workerInstanceId:proof.workerInstanceId,requestId:proof.requestId,requestFingerprint:proof.requestFingerprint});},close(){closed=true;for(const p of pending.values()){clearTimeout(p.timer);p.reject(Error('group-text-admission-outcome-unknown'));}pending.clear();}});
}
