import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import path from 'node:path'
import {postReadPlan} from '../lib/post-read-plan.js'

const fixture=fs.readFileSync(new URL('./fixtures/wake-prompt-0.4.6.js.txt',import.meta.url),'utf8').replace('export function','function')
function original(via,push){return vm.runInNewContext(fixture+';wakePrompt',{
  fs:{existsSync:()=>false},path,HOME:'C:/test',silenceLines:()=>['ORIGINAL_SILENCE_GUIDANCE'],
  diaryPushLines:()=>push?['8) ORIGINAL_NOTIFY_BACKEND']:['8) ORIGINAL_DIARY_PUSH_OFF'],
})({atText:'21:40'},'',17,via)}

for(const via of ['comment','dm'])for(const push of [true,false]){
  test(`original 0.4.6 ${via}/diaryPush=${push}: every non-reading instruction survives`,()=>{
    const task=original(via,push),prefix='原人设\n3) 这是人设里的第三条，不是浏览步骤\n'
    const result=postReadPlan({mission:prefix+task,task,node:'C:/node.exe',biliMjs:'C:/bili.mjs'})
    assert.ok(result.includes(prefix))
    for(const line of task.split('\n').filter(Boolean)){
      if(/^\s*(1|3|4)\)/.test(line))assert.ok(!result.includes(line),line)
      else assert.ok(result.includes(line),line)
    }
    assert.match(result,/ORIGINAL_SILENCE_GUIDANCE/)
    assert.match(result,/comment-digest/);assert.match(result,/quota react/)
    assert.match(result,/--better/);assert.match(result,/4\.5\)/)
    assert.match(result,push?/ORIGINAL_NOTIFY_BACKEND/:/ORIGINAL_DIARY_PUSH_OFF/)
    assert.match(result,/memory\(action="add", target="project"\)/) // watched videos land in memory
  })
}

test('missing canonical mission fails instead of silently substituting a reduced workflow',()=>{
  assert.throws(()=>postReadPlan({mission:'wrong',task:'actual',node:'node',biliMjs:'bili'}),/原始定时任务/)
})
