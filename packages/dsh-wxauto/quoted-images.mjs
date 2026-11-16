// Replace quote-reply image markers (from quote_render.py) with what the whale
// actually knows about that picture: its earlier vision evidence, the file to
// read now, or an explicit "never received". The marker never reaches the model.
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';

export const QUOTED_IMAGE_MARK='（被引用图片编号 wx-img:';
// svrid, then optionally |md5|cdnurl for a sticker's plain CDN copy.
const MARKER=/（被引用图片编号 wx-img:([1-9][0-9]{0,19})(?:\|([0-9a-f]{32})\|([^\s|）<>"]{1,2048}))?）/g;
const STICKER_HOST=/^https?:\/\/[A-Za-z0-9.-]+\.(?:qq|wechat)\.com\//; // wechat.com = overseas CDN
const MAX_STICKER_BYTES=20*1024*1024;

function imageExtension(bytes) {
  if(bytes.subarray(0,6).toString('ascii').startsWith('GIF8'))return '.gif';
  if(bytes[0]===0x89&&bytes.subarray(1,4).toString('ascii')==='PNG')return '.png';
  if(bytes[0]===0xff&&bytes[1]===0xd8)return '.jpg';
  if(bytes.subarray(0,4).toString('ascii')==='RIFF'&&bytes.subarray(8,12).toString('ascii')==='WEBP')return '.webp';
  return null;
}

/** Download a quoted sticker that was never received; the md5 WeChat recorded must match. */
export async function fetchQuotedSticker(md5,url,{directory,fetchImpl=fetch,timeoutMs=15000}={}) {
  if(!/^[0-9a-f]{32}$/.test(md5)||!STICKER_HOST.test(url)||!directory)return null;
  for(const ext of ['.gif','.png','.jpg','.webp']){
    const cached=path.join(directory,md5+ext);if(fs.existsSync(cached))return cached;
  }
  const response=await fetchImpl(url,{redirect:'error',signal:AbortSignal.timeout(timeoutMs)});
  if(!response.ok)return null;
  const bytes=Buffer.from(await response.arrayBuffer());
  if(!bytes.length||bytes.length>MAX_STICKER_BYTES)return null;
  if(createHash('md5').update(bytes).digest('hex')!==md5)return null;
  const ext=imageExtension(bytes);if(!ext)return null;
  fs.mkdirSync(directory,{recursive:true});
  const file=path.join(directory,md5+ext);
  fs.writeFileSync(file,bytes);
  return file;
}

export const STICKER_MARK='（微信表情原图 wx-emoji:';
const STICKER_MARKER=/（微信表情原图 wx-emoji:([0-9a-f]{32})\|([^\s|）<>"]{1,2048})）/g;

export async function resolveQuotedImages(batch,{lookup,dataRoot,stickerDirectory,fetchSticker=fetchQuotedSticker}) {
  const output=[];
  for(const message of batch) {
    const text=typeof message?.text==='string'?message.text:'';
    const matches=[...text.matchAll(MARKER)],stickers=[...text.matchAll(STICKER_MARKER)];
    if(!matches.length&&!stickers.length){output.push(message);continue;}
    let next=text;const images=[...(message.images??[])];const done=new Map();
    // A sticker received through the native catalog: fetch its recorded original.
    for(const [marker,md5,url] of stickers) {
      if(!done.has(marker)) {
        let file=null;
        try{file=await fetchSticker(md5,url,{directory:stickerDirectory});}catch{file=null;}
        if(file)images.push({path:file,source:'wechat-sticker',captureNote:'这是对方发来的微信表情，按微信记录的原表情下载；动图只看到静态画面。'});
        done.set(marker,file?'':'（这个表情的原图下载失败，内容未知，不要猜）');
      }
      next=next.split(marker).join(done.get(marker));
    }
    for(const [marker,id,md5,url] of matches) {
      if(!done.has(marker)) {
        let info=null;
        try{info=await lookup(id);}catch{info=null;}
        let note='（被引用的这张图你之前没有收到，内容未知；不要猜，可以请对方重发）';
        if(info?.received===true&&typeof info.path==='string'&&!path.isAbsolute(info.path)&&!info.path.split(/[\\/]/).includes('..')) {
          const file=path.join(dataRoot,info.path);
          if(typeof info.summary==='string'&&info.summary) {
            note=`（被引用的这张图你之前看过，当时的识图结果：${info.summary}。需要新细节可用modlens_read_image读取原图：${file}）`;
          } else {
            images.push({path:file,source:'quoted-image'});
            note='（被引用的这张图已作为本条附件附上）';
          }
        } else if(md5&&url) {
          let file=null;
          try{file=await fetchSticker(md5,url,{directory:stickerDirectory});}catch{file=null;}
          if(file) {
            images.push({path:file,source:'quoted-sticker',captureNote:'这是被引用的那个微信表情，当时没收到，现在按微信记录的原表情补下载；动图只看到静态画面。'});
            note='（被引用的这个表情当时没收到，现在已补下载并作为本条附件附上）';
          }
        }
        done.set(marker,note);
      }
      next=next.split(marker).join(done.get(marker));
    }
    output.push({...message,text:next,images});
  }
  return output;
}
