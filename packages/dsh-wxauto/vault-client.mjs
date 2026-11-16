import {createVaultProcess} from './vault-process.mjs';
import {uniqueSessionRoute} from './session-routing.mjs';

// One independent process owns the SQLite write queue. No listening socket.
export function createVaultClient(options) {
  return createVaultProcess(options);
}

export function limitedHistoryResult(value, maxChars=12000, {offset=0,anchorId}={}) {
  // Include all metadata in the output budget, not only message bodies.
  const copy=structuredClone(value);
  const originalMessages=Array.isArray(copy.messages)?copy.messages.length:0;
  const paged=copy.order==='newest-first'&&Array.isArray(copy.messages);
  if(paged&&(!Number.isSafeInteger(offset)||offset<0))throw Error('history-offset-invalid');
  const updateOffset=()=>{
    if(paged&&copy.messages.length<originalMessages)copy.next_offset=offset+copy.messages.length;
  };
  for(const key of ['messages','results','items']) {
    if(!Array.isArray(copy[key]))continue;
    while(copy[key].length && JSON.stringify(copy).length>maxChars){
      // Context reads retain the requested anchor, removing farthest neighbors.
      const at=key==='messages'&&anchorId?copy[key].findIndex(m=>m.id===anchorId):-1;
      if(copy[key].length===1&&key==='messages') {
        const row=copy[key][0],text=typeof row.text==='string'?row.text:'';
        copy[key][0]=Object.fromEntries(['id','chat_id','timestamp','timestamp_quality','direction','delivery_state','type','sender'].filter(k=>k in row).map(k=>[k,row[k]]));
        copy[key][0].text='';copy[key][0].text_truncated=!!text;
        copy[key][0].details_omitted=true;copy.truncated=true;updateOffset();
        let low=0,high=text.length;
        while(low<high){const mid=Math.ceil((low+high)/2);copy[key][0].text=text.slice(0,mid);
          if(JSON.stringify(copy).length<=maxChars)low=mid;else high=mid-1;}
        copy[key][0].text=text.slice(0,low);
        break;
      }
      if(at>=(copy[key].length-1)/2&&at>=0)copy[key].shift();else copy[key].pop();
      copy.truncated=true;updateOffset();
    }
  }
  if(JSON.stringify(copy).length>maxChars)return {untrusted_history_data:true,truncated:true,messages:[],notice:'结果超过预算，请缩小时间或关键词范围。'};
  return copy;
}

export function historyRoute({routes,sessionId,ownerSessionId,requestedChat}) {
  const list=[...routes.values()];
  const current=uniqueSessionRoute(routes,sessionId);
  if(!current)throw Error('history-session-denied');
  if(requestedChat && requestedChat!==current.target) {
    if(sessionId!==ownerSessionId)throw Error('history-chat-denied');
    const matches=list.filter(r=>r.target===requestedChat);
    if(matches.length!==1)throw Error('history-chat-unknown-or-ambiguous');
    return matches[0];
  }
  return current;
}
