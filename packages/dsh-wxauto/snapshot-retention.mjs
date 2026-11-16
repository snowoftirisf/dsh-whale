// Snapshots are full decrypted copies of the chat databases (~2.6 MB each); the
// vault import is incremental, so only the newest few are ever read again.
// Keep the newest `keep`, plus any snapshot whose id is mentioned anywhere in the
// config tree (inbox claims, group authorities/baselines, pinned monitors) or in
// latest/pending-candidate — those can be re-opened by id. Everything else goes.
import fs from 'node:fs';
import path from 'node:path';

const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/g;

function referencedIds(roots) {
  const ids = new Set();
  const visit = target => {
    let stat;
    try { stat = fs.lstatSync(target); } catch { return; }
    if (stat.isSymbolicLink()) return;
    if (stat.isDirectory()) { for (const name of fs.readdirSync(target)) visit(path.join(target, name)); return; }
    if (!/\.json$/i.test(target) || stat.size > 64 * 1024 * 1024) return;
    try { for (const m of fs.readFileSync(target, 'utf8').matchAll(UUID)) ids.add(m[0]); } catch {}
  };
  for (const root of roots) visit(root);
  return ids;
}

export function pruneSnapshots({dataRoot, keep = 50, maxDeletes = Infinity, dryRun = false}) {
  const live = path.join(dataRoot, 'snapshots', 'windows-live');
  const folder = path.join(live, 'snapshots');
  let entries;
  try { entries = fs.readdirSync(folder, {withFileTypes: true}); } catch { return {kept: 0, deleted: 0, protected: 0}; }
  const dirs = entries.filter(e => e.isDirectory() && /^[0-9a-f-]{36}$/.test(e.name))
    .map(e => ({name: e.name, mtime: fs.statSync(path.join(folder, e.name)).mtimeMs}))
    .sort((a, b) => b.mtime - a.mtime);
  const refs = referencedIds([path.join(dataRoot, 'config'), path.join(live, 'latest.json'), path.join(live, 'pending-candidate.json')]);
  const newest = new Set(dirs.slice(0, keep).map(d => d.name));
  let deleted = 0, protectedCount = 0;
  for (const d of dirs) {
    if (newest.has(d.name)) continue;
    if (refs.has(d.name)) { protectedCount++; continue; }
    if (deleted >= maxDeletes) break;
    const target = path.join(folder, d.name);
    // Only plain snapshot folders directly under the snapshot root.
    if (path.dirname(target) !== folder || fs.lstatSync(target).isSymbolicLink()) continue;
    if (!dryRun) fs.rmSync(target, {recursive: true, force: true});
    deleted++;
  }
  return {total: dirs.length, kept: newest.size, protected: protectedCount, deleted};
}
