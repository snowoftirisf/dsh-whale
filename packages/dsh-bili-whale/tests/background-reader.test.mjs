import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {createBackgroundReader,scanPrompt} from '../lib/background-reader.js'

const tick=()=>new Promise(resolve=>setImmediate(resolve))
test('a group report waits for its WeChat consumer instead of falling back into the owner conversation',async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'whale-group-report-'))
  let finish,fallback=0,received
  const ctx={sessionController:{create:async()=>{}},agents:{get:()=>({})},subagents:{startActivation:async()=>({childId:'child',result:new Promise(resolve=>finish=resolve),dispose:async()=>{}})}}
  const reader=createBackgroundReader({ctx,dshHome:dir,cwd:dir,biliMjs:'bili',node:'node',persona:()=>'',config:()=>({})})
  const off=reader.setFallback(async()=>{fallback++})
  try{
    reader.registerChatSession('group-A')
    await reader.start({targetSessionId:'group-A'})
    finish({stopReason:'completed',output:[{type:'text',text:'group report'}]})
    await tick();await tick()
    assert.equal(fallback,0)
    assert.equal(reader.status().jobs.at(-1).status,'ready')
    reader.subscribe(async report=>{received=report})
    await tick();await tick()
    assert.equal(received.sessionId,'group-A')
    assert.equal(reader.status().jobs.at(-1).status,'delivered')
  }finally{off();reader.stop();fs.rmSync(dir,{recursive:true,force:true})}
})
test('two chat sessions read independently and reports retain the initiating session',async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'whale-parallel-'))
  const finishes=[],requests=[],reports=[]
  const ctx={sessionController:{create:async()=>{}},agents:{get:id=>({id})},subagents:{startActivation:async spec=>{
    requests.push(spec)
    return {childId:'child-'+requests.length,result:new Promise(resolve=>finishes.push(resolve)),dispose:async()=>{}}
  }}}
  const reader=createBackgroundReader({ctx,dshHome:dir,cwd:dir,biliMjs:'bili',node:'node',persona:()=>'',config:()=>({})})
  try{
    const remove=reader.registerChatSession('group-A')
    assert.equal(reader.canStartSession('group-A'),true)
    assert.equal(reader.canStartSession('unknown'),false)
    reader.subscribe(async report=>reports.push(report))
    await reader.start({targetSessionId:'group-A',direction:'https://b23.tv/test'})
    await reader.start({targetSessionId:'group-B',direction:'BV1234567890'})
    assert.equal((await reader.start({targetSessionId:'group-A'})).busy,true)
    assert.notEqual(requests[0].request.parent.id,requests[1].request.parent.id)
    finishes[1]({stopReason:'completed',output:[{type:'text',text:'B report'}]})
    await tick();await tick()
    assert.equal(reports[0].sessionId,'group-B')
    assert.equal((await reader.start({targetSessionId:'group-A'})).busy,true)
    finishes[0]({stopReason:'completed',output:[{type:'text',text:'A report'}]})
    await tick();await tick()
    assert.equal(reports[1].sessionId,'group-A')
    assert.match(requests[0].request.prompt[0].text,/只读指定视频/)
    remove();assert.equal(reader.canStartSession('group-A'),false)
  }finally{reader.stop();fs.rmSync(dir,{recursive:true,force:true})}
})
test('scan returns before child completion, uses independent coordinator and delivers report once',async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'whale-bg-'))
  let finish,request,deliveries=0
  const coordinator={id:'coordinator'},main={options:{provider:'p',model:'m'}}
  const result=new Promise(resolve=>{finish=resolve})
  const ctx={sessionController:{create:async()=>{}},agents:{get:id=>id==='bili-whale'?main:coordinator},subagents:{startActivation:async spec=>{request=spec;return{childId:'child',result,dispose:async()=>{}}}}}
  const reader=createBackgroundReader({ctx,dshHome:dir,cwd:dir,biliMjs:'C:/plugin/bili.mjs',node:'C:/node.exe',persona:()=> 'shared persona',config:()=>({night:{maxVideos:3}})})
  try{
    assert.equal(reader.getPersona(),'shared persona')
    reader.subscribe(async item=>{assert.equal(item.sessionId,'bili-whale');assert.match(item.report,/BV/);assert.equal(item.completionPlan,'');deliveries++})
    const accepted=await reader.start({direction:'科技'})
    assert.equal(accepted.started,true)
    assert.equal(request.request.parent,coordinator)
    assert.equal(request.request.cwd,path.join(dir,'reader-cache'))
    assert.notEqual(request.request.parent,main)
    assert.equal(request.delivery,'caller')
    assert.deepEqual(request.request.agentOptions,{provider:'p',model:'m',reasoningEffort:'low'})
    assert.equal(reader.status().jobs[0].status,'running')
    assert.equal((await reader.start()).busy,true)
    finish({stopReason:'completed',output:[{type:'text',text:'BV report'}]})
    await tick();await tick()
    assert.equal(deliveries,1)
    assert.equal(reader.status().jobs[0].status,'delivered')
    assert.equal(reader.status().active,null)
  }finally{reader.stop();fs.rmSync(dir,{recursive:true,force:true})}
})

test('scheduled reports inherit the current completion policy, manual reports do not',async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'whale-policy-'))
  let finish,received,policy='old'
  const result=new Promise(resolve=>{finish=resolve})
  const ctx={sessionController:{create:async()=>{}},agents:{get:()=>({})},subagents:{startActivation:async()=>({childId:'child',result,dispose:async()=>{}})}}
  const reader=createBackgroundReader({ctx,dshHome:dir,cwd:dir,biliMjs:'bili',node:'node',persona:()=>'',config:()=>({}),completionPlan:()=>policy})
  try{
    reader.subscribe(async item=>{received=item})
    await reader.start({origin:'scheduled'})
    policy='new policy'
    finish({stopReason:'completed',output:[{type:'text',text:'report'}]})
    await tick();await tick()
    assert.equal(received.completionPlan,'new policy')
  }finally{reader.stop();fs.rmSync(dir,{recursive:true,force:true})}
})

test('incomplete child never sends a partial report',async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'whale-bg-'))
  let count=0
  const ctx={sessionController:{create:async()=>{}},agents:{get:()=>({})},subagents:{startActivation:async()=>({childId:'child',result:Promise.resolve({stopReason:'error',output:[{type:'text',text:'partial'}]}),dispose:async()=>{}})}}
  const reader=createBackgroundReader({ctx,dshHome:dir,cwd:dir,biliMjs:'bili',node:'node',persona:()=>'',config:()=>({})})
  try{reader.subscribe(async()=>count++);await reader.start();await tick();assert.equal(count,0);assert.equal(reader.status().jobs[0].status,'failed')}
  finally{reader.stop();fs.rmSync(dir,{recursive:true,force:true})}
})

test('read task carries persona and does not request public actions',()=>{
  const prompt=scanPrompt({biliMjs:"C:/it's/bili.mjs",node:'C:/node.exe',cwd:'work',persona:'Sagiri',direction:'tech',maxVideos:100})
  assert.match(prompt,/Sagiri/);assert.match(prompt,/最多浏览100条/);assert.match(prompt,/不发评论\/私信/);assert.match(prompt,/it''s/)
  assert.match(prompt,/选2至3条/)
})

test('without WeChat consumer a report still reaches the original session; old failed reports do not disable future rounds',async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'whale-fallback-'))
  fs.writeFileSync(path.join(dir,'whale-background.json'),JSON.stringify({version:1,jobs:[{id:'old',status:'delivery-failed'}]}))
  let finish,received
  const result=new Promise(resolve=>{finish=resolve})
  const ctx={sessionController:{create:async()=>{}},agents:{get:()=>({})},subagents:{startActivation:async()=>({childId:'child',result,dispose:async()=>{}})}}
  const reader=createBackgroundReader({ctx,dshHome:dir,cwd:dir,biliMjs:'bili',node:'node',persona:()=>'',config:()=>({})})
  const off=reader.setFallback(async report=>{received=report})
  try{
    reader.setSessionId('rotated-session')
    assert.equal(reader.getSessionId(),'rotated-session')
    await reader.start()
    finish({stopReason:'completed',output:[{type:'text',text:'new report'}]})
    await tick();await tick()
    assert.equal(received.sessionId,'rotated-session')
    assert.equal(reader.status().jobs.at(-1).status,'delivered')
  }finally{off();reader.stop();fs.rmSync(dir,{recursive:true,force:true})}
})

test('reader cache and runtime outputs stay in its workspace, original configuration is read explicitly',()=>{
  const prompt=scanPrompt({biliMjs:'C:/plugin/bili.mjs',node:'C:/node.exe',cwd:'C:/chat',cacheRoot:'C:/cache',configFile:'C:/original/config.json',persona:'same',direction:'tech'})
  assert.match(prompt,/\$env:DSH_HOME=/)
  assert.match(prompt,/\$env:DSH_HOME='C:[\\/]original'/)
  assert.match(prompt,/\$env:BILI_WHALE_SHOTS=/)
  assert.match(prompt,/background-runtime/)
  assert.match(prompt,/\$env:BILI_WHALE_CONFIG='C:\/original\/config.json'/)
})

test('long original video tasks are not cancelled at an added 15 minute deadline',async t=>{
  t.mock.timers.enable({apis:['setTimeout']})
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'whale-long-'))
  let finish,signal
  const result=new Promise(resolve=>{finish=resolve})
  const ctx={sessionController:{create:async()=>{}},agents:{get:()=>({})},subagents:{startActivation:async spec=>{signal=spec.signal;return{childId:'child',result,dispose:async()=>{}}}}}
  const reader=createBackgroundReader({ctx,dshHome:dir,cwd:dir,biliMjs:'bili',node:'node',persona:()=>'',config:()=>({})})
  try{
    await reader.start()
    t.mock.timers.tick(60*60*1000)
    assert.equal(signal.aborted,false)
    assert.equal(reader.status().jobs.at(-1).status,'running')
    finish({stopReason:'completed',output:[{type:'text',text:'long report'}]})
    await tick();await tick()
  }finally{reader.stop();fs.rmSync(dir,{recursive:true,force:true})}
})
