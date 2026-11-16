import path from 'node:path';
import {createHistoryInbox} from './history-inbox.mjs';
import {renderedFileEvidence} from './send-result-diagnostic.mjs';

const STATES=new Set(['generated','attempted','sent','failed','unknown']);

// Durable history observations only. No WeChat worker, send callback or Harness
// execution API is accepted by this module; replay cannot resend a message.
export function createOutgoingHistory({config,routes,chat,client,report}) {
  const active=new Map();let closed=false,shutdown=null;
  const journal=createHistoryInbox({config,routes,report,journalKind:'outgoing',write:async event=>{
    if(!STATES.has(event.deliveryState)||typeof event.sourceId!=='string'
        ||event.id!==event.sourceId+':'+event.deliveryState)throw Error('outgoing-history-observation-invalid');
    const c=await chat(event.target);
    const saved=await client.request('record_message',{chat_id:c.id,source:'wxauto-outgoing',source_id:event.sourceId,
      direction:'outgoing',type:event.mediaType,sender:'小鲸',text:event.text,timestamp:event.timestamp,
      timestamp_quality:'observed',delivery_state:'generated',metadata:event.messageMetadata});
    const evidence=event.mediaType==='attachment'&&event.deliveryState==='sent'
      ?renderedFileEvidence(event.deliveryDetail?.renderedFileEvidence):null;
    if(event.deliveryState!=='generated')await client.request('record_delivery_observation',{
      message_id:saved.id,operation_id:event.sourceId,state:event.deliveryState,
      detail:{observedAt:event.observedTimestamp,...(evidence?{renderedFileEvidence:evidence}:{})}});
    for(const media of event.files)if(media.path)await client.request('attach',{
      message_id:saved.id,path:media.path,metadata:media.metadata});
    return saved;
  }});
  const persist=event=>{
    let work;
    try{work=journal.enqueue(event);}catch{report('vault-outgoing-journal-failed');return false;}
    work.catch(error=>report('vault-outgoing-pending',{vaultError:String(error.message).slice(0,100)}));
    return true;
  };
  return {
    record(target,part,id){
      if(closed)throw Error('outgoing-history-closed');
      const route=routes.get(target);if(!route)throw Error('outgoing-history-route-invalid');
      if(typeof id!=='string'||!id)throw Error('outgoing-history-source-invalid');
      const files=[],file=part.filepath??part.file;
      if(typeof file==='string'){
        const relative=path.relative(path.resolve(config.dataRoot),path.resolve(file));
        if(relative&&relative!=='..'&&!relative.startsWith('..'+path.sep)&&!path.isAbsolute(relative))files.push({path:file,
          metadata:{kind:part.kind,contentStatus:'generated',durationSeconds:part.durationSeconds??null}});
      }
      const historyEvent={target,kind:route.kind,sourceId:id,text:part.text??part.query??'',
        mediaType:part.kind||'text',timestamp:Date.now(),files,
        messageMetadata:{mentions:part.at??[],...(part.kind==='voice'?{textStatus:'synthesis-input-not-segment-transcript'}:{})}};
      const journaled=persist({...historyEvent,id:id+':generated',deliveryState:'generated'});
      active.set(id,{historyEvent:structuredClone(historyEvent),attempted:false});
      return {historyEvent,journaled};
    },
    delivery(reference,id,state,detail){
      if(!STATES.has(state)||reference.historyEvent.sourceId!==id)throw Error('outgoing-history-delivery-invalid');
      // Close has already sealed attempted sends as unknown. A delayed native
      // callback cannot upgrade that terminal observation or reopen the writer.
      if(closed)return {journaled:false,closed:true};
      const entry=active.get(id);
      if(state==='attempted'&&entry)entry.attempted=true;
      const evidence=reference.historyEvent.mediaType==='attachment'&&state==='sent'
        ?renderedFileEvidence(detail?.renderedFileEvidence):null;
      const journaled=persist({...reference.historyEvent,id:id+':'+state,deliveryState:state,
        ...(evidence?{deliveryDetail:{renderedFileEvidence:evidence}}:{})});
      if(journaled&&['sent','failed','unknown'].includes(state))active.delete(id);
      return {journaled};
    },
    replay:()=>journal.replay(),idle:()=>journal.idle(),
    close(){
      if(closed)return {...shutdown};
      let unknownJournaled=0,unknownFailed=0;
      // Synchronous fsync+rename BEFORE history-service closes journals and its
      // sole writer. No wait for a 45/90s native callback and no UI retry.
      for(const [id,entry] of active)if(entry.attempted){
        if(persist({...entry.historyEvent,id:id+':unknown',deliveryState:'unknown'})){
          unknownJournaled++;active.delete(id);
        }else unknownFailed++;
      }
      shutdown={unknownJournaled,unknownFailed};closed=true;journal.close();
      if(unknownJournaled||unknownFailed)try{report('vault-outgoing-shutdown',shutdown);}catch{}
      return {...shutdown};
    },
  };
}
