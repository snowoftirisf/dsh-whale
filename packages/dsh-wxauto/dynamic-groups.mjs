// Trusted native identity orchestration. No UI, model tools or filesystem access.
import {createHash} from 'node:crypto';

const clone=x=>structuredClone(x);
const text=x=>typeof x==='string'&&x.length>0&&x.length<=512&&!/[\x00-\x1f]/.test(x);
const native=x=>text(x)&&/^wechat:[^\s]+@chatroom$/.test(x);
const identity=(account,key)=>JSON.stringify([account,key]);
export const dynamicGroupSessionId=(account,key)=>'whale-wx-native-'+createHash('sha256').update(identity(account,key)).digest('hex').slice(0,24);

export function createDynamicGroupRegistry({accountId,existing=[],load=async()=>null,save,
  ensureChat,activate,report=()=>{}}){
  if(!text(accountId)||![save,ensureChat,activate,load,report].every(x=>typeof x==='function')||!Array.isArray(existing))throw Error('dynamic-groups-invalid-options');
  let records=new Map(),tail=Promise.resolve(),loaded=false;
  const keyOf=r=>r.nativeChatKey||'legacy:'+r.sessionId;
  const approved=new Map();
  for(const input of existing){
    if(!text(input.target)||!text(input.sessionId)||(input.chatId!==undefined&&!text(input.chatId))
      ||(input.nativeChatKey!==undefined&&!native(input.nativeChatKey)))throw Error('dynamic-groups-invalid-existing');
    const record={accountId,target:input.target,sessionId:input.sessionId,status:'active',
      ...(input.nativeChatKey?{nativeChatKey:input.nativeChatKey}:{}),...(input.chatId?{chatId:input.chatId}:{})};
    if([...records.values()].some(r=>r.target===record.target||r.sessionId===record.sessionId||keyOf(r)===keyOf(record)))throw Error('dynamic-groups-existing-collision');
    records.set(keyOf(record),record);approved.set(record.sessionId,clone(record));
  }
  const emit=(code,record)=>{try{report({event:code,...(record?{sessionId:record.sessionId,status:record.status}:{})});}catch{}};
  const persist=(groups=[...records.values()])=>save({version:1,accountId,groups:clone(groups)});
  const result=(status,code,record)=>({status,...(code?{code}:{}),...(record?{record:clone(record)}:{})});
  async function hydrate(){
    if(loaded)return;
    const state=await load();
    if(state!==null&&state!==undefined){
      if(state.version!==1||state.accountId!==accountId||!Array.isArray(state.groups)||state.groups.length>1000)throw Error('dynamic-groups-invalid-state');
      const merged=new Map(records),sessions=new Set(),keys=new Set();
      for(const input of state.groups){
        if(input.accountId!==accountId||!text(input.sessionId)||!['active','pending','rename-required'].includes(input.status)
          ||(input.target!==null&&!text(input.target))||(input.nativeChatKey!==undefined&&!native(input.nativeChatKey))
          ||(input.chatId!==undefined&&!text(input.chatId))||(input.desiredTarget!==undefined&&!text(input.desiredTarget)))throw Error('dynamic-groups-invalid-state');
        const original=approved.get(input.sessionId);
        if(!original&&(!input.nativeChatKey||input.sessionId!==dynamicGroupSessionId(accountId,input.nativeChatKey)))throw Error('dynamic-groups-untrusted-session');
        if(original&&(input.target!==original.target||(original.chatId&&input.chatId!==original.chatId)
          ||(original.nativeChatKey&&input.nativeChatKey!==original.nativeChatKey)))throw Error('dynamic-groups-existing-state-mismatch');
        const r={accountId,sessionId:input.sessionId,target:input.target,status:input.status,
          ...(input.nativeChatKey?{nativeChatKey:input.nativeChatKey}:{}),...(input.chatId?{chatId:input.chatId}:{}),
          ...(input.desiredTarget?{desiredTarget:input.desiredTarget}:{})};
        if(keys.has(keyOf(r))||sessions.has(r.sessionId))throw Error('dynamic-groups-state-collision');
        keys.add(keyOf(r));sessions.add(r.sessionId);
        if(original){merged.delete(keyOf(original));r.status='active';}
        // A persisted dynamic active record is not live authorization after a
        // restart: require a fresh eligible candidate and activate preparation.
        else if(r.status==='active')r.status='pending';
        merged.set(keyOf(r),r);
      }
      records=merged;
    }
    loaded=true;
  }
  async function process(candidate,ambiguousNames,ambiguousKeys){
    if(!candidate||candidate.accountId!==accountId||!native(candidate.nativeChatKey)
      ||candidate.membershipConfirmed!==true)return result('unresolved','dynamic-groups-untrusted-candidate');
    const key=candidate.nativeChatKey;
    if(ambiguousKeys.has(key))return result('unresolved','dynamic-groups-native-name-conflict');
    const name=text(candidate.displayName)?candidate.displayName:null;
    let record=records.get(key);
    if(record?.status==='active'&&ambiguousNames.has(name)){
      if(!approved.has(record.sessionId)){
        record={...record,status:'pending'};records.set(key,record);
        try{await persist();}catch{emit('dynamic-groups-save-failed',record);}
      }
      return result('unresolved','dynamic-groups-name-collision',record);
    }
    if(record&&record.status==='active'){
      if(name&&name!==record.target){
        if(approved.has(record.sessionId))return result('unresolved','dynamic-groups-existing-rename-required',record);
        record={...record,status:'rename-required',desiredTarget:name};records.set(key,record);
        try{await persist();}catch{emit('dynamic-groups-save-failed',record);}
        return result('unresolved','dynamic-groups-rename-required',record);
      }
      if(approved.has(record.sessionId)||(candidate.eligible===true&&name))return result('active',undefined,record);
      record={...record,status:'pending'};records.set(key,record);
    }
    if(record?.status==='rename-required')return result('unresolved','dynamic-groups-rename-required',record);
    if(!record){
      const sessionId=dynamicGroupSessionId(accountId,key);
      if([...records.values()].some(r=>r.sessionId===sessionId))return result('unresolved','dynamic-groups-session-collision');
      record={accountId,nativeChatKey:key,target:name,sessionId,status:'pending'};
    }
    record={...record,status:'pending',...(record.target===null&&name?{target:name}:{})};records.set(key,record);
    try{await persist();}catch{emit('dynamic-groups-save-failed',record);return result('pending','dynamic-groups-save-failed',record);}
    let chat;
    try{chat=await ensureChat({target:name,nativeChatKey:key});}
    catch{emit('dynamic-groups-ensure-failed',record);return result('pending','dynamic-groups-ensure-failed',record);}
    if(!chat||!text(chat.id))return result('pending','dynamic-groups-chat-invalid',record);
    if(record.chatId&&record.chatId!==chat.id)return result('pending','dynamic-groups-canonical-chat-changed',record);
    // Canonical ID/redirect proof, never display name, may bind an already
    // approved route that was supplied without a native key. Ineligible native
    // display labels do not revoke that preexisting approved route.
    const matches=[...records.values()].filter(r=>r.sessionId!==record.sessionId&&r.chatId===chat.id);
    if(matches.length){
      const old=matches[0];
      if(matches.length!==1||!approved.has(old.sessionId)||(old.nativeChatKey&&old.nativeChatKey!==key))return result('unresolved','dynamic-groups-canonical-collision',record);
      const bound={...old,nativeChatKey:key,status:'active'};
      records.delete(key);records.delete(keyOf(old));records.set(key,bound);
      try{await persist();}
      catch{
        records.delete(key);records.set(keyOf(old),old);records.set(key,record);
        emit('dynamic-groups-save-failed',record);return result('pending','dynamic-groups-save-failed',record);
      }
      return result('active','dynamic-groups-existing-canonical-bound',bound);
    }
    if(candidate.eligible!==true||!name)return result('pending','dynamic-groups-ineligible',record);
    if(ambiguousNames.has(name)||[...records.values()].some(r=>r.sessionId!==record.sessionId&&r.target===name))return result('unresolved','dynamic-groups-name-collision',record);
    if(record.target!==name)return result('unresolved','dynamic-groups-pending-rename-required',record);
    record={...record,chatId:chat.id};records.set(key,record);
    try{await persist();}catch{return result('pending','dynamic-groups-save-failed',record);}
    let rollback;
    try{rollback=await activate(clone(record));}
    catch{emit('dynamic-groups-activate-failed',record);return result('pending','dynamic-groups-activate-failed',record);}
    const active={...record,status:'active'};
    // snapshot is the FINAL AUTHORIZATION GATE. activate must prepare services,
    // not authorize the worker independently before this final save succeeds.
    try{await persist([...records.entries()].map(([k,r])=>k===key?active:r));}
    catch{
      records.set(key,record);
      if(typeof rollback==='function')try{await rollback();}catch{emit('dynamic-groups-rollback-failed',record);}
      emit('dynamic-groups-save-failed',record);return result('pending','dynamic-groups-save-failed',record);
    }
    records.set(key,active);
    return result('active',undefined,active);
  }
  async function discover(candidates){
    if(!Array.isArray(candidates)||candidates.length>1000)throw Error('dynamic-groups-invalid-candidates');
    // Clone on submission: the producer cannot mutate trusted flags or identity
    // while another discovery occupies the serialized queue.
    const batch=clone(candidates);
    const job=tail.then(async()=>{
      await hydrate();
      const names=new Map(),nativeNames=new Map();
      for(const c of batch)if(c?.accountId===accountId&&native(c.nativeChatKey)&&c.membershipConfirmed===true){
        if(text(c.displayName)){const keys=names.get(c.displayName)||new Set();keys.add(c.nativeChatKey);names.set(c.displayName,keys);}
        const ns=nativeNames.get(c.nativeChatKey)||new Set();ns.add(c.displayName??null);nativeNames.set(c.nativeChatKey,ns);
      }
      const ambiguousNames=new Set([...names].filter(([,v])=>v.size>1).map(([k])=>k));
      const ambiguousKeys=new Set([...nativeNames].filter(([,v])=>v.size>1).map(([k])=>k));
      const outcomes=[];
      for(const c of batch)outcomes.push(await process(c,ambiguousNames,ambiguousKeys));
      return outcomes;
    });
    tail=job.catch(()=>{});return job;
  }
  return {discover,snapshot:()=>clone([...records.values()].filter(r=>r.status==='active'))};
}
