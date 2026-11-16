import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export const DEFAULT_DATA_ROOT = (process.env.WHALE_DATA_ROOT || path.join(os.homedir(), 'WhaleData')).replaceAll('\\', '/');
export const SCHEMA_VERSION = 2;
export const LAYOUT = Object.freeze(Object.fromEntries([
  ...['config','vault','media','memory','persona','voice','outbox','snapshots','backups'].map(name=>[name,name]),
  ['database','vault/whale.sqlite'],
]));

function localPath(value) {
  if(typeof value!=='string'||!value.trim()||value.includes('\0')||/^(\\\\|\/\/)/.test(value)
    ||value.split(/[\\/]/).includes('..'))throw Error('invalid-data-root');
  const result=path.resolve(value);
  let part=result;
  while(true) {
    try {if(fs.lstatSync(part).isSymbolicLink())throw Error('data-path-link-denied')}
    catch(error){if(error.code!=='ENOENT')throw error}
    const parent=path.dirname(part);if(parent===part)break;part=parent;
  }
  return result;
}

export function dataPaths(config={}) {
  if(!config||typeof config!=='object'||Array.isArray(config))throw Error('invalid-data-config');
  const root=localPath(config.dataRoot??DEFAULT_DATA_ROOT);
  return {root,manifest:path.join(root,'manifest.json'),
    ...Object.fromEntries(Object.entries(LAYOUT).map(([name,relative])=>[name,path.join(root,...relative.split('/'))]))};
}

export function relativeDataPath(file,config={}) {
  const root=dataPaths(config).root;
  const relative=path.relative(root,localPath(file));
  if(!relative||relative==='..'||relative.startsWith('..'+path.sep)||path.isAbsolute(relative))throw Error('data-path-outside-root');
  return relative.split(path.sep).join('/');
}

export function absoluteDataPath(relative,config={}) {
  if(typeof relative!=='string'||!relative||relative==='.'||relative.includes('\\')||relative.includes(':')
      ||relative.startsWith('/')||relative.split('/').includes('..'))throw Error('invalid-relative-data-path');
  const result=localPath(path.join(dataPaths(config).root,...relative.split('/')));
  relativeDataPath(result,config);return result;
}

export function validateManifest(value) {
  if(!value||value.format!=='whale-data'||!Number.isInteger(value.schemaVersion)||value.schemaVersion<1||value.schemaVersion>SCHEMA_VERSION)throw Error('unsupported-data-manifest');
  const supplied=value.paths??{};
  if(!supplied||typeof supplied!=='object'||Array.isArray(supplied))throw Error('invalid-data-manifest-paths');
  for(const [key,relative]of Object.entries(supplied))if(!(key in LAYOUT)||relative!==LAYOUT[key])throw Error('invalid-data-manifest-paths');
  return {...value,paths:{...LAYOUT,...supplied}};
}

export function readDataManifest(config={}, {required=false}={}) {
  const file=localPath(dataPaths(config).manifest);
  if(!fs.existsSync(file)){if(required)throw Error('data-manifest-missing');return null}
  return validateManifest(JSON.parse(fs.readFileSync(file,'utf8').replace(/^\uFEFF/,'')));
}
