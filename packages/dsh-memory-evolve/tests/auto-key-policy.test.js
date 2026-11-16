import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {MemoryStore,ArchiveStore,SuggestionQueue} from '../lib/store.js'
import {memoryTool,renderSnapshot,resolveConfig,RUNTIME_KEYS,validateRuntimePatch} from '../lib/index.js'
import {autoKeyEnabled} from '../lib/channel-memory.js'
import {createMemoryMetadataService} from '../../dsh-wxauto/memory-view.mjs'

function fixture(t,enabled=true) {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'auto-key-'))
  t.after(()=>fs.rmSync(dir,{recursive:true,force:true}))
  const workspace=path.join(dir,'whale-work');fs.mkdirSync(workspace)
  const policy={workspace,ownerSessionId:'owner',ownerChatId:'private',groups:[{sessionId:'a',chatId:'group-a'},{sessionId:'b',chatId:'group-b'}],metadataFile:path.join(dir,'metadata.json')}
  const config=resolveConfig({memoryDir:path.join(dir,'memory'),channelMemoryPolicy:policy,
    autoKeyPolicy:enabled?{workspace}:null,keyBranchFilter:false,perTurnKeyWrites:true})
  const store=new MemoryStore(config.memoryDir),archive=new ArchiveStore(config.memoryDir),queue=new SuggestionQueue(config.suggestionsFile)
  const tool=memoryTool({get:()=>undefined},config,store,queue,()=>config,archive)
  const agent=id=>({session:{id,header:{cwd:workspace}}})
  const run=(id,args)=>tool.execute(args,{agent:agent(id)})
  return {dir,workspace,policy,config,store,queue,tool,agent,run}
}

test('explicit scoped automatic KEY uses actual store, preserves summary, provenance, isolation and old queue',async t=>{
  const f=fixture(t)
  f.queue.write([{id:'old-pending',target:'key',content:'untouched old suggestion'}])
  const before=fs.readFileSync(f.queue.file)
  const agent={...f.agent('a'),ctx:{get:n=>n==='whaleHistory'?{currentSource:()=>({chatId:'group-a',messageIds:['synthetic-input'],sourceKind:'wechat-native-text'})}:undefined}}
  const result=await f.tool.execute({action:'add',target:'key',content:'group A durable fact',summary:'A summary'},{agent})
  assert.equal(result.ok,true)
  assert.ok(f.store.query('key',agent).some(x=>x.includes('group A durable fact')&&x.includes('[summary:A summary]')))
  const meta=Object.values(createMemoryMetadataService({file:f.policy.metadataFile}).read().entries)[0]
  assert.deepEqual(meta.visibility,{kind:'chat',chatId:'group-a'})
  assert.deepEqual(meta.sources,[{messageId:'synthetic-input',chatId:'group-a',sourceKind:'wechat-native-text'}])
  assert.equal((await f.run('b',{action:'list',target:'key'})).entries.length,0)
  assert.equal((await f.run('owner',{action:'list',target:'key'})).entries.length,1)
  await f.tool.execute({action:'add',target:'key',content:'group A durable fact',summary:'A summary'},{agent})
  assert.equal(f.store.query('key',agent).length,1)
  assert.deepEqual(fs.readFileSync(f.queue.file),before)
  assert.match(renderSnapshot(f.config,f.store,agent),/KEY 自动生效授权/)
  assert.doesNotMatch(renderSnapshot(f.config,f.store,agent),/target=key 提交 1 条建议/)
})

test('no policy, other cwd, nested cwd and untrusted session cannot directly write KEY',async t=>{
  const f=fixture(t,false)
  assert.equal(resolveConfig({}).autoKeyPolicy,null)
  assert.equal(RUNTIME_KEYS.includes('autoKeyPolicy'),false)
  assert.throws(()=>validateRuntimePatch('autoKeyPolicy',{workspace:f.workspace}))
  assert.doesNotMatch(f.tool.description,/autoKeyPolicy/)
  assert.equal((await f.run('owner',{action:'add',target:'key',content:'manual default'})).ok,true)
  assert.equal(f.store.query('key',f.agent('owner')).length,0)
  assert.equal(f.queue.read().length,1)
  f.config.autoKeyPolicy={workspace:f.workspace}
  assert.match(f.tool.description,/autoKeyPolicy/)
  for(const agent of [
    {...f.agent('owner'),session:{id:'owner',header:{cwd:path.join(f.dir,'other')}}},
    {...f.agent('owner'),session:{id:'owner',header:{cwd:path.join(f.workspace,'nested')}}},
    f.agent('unknown'),
  ]) {
    assert.equal(autoKeyEnabled(f.config,agent),false)
    await f.tool.execute({action:'add',target:'key',content:`queued ${f.queue.read().length}`,autoKeyPolicy:{workspace:f.workspace}},{agent})
    assert.equal(f.store.query('key',agent).length,0)
    assert.doesNotMatch(renderSnapshot(f.config,f.store,agent),/KEY 自动生效授权/)
  }
  assert.equal(f.queue.read().length,4)
})

test('authorization requires same absolute managed workspace and current trusted owner identity',async t=>{
  const f=fixture(t)
  for(const autoKeyPolicy of [{workspace:'relative'},{workspace:path.join(f.dir,'other')},{workspace:f.workspace,enabled:true},true]) {
    assert.throws(()=>resolveConfig({channelMemoryPolicy:f.policy,autoKeyPolicy}),/auto-key-policy/)
  }
  assert.throws(()=>resolveConfig({autoKeyPolicy:{workspace:f.workspace}}),/auto-key-policy/)
  let current='owner'
  const agent=id=>({...f.agent(id),ctx:{get:n=>n==='biliBackground'?{getSessionId:()=>current}:undefined}})
  assert.equal(autoKeyEnabled(f.config,agent('owner')),true)
  current='rotated'
  assert.equal(autoKeyEnabled(f.config,agent('owner')),false)
  assert.equal(autoKeyEnabled(f.config,agent('rotated')),true)
  assert.equal((await f.tool.execute({action:'add',target:'key',content:'rotated owner fact'},{agent:agent('rotated')})).ok,true)
  current=null
  assert.equal(autoKeyEnabled(f.config,agent('rotated')),false)
})

test('automatic KEY still uses scoped replacement and archive without changing old suggestions',async t=>{
  const f=fixture(t)
  await f.run('a',{action:'add',target:'key',content:'old group fact'})
  assert.equal((await f.run('b',{action:'replace',target:'key',match:'old group fact',content:'wrong group fact'})).ok,false)
  assert.equal((await f.run('a',{action:'replace',target:'key',match:'old group fact',content:'new group fact'})).ok,true)
  assert.equal((await f.run('a',{action:'archive',target:'key',match:'new group fact'})).ok,true)
  assert.equal((await f.run('a',{action:'list',target:'key'})).entries.length,0)
  assert.equal((await f.run('a',{action:'list',target:'key',archived:true})).entries.length,1)
  assert.equal((await f.run('b',{action:'list',target:'key',archived:true})).entries.length,0)
  assert.equal(f.queue.read().length,0)
})
