import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {spawnSync} from 'node:child_process'

test('background media cache does not redirect live schedule, diary or configuration',t=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'whale-paths-'))
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}))
  const original=path.join(root,'original'),shots=path.join(root,'cache','shots')
  fs.mkdirSync(original,{recursive:true})
  fs.writeFileSync(path.join(original,'whale-night.json'),JSON.stringify({atText:'21:40'}))
  const script=`import fs from 'node:fs';import assert from 'node:assert/strict';import path from 'node:path';
    const {dshPath,configPath}=await import(${JSON.stringify(new URL('../tools/lib/paths.mjs',import.meta.url).href)});
    assert.equal(dshPath('shots','video'),path.join(process.env.BILI_WHALE_SHOTS,'video'));
    assert.equal(dshPath('whale-diary.md'),path.join(process.env.DSH_HOME,'whale-diary.md'));
    assert.equal(configPath,path.join(process.env.DSH_HOME,'bili-whale.config.json'));
    const state=dshPath('whale-night.json');
    assert.equal(JSON.parse(fs.readFileSync(state)).atText,'21:40');
    fs.writeFileSync(state,JSON.stringify({atText:'22:00'}));
    assert.equal(JSON.parse(fs.readFileSync(dshPath('whale-night.json'))).atText,'22:00');
    delete process.env.BILI_WHALE_SHOTS;
    assert.equal(dshPath('shots','video'),path.join(process.env.DSH_HOME,'shots','video'));
  `
  const r=spawnSync(process.execPath,['--input-type=module','-e',script],{encoding:'utf8',env:{...process.env,DSH_HOME:original,BILI_WHALE_SHOTS:shots,BILI_WHALE_CONFIG:path.join(original,'bili-whale.config.json')}})
  assert.equal(r.status,0,r.stderr+r.stdout)
})
