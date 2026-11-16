import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createCipheriv, createHash} from 'node:crypto';
import {decryptDat, keyCandidates, findDat, createNativeImageReader, resolveNativeImages, IMAGE_MARK} from './native-images.mjs';

const JPEG = Buffer.concat([Buffer.from([0xff, 0xd8, 0xff, 0xe0]), Buffer.alloc(3000, 7), Buffer.from([0xff, 0xd9])]);

function encryptV2(clear, aesKey, xorKey, aesSize = 1024, xorSize = 200) {
  const c = createCipheriv('aes-128-ecb', aesKey, null);
  const head = Buffer.concat([c.update(clear.subarray(0, aesSize)), c.final()]);
  const middle = clear.subarray(aesSize, clear.length - xorSize);
  const tail = Buffer.from(clear.subarray(clear.length - xorSize).map(b => b ^ xorKey));
  const header = Buffer.alloc(15); Buffer.from([7, 8, 0x56, 0x32, 8, 7]).copy(header);
  header.writeUInt32LE(aesSize, 6); header.writeUInt32LE(xorSize, 10);
  return Buffer.concat([header, head, middle, tail]);
}

function world(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'nimg-'));
  t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  const appData = path.join(dir, 'Roaming'), kv = path.join(appData, 'Tencent', 'xwechat', 'net', 'kvcomm');
  fs.mkdirSync(kv, {recursive: true}); fs.writeFileSync(path.join(kv, 'key_123456789_1_input.statistic'), '');
  const account = path.join(dir, 'xwechat_files', 'wxid_abc_ab12');
  fs.mkdirSync(path.join(account, 'db_storage'), {recursive: true});
  const aesKey = Buffer.from(createHash('md5').update('123456789wxid_abc').digest('hex').slice(0, 16));
  return {dir, appData, account, aesKey, xorKey: 123456789 & 255};
}

test('V2 decrypt round-trips and rejects a wrong key', () => {
  const key = Buffer.from('0123456789abcdef'), data = encryptV2(JPEG, key, 0x9d);
  assert.deepEqual(decryptDat(data, key, 0x9d), JPEG);
  assert.throws(() => decryptDat(data, Buffer.from('fedcba9876543210'), 0x9d));
});

test('kvcomm code + wxid derive the key; reader decrypts the original from the chat folder', async t => {
  const w = world(t);
  assert.ok(keyCandidates({wxid: 'wxid_abc_ab12', appData: w.appData}).some(k => k.aesKey.equals(w.aesKey) && k.xorKey === w.xorKey));
  const chatDir = 'a'.repeat(32), md5 = 'b'.repeat(32), img = path.join(w.account, 'msg', 'attach', chatDir, '2026-11', 'Img');
  fs.mkdirSync(img, {recursive: true});
  fs.writeFileSync(path.join(img, md5 + '_t.dat'), encryptV2(JPEG, w.aesKey, w.xorKey));
  fs.writeFileSync(path.join(img, md5 + '.dat'), encryptV2(JPEG, w.aesKey, w.xorKey));
  assert.deepEqual(findDat(path.join(w.account, 'msg', 'attach'), chatDir, md5).map(x => x.level), ['original', 'thumbnail']);
  const read = createNativeImageReader({dbRoot: path.join(w.account, 'db_storage'), imageDirectory: path.join(w.dir, 'out'), appData: w.appData});
  const found = await read(chatDir, md5);
  assert.equal(found.level, 'original');
  assert.deepEqual(fs.readFileSync(found.path), JPEG);
  assert.equal((await read(chatDir, md5)).level, 'cached');
});

test('marker becomes an attached image; a missing file says so without guessing', async t => {
  const batch = [{text: `[收到一张微信图片]${IMAGE_MARK}${'a'.repeat(32)}|${'b'.repeat(32)}）`}, {text: '普通消息'}];
  const ok = await resolveNativeImages(batch, async () => ({path: 'X.jpg', level: 'original'}));
  assert.equal(ok[0].text, '[收到一张微信图片]');
  assert.deepEqual(ok[0].images, [{path: 'X.jpg', source: 'wechat-native-image'}]);
  assert.equal(ok[1], batch[1]);
  const missing = await resolveNativeImages(batch, async () => { throw Error('image-file-missing'); });
  assert.match(missing[0].text, /内容未知/);
  assert.equal(missing[0].images.length, 0);
});
