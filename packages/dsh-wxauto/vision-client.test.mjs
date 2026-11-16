import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createVisionReader, readBatchImages } from './vision-client.mjs';
import { chatPrompt } from './logic.mjs';

test('Codex evidence stays with its sender; one failed image cannot invent contents or drop other messages', async () => {
  const batch = [{sender:'甲', text:'看这个', images:[{path:'first.png'}]},
    {sender:'乙', text:'这张呢', images:[{path:'failed.png'}]}, {sender:'丙',text:'文字消息'}];
  const enriched = await readBatchImages(batch, async image => {
    if (image.path === 'failed.png') throw Error('timeout');
    return {summary:'红色矩形',ocr:{full_text:'7429'}};
  });
  const prompt = chatPrompt({kind:'group',target:'测试群'}, enriched);
  const lines = prompt.split('\n').filter(line => line.startsWith('{')).map(line => JSON.parse(line));
  assert.equal(lines[0].发言人, '甲');
  assert.match(lines[0].消息, /红色矩形/);
  assert.equal(lines[1].发言人, '乙');
  assert.match(lines[1].消息, /内容未知/);
  assert.doesNotMatch(lines[1].消息, /红色矩形/);
  assert.equal(lines[2].消息, '文字消息');
  assert.equal(batch[0].images[0].evidence, undefined);
});

test('reader confines paths, rejects another provider and caches only valid Codex results', async t => {
  const root = await mkdtemp(path.join(tmpdir(),'whale-vision-'));
  t.after(() => rm(root,{recursive:true,force:true}));
  const image = path.join(root,'test.png'); await writeFile(image,'synthetic');
  let calls=0;
  const read = createVisionReader({cli:'modlens.js',imageRoot:root,codexBin:process.execPath,
    execute(bin,args,options,callback) {
      calls++;
      assert.equal(bin,process.execPath);
      assert.equal(options.windowsHide,true);
      assert.equal(options.env.ELECTRON_RUN_AS_NODE,'1');
      assert.equal(args[2],image);
      callback(null,JSON.stringify(calls===1?{provider:'gemini-api',result:{}}:{provider:'gemini-api',result:{summary:'测试'}}));
    }});
  await assert.rejects(read({path:image}), /vision-result-invalid/);
  assert.deepEqual(await read({path:image}),{summary:'测试'});
  await read({path:image}); assert.equal(calls,2);
  await assert.rejects(read({path:process.execPath}), /image-path-denied/);
});

test('cancellation stops a batch instead of generating a failure reply', async () => {
  const abort = new AbortController();
  await assert.rejects(readBatchImages([{images:[{path:'test.png'}]}], async () => {
    abort.abort(new Error('cancelled')); throw Error('aborted');
  },{signal:abort.signal}), /cancelled/);
});

test('success and failure retain image source quality and failure never reuses old evidence',async()=>{
  const source={path:'thumbnail.png',source:'visible-thumbnail',width:320,height:180,
    captureNote:'Only a visible thumbnail.',evidence:{summary:'must not reuse stale evidence'}};
  const good=await readBatchImages([{images:[source]}],async()=>({summary:'actual observation'}));
  const bad=await readBatchImages([{images:[source]}],async()=>{throw Error('recognition failed');});
  for(const result of [good,bad]){
    assert.equal(result[0].images[0].path,'thumbnail.png');assert.equal(result[0].images[0].source,'visible-thumbnail');
    assert.equal(result[0].images[0].width,320);assert.equal(result[0].images[0].captureNote,source.captureNote);
  }
  assert.equal(good[0].images[0].evidence.summary,'actual observation');assert.equal(bad[0].images[0].evidence,undefined);
  assert.equal(bad[0].images[0].status,'failed');assert.match(bad[0].images[0].message,/内容未知/);
});
