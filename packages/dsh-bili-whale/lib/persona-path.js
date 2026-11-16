import path from 'node:path';
import {HOME} from '../tools/lib/paths.mjs';

export function personaFile(config={}) {
  if(config.dataRoot) {
    if(typeof config.dataRoot!=='string'||!path.isAbsolute(config.dataRoot))throw Error('invalid-persona-data-root');
    return path.join(config.dataRoot,'persona','persona.md');
  }
  return path.join(HOME,'.bili-ai','persona.md');
}
