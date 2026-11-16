import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {createHistoryService,registerHistoryTools} from './history-service.mjs';

const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
function pendingContextFixture(t){
  const data=fs.mkdtempSync(path.join(os.tmpdir(),'whale-context-race-'));
  t.after(()=>fs.rmSync(data,{recursive:true,force:true}));
  const searches=[];
  const client={async request(op,args){
    if(op==='ensure_chat')return {id:'synthetic-canonical-'+args.chat_key};
    if(op==='get_summaries')return {summaries:[]};
    if(op==='search'){const gate=deferred();searches.push({args:structuredClone(args),gate});return gate.promise;}
    throw Error('unexpected-fixture-operation');
  },close(){}};
  const routes=new Map([['owner',{target:'owner',kind:'friend'}],['group',{target:'group',kind:'group'}]]);
  const service=createHistoryService({config:{dataRoot:data,accountId:'synthetic-account',chatKeys:{owner:'private',group:'group'}},
    root:data,routes,routeSessionId:r=>r.kind==='friend'?'owner-session':'group-session',currentSessionId:()=> 'owner-session',report(){},client});
  t.after(()=>service.close());
  const until=async count=>{for(let i=0;i<50&&searches.length<count;i++)await Promise.resolve();assert.equal(searches.length,count);};
  const row=id=>({id,text:'synthetic context fixture',timestamp:1});
  return {service,searches,until,row};
}

test('current-input exclusions supersede a background pending refresh and stale completion cannot overwrite cache',async t=>{
  const f=pendingContextFixture(t);
  const old=f.service.prepareContext('owner-session',{refresh:true,excludeMessageIds:[]});
  const oldResult=old.then(()=>null,error=>error.message);await f.until(1);
  const current=f.service.prepareContext('owner-session',{refresh:true,excludeMessageIds:['synthetic-current']});
  await f.until(2);assert.notEqual(old,current);
  assert.deepEqual(f.searches[0].args.exclude_message_ids,[]);
  assert.deepEqual(f.searches[1].args.exclude_message_ids,['synthetic-current']);
  f.searches[1].gate.resolve({messages:[f.row('synthetic-past')]});
  assert.deepEqual((await current).messages.map(m=>m.id),['synthetic-past']);
  f.searches[0].gate.resolve({messages:[f.row('synthetic-current')]});
  assert.equal(await oldResult,'history-context-refresh-superseded');
  assert.ok(f.service.contextText('owner-session').includes('synthetic-past'));
  assert.ok(!f.service.contextText('owner-session').includes('synthetic-current'));
});

test('equivalent pending exclusion sets coalesce; caller array mutation cannot change queued query',async t=>{
  const f=pendingContextFixture(t),ids=['synthetic-b','synthetic-a','synthetic-a'];
  const first=f.service.prepareContext('owner-session',{excludeMessageIds:ids});ids.push('synthetic-mutated');
  const second=f.service.prepareContext('owner-session',{refresh:true,excludeMessageIds:['synthetic-a','synthetic-b']});
  assert.equal(first,second);await f.until(1);
  assert.deepEqual(f.searches[0].args.exclude_message_ids,['synthetic-a','synthetic-b']);
  f.searches[0].gate.resolve({messages:[]});await first;
});

test('cutover status includes unresolved context retrieval even with empty journal tails',async t=>{
  const f=pendingContextFixture(t),work=f.service.prepareContext('owner-session');await f.until(1);
  assert.equal(f.service.cutoverStatus().contextPending,1);
  f.searches[0].gate.resolve({messages:[]});await work;await Promise.resolve();
  assert.equal(f.service.cutoverStatus().contextPending,0);
});

test('old completion does not clear the newer pending request, and group scope stays independent',async t=>{
  const f=pendingContextFixture(t);
  const old=f.service.prepareContext('owner-session',{excludeMessageIds:[]});const rejected=old.catch(error=>error.message);await f.until(1);
  const current=f.service.prepareContext('owner-session',{excludeMessageIds:['synthetic-current']});await f.until(2);
  f.searches[0].gate.resolve({messages:[f.row('stale')]});assert.equal(await rejected,'history-context-refresh-superseded');
  assert.equal(f.service.prepareContext('owner-session',{excludeMessageIds:['synthetic-current']}),current);
  const group=f.service.prepareContext('group-session',{excludeMessageIds:[]});await f.until(3);
  assert.equal(f.searches[1].args.chat_id,'synthetic-canonical-private');
  assert.equal(f.searches[2].args.chat_id,'synthetic-canonical-group');
  assert.deepEqual(f.searches[2].args.allowed_chat_ids,['synthetic-canonical-group']);
  f.searches[1].gate.resolve({messages:[]});f.searches[2].gate.resolve({messages:[f.row('group-only')]});await Promise.all([current,group]);
  assert.ok(!f.service.contextText('owner-session').includes('group-only'));
});

test('cached exclusions must match later non-refresh calls and explicit invalidation still supersedes pending',async t=>{
  const f=pendingContextFixture(t);
  const original=f.service.prepareContext('owner-session');await f.until(1);
  f.searches[0].gate.resolve({messages:[f.row('synthetic-current')]});await original;
  const current=f.service.prepareContext('owner-session',{excludeMessageIds:['synthetic-current']});await f.until(2);
  assert.equal(f.service.contextText('owner-session'),'');
  const outcome=current.catch(error=>error.message);f.service.invalidateContext('owner-session');
  f.searches[1].gate.resolve({messages:[]});assert.equal(await outcome,'history-context-refresh-superseded');
  assert.equal(f.service.contextText('owner-session'),'');
});

test('dynamic memory mapping publishes canonical active groups and preserves original routes',async t=>{
  const data=fs.mkdtempSync(path.join(os.tmpdir(),'whale-dynamic-history-'));
  const routes=new Map([['old',{target:'old',kind:'group',sessionId:'old-session'}]]);
  const config={dataRoot:data,accountId:'account',chatKeys:{old:'old-key'}};
  const calls=[];
  const client={async request(op,args){calls.push({op,args});return {id:args.chat_key==='old-key'?'old-canonical':'new-canonical'};},close(){}};
  const service=createHistoryService({config,root:data,routes,routeSessionId:r=>r.sessionId,
    currentSessionId:()=> 'owner',report(){},client});
  t.after(()=>service.close());
  await service.chat('old');
  const before=service.getMemoryGroups();
  assert.deepEqual(before.groups,[{sessionId:'old-session',chatId:'old-canonical'}]);
  const native=await service.prepareNativeGroup('new','wechat:12345@chatroom');
  assert.equal(native.id,'new-canonical');
  assert.deepEqual(service.getMemoryGroups(),before); // Preparation grants no scope.
  config.chatKeys.new='wechat:12345@chatroom';
  const route={target:'new',kind:'group',sessionId:'native-stable-session',active:false};routes.set('new',route);
  await service.chat('new');
  assert.deepEqual(service.getMemoryGroups(),before);
  route.active=true;
  const after=service.getMemoryGroups();assert.ok(after.revision>before.revision);
  assert.deepEqual(after.groups,[...before.groups,{sessionId:'native-stable-session',chatId:'new-canonical'}]);
  assert.deepEqual(service.getMemoryGroups(),after);
  routes.delete('new');assert.deepEqual(service.getMemoryGroups().groups,before.groups);
  assert.throws(()=>service.prepareNativeGroup('new','private-account'),/identity-invalid/);
});

test('real private worker persists incoming, sent evidence, and scoped historical reads',async t=>{
  const root=path.dirname(fileURLToPath(import.meta.url));
  const data=fs.mkdtempSync(path.join(os.tmpdir(),'whale-live-history-'));
  const routes=new Map([['owner',{target:'owner',kind:'friend'}],['group',{target:'group',kind:'group'}]]);
  const errors=[];
  const config={dataRoot:data,python:(process.env.WHALE_TEST_PYTHON||path.resolve(root,'../../.venv-wxauto/Scripts/python.exe')),accountId:'test-account',chatKeys:{owner:'local-owner',group:'local-group'}};
  let ownerSession='bili';
  const service=createHistoryService({config,root,routes,routeSessionId:r=>r.kind==='friend'?ownerSession:'group-session',currentSessionId:()=>ownerSession,report:(...args)=>errors.push(args)});
  t.after(()=>{service.close();});
  // The test folder is intentionally retained until the child releases its DB.
  await service.incoming({id:'incoming-1',target:'owner',kind:'friend',sender:'owner',text:'私聊专属小字原图',images:[]});
  await service.incoming({id:'incoming-1',target:'owner',kind:'friend',sender:'owner',text:'私聊专属小字原图',images:[]});
  await service.incoming({id:'group-1',target:'group',kind:'group',sender:'member',text:'群里的公开问题'});
  const token=await service.beginInput('bili','owner',[{id:'incoming-1'}]);
  assert.equal(service.currentSource('bili').messageIds.length,1);
  assert.equal(service.currentSource('group-session'),null);
  service.endInput('bili','wrong-token');assert.ok(service.currentSource('bili'));
  service.endInput('bili',token);assert.equal(service.currentSource('bili'),null);
  const native={target:'group',kind:'group',sender:'native member',text:'原生未读唯一消息',nativeSourceText:'fixture-peer:\n原生未读唯一消息',
    sourceKind:'wechat-native-unread',nativeId:'wechat-server:123456',nativeSenderId:'fixture-peer',
    nativeSnapshotId:'fixture-snapshot',timestamp:1700000000000,mediaType:'text'};
  const firstNative=await service.incoming({...native,id:'native-source-a'});
  const againNative=await service.incoming({...native,id:'native-source-b'});
  assert.equal(firstNative.id,againNative.id); // Same server identity, no text/time guessing.
  const nativeSource=await service.beginInput('group-session','group',[{...native,id:'native-source-a'}]);
  assert.equal(service.currentSource('group-session').sourceKind,'wechat-native-unread');
  assert.deepEqual(service.currentSource('group-session').messageIds,[firstNative.id]);
  service.endInput('group-session',nativeSource);
  const nativeHistory=(await service.query('group-session',{query:'原生未读唯一消息'})).messages;
  assert.equal(nativeHistory.length,1);
  assert.equal(nativeHistory[0].text,native.nativeSourceText);
  const audioFolder=path.join(data,'voice','incoming-native','fixture');fs.mkdirSync(audioFolder,{recursive:true});
  const audio=['source.silk','decoded.wav'].map((name,i)=>{
    const file=path.join(audioFolder,name);fs.writeFileSync(file,'synthetic audio fixture '+i);
    return {path:file,sha256:createHash('sha256').update(fs.readFileSync(file)).digest('hex'),role:i?'decoded':'source'};
  });
  const voice={...native,id:'native-voice-source-a',sourceKind:'wechat-native-voice',nativeId:'wechat-server:123457',
    mediaType:'voice',nativeSourceText:'[语音]',text:'[本地语音转文字，可能有误]\n本地原音转录证据',
    transcript:'本地原音转录证据',asrBackend:{name:'sensevoice-int8',provider:'cpu'},audio};
  const voiceSaved=await service.incoming(voice);assert.equal(voiceSaved.messageIds.length,2);
  assert.deepEqual((await service.incoming(voice)).messageIds,voiceSaved.messageIds);
  const voiceToken=await service.beginInput('group-session','group',[voice]);
  assert.equal(service.currentSource('group-session').sourceKind,'wechat-native-voice');
  assert.deepEqual(service.currentSource('group-session').messageIds,voiceSaved.messageIds);
  service.endInput('group-session',voiceToken);
  const transcript=(await service.query('group-session',{query:'本地原音转录证据'})).messages;
  assert.equal(transcript.length,1);assert.equal(transcript[0].parent_message_id,voiceSaved.id);
  assert.equal((await service.query('bili',{query:'本地原音转录证据'})).messages.length,0);
  const sent=await service.outgoing('owner',{kind:'text',text:'已经收到原图'},'reply-1:0');
  await service.delivery(sent,'reply-1:0','attempted');await service.delivery(sent,'reply-1:0','sent');
  const history=await service.query('bili',{query:'原图'});
  const records=history.messages??history.results??[];
  assert.equal(records.length,2);
  assert.equal(records.find(m=>m.direction==='outgoing').delivery_state,'sent');
  const file=path.join(data,'outbox','owner','report.txt');
  fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,'outgoing test attachment');
  await service.outgoing('owner',{kind:'attachment',text:'附件测试',filepath:file},'attachment-1:0');
  const files=await service.query('bili',{query:'附件测试'});
  assert.equal(files.messages[0].delivery_state,'generated');
  assert.equal(files.messages[0].attachments[0].path,'outbox/owner/report.txt');
  assert.equal(files.messages[0].attachments[0].sha256.length,64);
  const group=await service.query('group-session',{query:'私聊'});
  assert.equal((group.messages??group.results??[]).length,0);
  await assert.rejects(()=>service.query('group-session',{chat:'owner',query:'私聊'}),/history-chat-denied/);
  const tools=new Map();
  const remove=registerHistoryTools({tools:{register:tool=>{tools.set(tool.name,tool);return ()=>tools.delete(tool.name);}}},service);
  const exec=id=>({agent:{session:{id}}});
  const original=records.find(m=>m.direction==='incoming');let offset=0,reconstructed='';
  while(offset!==null){const value=await tools.get('whale_history_message').execute({messageId:original.id,offset,maxChars:3},exec('bili'));
    reconstructed+=value.message.text;offset=value.next_offset;}
  assert.equal(reconstructed,original.text);
  await assert.rejects(()=>tools.get('whale_history_message').execute({messageId:original.id},exec('group-session')),/message-not-found/);
  const note=await tools.get('whale_context_checkpoint').execute({text:'Fixture continuity note; actual received image request, reply confirmed sent.',
    sourceIds:records.map(m=>m.id)},exec('bili'));
  assert.equal(note.kind,'model-written-checkpoint');
  const clues=await tools.get('whale_context_search').execute({query:'continuity'},exec('bili'));
  assert.equal(clues.kind,'model-written-continuity-notes');assert.equal(clues.summaries[0].id,note.id);
  const fullNote=await tools.get('whale_context_read').execute({summaryId:note.id,maxChars:10},exec('bili'));
  assert.equal(fullNote.summary.text,'Fixture co');assert.equal(fullNote.next_offset,10);
  assert.deepEqual(new Set(fullNote.summary.source_ids),new Set(records.map(m=>m.id)));
  assert.equal((await tools.get('whale_context_search').execute({query:'continuity'},exec('group-session'))).summaries.length,0);
  await assert.rejects(()=>tools.get('whale_context_read').execute({summaryId:note.id},exec('group-session')),/summary-not-found/);
  await assert.rejects(()=>tools.get('whale_context_search').execute({chat:'owner'},exec('group-session')),/history-chat-denied/);
  const resume=await tools.get('whale_context_resume').execute({},exec('bili'));
  assert.equal(resume.summaries[0].id,note.id);assert.ok(service.contextText('bili').includes(note.id));
  await assert.rejects(()=>tools.get('whale_context_checkpoint').execute({text:'foreign',sourceIds:records.map(m=>m.id)},exec('group-session')),/scope-denied/);
  const groupResume=await tools.get('whale_context_resume').execute({},exec('group-session'));
  assert.equal(groupResume.summaries.length,0);assert.ok(!JSON.stringify(groupResume).includes('私聊专属'));
  ownerSession='bili-next';
  assert.equal(service.contextText('bili'),'');
  assert.throws(()=>service.prepareContext('bili'),/session-denied/);
  const rotated=await service.prepareContext('bili-next');assert.equal(rotated.summaries[0].id,note.id);
  assert.ok(service.contextText('bili-next').includes(note.id));
  const withoutCurrent=await service.prepareContext('bili-next',{refresh:true,excludeMessageIds:[original.id]});
  assert.ok(!withoutCurrent.messages.some(row=>row.id===original.id));
  service.invalidateContext('bili-next');assert.equal(service.contextText('bili-next'),'');
  const afterCompaction=await service.prepareContext('bili-next');
  assert.ok(afterCompaction.messages.some(row=>row.id===original.id));assert.equal(afterCompaction.summaries[0].id,note.id);
  const image=path.join(data,'media','context.png');fs.mkdirSync(path.dirname(image),{recursive:true});fs.writeFileSync(image,'fixture bytes, not a live image');
  await service.evidence({id:'incoming-1',target:'owner',kind:'friend',images:[{path:image,status:'read',source:'visible-thumbnail',evidence:{summary:'图中文字：收到测试资料'}}]});
  const foundEvidence=await service.query('bili-next',{query:'图中文字'});
  assert.equal(foundEvidence.messages.length,1);assert.equal(foundEvidence.messages[0].source,'vision-evidence');
  assert.equal(foundEvidence.messages[0].metadata.parentSourceId,'incoming-1');
  assert.equal(foundEvidence.messages[0].parent_message_id,original.id);
  const evidenceText=await service.readMessage('bili-next',{messageId:foundEvidence.messages[0].id});
  assert.equal(evidenceText.message.parent_message_id,original.id);
  assert.equal(foundEvidence.messages[0].metadata.imageSource,'visible-thumbnail');
  assert.equal(foundEvidence.messages[0].metadata.path,'media/context.png');
  assert.equal((await service.query('group-session',{query:'图中文字'})).messages.length,0);
  await service.evidence({id:'incoming-failed',target:'owner',kind:'friend',images:[{path:image,status:'failed',message:'识图失败，内容未知。'}]});
  const failedEvidence=await service.query('bili-next',{query:'识图失败'});
  assert.equal(failedEvidence.messages.length,1);assert.equal(failedEvidence.messages[0].metadata.visionStatus,'failed');
  assert.equal(failedEvidence.messages[0].parent_message_id,null);
  assert.equal(JSON.parse(failedEvidence.messages[0].text).status,'failed');
  remove();assert.equal(tools.size,0);
  assert.deepEqual(errors.filter(([event])=>!['vault-inbox-replay','vault-outgoing-replay','vault-evidence-replay','vault-worker-state','history-context-prepared'].includes(event)),[]);
  assert.ok(errors.filter(([event])=>event==='vault-inbox-replay').every(([,value])=>value.historyInbox.pending===0));
  assert.ok(errors.some(([event,value])=>event==='vault-worker-state'&&value.historyWriter.state==='ready'));
});
