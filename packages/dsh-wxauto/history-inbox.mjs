import fs from 'node:fs';
import path from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import {absoluteDataPath,relativeDataPath} from './data-paths.mjs';

const digest=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const MAX_BYTES=8*1024*1024;
export const historyJournalVersion=3;

// This journal only retries history ingestion. It never enqueues a Harness
// request, forwards a message or sends anything to WeChat.
export function createHistoryInbox({config,routes,write,report=()=>{},journalKind='incoming'}) {
  if(!['incoming','outgoing','evidence'].includes(journalKind))throw Error('history-journal-kind-invalid');
  const relativeFolder=journalKind==='incoming'?'snapshots/realtime-inbox':'snapshots/realtime-'+journalKind;
  const directory=()=>absoluteDataPath(relativeFolder,config);
  const active=new Map();let tail=Promise.resolve(),closed=false;
  function encode(event) {
    const route=routes.get(event.target);
    if(!route||route.kind!==event.kind||typeof event.id!=='string'||!event.id
        ||typeof event.text!=='string')throw Error('history-inbox-event-invalid');
    const result=structuredClone(event);
    for(const kind of ['images','files',...(Object.hasOwn(event,'audio')?['audio']:[])])result[kind]=(result[kind]||[]).map(media=>{
      const item={...media};
      if(item.path){item.path=relativeDataPath(item.path,config);item.journalRelativePath=true;}
      return item;
    });
    return result;
  }
  function decode(value) {
    if(value?.format!=='whale-history-inbox'||value.version!==1||value.accountId!==config.accountId
        ||(value.journalKind??'incoming')!==journalKind
        ||digest(value.event)!==value.eventDigest||!Number.isFinite(value.observedAt))throw Error('history-inbox-record-invalid');
    const event=structuredClone(value.event),route=routes.get(event.target);
    if(!route||route.kind!==event.kind||typeof event.id!=='string'||typeof event.text!=='string')throw Error('history-inbox-route-invalid');
    for(const kind of ['images','files',...(Object.hasOwn(event,'audio')?['audio']:[])])event[kind]=(event[kind]||[]).map(media=>{
      const item={...media};
      if(item.path){if(item.journalRelativePath!==true)throw Error('history-inbox-media-invalid');item.path=absoluteDataPath(item.path,config);}
      delete item.journalRelativePath;return item;
    });
    event.observedTimestamp=value.observedAt;
    return event;
  }
  function read(file) {
    const info=fs.lstatSync(file);
    if(!info.isFile()||info.isSymbolicLink()||info.nlink!==1||info.size>MAX_BYTES)throw Error('history-inbox-file-invalid');
    const value=JSON.parse(fs.readFileSync(file,'utf8'));
    const event=decode(value);
    if(path.basename(file)!==digest([config.accountId,event.target,event.id])+'.json')throw Error('history-inbox-file-identity-invalid');
    return {value,event};
  }
  function deliver(file) {
    if(active.has(file))return active.get(file);
    const work=tail.then(async()=>{
      if(closed)return null;
      let event;
      try{({event}=read(file));}catch(error){if(error.code==='ENOENT')return null;throw error;}
      const saved=await write(event);
      if(!saved?.id)throw Error('history-inbox-write-not-confirmed');
      // A crash after commit but before unlink is safe: same source ID is
      // idempotent in the sole SQLite writer on the next startup.
      fs.unlinkSync(file);return saved;
    });
    active.set(file,work);tail=work.catch(()=>{});
    work.finally(()=>active.delete(file)).catch(()=>{});
    return work;
  }
  return {
    enqueue(event) {
      if(closed)throw Error('history-inbox-closed');
      const encoded=encode(event),folder=directory();fs.mkdirSync(folder,{recursive:true});
      const file=absoluteDataPath(relativeFolder+'/'+digest([config.accountId,event.target,event.id])+'.json',config);
      if(fs.existsSync(file)) {
        if(read(file).value.eventDigest!==digest(encoded))throw Error('history-inbox-source-conflict');
      } else {
        const value={format:'whale-history-inbox',version:1,accountId:config.accountId,journalKind,
          event:encoded,eventDigest:digest(encoded),observedAt:Date.now()};
        const text=JSON.stringify(value);
        if(Buffer.byteLength(text)>MAX_BYTES)throw Error('history-inbox-event-too-large');
        const temp=path.join(folder,randomUUID()+'.tmp');let fd;
        try{fd=fs.openSync(temp,'wx');fs.writeFileSync(fd,text);fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;fs.renameSync(temp,file);}
        finally{if(fd!==undefined)fs.closeSync(fd);if(fs.existsSync(temp))fs.unlinkSync(temp);}
      }
      return deliver(file);
    },
    async replay() {
      if(closed)return;
      const folder=directory();if(!fs.existsSync(folder))return;
      const files=fs.readdirSync(folder).filter(name=>/^[a-f0-9]{64}\.json$/.test(name));
      let restored=0,failed=0;
      for(const name of files){
        if(closed)break;
        try{if(await deliver(absoluteDataPath(relativeFolder+'/'+name,config)))restored++;}
        catch{failed++;}
      }
      report(journalKind==='incoming'?'vault-inbox-replay':'vault-'+journalKind+'-replay',
        {[{incoming:'historyInbox',outgoing:'historyOutgoing',evidence:'historyEvidence'}[journalKind]]:{restored,pending:failed}});
    },
    idle(){return tail;},
    close(){closed=true;},
  };
}
