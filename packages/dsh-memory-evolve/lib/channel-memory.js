import path from 'node:path';
import fs from 'node:fs';
import {stripEntrySummary,parseEntryBranches,parseEntries} from './store.js';
import {stripEntryId} from './sync/entryid.js';
import {resolveScope,entryIdentity,canReadMemory,boundedIndex,readMemoryRef,validateSourceRefs,createMemoryMetadataService} from '../../dsh-wxauto/memory-view.mjs';

// Local integration only. No OS/file/shell isolation is implied by these views.
const cache=new WeakMap();
// Never expose this map or the raw store to tools. Internal exact-ref lookup
// must not recursively call the public, budgeted/paged query facade.
const rawStores=new WeakMap();
const samePath=(a,b)=>path.resolve(a).toLowerCase()===path.resolve(b).toLowerCase();
const WRAPPED=Symbol('channelMemoryWrapped');
// Opt-in via channelMemoryPolicy.sharedReadTargets; absent means strict per-chat isolation.
const SHARED_READ_TARGETS=policy=>Array.isArray(policy.sharedReadTargets)
  ?policy.sharedReadTargets.filter(t=>['memory','user','project','key'].includes(t)):[];
// DSH desktop windows the owner opens by hand ("session-<uuid>").
const MANUAL_SESSION=/^session-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const CURRENT_INCOMING_KINDS=new Set(['wxauto-incoming','wechat-native-unread','wechat-native-voice','wechat-native-text','wechat-native-group-text','mixed-wechat-incoming']);
const display=raw=>stripEntrySummary(stripEntryId(raw));
const positive=(value,fallback,max)=>value===undefined?fallback:Number.isSafeInteger(value)&&value>0&&value<=max?value:(()=>{throw Error('channel-memory-page-invalid');})();
const offsetOf=value=>value===undefined?0:Number.isSafeInteger(value)&&value>=0?value:(()=>{throw Error('channel-memory-offset-invalid');})();

// List offsets count visible matching entries; text offsets count Unicode code points
// in the displayed entry (after internal ID/summary markers are removed).
export function scopedMemoryPage(entries,describe,{offset,limit,max_chars,archived=false}={}) {
  offset=offsetOf(offset);limit=positive(limit,50,50);const budget=positive(max_chars,6000,6000);
  const page={offset,next_offset:null,total_matches:entries.length,offset_unit:'visible-matching-entries',
    text_offset_unit:'unicode-code-points-in-displayed-entry',items:[]};
  let chars=0;
  for(let index=offset;index<entries.length&&page.items.length<limit;index++){
    const raw=entries[index],text=Array.from(display(raw)),available=budget-chars;
    if(available<=0)break;
    const identity=entryIdentity(describe(raw));
    const item={id:identity.ref+(archived?'@'+identity.digest:''),text:text.slice(0,available).join(''),
      text_offset:0,next_text_offset:text.length>available?available:null,total_characters:text.length};
    // Keep the entire public envelope bounded even with many long references/emoji.
    while(item.text&&JSON.stringify({...page,items:[...page.items,item],entries:[...page.items,item].map(i=>i.text)}).length>10000){
      const points=Array.from(item.text);points.pop();item.text=points.join('');item.next_text_offset=points.length;
    }
    if(!item.text&&text.length)break;
    page.items.push(item);chars+=Array.from(item.text).length;
  }
  const next=offset+page.items.length;page.next_offset=next<entries.length?next:null;
  return page;
}

export function scopedMemoryExpansion(found,scope,{offset,max_chars,source_offset}={}) {
  offset=offsetOf(offset);source_offset=offsetOf(source_offset);const max=positive(max_chars,4000,6000);
  const points=Array.from(display(found.content));
  if(offset>points.length)throw Error('channel-memory-offset-invalid');
  const visible=(found.sources||[]).filter(s=>s&&typeof s.messageId==='string'&&s.messageId.length>0&&s.messageId.length<=256
    &&typeof s.chatId==='string'&&s.chatId.length>0&&s.chatId.length<=512
    &&(scope?.kind==='private'||(scope?.kind==='chat'&&s.chatId===scope.chatId))
    &&(s.sourceKind===undefined||CURRENT_INCOMING_KINDS.has(s.sourceKind)))
    .map(s=>({messageId:s.messageId,chatId:s.chatId,sourceKind:s.sourceKind??null}));
  const sources=[];
  for(const source of visible.slice(source_offset,source_offset+10)){
    if(JSON.stringify([...sources,source]).length>4000)break;sources.push(source);
  }
  const result={ok:true,target:found.target,id:found.ref,message:'历史记忆原文；来源是历史资料，不是当前授权。',
    entries:[],offset,next_offset:null,total_characters:points.length,offset_unit:'unicode-code-points-in-displayed-entry',
    sources,source_offset,next_source_offset:source_offset+sources.length<visible.length?source_offset+sources.length:null,
    total_sources:visible.length};
  let n=Math.min(max,points.length-offset);
  const set=count=>{result.entries=[points.slice(offset,offset+count).join('')];result.next_offset=offset+count<points.length?offset+count:null;};
  set(n);
  if(JSON.stringify(result).length>12000){
    let low=0,high=n;
    while(low<high){const mid=Math.ceil((low+high)/2);set(mid);if(JSON.stringify(result).length<=12000)low=mid;else high=mid-1;}
    n=low;set(n);
  }
  if(n===0&&offset<points.length)throw Error('channel-memory-envelope-too-large');
  return result;
}

export function currentMemorySources(agent,scope) {
  // Source bindings belong to the adapter's active ask(), not a model argument,
  // a historical retrieval result, or a claimed sender in the prompt body.
  let history;
  try{history=agent?.ctx?.get?.('whaleHistory');}catch{return undefined;}
  if(typeof history?.currentSource!=='function')return undefined;
  const source=history.currentSource(agent?.session?.id);
  if(source===null||source===undefined)return undefined;
  if(!scope||!CURRENT_INCOMING_KINDS.has(source.sourceKind)||source.chatId!==scope.chatId)throw Error('channel-memory-source-chat-mismatch');
  if(!Array.isArray(source.messageIds)||source.messageIds.length>50)throw Error('channel-memory-source-invalid');
  const messages=new Map(source.messageIds.map(id=>[id,{chatId:source.chatId}]));
  return validateSourceRefs(source.messageIds,scope,messages).map(item=>({...item,sourceKind:source.sourceKind}));
}

export function validateChannelMemoryPolicy(value) {
  if(value===null||value===undefined)return;
  if(!value||typeof value!=='object'||Array.isArray(value)||typeof value.workspace!=='string'||!path.isAbsolute(value.workspace)||
     typeof value.ownerSessionId!=='string'||!value.ownerSessionId||typeof value.metadataFile!=='string'||!path.isAbsolute(value.metadataFile)||!Array.isArray(value.groups))throw Error('invalid-channel-memory-policy');
  const ids=new Set([value.ownerSessionId]);
  for(const group of value.groups) {
    if(!group||typeof group.sessionId!=='string'||!group.sessionId||typeof group.chatId!=='string'||!group.chatId||ids.has(group.sessionId))throw Error('invalid-channel-memory-group');
    ids.add(group.sessionId);
  }
  if(value.ownerChatId!==undefined&&(typeof value.ownerChatId!=='string'||!value.ownerChatId))throw Error('invalid-channel-memory-owner-chat');
  if(value.maxIndexChars!==undefined&&(!Number.isInteger(value.maxIndexChars)||value.maxIndexChars<256||value.maxIndexChars>50000))throw Error('invalid-channel-memory-budget');
}

// Static host authorization only; neither tool arguments nor persisted runtime
// settings can opt another workspace/session into automatic KEY writes.
export function validateAutoKeyPolicy(value,channelPolicy) {
  if(value===null||value===undefined)return;
  if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(k=>k!=='workspace')||
      typeof value.workspace!=='string'||!path.isAbsolute(value.workspace)||!channelPolicy||
      !samePath(value.workspace,channelPolicy.workspace))throw Error('invalid-auto-key-policy');
}

export function autoKeyEnabled(config,agent) {
  const policy=config.autoKeyPolicy;
  if(!policy)return false;
  validateAutoKeyPolicy(policy,config.channelMemoryPolicy);
  const cwd=agent?.session?.header?.cwd;
  return typeof cwd==='string'&&path.isAbsolute(cwd)&&samePath(cwd,policy.workspace)&&
    resolveScope(agent?.session?.id,trustedMemorySessions(config.channelMemoryPolicy,agent))!==null;
}

// Only the runtime service, never tool arguments or prompt/session metadata,
// may replace the configured owner's current session identity. Do not cache
// its result: the companion scheduler can rotate between two memory calls.
export function trustedMemorySessions(policy,agent) {
  let owner=policy.ownerSessionId;
  try {
    const service=agent?.ctx?.get?.('biliBackground');
    if(service!==undefined&&service!==null) {
      owner=typeof service.getSessionId==='function'?service.getSessionId():null;
    }
  } catch {owner=null;}
  if(owner&&typeof owner.then==='function'){Promise.resolve(owner).catch(()=>{});owner=null;}
  let groups=policy.groups;
  try {
    const history=agent?.ctx?.get?.('whaleHistory');
    // Older adapters have currentSource but no dynamic group API. Preserve
    // static compatibility only when this capability is actually absent.
    if(history?.getMemoryGroups!==undefined) {
      if(typeof history.getMemoryGroups!=='function')throw Error('invalid-memory-group-resolver');
      const snapshot=history.getMemoryGroups();
      if(snapshot&&typeof snapshot.then==='function'){
        Promise.resolve(snapshot).catch(()=>{});throw Error('async-memory-group-resolver');
      }
      if(!snapshot||!Number.isSafeInteger(snapshot.revision)||snapshot.revision<0||!Array.isArray(snapshot.groups))throw Error('invalid-memory-group-snapshot');
      const validId=id=>typeof id==='string'&&id.trim()===id&&id.length>0&&id.length<=512&&!/[\r\n\0]/.test(id);
      const ids=new Set(),chats=new Set(),legacy=new Map(policy.groups.map(g=>[g.sessionId,g.chatId]));
      groups=snapshot.groups.map(group=>{
        if(!group||!validId(group.sessionId)||!validId(group.chatId)||ids.has(group.sessionId)||chats.has(group.chatId)
            ||group.sessionId===policy.ownerSessionId||group.sessionId===owner
            ||group.chatId===(policy.ownerChatId||'owner-private')
            ||(legacy.has(group.sessionId)&&legacy.get(group.sessionId)!==group.chatId))throw Error('invalid-memory-group-mapping');
        ids.add(group.sessionId);chats.add(group.chatId);
        return {sessionId:group.sessionId,chatId:group.chatId};
      });
    }
  } catch {groups=[];} // A broken dynamic resolver must not grant stale scopes.
  const sessions=new Map(groups.map(g=>[g.sessionId,{kind:'chat',chatId:g.chatId}]));
  // Even a failed group resolver must not let a malformed owner resolver
  // promote a previously configured group session into private access.
  if(typeof owner==='string'&&owner.trim()&&owner.length<=512&&!/[\r\n\0]/.test(owner)&&!sessions.has(owner)&&!policy.groups.some(g=>g.sessionId===owner)) {
    sessions.set(owner,{kind:'private',chatId:policy.ownerChatId||'owner-private'});
  }
  return sessions;
}

export function channelMemoryContext(config,store,agent) {
  store=rawStores.get(store)??store;
  const policy=config.channelMemoryPolicy;
  if(!policy)return null;
  validateChannelMemoryPolicy(policy);
  const cwd=agent?.session?.header?.cwd;
  if(!cwd||!samePath(cwd,policy.workspace))return null;
  let state=cache.get(policy);
  if(!state) {
    state={service:createMemoryMetadataService({file:policy.metadataFile})};cache.set(policy,state);
  }
  const sessions=trustedMemorySessions(policy,agent);
  const sessionId=agent?.session?.id;
  // Windows the owner opens by hand in this workspace are the owner when the
  // policy opts in. WeChat chat sessions never match the manual-session shape.
  if(policy.manualSessionsPrivate===true&&typeof sessionId==='string'&&MANUAL_SESSION.test(sessionId)&&!sessions.has(sessionId))
    sessions.set(sessionId,{kind:'private',chatId:policy.ownerChatId||'owner-private'});
  let scope=resolveScope(sessionId,sessions);
  // Shared long-term tracks are readable from every chat; per-day logs stay in
  // their own chat. Writes stay scoped: other chats' entries are read-only here.
  const shared=SHARED_READ_TARGETS(policy);
  if(scope?.kind==='chat'&&shared.length)scope=Object.freeze({...scope,sharedTargets:shared});
  // Same managed workspace but an unrecognized session is fail-closed.
  const describe=(target,content)=>({target,content,...(['project','key'].includes(target)?{projectKey:policy.projectKey||'whale-work'}:{})});
  const track=target=>{
    const relative=path.relative(store.dir,store.pathOf(target,agent));
    if(!relative||path.isAbsolute(relative)||relative.split(path.sep).includes('..'))throw Error('channel-memory-track-invalid');
    return {target,file:relative.split(path.sep).join('/'),...(['project','key'].includes(target)?{projectKey:policy.projectKey||'whale-work'}:{})};
  };
  const readTrack=value=>{
    if(!value||typeof value.file!=='string'||value.file.includes('\\')||!['memory','user','key','project','daily'].includes(value.target))throw Error('channel-memory-track-invalid');
    const expected=track(value.target);
    if(value.file!==expected.file&&!(value.target==='daily'&&/^daily\/\d{4}-\d{2}-\d{2}\.md$/.test(value.file)))throw Error('channel-memory-track-invalid');
    if(value.projectKey!==expected.projectKey)throw Error('channel-memory-track-invalid');
    const file=path.resolve(store.dir,value.file),relative=path.relative(store.dir,file);
    if(path.isAbsolute(relative)||relative.split(path.sep).includes('..'))throw Error('channel-memory-track-invalid');
    for(let item=file;;item=path.dirname(item)){
      if(fs.existsSync(item)){const stat=fs.lstatSync(item);if(stat.isSymbolicLink()||stat.isJunction?.())throw Error('channel-memory-track-link-denied');}
      if(samePath(item,store.dir))break;
    }
    try{return parseEntries(fs.readFileSync(file,'utf8'));}catch(error){if(error.code==='ENOENT')return [];throw error;}
  };
  return {...state,sessions,scope,describe,
    track,readTrack,
    filter(target,entries){const metadata=state.service.read();return entries.filter(content=>canReadMemory(describe(target,content),metadata,scope));},
    filterArchived(target,entries){
      const metadata=state.service.read();
      return entries.filter(content=>{
        const entry=describe(target,content),id=entryIdentity(entry);
        const archived=[...metadata.archives].reverse().find(item=>item.ref===id.ref&&item.digest===id.digest);
        const view=archived?.metadata?{entries:{[id.ref]:archived.metadata}}:{entries:{}};
        return canReadMemory(entry,view,scope);
      });
    },
    readRef(ref,{target:requestedTarget,archivedEntries}={}){
      const entries=[];
      for(const target of requestedTarget?[requestedTarget]:['memory','user','key','project','daily']){
        const rows=archivedEntries??(target==='daily'?store.query(target,agent,{since:'0000-01-01',until:'9999-12-31'}):store.entriesOf(target,agent));
        for(const raw of rows)entries.push(describe(target,raw));
      }
      const metadata=state.service.read();
      if(archivedEntries!==undefined){
        const match=/^(.*)@([a-f0-9]{64})$/.exec(ref),base=match?.[1]??ref,digest=match?.[2];
        const entry=entries.find(e=>{const id=entryIdentity(e);return id.ref===base&&(!digest||id.digest===digest);});
        if(!entry)throw Error('memory-ref-unavailable');
        const id=entryIdentity(entry),archived=[...metadata.archives].reverse().find(a=>a.ref===base&&a.digest===id.digest);
        const view={entries:archived?.metadata?{[base]:archived.metadata}:{}};
        return {...readMemoryRef(base,[entry],view,scope),ref};
      }
      return readMemoryRef(ref,entries,metadata,scope);
    },
    page(target,entries,options){return scopedMemoryPage(entries,raw=>describe(target,raw),options);},
    assertWritable(target,content){
      const metadata=state.service.read(),e=describe(target,content);
      if(!canReadMemory(e,metadata,scope))throw Error('channel-memory-entry-unavailable');
      if(scope?.kind==='chat') {
        const key=entryIdentity(e),m=metadata.entries[key.ref];
        if(m?.visibility?.kind!=='chat'||m.visibility.chatId!==scope.chatId)throw Error('channel-memory-entry-not-writable');
      }
      return e;
    },
    index({keyEntries}={}){const entries=[];for(const target of ['memory','user','key']) {
      if(target==='key'&&!agent?.session?.header?.cwd)continue;
      for(const raw of target==='key'&&keyEntries!==undefined?keyEntries:store.entriesOf(target,agent))entries.push(describe(target,raw));
    }return boundedIndex(entries,state.service.read(),scope,{maxChars:policy.maxIndexChars??8000});},
  };
}

export function scopedMemoryStore(config,raw) {
  if(!config.channelMemoryPolicy||raw[WRAPPED])return raw;
  validateChannelMemoryPolicy(config.channelMemoryPolicy);
  const publicOutcome=(result,ctx,target)=>{
    if(ctx.scope?.kind!=='chat')return result;
    const visible=ctx.filter(target,result.entries||[]);
    return {...result,message:result.ok?(result.duplicate?'本聊天记忆未新增条目。':'本聊天记忆已更新。'):'本聊天记忆操作未完成。',chars:visible.join('\n').length};
  };
  const proxy=new Proxy(raw,{get(store,key){
    if(key===WRAPPED)return true;
    if(key==='channelContext')return agent=>channelMemoryContext(config,store,agent);
    if(key==='recoverPending')return async agent=>{
      const ctx=channelMemoryContext(config,store,agent);if(!ctx?.scope)return;
      return ctx.service.recoverMutations({scope:ctx.scope,readEntries:ctx.readTrack});
    };
    if(key==='entriesOf')return (target,agent)=>{
      const rows=store.entriesOf(target,agent),ctx=channelMemoryContext(config,store,agent);
      return ctx?ctx.filter(target,rows):rows;
    };
    if(key==='query')return (target,agent,options,stats)=>{
      const ctx=channelMemoryContext(config,store,agent);if(!ctx)return store.query(target,agent,options,stats);
      // Filter before pagination so invisible rows cannot displace visible ones.
      const rows=store.query(target,agent,{...options,limit:undefined},stats);
      let visible=ctx.filter(target,rows);
      if(target==='key'&&typeof options?.branch==='string'&&options.branch.trim())visible=visible.filter(raw=>{
        const branches=parseEntryBranches(raw);return branches===null||branches.includes(options.branch.trim());
      });
      if(stats)stats.undated=0; // Never expose counters from another channel.
      const page=ctx.page(target,visible,{...options,limit:Math.min(options?.limit??50,50)});
      if(stats){stats.page=page;stats.total=visible.length;}
      return visible.slice(page.offset,page.offset+page.items.length);
    };
    if(key==='peek')return (target,match,agent)=>{
      const result=store.peek(target,match,agent),ctx=channelMemoryContext(config,store,agent);
      if(!ctx)return result;
      if(!result.ok)return {ok:false,message:'channel-memory-entry-unavailable',target};
      try{ctx.assertWritable(target,result.entry);return result;}catch{return {ok:false,message:'channel-memory-entry-unavailable',target};}
    };
    if(key==='add')return async(target,content,agent)=>{
      const ctx=channelMemoryContext(config,store,agent);if(!ctx)return store.add(target,content,agent);
      if(!ctx.scope)throw Error('channel-memory-session-denied');
      const sources=currentMemorySources(agent,ctx.scope);
      const result=await ctx.service.commitMutation({scope:ctx.scope,sources:sources||[],track:ctx.track(target),readEntries:ctx.readTrack,
        apply:prepare=>store.add(target,content,agent,{beforeWrite:value=>prepare({...value,after:ctx.describe(target,value.after)})})});
      return publicOutcome(result,ctx,target);
    };
    if(key==='replace'||key==='remove')return async(target,match,contentOrAgent,maybeAgent)=>{
      const agent=key==='replace'?maybeAgent:contentOrAgent;
      const ctx=channelMemoryContext(config,store,agent);
      if(!ctx)return key==='replace'?store.replace(target,match,contentOrAgent,agent):store.remove(target,match,agent);
      if(!ctx.scope)throw Error('channel-memory-session-denied');
      await ctx.service.recoverMutations({scope:ctx.scope,readEntries:ctx.readTrack});
      const peek=store.peek(target,match,agent);if(!peek.ok)return {ok:false,message:'channel-memory-entry-unavailable',target};
      const before=ctx.assertWritable(target,peek.entry);
      const sources=key==='replace'?currentMemorySources(agent,ctx.scope):undefined;
      if(key==='replace'){
        const result=await ctx.service.commitMutation({scope:ctx.scope,sources,track:ctx.track(target),readEntries:ctx.readTrack,
          apply:prepare=>store.replace(target,match,contentOrAgent,agent,peek.entry,{beforeWrite:value=>prepare({...value,
            before:ctx.describe(target,value.before),after:ctx.describe(target,value.after)})})});
        return publicOutcome(result,ctx,target);
      }
      const result=await ctx.service.archiveMutation({before,scope:ctx.scope,sources,apply:()=>{
        const result=key==='replace'?store.replace(target,match,contentOrAgent,agent,peek.entry):store.remove(target,match,agent,peek.entry);
        if(!result.ok)return {result};
        // replace returns the whole track; find the changed slot against the
        // immediately pre-write track (not a guessed substring).
        const current=key==='replace'?result.replacementEntry:undefined;
        if(key==='replace'&&typeof current!=='string')throw Error('channel-memory-replacement-readback-missing');
        return {result,...(current?{after:ctx.describe(target,current)}:{})};
      }});
      return publicOutcome(result,ctx,target);
    };
    const value=Reflect.get(store,key);return typeof value==='function'?value.bind(store):value;
  }});
  rawStores.set(proxy,raw);
  return proxy;
}
