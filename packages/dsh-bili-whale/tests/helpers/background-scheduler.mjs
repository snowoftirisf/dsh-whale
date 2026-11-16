import fs from 'node:fs'
import path from 'node:path'
import assert from 'node:assert/strict'
const RealDate=Date
let now=new RealDate('2026-11-01T21:00:00+08:00').getTime()
globalThis.Date=class extends RealDate{constructor(...args){super(...(args.length?args:[now]))}static now(){return now}}
globalThis.setInterval=globalThis.setTimeout=()=>({})
globalThis.clearInterval=globalThis.clearTimeout=()=>{}
const {startScheduler}=await import('../../lib/scheduler.js')
const {makePlan}=await import('../../tools/lib/night-plan.mjs')
const root=process.env.DSH_HOME,file=path.join(root,'whale-night.json')
const cfg={night:{windows:[{start:'20:00',end:'24:00',wakes:4}],skipChance:0,minGapMin:1,maxWakesPerDay:4,maxVideos:17},listen:{comment:false,dm:false,live:false},share:{via:'comment'},persona:{name:'ConfiguredName'},sessionRotateDays:1,personaOncePerSession:true}
fs.writeFileSync(process.env.BILI_WHALE_CONFIG,JSON.stringify(cfg))
fs.writeFileSync(path.join(process.env.USERPROFILE,'.bili-ai/persona.md'),'CANONICAL_PERSONA')
fs.writeFileSync(path.join(root,'whale-session.json'),JSON.stringify({id:'old-session',startedAt:now-3*86400000,deliveredAt:0}))
function due(action='wake'){
  const state=makePlan(cfg.night,{},now,()=>0.5)
  state.atMs=now-1;state.action=action;state.fired=false
  fs.writeFileSync(file,JSON.stringify(state))
}
let active=null,available=true,deferred=false
const delegated=[],prompts=[],created=[]
const background={available:()=>available,status:()=>({active}),setSessionId(){},setFallback:()=>()=>{},start:async options=>{
  if(deferred)return{started:false,busy:true}
  delegated.push(options);return{started:true,id:'job'}
}}
const ctx={sessionController:{create:async request=>{created.push(request)},prompt:async request=>{prompts.push(request)}}}
let sched=startScheduler(ctx,{dshHome:root,cwd:process.env.USERPROFILE,biliMjs:'unused',background})
due();await sched.tick()
assert.equal(delegated.length,1);assert.equal(prompts.length,0)
assert.notEqual(delegated[0].targetSessionId,'old-session')
assert.equal(delegated[0].origin,'scheduled')
assert.match(delegated[0].mission,/CANONICAL_PERSONA/)
assert.match(delegated[0].task,/≤17 条/)
assert.match(delegated[0].task,/share.via = comment/)
assert.match(delegated[0].task,/quota react/)
assert.equal(JSON.parse(fs.readFileSync(file)).fired,true)
due();await sched.tick();assert.equal(delegated.length,1,'original hard cooldown retained')
now+=8*60000+1;active='job';await sched.tick();assert.equal(delegated.length,1)
active=null;deferred=true;await sched.tick();assert.equal(JSON.parse(fs.readFileSync(file)).fired,false,'busy race must not consume a scheduled round')
deferred=false;await sched.tick();assert.equal(delegated.length,2)
sched.stop()
available=false;due();sched=startScheduler(ctx,{dshHome:root,cwd:process.env.USERPROFILE,biliMjs:'unused',background})
await sched.tick();assert.equal(prompts.length,1,'unavailable background falls back to original prompt')
assert.match(prompts[0].content[0].text,/CANONICAL_PERSONA/)
sched.stop()
available=true;due('skip');sched=startScheduler(ctx,{dshHome:root,cwd:process.env.USERPROFILE,biliMjs:'unused',background})
await sched.tick();assert.equal(prompts.length,2);assert.match(prompts[1].content[0].text,/今晚不看/)
sched.stop()
assert.deepEqual(JSON.parse(fs.readFileSync(process.env.BILI_WHALE_CONFIG)),cfg,'configuration preserved')
console.log('PASS: original scheduling, rotation, persona, cooldown, skip, fallback and deferred quota')
