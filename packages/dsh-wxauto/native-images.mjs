// Received photos straight from WeChat's local attachment store (no UI clicks).
// quote_render.py marks a photo row as 「（微信图片原图 wx-image:<chatDir>|<md5>）」;
// here the .dat is found, decrypted (V2: AES-128-ECB head + plain middle + XOR
// tail) and WeChat's own wxgf/HEVC originals are converted to JPEG for vision.
// Key derivation follows yichen-wechat-local-vault's group_image_media.py:
// aes = md5(<kvcomm code><wxid>)[:16], xor = code & 255, verified on the file.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createDecipheriv, createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';

export const IMAGE_MARK = '（微信图片原图 wx-image:';
const MARKER = /（微信图片原图 wx-image:([0-9a-f]{32})\|([0-9a-f]{32})）/g;
const MAGIC_V2 = Buffer.from([0x07, 0x08, 0x56, 0x32, 0x08, 0x07]);
const MAGIC_V1 = Buffer.from([0x07, 0x08, 0x56, 0x31, 0x08, 0x07]);
const V1_KEY = Buffer.from('cfcd208495d565ef');
const MAX_BYTES = 40 * 1024 * 1024;

export function imageKind(b) {
  if (b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return 'jpg';
  if (b.subarray(0, 4).toString('latin1') === '\x89PNG') return 'png';
  if (b.subarray(0, 4).toString('ascii') === 'GIF8') return 'gif';
  if (b.subarray(0, 4).toString('ascii') === 'RIFF' && b.subarray(8, 12).toString('ascii') === 'WEBP') return 'webp';
  if (b.subarray(0, 4).toString('ascii') === 'wxgf') return 'wxgf';
  return null;
}

export function decryptDat(data, aesKey, xorKey) {
  const v2 = data.subarray(0, 6).equals(MAGIC_V2), v1 = data.subarray(0, 6).equals(MAGIC_V1);
  if (!v1 && !v2) {
    // Legacy whole-file XOR: key from the JPEG/PNG head.
    const x = data[0] ^ 0xff;
    const out = Buffer.from(data.map(b => b ^ x));
    if (imageKind(out)) return out;
    throw Error('image-dat-format-unknown');
  }
  const aesSize = data.readUInt32LE(6), xorSize = data.readUInt32LE(10);
  const body = data.subarray(15), aligned = aesSize + (16 - aesSize % 16);
  if (aligned > body.length || xorSize > body.length - aligned) throw Error('image-dat-layout-invalid');
  const decipher = createDecipheriv('aes-128-ecb', v1 ? V1_KEY : aesKey, null);
  const head = Buffer.concat([decipher.update(body.subarray(0, aligned)), decipher.final()]);
  const middle = body.subarray(aligned, body.length - xorSize);
  const tail = Buffer.from(body.subarray(body.length - xorSize).map(b => b ^ xorKey));
  const out = Buffer.concat([head, middle, tail]);
  if (!imageKind(out)) throw Error('image-key-wrong');
  return out;
}

export function keyCandidates({wxid, appData = process.env.APPDATA ?? path.join(os.homedir(), 'AppData', 'Roaming')}) {
  const codes = new Set();
  for (const dir of ['net', 'net_1', 'ilink']) {
    const folder = path.join(appData, 'Tencent', 'xwechat', dir, 'kvcomm');
    let names = [];
    try { names = fs.readdirSync(folder); } catch {}
    for (const name of names) { const m = /^key_(\d+)(?:_|$)/.exec(name); if (m) codes.add(Number(m[1])); }
  }
  const ids = [...new Set([wxid.split('_').slice(0, 2).join('_'), wxid])];
  const out = [];
  for (const code of codes) for (const id of ids)
    out.push({aesKey: Buffer.from(createHash('md5').update(`${code}${id}`).digest('hex').slice(0, 16)), xorKey: code & 255});
  return out;
}

export function findDat(attachRoot, chatDir, md5) {
  const folder = path.join(attachRoot, chatDir);
  let months = [];
  try { months = fs.readdirSync(folder).sort().reverse(); } catch { return []; }
  const found = [];
  for (const month of months) {
    const img = path.join(folder, month, 'Img');
    for (const suffix of ['.dat', '_h.dat', '_t.dat']) {
      const file = path.join(img, md5 + suffix);
      if (fs.existsSync(file)) found.push({file, level: suffix === '_t.dat' ? 'thumbnail' : suffix === '_h.dat' ? 'hd' : 'original'});
    }
    if (found.length) break;
  }
  return found;
}

export function createNativeImageReader({dbRoot, imageDirectory, ffmpeg, appData}) {
  const account = path.dirname(dbRoot);
  const attachRoot = path.join(account, 'msg', 'attach');
  const wxid = path.basename(account);
  let key = null;
  const toJpeg = (wxgf, out) => {
    const i = wxgf.indexOf(Buffer.from([0, 0, 0, 1]));
    if (i < 0 || !ffmpeg) throw Error('image-wxgf-unsupported');
    const hevc = out.replace(/\.jpg$/, '.hevc');
    fs.writeFileSync(hevc, wxgf.subarray(i));
    try { execFileSync(ffmpeg, ['-hide_banner', '-loglevel', 'error', '-y', '-f', 'hevc', '-i', hevc, '-frames:v', '1', '-q:v', '2', out], {timeout: 30000, windowsHide: true}); }
    finally { fs.rmSync(hevc, {force: true}); }
    if (!fs.existsSync(out) || !fs.statSync(out).size) throw Error('image-wxgf-convert-failed');
    return out;
  };
  const decrypt = data => {
    if (key) { try { return decryptDat(data, key.aesKey, key.xorKey); } catch { key = null; } }
    for (const candidate of keyCandidates({wxid, appData})) {
      try { const out = decryptDat(data, candidate.aesKey, candidate.xorKey); key = candidate; return out; } catch {}
    }
    throw Error('image-key-unavailable');
  };
  return async function read(chatDir, md5) {
    const directory = path.join(imageDirectory, 'native', chatDir);
    const done = ['.jpg', '.png', '.gif', '.webp'].map(ext => path.join(directory, md5 + ext)).find(f => fs.existsSync(f));
    if (done) return {path: done, level: 'cached'};
    let lastError = 'image-file-missing';
    for (const {file, level} of findDat(attachRoot, chatDir, md5)) {
      try {
        const data = fs.readFileSync(file);
        if (!data.length || data.length > MAX_BYTES) throw Error('image-size-invalid');
        const clear = decrypt(data), kind = imageKind(clear);
        fs.mkdirSync(directory, {recursive: true});
        const out = path.join(directory, md5 + '.' + (kind === 'wxgf' ? 'jpg' : kind));
        if (kind === 'wxgf') toJpeg(clear, out); else fs.writeFileSync(out, clear);
        return {path: out, level};
      } catch (error) { lastError = error.message; }
    }
    throw Error(lastError);
  };
}

export async function resolveNativeImages(batch, read) {
  const output = [];
  for (const message of batch) {
    const text = typeof message?.text === 'string' ? message.text : '';
    const matches = [...text.matchAll(MARKER)];
    if (!matches.length) { output.push(message); continue; }
    let next = text;
    const images = [...(message.images ?? [])];
    for (const [marker, chatDir, md5] of matches) {
      let note = '（这张图本地还没下载好，内容未知；不要猜，可以请对方再发一次）';
      try {
        const found = await read(chatDir, md5);
        images.push({path: found.path, source: 'wechat-native-image',
          ...(found.level === 'thumbnail' ? {captureNote: '原图还没下载，这是微信的缩略图，细节可能看不清；只按看得清的部分回答。'} : {})});
        note = '';
      } catch {}
      next = next.split(marker).join(note);
    }
    output.push({...message, text: next, images});
  }
  return output;
}
