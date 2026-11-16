// One-time local submit permit for staged owner-text batches. This is not a
// transaction across two inboxes or a network exactly-once guarantee. Once a
// reservation exists, ANY unknown/partial failure is terminal: caller must not
// ask a model or release consumed claims. No reservation/issued marker is reset.
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
export const incomingSubmissionVersion=2;
const hash=v=>createHash('sha256').update(JSON.stringify(v)).digest('hex');
const fail=code=>{throw Error('incoming-submit-'+code);};
const text=v=>typeof v==='string'&&v.length>0&&v.length<=4000&&!v.includes('\0');
const sig=s=>JSON.stringify([s.dev,s.ino,s.size,s.mtimeMs,s.nlink]);
// Capability applies only to this exact locally-created rejection object and
// this call's preflight. Never infer it from message/code/cause or JSON fields.
const safelyUnissued=new WeakSet();
export const isIncomingSubmissionSafelyUnissued=error=>safelyUnissued.has(error);
const rejectPreflight=()=>{const error=Error('incoming-submit-not-issued');safelyUnissued.add(error);throw error;};

export function createIncomingSubmissionGate({dataRoot,accountId,voiceProcessor,textProcessor}={}){
  if(!text(dataRoot)||!path.isAbsolute(dataRoot)||!text(accountId)
    ||[voiceProcessor,textProcessor].some(p=>typeof p?.validateSubmitting!=='function'||typeof p?.markSubmitting!=='function'))fail('config-invalid');
  const directory=path.join(dataRoot,'config','incoming-submissions');
  function parents(){for(let p=directory;;p=path.dirname(p)){
    let s;try{s=fs.lstatSync(p);}catch(e){if(e.code!=='ENOENT')throw e;}
    if(s&&(!s.isDirectory()||s.isSymbolicLink()))fail('path-invalid');if(path.dirname(p)===p)break;
  }}
  function noPriorReservation(events){
    // A preflight failure is releasable only when the FULL claim subset can be
    // identified and a stable cross-instance disk inventory proves no overlap.
    // Corrupt/partial/unrecognized artifacts are unknown, never absence proof.
    try{
      if(!Array.isArray(events))return false;
      const wanted=new Set();let owner=false;
      for(const e of events){
        if(!e||typeof e!=='object')return false;
        for(const [kind,field,source] of [['voice','nativeVoiceClaimId','wechat-native-voice'],['text','nativeOwnerTextClaimId','wechat-native-text']]){
          if(e[field]===undefined&&e.sourceKind!==source)continue;
          if(e.sourceKind!==source||typeof e[field]!=='string'||!/^[a-f0-9]{32}$/.test(e[field]))return false;
          wanted.add(kind+':'+e[field]);owner||=kind==='text';
        }
      }
      if(!owner||!wanted.size)return false;
      parents();let before;
      try{before=fs.lstatSync(directory);}catch(e){if(e.code==='ENOENT')return true;throw e;}
      const dirSig=s=>JSON.stringify([s.dev,s.ino,s.size,s.mtimeMs,s.ctimeMs]);
      const names=fs.readdirSync(directory).sort();if(names.length>20000)return false;
      const records=new Map(),issued=[],tokens=[];
      for(const name of names){
        if(!/^[a-f0-9]{64}\.(reservation|issued)\.json$/.test(name))return false;
        const file=path.join(directory,name),info=fs.lstatSync(file);
        if(!info.isFile()||info.isSymbolicLink()||info.nlink!==1||info.size<1||info.size>256*1024)return false;
        const fd=fs.openSync(file,'r');let raw;
        try{if(sig(fs.fstatSync(fd))!==sig(info))return false;raw=fs.readFileSync(fd,'utf8');if(sig(fs.fstatSync(fd))!==sig(info))return false;}
        finally{fs.closeSync(fd);}
        if(sig(fs.lstatSync(file))!==sig(info))return false;
        const v=JSON.parse(raw),id=name.slice(0,64);tokens.push([file,sig(info)]);
        if(JSON.stringify(v)!==raw)return false; // reject duplicate keys/noncanonical tampering
        if(v?.version!==1||v.reservationId!==id||!text(v.accountId)||!text(v.sessionId)||!text(v.target))return false;
        if(name.endsWith('.reservation.json')){
          if(Object.keys(v).sort().join(',')!=='accountId,claims,format,payloadHashes,reservationId,sessionId,target,version'
            ||v.format!=='whale-incoming-submit-reservation'||!Array.isArray(v.claims)||!v.claims.length||v.claims.length>200
            ||!Array.isArray(v.payloadHashes)||v.payloadHashes.length>200||v.payloadHashes.some(h=>typeof h!=='string'||!/^[a-f0-9]{64}$/.test(h))
            ||v.claims.some(c=>!Array.isArray(c)||c.length!==2||!['voice','text'].includes(c[0])||typeof c[1]!=='string'||!/^[a-f0-9]{32}$/.test(c[1]))
            ||new Set(v.claims.map(c=>c.join(':'))).size!==v.claims.length
            ||hash([v.accountId,v.sessionId,v.target,v.claims])!==id)return false;
          records.set(id,v);
        }else{
          if(Object.keys(v).sort().join(',')!=='accountId,format,reservationHash,reservationId,sessionId,target,version'
            ||v.format!=='whale-incoming-submit-issued'||typeof v.reservationHash!=='string'||!/^[a-f0-9]{64}$/.test(v.reservationHash))return false;
          issued.push(v);
        }
      }
      // Validate issued markers against their reservation, including payload
      // digest. Read-only scans never repair, delete or complete either file.
      for(const v of issued){
        const r=records.get(v.reservationId);
        if(!r||v.accountId!==r.accountId||v.sessionId!==r.sessionId||v.target!==r.target
          ||v.reservationHash!==hash([r.accountId,r.sessionId,r.target,r.payloadHashes]))return false;
      }
      if(dirSig(fs.lstatSync(directory))!==dirSig(before)||JSON.stringify(fs.readdirSync(directory).sort())!==JSON.stringify(names)
        ||tokens.some(([file,token])=>sig(fs.lstatSync(file))!==token))return false;
      return [...records.values()].every(r=>r.claims.every(c=>!wanted.has(c.join(':'))));
    }catch{return false;}
  }
  function durable(file,value){
    parents();const bytes=JSON.stringify(value);let fd;
    try{
      fd=fs.openSync(file,'wx',0o600);fs.writeFileSync(fd,bytes);fs.fsyncSync(fd);
      const before=fs.fstatSync(fd);fs.closeSync(fd);fd=undefined;
      const info=fs.lstatSync(file);
      if(!info.isFile()||info.isSymbolicLink()||info.nlink!==1||sig(info)!==sig(before))fail('readback-unverified');
      const read=fs.openSync(file,'r');try{
        if(sig(fs.fstatSync(read))!==sig(info)||fs.readFileSync(read,'utf8')!==bytes||sig(fs.fstatSync(read))!==sig(info)
          ||sig(fs.lstatSync(file))!==sig(info))fail('readback-unverified');
      }finally{fs.closeSync(read);}
      if(process.platform!=='win32'){const dir=fs.openSync(directory,'r');try{fs.fsyncSync(dir);}finally{fs.closeSync(dir);}}
    }finally{if(fd!==undefined)fs.closeSync(fd);}
  }
  return Object.freeze({async submit({sessionId,target,batch}={}){
    // Clone before any await; caller mutation cannot alter what is submitted.
    let events,voiceIds,textIds,reservationId,reservation;
    try{
      if(!text(sessionId)||!text(target)||!Array.isArray(batch)||!batch.length||batch.length>200)fail('invalid');
      events=structuredClone(batch);
      if(new Set(events.map(e=>e?.id)).size!==events.length)fail('invalid');
      if(events.some(e=>!e||e.target!==target||e.kind!=='friend'||!text(e.id)||typeof e.text!=='string'
        ||(e.sourceKind==='wechat-native-text'&&e.nativeAccountId!==accountId)
        ||(!['wechat-native-text','wechat-native-voice','wxauto-incoming',undefined].includes(e.sourceKind))))fail('invalid');
      const scope={sessionId,target};
      voiceIds=voiceProcessor.validateSubmitting(events,scope);textIds=textProcessor.validateSubmitting(events,scope);
      if(!Array.isArray(voiceIds)||!Array.isArray(textIds)||!textIds.length)fail('owner-text-required');
      const claims=[...voiceIds.map(id=>['voice',id]),...textIds.map(id=>['text',id])]
        .sort((a,b)=>JSON.stringify(a)<JSON.stringify(b)?-1:JSON.stringify(a)>JSON.stringify(b)?1:0);
      if(claims.some(([,id])=>typeof id!=='string'||!/^[a-f0-9]{32}$/.test(id)))fail('invalid');
      reservationId=hash([accountId,sessionId,target,claims]);
      reservation={format:'whale-incoming-submit-reservation',version:1,
        accountId,sessionId,target,claims,payloadHashes:events.map(e=>hash(e)).sort(),reservationId};
    }catch{if(noPriorReservation(events))rejectPreflight();fail('not-issued');}
    // A restart may rebuild only the still-queued subset of a partial batch.
    // A different session/batch reservation key must not bypass its prior hold.
    if(!noPriorReservation(events))fail('not-issued');
    // No branded errors beyond this boundary: even parent validation/mkdir or
    // an exclusive-create EEXIST can refer to an earlier unknown reservation.
    try{
      parents();fs.mkdirSync(directory,{recursive:true});parents();
      durable(path.join(directory,reservationId+'.reservation.json'),reservation);
      const scope={sessionId,target};
      if(voiceIds.length){const v=await voiceProcessor.markSubmitting(events,scope);if(v?.submitted!==voiceIds.length)fail('not-durable');}
      const t=await textProcessor.markSubmitting(events,scope);if(t?.submitted!==textIds.length)fail('not-durable');
      durable(path.join(directory,reservationId+'.issued.json'),{format:'whale-incoming-submit-issued',version:1,
        accountId,sessionId,target,reservationId,reservationHash:hash([accountId,sessionId,target,events.map(e=>hash(e)).sort()])});
      return {issued:true,reservationId};
    }catch{fail('not-issued');}
  }});
}
