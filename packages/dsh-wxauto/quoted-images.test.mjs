import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {resolveQuotedImages,QUOTED_IMAGE_MARK} from './quoted-images.mjs';

const quote=id=>`看这个\n[引用 小明 的消息：[图片]${QUOTED_IMAGE_MARK}${id}）]`;

test('seen picture becomes its earlier evidence; marker never reaches the model', async () => {
  const [out]=await resolveQuotedImages([{text:quote('42'),images:[]}],{dataRoot:'D:/Data',
    lookup:async()=>({found:true,received:true,path:'media/images/a/b.jpg',summary:'一只猫'})});
  assert.match(out.text,/之前看过，当时的识图结果：一只猫/);
  assert.ok(out.text.includes(path.join('D:/Data','media/images/a/b.jpg')));
  assert.ok(!out.text.includes('wx-img:'));
  assert.equal(out.images.length,0);
});

test('downloaded but unread picture is attached for reading', async () => {
  const [out]=await resolveQuotedImages([{text:quote('7')}],{dataRoot:'D:/Data',
    lookup:async()=>({found:true,received:true,path:'media/images/a/c.png',summary:null})});
  assert.equal(out.images.length,1);assert.equal(out.images[0].source,'quoted-image');
  assert.match(out.text,/已作为本条附件附上/);
});

test('unknown, failed or unsafe lookups say the picture was never received', async () => {
  for(const lookup of [async()=>({found:false}),async()=>{throw Error('x')},
      async()=>({received:true,path:'../../etc/x',summary:'bad'})]) {
    const [out]=await resolveQuotedImages([{text:quote('9')}],{dataRoot:'D:/Data',lookup});
    assert.match(out.text,/之前没有收到，内容未知/);assert.ok(!out.text.includes('wx-img:'));
  }
});

test('never-received sticker is fetched from its recorded CDN copy and attached', async () => {
  const text=`这个呢\n[引用 我 的消息：[表情]${QUOTED_IMAGE_MARK}5|${'a'.repeat(32)}|http://vweixinf.tc.qq.com/x?m=1）]`;
  const calls=[];
  const [out]=await resolveQuotedImages([{text}],{dataRoot:'D:/Data',stickerDirectory:'D:/Data/media/images/quoted-stickers',
    lookup:async()=>({found:true,received:false}),fetchSticker:async(md5,url)=>{calls.push([md5,url]);return 'D:/Data/media/images/quoted-stickers/x.gif';}});
  assert.deepEqual(calls,[['a'.repeat(32),'http://vweixinf.tc.qq.com/x?m=1']]);
  assert.equal(out.images[0].source,'quoted-sticker');assert.match(out.text,/补下载/);assert.ok(!out.text.includes('wx-img:'));
});

test('sticker download refuses non-WeChat hosts and md5 mismatches', async () => {
  const {fetchQuotedSticker}=await import('./quoted-images.mjs');
  const fs=await import('node:fs'),os=await import('node:os');
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'qs-'));
  const gif=Buffer.from('GIF89a-test');
  const ok=async()=>({ok:true,arrayBuffer:async()=>gif});
  assert.equal(await fetchQuotedSticker('0'.repeat(32),'https://evil.example/x',{directory:dir,fetchImpl:ok}),null);
  assert.equal(await fetchQuotedSticker('0'.repeat(32),'http://vweixinf.tc.qq.com/x',{directory:dir,fetchImpl:ok}),null);
  const {createHash}=await import('node:crypto');const md5=createHash('md5').update(gif).digest('hex');
  assert.equal(path.extname(await fetchQuotedSticker(md5,'http://vweixinf.tc.qq.com/x',{directory:dir,fetchImpl:ok})),'.gif');
  fs.rmSync(dir,{recursive:true,force:true});
});

test('received native sticker downloads its original and leaves no marker', async () => {
  const {STICKER_MARK}=await import('./quoted-images.mjs');
  const text=`[收到一个微信表情]${STICKER_MARK}${'b'.repeat(32)}|http://vweixinf.tc.qq.com/y）`;
  const [ok]=await resolveQuotedImages([{text}],{dataRoot:'D:/Data',lookup:async()=>{throw Error('unused')},fetchSticker:async()=>'D:/Data/s.gif'});
  assert.equal(ok.text,'[收到一个微信表情]');assert.equal(ok.images[0].source,'wechat-sticker');
  const [bad]=await resolveQuotedImages([{text}],{dataRoot:'D:/Data',lookup:async()=>null,fetchSticker:async()=>null});
  assert.match(bad.text,/下载失败，内容未知/);assert.equal(bad.images.length,0);
});

test('messages without markers are returned untouched', async () => {
  const message={text:'普通消息'};
  const [out]=await resolveQuotedImages([message],{dataRoot:'D:/Data',lookup:async()=>{throw Error('unused')}});
  assert.equal(out,message);
});
