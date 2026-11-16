import {randomUUID} from 'node:crypto';
const hex=v=>typeof v==='string'&&/^[a-f0-9]{32}$/.test(v);
const fields={'group-text-status':[],'group-text-prepare':[],'group-text-abort':['ticket'],'group-text-commit':['ticket','authorityEpoch']};
export function groupTextRpcClient({write,routeForGroup,isReady=()=>true,timeoutMs=45000}={}){
  if(typeof write!=='function'||typeof routeForGroup!=='function'||typeof isReady!=='function'||!Number.isSafeInteger(timeoutMs)||timeoutMs<1)throw Error('group-text-client-config-invalid');
  let closed=false;const pending=new Map();
  return Object.freeze({request(op,args={}){
    if(closed||!isReady())return Promise.reject(Error('group-text-worker-not-ready'));
    if(!args||typeof args!=='object'||Array.isArray(args))return Promise.reject(Error('group-text-request-invalid'));
    const {nativeChatKey,...rest}=args;let route;try{route=routeForGroup(nativeChatKey);}catch{}
    if(!Object.hasOwn(fields,op)||!/^wechat:[A-Za-z0-9_-]+@chatroom$/.test(nativeChatKey??'')||!route||route.kind!=='group'||route.active===false||!route.target
      ||Object.keys(rest).length!==fields[op].length||fields[op].some(k=>!hex(rest[k])))return Promise.reject(Error('group-text-request-invalid'));
    return new Promise((resolve,reject)=>{const id='group-text:'+randomUUID(),timer=setTimeout(()=>{pending.delete(id);reject(Error('group-text-outcome-unknown'));},timeoutMs);
      pending.set(id,{resolve,reject,timer,nativeChatKey,target:route.target});
      const failed=err=>{if(!err)return;const p=pending.get(id);if(p){clearTimeout(p.timer);pending.delete(id);reject(Error('group-text-write-failed'));}};
      try{write(JSON.stringify({op,id,target:route.target,nativeChatKey,...rest})+'\n',failed);}catch{failed(true);}
    });
  },handle(e){if(e?.event!=='group-text-result')return false;const p=pending.get(e.id);if(!p)return false;
    clearTimeout(p.timer);pending.delete(e.id);let route;try{route=routeForGroup(p.nativeChatKey);}catch{}
    if(!route||route.active===false||route.kind!=='group'||route.target!==p.target)p.reject(Error('group-text-route-changed'));
    else if(e.ok===true)p.resolve(e.value);else p.reject(Error(/^group-text-[a-z-]{1,100}$/.test(e.code??'')?e.code:'group-text-operation-failed'));return true;
  },close(){closed=true;for(const p of pending.values()){clearTimeout(p.timer);p.reject(Error('group-text-worker-exited'));}pending.clear();}});
}
