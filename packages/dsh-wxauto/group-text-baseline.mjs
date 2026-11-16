// First-cutover baseline publication only. No worker, capture, authority commit,
// model, WeChat, or automatic rollback. Tokens are internal object capabilities.
import fs from 'node:fs';
import path from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import {createNativeGroupTextInbox} from './native-group-text-inbox.mjs';
import {nativeGroupTextPaths} from './group-text-contract.mjs';

const hash=v=>createHash('sha256').update(v).digest('hex');
const fail=code=>{throw Error('group-text-baseline-'+code);};
const sig=s=>JSON.stringify([s.dev,s.ino,s.size,s.mtimeMs,s.nlink]);
const MAX=64*1024*1024;

function checked(file,{directory=false,missing=false}={}){
  for(let p=file;;p=path.dirname(p)){
    let s;try{s=fs.lstatSync(p)}catch(e){if(e.code==='ENOENT'&&missing)continue;if(e.code==='ENOENT'&&p!==file)continue;throw e}
    if(s.isSymbolicLink()||p!==file&&!s.isDirectory())fail('path-invalid');
    if(p===file){if(directory?!s.isDirectory():!s.isFile()||s.nlink!==1)fail('file-invalid')}
    if(path.dirname(p)===p)break;
  }
  return file;
}
function absent(file){
  checked(path.dirname(file),{directory:true,missing:true});
  try{fs.lstatSync(file)}catch(e){if(e.code==='ENOENT')return true;throw e}
  return false;
}
function read(file){
  checked(file);const before=fs.lstatSync(file);if(before.size<1||before.size>MAX)fail('size-invalid');
  const fd=fs.openSync(file,'r');let bytes;
  try{
    if(sig(fs.fstatSync(fd))!==sig(before))fail('file-changed');bytes=fs.readFileSync(fd);
    if(sig(fs.fstatSync(fd))!==sig(before)||sig(fs.lstatSync(checked(file)))!==sig(before))fail('file-changed');
  }finally{fs.closeSync(fd)}
  let value;try{value=JSON.parse(bytes.toString('utf8'))}catch{fail('state-invalid')}
  if(!value||typeof value!=='object'||Array.isArray(value))fail('state-invalid');
  return {value,sha:hash(bytes),signature:sig(before)};
}
function directorySync(folder){
  if(process.platform==='win32')return;
  const fd=fs.openSync(folder,'r');try{fs.fsyncSync(fd)}finally{fs.closeSync(fd)}
}
function fileSync(file){const fd=fs.openSync(checked(file),'r+');try{fs.fsyncSync(fd)}finally{fs.closeSync(fd)}}

export function createGroupTextBaselinePublisher({config,nativeChatKey}={}){
  // Constructor validates lexical configuration only; no filesystem access.
  if(!config||typeof config.dataRoot!=='string'||!path.isAbsolute(config.dataRoot)||typeof config.accountId!=='string'||!config.accountId
    ||!/^wechat:[A-Za-z0-9_-]+@chatroom$/.test(nativeChatKey??''))fail('config-invalid');
  const dataRoot=path.resolve(config.dataRoot),accountId=config.accountId,key=nativeChatKey;
  const {directory:configFolder,file,lock,authority}=nativeGroupTextPaths({dataRoot,nativeChatKey:key});
  const capabilities=new WeakMap();
  const inbox=root=>createNativeGroupTextInbox({dataRoot:root,accountId,nativeChatKey:key});
  function owned(token){const v=token&&capabilities.get(token);if(!v)fail('token-invalid');return v}
  function baseline(record,token){
    const value=record.value;
    if(record.sha!==token.baselineSha256||value.format!=='whale-native-group-text-inbox'||value.version!==1
      ||value.accountId!==accountId||value.nativeChatKey!==key
      ||value.authorityEpoch!==token.authorityEpoch||value.revision!==1||!Array.isArray(value.claims)
      ||typeof value.baselineSnapshotId!=='string'||!value.baselineSnapshotId
      ||typeof value.baselineCatalogDigest!=='string'||!(/^[a-f0-9]{64}$/.test(value.baselineCatalogDigest))
      ||value.claims.some(c=>c.state!=='baseline'&&!(c.state==='held'&&c.firstSeenMode==='baseline')))fail('baseline-changed');
    const {digest,...body}=value;if(digest!==hash(JSON.stringify(body)))fail('digest-invalid');
  }
  function locked(work){
    checked(configFolder,{directory:true});let fd,identity;
    try{
      try{fd=fs.openSync(lock,'wx',0o600)}catch(e){if(e.code==='EEXIST')fail('busy');throw e}
      identity=sig(fs.fstatSync(fd));return work();
    }finally{
      if(fd!==undefined){fs.closeSync(fd);const now=fs.lstatSync(lock);
        if(now.isSymbolicLink()||now.nlink!==1||sig(now)!==identity)fail('lock-changed');fs.unlinkSync(lock)}
    }
  }
  return Object.freeze({
    exists(){return !absent(file)},
    prepare(catalog,{cutoverAtMs}={}){
      // Existing native-inbox implementation validates authenticated catalog
      // shape and records its baseline proof; this is not source acquisition.
      checked(dataRoot,{directory:true,missing:true});fs.mkdirSync(configFolder,{recursive:true});checked(configFolder,{directory:true});
      if(!absent(file)||!absent(authority))fail('existing-state');
      const attemptId=randomUUID().replaceAll('-','');
      const attempt=path.join(configFolder,'native-group-text-cutover',attemptId),stage=path.join(attempt,'stage');
      checked(attempt,{directory:true,missing:true});fs.mkdirSync(stage,{recursive:true});checked(stage,{directory:true});
      const authorityEpoch=randomUUID().replaceAll('-','');
      inbox(stage).baseline({catalog,authorityEpoch,cutoverAtMs,delegatedUnreadNativeIds:[]});
      const stageFile=nativeGroupTextPaths({dataRoot:stage,nativeChatKey:key}).file,record=read(stageFile);
      const token=Object.freeze({attemptId,authorityEpoch,baselineSha256:record.sha});
      baseline(record,token);capabilities.set(token,{attempt,stage,stageFile,state:'prepared'});
      return token;
    },
    publish(token){
      const item=owned(token);if(item.state!=='prepared')fail('attempt-state-invalid');
      return locked(()=>{
        if(!absent(file)||!absent(authority))fail('existing-state');
        const original=read(item.stageFile);baseline(original,token);inbox(item.stage).status();
        // A cooperating inbox writer must acquire this same exclusive lock.
        // A collision is refused; the first baseline is never overwritten.
        if(!absent(file)||!absent(authority)||read(item.stageFile).sha!==original.sha)fail('state-changed');
        item.state='publication-unknown';
        fs.renameSync(item.stageFile,file);fileSync(file);directorySync(configFolder);
        const actual=read(file);baseline(actual,token);inbox(dataRoot).status();item.state='published';
        return {published:true,authorityEpoch:token.authorityEpoch,baselineSha256:token.baselineSha256};
      });
    },
    discardPublished(token,{workerMode,commitNotActive}={}){
      // Internal caller assertion only, not OS isolation or a worker query.
      // Orchestrator must obtain a fresh explicit worker status after any
      // uncertain commit. An authority marker always overrides this assertion.
      const item=owned(token);
      if(!['published','publication-unknown'].includes(item.state)||commitNotActive!==true||!['preparing','ui'].includes(workerMode))fail('discard-denied');
      return locked(()=>{
        if(!absent(authority))fail('authority-present');
        const current=read(file);baseline(current,token);inbox(dataRoot).status();
        const archive=path.join(item.attempt,'discarded-native-group-text-inbox.json');
        if(!absent(archive)||!absent(authority)||read(file).sha!==current.sha)fail('state-changed');
        item.state='discard-unknown';fs.renameSync(file,archive);fileSync(archive);directorySync(configFolder);directorySync(item.attempt);
        baseline(read(archive),token);item.state='discarded';
        return {discarded:true,authorityEpoch:token.authorityEpoch,baselineSha256:token.baselineSha256};
      });
    },
  });
}
