import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import {spawnSync} from 'node:child_process'
test('background scheduling preserves original policy and delivery behavior',t=>{
  const home=fs.mkdtempSync(path.join(os.tmpdir(),'whale-scheduler-'))
  t.after(()=>fs.rmSync(home,{recursive:true,force:true}))
  const dsh=path.join(home,'.dsh');fs.mkdirSync(dsh);fs.mkdirSync(path.join(home,'.bili-ai'))
  fs.writeFileSync(path.join(home,'.bili-ai/credentials.json'),JSON.stringify({sessdata:'test-cookie',bili_jct:'test'}))
  const run=spawnSync(process.execPath,[fileURLToPath(new URL('./helpers/background-scheduler.mjs',import.meta.url))],{encoding:'utf8',timeout:30000,env:{...process.env,USERPROFILE:home,HOME:home,DSH_HOME:dsh,DSH_NODE:process.execPath,BILI_WHALE_CONFIG:path.join(dsh,'bili-whale.config.json'),BILI_TRAFFIC_TEST:'1'}})
  assert.equal(run.status,0,run.stdout+run.stderr)
  assert.match(run.stdout,/PASS:/)
})
