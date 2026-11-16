import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {MemoryStore,ArchiveStore,todayStamp,stripEntrySummary} from '../lib/store.js'
import {stripEntryId} from '../lib/sync/entryid.js'
import {memoryTool,renderSnapshot,resolveConfig} from '../lib/index.js'
import {channelMemoryContext,trustedMemorySessions} from '../lib/channel-memory.js'
import {createMemoryMetadataService,entryIdentity} from '../../dsh-wxauto/memory-view.mjs'

const displayed=raw=>stripEntrySummary(stripEntryId(raw))

function fixture(t) {
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'channel-memory-'))
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}))
  const workspace=path.join(directory,'workspace');fs.mkdirSync(workspace)
  const policy={workspace,ownerSessionId:'owner',groups:[{sessionId:'a',chatId:'stable-a'},{sessionId:'b',chatId:'stable-b'}],metadataFile:path.join(directory,'metadata','provenance.json')}
  const config=resolveConfig({memoryDir:path.join(directory,'memory'),channelMemoryPolicy:policy,reviewMode:'auto',reviewEnabled:true,keyBranchFilter:false})
  const agent=id=>({session:{id,header:{cwd:workspace}}})
  const store=new MemoryStore(config.memoryDir),archive=new ArchiveStore(config.memoryDir)
  const tool=memoryTool({get:()=>undefined},config,store,{},()=>config,archive)
  const metadata=createMemoryMetadataService({file:policy.metadataFile})
  const run=(id,args)=>tool.execute(args,{agent:agent(id),callId:'test'})
  return {directory,workspace,policy,config,agent,store,archive,tool,metadata,run}
}

test('policy opt-in is validated, defaults off and normal outside-workspace sessions keep upstream behavior',async t=>{
  const f=fixture(t);assert.equal(resolveConfig({}).channelMemoryPolicy,null)
  assert.throws(()=>resolveConfig({channelMemoryPolicy:{}}),/policy/)
  assert.throws(()=>resolveConfig({channelMemoryPolicy:{...f.policy,groups:[{sessionId:'owner',chatId:'group'}]}}),/group/)
  f.store.add('memory','old private entry')
  const outside={session:{id:'other',header:{cwd:path.join(f.directory,'other')}}}
  const result=await f.tool.execute({action:'list',target:'memory'},{agent:outside})
  assert.ok(result.entries[0].includes('old private entry'))
  const rawTool=memoryTool({get:()=>undefined},{toolName:'memory'},f.store,{},()=>({}),f.archive)
  const raw=await rawTool.execute({action:'list',target:'memory'},{agent:f.agent('a')})
  assert.ok(raw.entries[0].includes('old private entry'))
})

test('trusted companion rotation follows current owner and revokes old owner without widening groups',async t=>{
  const f=fixture(t);f.policy.ownerChatId='stable-owner';let current='owner';let source=null
  const ctx={get:name=>name==='biliBackground'?{getSessionId:()=>current}:name==='whaleHistory'?{currentSource:()=>source}:undefined}
  const agent=id=>({...f.agent(id),ctx})
  const run=(id,args)=>f.tool.execute(args,{agent:agent(id)})
  assert.equal((await run('owner',{action:'add',target:'memory',content:'private before rotation'})).ok,true)
  current='trusted-rotated-owner'
  assert.match(renderSnapshot(f.config,f.store,agent(current)),/private before rotation/)
  assert.equal((await run('owner',{action:'list',target:'memory'})).entries.length,0)
  assert.equal((await run('owner',{action:'add',target:'memory',content:'stale owner must fail'})).ok,false)
  source={chatId:'stable-owner',messageIds:['actual-current-input'],sourceKind:'wxauto-incoming'}
  assert.equal((await run(current,{action:'add',target:'memory',content:'private after rotation'})).ok,true)
  const meta=Object.values(f.metadata.read().entries).find(x=>x.sources.length)
  assert.equal(meta.createdBySession,current);assert.equal(meta.visibility.kind,'private')
  assert.deepEqual(meta.sources,[{messageId:'actual-current-input',chatId:'stable-owner',sourceKind:'wxauto-incoming'}])
  source=null
  assert.equal((await run(current,{action:'add',target:'memory',content:'background after cleared input'})).ok,true)
  const last=Object.values(f.metadata.read().entries).find(x=>x.createdBySession===current&&x.sources.length===0)
  assert.ok(last);assert.deepEqual(last.sources,[])
  for(const group of ['a','b'])assert.equal((await run(group,{action:'list',target:'memory'})).entries.length,0)
  const forged={...agent('unknown'),session:{id:'unknown',header:{cwd:f.workspace,ownerSessionId:current,parentSessionId:current}}}
  assert.equal(channelMemoryContext(f.config,f.store,forged).scope,null)
})

test('invalid trusted rotation fails owner closed; a group cannot become private through ID collision',t=>{
  const f=fixture(t)
  assert.equal(trustedMemorySessions(f.policy,f.agent('owner')).get('owner').kind,'private')
  for(const service of [{getSessionId:()=>''},{getSessionId:()=>Promise.resolve('owner')},{},{getSessionId:()=>{throw Error('unavailable')}}]) {
    const agent={...f.agent('owner'),ctx:{get:()=>service}}
    assert.equal(channelMemoryContext(f.config,f.store,agent).scope,null)
    assert.equal(trustedMemorySessions(f.policy,agent).get('a').kind,'chat')
  }
  const agent={...f.agent('a'),ctx:{get:()=>({getSessionId:()=> 'a'})}}
  assert.deepEqual(channelMemoryContext(f.config,f.store,agent).scope,{kind:'chat',chatId:'stable-a',sessionId:'a'})
  assert.equal(trustedMemorySessions(f.policy,agent).has('owner'),false)
})

test('trusted active groups update on each real memory-tool call without restarting or sharing private entries',async t=>{
  const f=fixture(t);f.policy.ownerChatId='stable-owner'
  let snapshot={revision:0,groups:structuredClone(f.policy.groups)},active=null
  const history={getMemoryGroups:()=>snapshot,currentSource:()=>active}
  const agent=id=>({...f.agent(id),ctx:{get:name=>name==='whaleHistory'?history:name==='biliBackground'?{getSessionId:()=> 'owner'}:undefined}})
  const run=(id,args)=>f.tool.execute(args,{agent:agent(id)})
  assert.equal((await run('owner',{action:'add',target:'memory',content:'owner private persists'})).ok,true)
  assert.equal((await run('a',{action:'add',target:'memory',content:'original group A persists'})).ok,true)
  assert.equal((await run('new-group',{action:'add',target:'memory',content:'not admitted'})).ok,false)
  assert.equal((await run('new-group',{action:'add',target:'memory',content:'model cannot admit itself',
    sessionId:'owner',chatId:'stable-owner',groups:[{sessionId:'new-group',chatId:'stable-new'}]})).ok,false)
  snapshot={revision:1,groups:[...f.policy.groups,{sessionId:'new-group',chatId:'stable-new'}]}
  active={chatId:'stable-new',messageIds:['new-group-current-source'],sourceKind:'wxauto-incoming'}
  assert.equal((await run('new-group',{action:'add',target:'memory',content:'new group fact'})).ok,true)
  const metadata=Object.values(f.metadata.read().entries).find(x=>x.createdBySession==='new-group')
  assert.deepEqual(metadata.visibility,{kind:'chat',chatId:'stable-new'})
  assert.equal(metadata.sources[0].messageId,'new-group-current-source')
  active=null
  const raw=f.store.entriesOf('memory').find(x=>x.includes('new group fact')),ref=entryIdentity({target:'memory',content:raw}).ref
  assert.equal((await run('new-group',{action:'expand',target:'memory',id:ref})).ok,true)
  assert.match(renderSnapshot(f.config,f.store,agent('new-group')),/new group fact/)
  assert.ok(!renderSnapshot(f.config,f.store,agent('new-group')).includes('owner private'))
  assert.equal((await run('b',{action:'list',target:'memory'})).entries.length,0)
  assert.equal((await run('a',{action:'list',target:'memory'})).entries.length,1)
  assert.equal((await run('owner',{action:'list',target:'memory'})).entries.length,3)
  snapshot={revision:2,groups:structuredClone(f.policy.groups)}
  assert.equal((await run('new-group',{action:'list',target:'memory'})).entries.length,0)
  assert.equal((await run('new-group',{action:'add',target:'memory',content:'removed route'})).ok,false)
  assert.equal((await run('owner',{action:'list',target:'memory'})).entries.length,3)
})

test('dynamic mappings reject malformed snapshots, duplicates, private collisions and legacy remapping',async t=>{
  const f=fixture(t);f.policy.ownerChatId='stable-owner'
  const valid={revision:1,groups:structuredClone(f.policy.groups)}
  const invalid=[null,{},Promise.reject(Error('unexpected async resolver')), {revision:-1,groups:[]},
    {revision:Number.MAX_SAFE_INTEGER+1,groups:[]},{revision:1.5,groups:[]},{revision:true,groups:[]},
    {revision:1,groups:{}},
    {revision:1,groups:[{sessionId:'a',chatId:'changed-existing-scope'}]},
    {revision:1,groups:[{sessionId:'new',chatId:'stable-owner'}]},
    {revision:1,groups:[{sessionId:'owner',chatId:'new'}]},
    {revision:1,groups:[{sessionId:'rotated-owner',chatId:'new'}]},
    {revision:1,groups:[{sessionId:'new',chatId:'x'},{sessionId:'new',chatId:'y'}]},
    {revision:1,groups:[{sessionId:'new1',chatId:'x'},{sessionId:'new2',chatId:'x'}]},
    {revision:1,groups:[{sessionId:' new ',chatId:'x'}]}]
  for(const value of invalid) {
    const history={getMemoryGroups:()=>value}
    const agent={...f.agent('a'),ctx:{get:name=>name==='whaleHistory'?history:name==='biliBackground'?{getSessionId:()=> 'rotated-owner'}:undefined}}
    const sessions=trustedMemorySessions(f.policy,agent)
    assert.equal(sessions.has('a'),false)
    assert.equal(sessions.has('b'),false)
    assert.deepEqual(sessions.get('rotated-owner'),{kind:'private',chatId:'stable-owner'})
  }
  await new Promise(resolve=>setImmediate(resolve)) // Rejecting async resolver is consumed, never awaited/granted.
  const agent={...f.agent('a'),ctx:{get:name=>name==='whaleHistory'?{getMemoryGroups:()=>valid}:undefined}}
  assert.equal(trustedMemorySessions(f.policy,agent).get('a').chatId,'stable-a')
})

test('absent capability keeps static compatibility; a broken dynamic service fails groups closed and can recover',t=>{
  const f=fixture(t)
  for(const history of [undefined,null,{currentSource:()=>null}]) {
    const agent={...f.agent('a'),ctx:{get:name=>name==='whaleHistory'?history:undefined}}
    assert.equal(trustedMemorySessions(f.policy,agent).get('a').chatId,'stable-a')
  }
  let fail=true
  const history={getMemoryGroups:()=>{if(fail)throw Error('temporary unavailable');return {revision:5,groups:f.policy.groups}}}
  const agent={...f.agent('a'),ctx:{get:name=>name==='whaleHistory'?history:undefined}}
  assert.equal(channelMemoryContext(f.config,f.store,agent).scope,null)
  fail=false;assert.equal(channelMemoryContext(f.config,f.store,agent).scope.chatId,'stable-a')
  fail=true;assert.equal(channelMemoryContext(f.config,f.store,agent).scope,null)
  assert.equal(trustedMemorySessions(f.policy,{...f.agent('a'),ctx:{get:name=>{if(name==='whaleHistory')throw Error('provider lookup failed')}}}).has('a'),false)
  assert.equal(trustedMemorySessions(f.policy,{...f.agent('a'),ctx:{get:name=>name==='whaleHistory'?{getMemoryGroups:null}:undefined}}).has('a'),false)
})

test('real memory tool add/list/query and snapshot share one channel policy',async t=>{
  const f=fixture(t)
  assert.equal((await f.run('owner',{action:'add',target:'memory',content:'PRIVATE OWNER SECRET'})).ok,true)
  assert.equal((await f.run('a',{action:'add',target:'memory',content:'A group preference'})).ok,true)
  assert.equal((await f.run('b',{action:'add',target:'user',content:'B user profile'})).ok,true)
  for(const id of ['a','b']) {
    const snapshot=renderSnapshot(f.config,f.store,f.agent(id))
    assert.ok(!snapshot.includes('PRIVATE OWNER SECRET'))
    assert.ok(!snapshot.includes(id==='a'?'B user profile':'A group preference'))
    assert.ok(snapshot.includes('memory:'))
  }
  const a=await f.run('a',{action:'list',target:'memory'})
  assert.equal(a.total,1);assert.match(a.entries.join('\n'),/A group preference/)
  assert.equal((await f.run('b',{action:'list',target:'memory'})).entries.length,0)
  assert.equal((await f.run('owner',{action:'list',target:'memory'})).entries.length,2)
  assert.equal((await f.run('unknown',{action:'list',target:'memory'})).entries.length,0)
  assert.equal((await f.run('unknown',{action:'add',target:'memory',content:'not allowed'})).ok,false)
})

test('daily multi-file range, project logs, and original key expand all filter by trusted caller',async t=>{
  const f=fixture(t)
  for(const id of ['owner','a','b'])for(const target of ['daily','project'])assert.equal((await f.run(id,{action:'add',target,content:`${id}-${target}-entry`})).ok,true)
  for(const target of ['daily','project']) {
    const result=await f.run('a',{action:'list',target,since:todayStamp(),until:todayStamp()})
    assert.equal(result.entries.length,1);assert.match(result.entries[0],new RegExp(`a-${target}`))
    assert.equal(result.total,1)
  }
  const raw=f.store.add('key','[id:11223344] A private key',f.agent('a')).entries.at(-1)
  await f.metadata.record({entry:{target:'key',content:raw,projectKey:'whale-work'},scope:channelMemoryContext(f.config,f.store,f.agent('a')).scope})
  assert.equal((await f.run('a',{action:'expand',target:'key',id:'11223344'})).ok,true)
  assert.equal((await f.run('b',{action:'expand',target:'key',id:'11223344'})).ok,false)
})

test('index refs expand through the real tool; groups cannot read another ref or change its track',async t=>{
  const f=fixture(t);await f.run('a',{action:'add',target:'memory',content:'Original A fact'})
  const raw=f.store.entriesOf('memory')[0],ref=entryIdentity({target:'memory',content:raw}).ref
  assert.match((await f.run('a',{action:'expand',target:'memory',id:ref})).entries[0],/Original A fact/)
  assert.equal((await f.run('b',{action:'expand',target:'memory',id:ref})).ok,false)
  assert.equal((await f.run('a',{action:'expand',target:'user',id:ref})).ok,false)
})

test('group mutation output does not disclose private track counts or character totals',async t=>{
  const f=fixture(t);await f.run('owner',{action:'add',target:'memory',content:'OWNER PRIVATE '.repeat(50)})
  const added=await f.run('a',{action:'add',target:'memory',content:'A fact'})
  assert.equal(added.chars,f.store.entriesOf('memory').at(-1).length)
  assert.ok(!added.message.includes('2'))
  assert.equal(added.entries,undefined)
})

test('replace archives before write, registers actual stamped body, and remove cannot target another scope',async t=>{
  const f=fixture(t);await f.run('a',{action:'add',target:'memory',content:'before A fact'})
  const before=f.store.entriesOf('memory')[0]
  assert.equal((await f.run('b',{action:'replace',target:'memory',match:'before A',content:'stolen'})).ok,false)
  assert.equal((await f.run('a',{action:'replace',target:'memory',match:'before A',content:'after A fact'})).ok,true)
  assert.equal(f.metadata.read().archives.at(-1).entry.content,before)
  assert.match((await f.run('a',{action:'list',target:'memory'})).entries[0],/after A fact/)
  assert.equal((await f.run('b',{action:'remove',target:'memory',match:'after A'})).ok,false)
  assert.equal((await f.run('a',{action:'remove',target:'memory',match:'after A'})).ok,true)
  assert.equal((await f.run('a',{action:'list',target:'memory'})).entries.length,0)
  assert.ok(f.metadata.read().archives.length>=2)
})

test('archive list retains original visibility instead of exposing another chat archive',async t=>{
  const f=fixture(t)
  for(const id of ['a','b']) {
    await f.run(id,{action:'add',target:'memory',content:`${id} archived fact`})
    assert.equal((await f.run(id,{action:'archive',target:'memory',match:`${id} archived`})).ok,true)
  }
  assert.equal((await f.run('a',{action:'list',target:'memory',archived:true})).entries.length,1)
  assert.match((await f.run('a',{action:'list',target:'memory',archived:true})).entries[0],/a archived/)
  assert.equal((await f.run('owner',{action:'list',target:'memory',archived:true})).entries.length,2)
})

test('original store checks expected old body inside its existing lock',t=>{
  const f=fixture(t);f.store.add('memory','one');const before=f.store.entriesOf('memory')[0]
  f.store.replace('memory','one','two')
  assert.equal(f.store.replace('memory','two','three',undefined,before).ok,false)
  assert.equal(f.store.remove('memory','two',undefined,before).ok,false)
  assert.match(f.store.entriesOf('memory')[0],/two/)
})

test('active history service binds actual message refs to adds and replacements; clearing it creates no fake source',async t=>{
  const f=fixture(t);let active={chatId:'stable-a',messageIds:['msg-a1','msg-a1','msg-a2'],sourceKind:'wxauto-incoming'}
  const calls=[]
  const agent={...f.agent('a'),ctx:{get:name=>name==='whaleHistory'?{currentSource:id=>{calls.push(id);return active}}:undefined}}
  const run=args=>f.tool.execute(args,{agent})
  assert.equal((await run({action:'add',target:'memory',content:'source fact',messageIds:['model-forged-id']})).ok,true)
  let state=f.metadata.read(),meta=Object.values(state.entries)[0]
  assert.deepEqual(meta.sources,[{messageId:'msg-a1',chatId:'stable-a',sourceKind:'wxauto-incoming'},{messageId:'msg-a2',chatId:'stable-a',sourceKind:'wxauto-incoming'}])
  assert.equal(meta.createdBySession,'a');assert.deepEqual(calls,['a'])
  active={chatId:'stable-a',messageIds:['msg-a3'],sourceKind:'wxauto-incoming'}
  assert.equal((await run({action:'replace',target:'memory',match:'source fact',content:'new source fact'})).ok,true)
  state=f.metadata.read();meta=Object.values(state.entries)[0]
  assert.equal(meta.sources[0].messageId,'msg-a3')
  assert.equal(state.archives.at(-1).metadata.sources[0].messageId,'msg-a1')
  active=null
  assert.equal((await run({action:'add',target:'memory',content:'background fact'})).ok,true)
  meta=Object.values(f.metadata.read().entries).find(item=>item.sources.length===0)
  assert.equal(meta.createdBySession,'a')
})

test('owner sources require the configured owner chat ID; another chat or invalid kind cannot contaminate writes',async t=>{
  const f=fixture(t);f.policy.ownerChatId='vault-owner'
  let active={chatId:'vault-owner',messageIds:['owner-msg'],sourceKind:'wxauto-incoming'}
  const agent={...f.agent('owner'),ctx:{get:name=>name==='whaleHistory'?{currentSource:()=>active}:undefined}}
  const run=args=>f.tool.execute(args,{agent})
  assert.equal((await run({action:'add',target:'memory',content:'owner source fact'})).ok,true)
  assert.equal(Object.values(f.metadata.read().entries)[0].sources[0].chatId,'vault-owner')
  for(const bad of [
    {chatId:'stable-b',messageIds:['group-msg'],sourceKind:'wxauto-incoming'},
    {chatId:'vault-owner',messageIds:['old-search-result'],sourceKind:'history-search'},
    {chatId:'vault-owner',messageIds:[''],sourceKind:'wxauto-incoming'},
  ]) {
    active=bad
    assert.equal((await run({action:'add',target:'memory',content:'must not be persisted'})).ok,false)
  }
  assert.equal(f.store.entriesOf('memory').length,1)
})

test('missing service or an unrelated DSH workspace does not require source bindings',async t=>{
  const f=fixture(t)
  const missing={...f.agent('a'),ctx:{get:()=>undefined}}
  assert.equal((await f.tool.execute({action:'add',target:'memory',content:'source unavailable'},{agent:missing})).ok,true)
  assert.deepEqual(Object.values(f.metadata.read().entries)[0].sources,[])
  const outside={session:{id:'ordinary',header:{cwd:path.join(f.directory,'other')}},ctx:{get:()=>{throw Error('must never need service')}}}
  assert.equal((await f.tool.execute({action:'add',target:'memory',content:'ordinary old behavior'},{agent:outside})).ok,true)
})

test('registered memory tools retain native-unread and mixed source kinds from the active adapter boundary',async t=>{
  for(const kind of ['wechat-native-unread','wechat-native-voice','wechat-native-group-text','mixed-wechat-incoming']) {
    const f=fixture(t)
    let active={chatId:'stable-a',messageIds:[`fixture-${kind}-1`],sourceKind:kind}
    const agent={...f.agent('a'),ctx:{get:name=>name==='whaleHistory'?{
      currentSource:id=>{assert.equal(id,'a');return active},
      getMemoryGroups:()=>({revision:1,groups:f.policy.groups}),
    }:undefined}}
    const run=args=>f.tool.execute(args,{agent})
    assert.equal((await run({action:'add',target:'memory',content:'native bound fact',
      sourceKind:'wxauto-incoming',messageIds:['forged-model-id'],chatId:'stable-b'})).ok,true)
    let state=f.metadata.read(),meta=Object.values(state.entries)[0]
    assert.deepEqual(meta.sources,[{messageId:`fixture-${kind}-1`,chatId:'stable-a',sourceKind:kind}])
    const nextKind=kind==='wechat-native-unread'?'mixed-wechat-incoming':'wechat-native-unread'
    active={chatId:'stable-a',messageIds:[`fixture-${kind}-replacement`],sourceKind:nextKind}
    assert.equal((await run({action:'replace',target:'memory',match:'native bound fact',content:'replacement bound fact'})).ok,true)
    state=f.metadata.read();meta=Object.values(state.entries)[0]
    assert.deepEqual(meta.sources,[{messageId:`fixture-${kind}-replacement`,chatId:'stable-a',sourceKind:nextKind}])
    assert.equal(state.archives.at(-1).metadata.sources[0].sourceKind,kind)
    assert.equal((await f.run('b',{action:'list',target:'memory'})).entries.length,0)
    active=null
    assert.equal((await run({action:'add',target:'user',content:'no active boundary',
      source:{chatId:'stable-a',messageIds:['forged'],sourceKind:kind},sourceKind:kind,messageIds:['forged']})).ok,true)
    const userMeta=Object.values(f.metadata.read().entries).find(m=>m.sources.length===0)
    assert.ok(userMeta);assert.equal(userMeta.createdBySession,'a')
  }
})

test('native and mixed current sources cannot cross chat scope or expand accepted source kinds',async t=>{
  const f=fixture(t)
  let active
  const agent={...f.agent('a'),ctx:{get:name=>name==='whaleHistory'?{currentSource:()=>active}:undefined}}
  for(const source of [
    {chatId:'stable-b',messageIds:['other-group-native'],sourceKind:'wechat-native-unread'},
    {chatId:'stable-b',messageIds:['other-group-voice'],sourceKind:'wechat-native-voice'},
    {chatId:'stable-b',messageIds:['other-group-text'],sourceKind:'wechat-native-group-text'},
    {chatId:'stable-b',messageIds:['other-owner-text'],sourceKind:'wechat-native-text'},
    {chatId:'stable-b',messageIds:['other-group-mixed'],sourceKind:'mixed-wechat-incoming'},
    {chatId:'stable-a',messageIds:['old-history'],sourceKind:'wechat-native-history'},
    {chatId:'stable-a',messageIds:['search-result'],sourceKind:'history-search'},
  ]) {
    active=source
    assert.equal((await f.tool.execute({action:'add',target:'memory',content:'must not persist',
      sourceKind:'wechat-native-unread',chatId:'stable-a',messageIds:['forged-local']},{agent})).ok,false)
  }
  assert.equal(f.store.entriesOf('memory').length,0)
  assert.equal(Object.keys(f.metadata.read().entries).length,0)
})

test('owner native-text provenance is preserved by registered add/expand and remains private',async t=>{
  const f=fixture(t);f.policy.ownerChatId='vault-owner'
  let active={chatId:'vault-owner',messageIds:['synthetic-owner-native-text-id'],sourceKind:'wechat-native-text'}
  const agent={...f.agent('owner'),ctx:{get:name=>name==='whaleHistory'?{currentSource:()=>active}:undefined}}
  const run=args=>f.tool.execute(args,{agent})
  assert.equal((await run({action:'add',target:'memory',content:'synthetic owner native fact',
    sourceKind:'wxauto-incoming',messageIds:['forged'],chatId:'stable-a'})).ok,true)
  const [meta]=Object.values(f.metadata.read().entries)
  assert.deepEqual(meta.sources,[{messageId:'synthetic-owner-native-text-id',chatId:'vault-owner',sourceKind:'wechat-native-text'}])
  const ref=entryIdentity({target:'memory',content:f.store.entriesOf('memory')[0]}).ref
  assert.deepEqual((await run({action:'expand',target:'memory',id:ref})).sources,meta.sources)
  for(const id of ['a','b'])assert.equal((await f.run(id,{action:'expand',target:'memory',id:ref})).ok,false)
  active={chatId:'stable-a',messageIds:['cross-chat'],sourceKind:'wechat-native-text'}
  assert.equal((await run({action:'add',target:'memory',content:'must not persist'})).ok,false)
  active=null
  assert.equal((await run({action:'add',target:'user',content:'ordinary owner no current source',
    sourceKind:'wechat-native-text',messageIds:['forged']})).ok,true)
  assert.ok(Object.values(f.metadata.read().entries).some(m=>m.sources.length===0))
})

test('managed list returns an oversized first entry and reconstructs its exact Unicode text through expand pages',async t=>{
  const f=fixture(t),body='long sentinel '+('中🙂\\"\n'.repeat(3500))+'final tail'
  assert.equal((await f.run('a',{action:'add',target:'memory',content:body})).ok,true)
  await f.run('a',{action:'add',target:'memory',content:'second visible sentinel'})
  await f.run('b',{action:'add',target:'memory',content:'other group secret'})
  const expected=displayed(f.store.entriesOf('memory')[0])
  const first=await f.run('a',{action:'list',target:'memory',max_chars:6000})
  assert.deepEqual(JSON.parse(f.tool.output.render({},first)[0].text),first)
  assert.ok(Object.keys(first).every(key=>Object.hasOwn(f.tool.output.schema.properties,key)))
  assert.equal(first.entries.length,1);assert.equal(first.next_offset,1);assert.equal(first.total_matches,2)
  assert.ok(first.items[0].next_text_offset>0);assert.ok(JSON.stringify(first).length<=12000)
  let reconstructed=first.items[0].text,offset=first.items[0].next_text_offset,pages=0
  while(offset!==null){
    const result=await f.run('a',{action:'expand',target:'memory',id:first.items[0].id,offset,max_chars:6000})
    assert.equal(result.ok,true);assert.equal(result.offset_unit,'unicode-code-points-in-displayed-entry')
    assert.ok(JSON.stringify(result).length<=12000);assert.ok(result.next_offset===null||result.next_offset>offset)
    reconstructed+=result.entries[0];offset=result.next_offset;assert.ok(++pages<100)
  }
  assert.equal(reconstructed,expected)
  const second=await f.run('a',{action:'list',target:'memory',offset:first.next_offset})
  assert.match(second.entries.join(''),/second visible sentinel/);assert.equal(second.next_offset,null)
  assert.ok(!JSON.stringify(first).includes('other group secret'))
  assert.equal((await f.run('b',{action:'expand',target:'memory',id:first.items[0].id})).ok,false)
})

test('visible entry offsets paginate all matches beyond fifty without displacement by private rows',async t=>{
  const f=fixture(t)
  f.store.add('memory','private legacy entry')
  for(let i=0;i<54;i++)await f.run('a',{action:'add',target:'memory',content:'paged synthetic item '+i})
  const first=await f.run('a',{action:'list',target:'memory',limit:50})
  const pages=[first];let offset=first.next_offset
  assert.ok(first.entries.length<=50);assert.equal(first.next_offset,first.entries.length)
  while(offset!==null){const page=await f.run('a',{action:'list',target:'memory',offset,limit:50});
    assert.ok(page.next_offset===null||page.next_offset===offset+page.entries.length)
    pages.push(page);offset=page.next_offset;assert.ok(pages.length<10)
  }
  assert.equal(new Set(pages.flatMap(page=>page.items.map(i=>i.id))).size,54)
  assert.ok(pages.every(page=>JSON.stringify(page).length<=12000))
  assert.ok(!JSON.stringify(pages).includes('private legacy'))
})

test('expand source pagination is independent of text, returns actual kinds and clips foreign refs from shared entries',async t=>{
  const f=fixture(t);await f.run('a',{action:'add',target:'memory',content:'shared source test 🙂'.repeat(500)})
  const raw=f.store.entriesOf('memory')[0],entry={target:'memory',content:raw},id=entryIdentity(entry).ref
  const sources=Array.from({length:25},(_,i)=>({messageId:'synthetic-a-'+i,chatId:'stable-a',sourceKind:i%2?'wechat-native-voice':'wxauto-incoming'}))
  await f.metadata.record({entry,scope:{kind:'chat',chatId:'stable-a',sessionId:'a'},sources})
  await f.metadata.promoteShared({entry,scope:{kind:'private',chatId:'owner-private',sessionId:'owner'},explicitOwnerRequest:true})
  // Legacy/imported sidecars may contain mixed-chat references. Visibility alone must not expose them.
  const state=f.metadata.read();state.entries[id].sources.push({messageId:'private-reference',chatId:'owner-private',sourceKind:'wxauto-incoming'},
    {messageId:'other-group-reference',chatId:'stable-b',sourceKind:'wechat-native-unread'})
  fs.writeFileSync(f.policy.metadataFile,JSON.stringify(state))
  let offset=0;const seen=[]
  do{
    const result=await f.run('a',{action:'expand',target:'memory',id,offset:7,max_chars:11,source_offset:offset})
    assert.equal(result.ok,true);assert.equal(result.offset,7);assert.equal(result.next_offset,18)
    assert.equal(result.total_sources,25);assert.ok(JSON.stringify(result).length<=12000)
    assert.deepEqual(JSON.parse(f.tool.output.render({},result)[0].text),result)
    assert.ok(Object.keys(result).every(key=>Object.hasOwn(f.tool.output.schema.properties,key)))
    assert.ok(!JSON.stringify(result).includes('private-reference'));assert.ok(!JSON.stringify(result).includes('other-group-reference'))
    seen.push(...result.sources);offset=result.next_source_offset
  }while(offset!==null)
  assert.deepEqual(seen,sources)
  const owner=await f.run('owner',{action:'expand',target:'memory',id,source_offset:25,max_chars:1})
  assert.equal(owner.total_sources,27);assert.equal(owner.sources.length,2)
  const b=await f.run('b',{action:'expand',target:'memory',id,max_chars:1})
  assert.deepEqual(b.sources,[{messageId:'other-group-reference',chatId:'stable-b',sourceKind:'wechat-native-unread'}])
})

test('archived list pages and exact archived expand retain scope and full text',async t=>{
  const f=fixture(t)
  await f.run('a',{action:'add',target:'memory',content:'archive long '+ '🙂旧'.repeat(7000)})
  const expected=displayed(f.store.entriesOf('memory')[0])
  assert.equal((await f.run('a',{action:'archive',target:'memory',match:'archive long'})).ok,true)
  await f.run('a',{action:'add',target:'memory',content:'archive second'})
  await f.run('a',{action:'archive',target:'memory',match:'archive second'})
  const first=await f.run('a',{action:'list',target:'memory',archived:true,limit:1,max_chars:7})
  assert.equal(first.next_offset,1)
  const next=await f.run('a',{action:'list',target:'memory',archived:true,limit:1,offset:1})
  assert.match(next.entries[0],/archive second/)
  let text='',offset=0
  do{const page=await f.run('a',{action:'expand',target:'memory',archived:true,id:first.items[0].id,offset});
    assert.equal(page.ok,true);text+=page.entries[0];offset=page.next_offset
  }while(offset!==null)
  assert.equal(text,expected)
  assert.equal((await f.run('b',{action:'expand',target:'memory',archived:true,id:first.items[0].id})).ok,false)
})

test('historical daily entry previews can be expanded after its file is no longer today',async t=>{
  const f=fixture(t);await f.run('a',{action:'add',target:'daily',content:'old daily long '+ '中🙂'.repeat(4000)})
  const original=f.store.pathOf('daily',f.agent('a')),older=path.join(path.dirname(original),'2001-01-01.md')
  fs.renameSync(original,older)
  const result=await f.run('a',{action:'list',target:'daily',since:'2001-01-01',until:'2001-01-01',max_chars:100})
  assert.equal(result.items.length,1)
  const page=await f.run('a',{action:'expand',target:'daily',id:result.items[0].id,offset:result.items[0].next_text_offset})
  assert.equal(page.ok,true);assert.ok(page.entries[0].length>0)
})

test('archived versions retaining the same stable entry ID expand by their own digest',async t=>{
  const f=fixture(t),scope={kind:'chat',chatId:'stable-a',sessionId:'a'}
  for(const content of ['[id:abcd1234] [2026-11-02] first archived version','[id:abcd1234] [2026-11-02] second archived version']){
    const entry={target:'memory',content}
    await f.metadata.record({entry,scope})
    f.archive.append('memory',content,f.workspace)
    await f.metadata.archiveMutation({before:entry,scope,apply:()=>({result:{ok:true}})})
  }
  const listed=await f.run('a',{action:'list',target:'memory',archived:true})
  assert.equal(listed.items.length,2);assert.notEqual(listed.items[0].id,listed.items[1].id)
  for(let i=0;i<2;i++){
    const expanded=await f.run('a',{action:'expand',target:'memory',archived:true,id:listed.items[i].id})
    assert.equal(expanded.ok,true);assert.equal(expanded.entries[0],listed.items[i].text)
  }
})

test('registered daily expand finds the 129th entry behind public query paging and retains private/group scope',async t=>{
  const f=fixture(t),owner=f.agent('owner');f.policy.ownerChatId='owner-private'
  // Use the actual Store writer for a long synthetic daily file. Avoid 128
  // unrelated model writes: these predecessors are unclassified legacy private
  // entries and already exceed both the public 50-entry and text budgets.
  fs.mkdirSync(path.dirname(f.store.pathOf('daily',owner)),{recursive:true})
  f.store.write('daily',Array.from({length:128},(_,i)=>`[12:00] synthetic earlier daily ${i} `+'旧'.repeat(160)),owner)
  const source={chatId:'owner-private',messageIds:['synthetic-deep-daily-message'],sourceKind:'wechat-native-voice'}
  const agent={...owner,ctx:{get:name=>name==='whaleHistory'?{currentSource:()=>source}:undefined}}
  assert.equal((await f.tool.execute({action:'add',target:'daily',content:'synthetic target 🙂 deep original text'},{agent})).ok,true)
  const entry=f.store.entriesOf('daily',owner)[128],ref=entryIdentity({target:'daily',content:entry}).ref
  const listed=await f.run('owner',{action:'list',target:'daily',since:'0000-01-01',until:'9999-12-31',recent:false,limit:50})
  assert.ok(listed.items.length<50);assert.ok(!listed.items.some(item=>item.id===ref))
  let offset=0,text='',calls=0
  do{
    const page=await f.run('owner',{action:'expand',target:'daily',id:ref,offset,max_chars:8})
    assert.equal(page.ok,true);assert.deepEqual(page.sources,[{messageId:'synthetic-deep-daily-message',chatId:'owner-private',sourceKind:'wechat-native-voice'}])
    assert.ok(page.next_offset===null||page.next_offset===offset+Array.from(page.entries[0]).length)
    text+=page.entries[0];offset=page.next_offset;assert.ok(++calls<50)
  }while(offset!==null)
  assert.equal(text,displayed(entry));assert.ok(calls>1)
  for(const id of ['a','b'])assert.equal((await f.run(id,{action:'expand',target:'daily',id:ref,max_chars:8})).ok,false)
  // Group A's later entry is also beyond the raw query budget. Only A and the
  // existing owner authority can expand it; B cannot borrow its exact ref.
  const groupAgent={...f.agent('a'),ctx:{get:name=>name==='whaleHistory'?{currentSource:()=>({chatId:'stable-a',
    messageIds:['synthetic-group-deep-message'],sourceKind:'wechat-native-unread'})}:undefined}}
  assert.equal((await f.tool.execute({action:'add',target:'daily',content:'synthetic deep group A entry'},{agent:groupAgent})).ok,true)
  const groupEntry=f.store.entriesOf('daily',owner).at(-1),groupRef=entryIdentity({target:'daily',content:groupEntry}).ref
  const visible=await f.run('a',{action:'expand',target:'daily',id:groupRef,max_chars:8})
  assert.equal(visible.ok,true);assert.equal(visible.sources[0].chatId,'stable-a')
  assert.equal((await f.run('b',{action:'expand',target:'daily',id:groupRef})).ok,false)
})
