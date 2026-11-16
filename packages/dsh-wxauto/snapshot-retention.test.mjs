import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {pruneSnapshots} from './snapshot-retention.mjs';

const id = n => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;

test('keeps the newest N and every snapshot referenced from config; deletes the rest', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'snapret-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const folder = path.join(root, 'snapshots', 'windows-live', 'snapshots');
  for (let i = 1; i <= 10; i++) {
    const dir = path.join(folder, id(i));
    fs.mkdirSync(path.join(dir, 'message'), {recursive: true});
    fs.writeFileSync(path.join(dir, 'message', 'message_0.db'), 'x');
    const when = new Date(Date.UTC(2026, 10, i)); fs.utimesSync(dir, when, when);
  }
  fs.mkdirSync(path.join(root, 'config', 'native-group-text', 'g'), {recursive: true});
  fs.writeFileSync(path.join(root, 'config', 'native-group-text', 'g', 'inbox.json'), JSON.stringify({claims: [{snapshotId: id(2)}]}));
  fs.writeFileSync(path.join(root, 'snapshots', 'windows-live', 'latest.json'), JSON.stringify({snapshot_id: id(1)}));
  const dry = pruneSnapshots({dataRoot: root, keep: 3, dryRun: true});
  assert.deepEqual(dry, {total: 10, kept: 3, protected: 2, deleted: 5});
  assert.equal(fs.readdirSync(folder).length, 10);
  pruneSnapshots({dataRoot: root, keep: 3});
  assert.deepEqual(fs.readdirSync(folder).sort(), [id(1), id(2), id(8), id(9), id(10)]);
});

test('maxDeletes bounds one pass', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'snapret-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const folder = path.join(root, 'snapshots', 'windows-live', 'snapshots');
  for (let i = 1; i <= 6; i++) fs.mkdirSync(path.join(folder, id(i)), {recursive: true});
  assert.equal(pruneSnapshots({dataRoot: root, keep: 1, maxDeletes: 2}).deleted, 2);
  assert.equal(fs.readdirSync(folder).length, 4);
});
