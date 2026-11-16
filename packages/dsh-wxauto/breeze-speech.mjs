// BreezeBlue cloud TTS, shaped like the local speech workers:
// request('synthesize',{text,emotion?}) → {file,durationSeconds,sampleRate,backend,segments}.
// The API key lives only in <dataRoot>/config/breezeblue.local.json, never in code or prompts.
import fs from 'node:fs';
import path from 'node:path';
import {randomUUID} from 'node:crypto';

const ENDPOINT = 'https://api.breeze.blue/v1/text-to-speech/';
const MAX_CHARS = 900; // API default limit is 1000 per request

export function readBreezeConfig(dataRoot) {
  try {
    const value = JSON.parse(fs.readFileSync(path.join(dataRoot, 'config', 'breezeblue.local.json'), 'utf8'));
    if (typeof value.apiKey === 'string' && /^brz_[A-Za-z0-9_-]{8,}$/.test(value.apiKey)) return value;
  } catch {}
  return null;
}

// Split on sentence ends so each request stays under the API limit.
export function chunkText(text, max = MAX_CHARS) {
  const out = [];
  let current = '';
  for (const piece of String(text).match(/[^。！？!?；;\n]+[。！？!?；;\n]*/g) ?? [String(text)]) {
    if ((current + piece).length > max && current) { out.push(current); current = ''; }
    if (piece.length > max) { for (let i = 0; i < piece.length; i += max) out.push(piece.slice(i, i + max)); continue; }
    current += piece;
  }
  if (current.trim()) out.push(current);
  return out.map(s => s.trim()).filter(Boolean);
}

export function wavSeconds(buffer) {
  // RIFF/WAVE: find "fmt " (byte rate) and "data" (size).
  if (buffer.length < 44 || buffer.toString('ascii', 0, 4) !== 'RIFF' || buffer.toString('ascii', 8, 12) !== 'WAVE') throw Error('breeze-audio-not-wav');
  let offset = 12, byteRate = 0, sampleRate = 0, dataSize = 0;
  while (offset + 8 <= buffer.length) {
    const id = buffer.toString('ascii', offset, offset + 4), size = buffer.readUInt32LE(offset + 4);
    if (id === 'fmt ') { sampleRate = buffer.readUInt32LE(offset + 12); byteRate = buffer.readUInt32LE(offset + 16); }
    if (id === 'data') { dataSize = Math.min(size, buffer.length - offset - 8); break; }
    offset += 8 + size + (size % 2);
  }
  if (!byteRate || !dataSize) throw Error('breeze-audio-not-wav');
  return {seconds: dataSize / byteRate, sampleRate};
}

export function emotionInstructions(emotion) {
  const value = String(emotion ?? '').replace(/[\r\n]/g, ' ').trim().slice(0, 160);
  return value ? `用自然口语说，情绪：${value}` : null;
}

export function createBreezeSpeech({dataRoot, voiceId: voiceIdOf, fetchImpl = fetch, timeoutMs = 60000}) {
  const outbox = path.join(dataRoot, 'voice', 'outbox');
  const currentVoice = () => {
    const id = typeof voiceIdOf === 'function' ? voiceIdOf() : voiceIdOf;
    return typeof id === 'string' && /^voc_[A-Za-z0-9]+$/.test(id) ? id : null;
  };
  async function one(text, emotion, apiKey, voiceId) {
    const body = {text, language_code: 'zh'};
    const instructions = emotionInstructions(emotion);
    if (instructions) body.instructions = instructions;
    const signal = AbortSignal.timeout(timeoutMs);
    const response = await fetchImpl(`${ENDPOINT}${encodeURIComponent(voiceId)}?output_format=wav_24000`, {
      method: 'POST', signal,
      headers: {'xi-api-key': apiKey, 'Content-Type': 'application/json; charset=utf-8'},
      body: JSON.stringify(body),
    });
    if (!response.ok) throw Error(`breeze-http-${response.status}`);
    const audio = Buffer.from(await response.arrayBuffer());
    const {seconds, sampleRate} = wavSeconds(audio);
    fs.mkdirSync(outbox, {recursive: true});
    const file = path.join(outbox, randomUUID().replaceAll('-', '') + '.wav');
    fs.writeFileSync(file, audio);
    return {file, durationSeconds: Math.round(seconds * 100) / 100, sampleRate};
  }
  return {
    available: () => !!(currentVoice() && readBreezeConfig(dataRoot)),
    async request(op, args = {}) {
      if (op === 'status') return {ready: !!(currentVoice() && readBreezeConfig(dataRoot)), backend: 'breezeblue', voiceId: currentVoice()};
      if (op === 'release') return {released: true};
      if (op !== 'synthesize') throw Error('breeze-operation-unsupported');
      const config = readBreezeConfig(dataRoot), voiceId = currentVoice();
      if (!config) throw Error('breeze-key-missing');
      if (!voiceId) throw Error('breeze-voice-missing');
      const text = String(args.text ?? '').trim();
      if (!text || text.length > 3000) throw Error('invalid-speech-length');
      const segments = [];
      for (const piece of chunkText(text)) segments.push(await one(piece, args.emotion, config.apiKey, voiceId));
      const durationSeconds = Math.round(segments.reduce((s, x) => s + x.durationSeconds, 0) * 100) / 100;
      return {file: segments[0].file, durationSeconds, sampleRate: segments[0].sampleRate, backend: 'breezeblue',
        segments: segments.map(({file, durationSeconds}) => ({file, durationSeconds}))};
    },
    close() {},
  };
}
