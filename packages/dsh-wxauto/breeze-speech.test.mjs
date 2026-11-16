import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createBreezeSpeech, chunkText, wavSeconds, emotionInstructions} from './breeze-speech.mjs';
import {selectedSpeech, speechCapabilities} from './voice-backend.mjs';

function wav(seconds, rate = 24000) {
  const data = Buffer.alloc(Math.round(seconds * rate) * 2), h = Buffer.alloc(44);
  h.write('RIFF', 0); h.writeUInt32LE(36 + data.length, 4); h.write('WAVE', 8); h.write('fmt ', 12);
  h.writeUInt32LE(16, 16); h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22); h.writeUInt32LE(rate, 24);
  h.writeUInt32LE(rate * 2, 28); h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34); h.write('data', 36); h.writeUInt32LE(data.length, 40);
  return Buffer.concat([h, data]);
}

function fixture(t) {
  const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'breeze-'));
  t.after(() => fs.rmSync(dataRoot, {recursive: true, force: true}));
  fs.mkdirSync(path.join(dataRoot, 'config'));
  fs.writeFileSync(path.join(dataRoot, 'config', 'breezeblue.local.json'), JSON.stringify({apiKey: 'brz_testkey_123456'}));
  return dataRoot;
}

test('synthesize posts to the voice with key header and returns local wav segments', async t => {
  const dataRoot = fixture(t), calls = [];
  const fetchImpl = async (url, init) => { calls.push({url, init}); return new Response(wav(1.5)); };
  const speech = createBreezeSpeech({dataRoot, voiceId: () => 'voc_abc123', fetchImpl});
  assert.equal(speech.available(), true);
  const out = await speech.request('synthesize', {text: '你好呀。', emotion: '开心、俏皮'});
  assert.match(calls[0].url, /\/v1\/text-to-speech\/voc_abc123\?output_format=wav_24000$/);
  assert.equal(calls[0].init.headers['xi-api-key'], 'brz_testkey_123456');
  const body = JSON.parse(calls[0].init.body);
  assert.equal(body.text, '你好呀。'); assert.equal(body.language_code, 'zh'); assert.match(body.instructions, /开心/);
  assert.equal(out.backend, 'breezeblue'); assert.equal(out.durationSeconds, 1.5); assert.equal(out.segments.length, 1);
  assert.ok(fs.existsSync(out.file));
});

test('long text is split into several requests; HTTP errors and missing key/voice fail closed', async t => {
  const dataRoot = fixture(t);
  let n = 0;
  const speech = createBreezeSpeech({dataRoot, voiceId: 'voc_x', fetchImpl: async () => { n++; return new Response(wav(0.5)); }});
  const out = await speech.request('synthesize', {text: ('一句话。'.repeat(300))});
  assert.ok(n >= 2 && out.segments.length === n);
  const bad = createBreezeSpeech({dataRoot, voiceId: 'voc_x', fetchImpl: async () => new Response('no', {status: 401})});
  await assert.rejects(bad.request('synthesize', {text: '你好'}), /breeze-http-401/);
  assert.equal(createBreezeSpeech({dataRoot, voiceId: 'bad id'}).available(), false);
  fs.rmSync(path.join(dataRoot, 'config', 'breezeblue.local.json'));
  assert.equal(createBreezeSpeech({dataRoot, voiceId: 'voc_x'}).available(), false);
});

test('helpers: chunking keeps sentences, wav duration, emotion text', () => {
  assert.deepEqual(chunkText('甲。乙！丙？', 3), ['甲。', '乙！', '丙？']);
  assert.equal(wavSeconds(wav(2)).seconds, 2);
  assert.throws(() => wavSeconds(Buffer.from('not wav at all, definitely not a wav file header')), /not-wav/);
  assert.equal(emotionInstructions(''), null);
});

test('breezeblue backend is selected and supports emotion without a local GPU model', () => {
  const cloud = {available: () => true}, legacy = {available: () => true};
  const settings = {backend: 'breezeblue', approved: true};
  assert.equal(selectedSpeech(settings, legacy, null, cloud), cloud);
  assert.deepEqual(speechCapabilities(settings, {legacy, emotional: null, emotionalReady: false, nativeVoice: true, cloud}), {voice: true, emotion: true});
  assert.deepEqual(speechCapabilities(settings, {legacy, emotional: null, nativeVoice: true, cloud: null}), {voice: false, emotion: false});
});
