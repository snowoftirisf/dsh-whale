// Runtime coordination of trusted discovery, durable identity and UI admission.
import fs from 'node:fs';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {createDynamicGroupRegistry} from './dynamic-groups.mjs';

export function createAutoGroups({accountId,file,existing,readCandidates,ensureChat,prepare,admit,deactivate=async()=>{},report=()=>{}}) {
  let active=null,closed=false;
  const fixed=new Set(existing.map(r=>r.sessionId));
  let admitted=new Map();
  const registry=createDynamicGroupRegistry({accountId,existing,
    load:async()=>{try{return JSON.parse(fs.readFileSync(file,'utf8'));}catch(error){if(error.code==='ENOENT')return null;throw error;}},
    save:async value=>{
      fs.mkdirSync(path.dirname(file),{recursive:true});
      const temporary=file+'.'+randomUUID()+'.tmp';
      fs.writeFileSync(temporary,JSON.stringify(value,null,2),{flag:'wx'});
      fs.renameSync(temporary,file);
    },ensureChat,activate:prepare});
  const refresh=()=>{
    if(closed)return Promise.resolve({closed:true});
    if(active)return active;
    active=(async()=>{
      const discovery=await readCandidates();
      if(discovery?.source!=='validated-native-snapshot'||discovery.accountId!==accountId||!Array.isArray(discovery.groups))throw Error('auto-groups-discovery-invalid');
      const candidates=discovery.groups.map(g=>({accountId,nativeChatKey:g.chatKey,displayName:g.displayName,
        membershipConfirmed:g.membership?.deleteFlag===0&&g.membership?.isInChatRoom===1
          &&g.membership?.roomRows===1&&g.membership?.selfMembershipRows===1,
        eligible:g.eligible===true}));
      const outcomes=await registry.discover(candidates);
      const permitted=new Map(registry.snapshot().filter(record=>fixed.has(record.sessionId)||candidates.some(c=>
        c.nativeChatKey===record.nativeChatKey&&c.membershipConfirmed&&c.eligible&&c.displayName===record.target))
        .map(record=>[record.sessionId,record]));
      for(const [id,record] of admitted)if(!fixed.has(id)&&!permitted.has(id)) {
        await deactivate(record);admitted.delete(id);
      }
      const admission=[];
      for(const record of permitted.values()) {
        if(closed)break;
        try{await admit(record);admitted.set(record.sessionId,record);admission.push({sessionId:record.sessionId,state:'active'});}
        catch(error){admission.push({sessionId:record.sessionId,state:'pending',code:String(error.code??error.message??'auto-group-admission-failed').slice(0,120)});}
      }
      const value={snapshotId:discovery.snapshotId,discovered:candidates.length,
        active:admission.filter(x=>x.state==='active').length,pending:admission.filter(x=>x.state==='pending').length,
        unresolved:outcomes.filter(x=>x.status!=='active').map(x=>({status:x.status,code:x.code})),admission};
      report(value);return value;
    })().finally(()=>{active=null;});
    return active;
  };
  return {refresh,close(){closed=true;},snapshot:registry.snapshot};
}
