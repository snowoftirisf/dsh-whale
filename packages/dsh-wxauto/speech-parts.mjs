import { outgoingParts } from './logic.mjs';

// The model chooses modality, the transport only checks capability and syntax.
// A disabled capability strips the marker and preserves the original words.
export function speechParts(text, kind, available = false) {
  text = String(text || '');
  if (text.includes('```')) return outgoingParts(text, kind);
  const opening = /\[\[wx-voice(?::([^\]\r\n]*))?\]\]/g, close = '[[/wx-voice]]';
  const parts = [];
  let start = 0;
  let match;
  while ((match = opening.exec(text)) !== null) {
    const at = match.index, end = text.indexOf(close, at + match[0].length);
    if (end < 0) throw Error('unclosed-voice-marker');
    const before=text.slice(start,at);
    if(before.includes('[[wx-voice'))throw Error('invalid-voice-marker');
    parts.push(...outgoingParts(before, kind));
    const emotion=match[1]?.trim();
    if(match[1]!==undefined&&(!emotion||emotion.length>160||emotion.includes('[[')))throw Error('invalid-voice-emotion');
    const content = text.slice(at + match[0].length, end).trim();
    if (!content || content.includes('[[') || /\[表情\s*[:：]/.test(content) || content.length > 1500)
      throw Error('invalid-voice-content');
    parts.push({kind:available ? 'voice' : 'text',text:content,...(available&&emotion?{emotion}:{})});
    start = end + close.length;
    opening.lastIndex=start;
  }
  const tail = text.slice(start);
  if (tail.includes(close)) throw Error('unexpected-voice-close');
  if (tail.includes('[[wx-voice')) throw Error('invalid-voice-marker');
  parts.push(...outgoingParts(tail, kind));
  if (parts.filter(p=>p.kind==='meme').length>3) throw Error('too-many-memes');
  return parts;
}
