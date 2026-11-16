import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {dataPaths,relativeDataPath,absoluteDataPath,validateManifest,readDataManifest} from './data-paths.mjs';

function fixture(t){const root=fs.mkdtempSync(path.join(path.dirname(fileURLToPath(import.meta.url)),'paths-test-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));return root}

test('layout and relative attachments remain portable without creating data directories',t=>{
  const base=fixture(t),root=path.join(base,'data'),config={dataRoot:root};
  const paths=dataPaths(config);
  assert.equal(paths.database,path.join(root,'vault','whale.sqlite'));
  assert.equal(fs.existsSync(root),false);
  const relative=relativeDataPath(path.join(paths.media,'聊天','image.png'),config);
  assert.equal(relative,'media/聊天/image.png');
  assert.equal(absoluteDataPath(relative,{dataRoot:path.join(base,'moved')}),path.join(base,'moved','media','聊天','image.png'));
});
test('legacy manifest is accepted but invalid versions and escaping layouts are rejected',()=>{
  assert.equal(validateManifest({format:'whale-data',schemaVersion:1,paths:{database:'vault/whale.sqlite'}}).paths.persona,'persona');
  for(const value of [{format:'other',schemaVersion:1},{format:'whale-data',schemaVersion:true},{format:'whale-data',schemaVersion:99},{format:'whale-data',schemaVersion:1,paths:{media:'../private'}}])assert.throws(()=>validateManifest(value));
});
test('relative path and root traversal are rejected',t=>{
  const root=fixture(t),config={dataRoot:root};
  for(const value of ['../secret','/private','C:/private','voice\\private','.'])assert.throws(()=>absoluteDataPath(value,config));
  assert.throws(()=>relativeDataPath(path.join(root,'..','outside'),config));
  assert.throws(()=>dataPaths({dataRoot:'\\\\server\\share'}));
});
test('manifest reads do not silently initialize missing data',t=>{
  const root=fixture(t),config={dataRoot:root};
  assert.equal(readDataManifest(config),null);
  assert.throws(()=>readDataManifest(config,{required:true}),/missing/);
  fs.writeFileSync(path.join(root,'manifest.json'),JSON.stringify({format:'whale-data',schemaVersion:1}));
  assert.equal(readDataManifest(config).paths.database,'vault/whale.sqlite');
});
