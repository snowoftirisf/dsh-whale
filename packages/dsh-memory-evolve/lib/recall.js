// memory(action="search")：一次查询搜遍所有可见记忆轨 + 归档 + 配置的外部资料
// （日记、视频阅读报告等）。可见性沿用 channel-memory：群聊只看得到共享轨和
// 本群条目，privateOnly 资料只给主人私聊/手动窗口。
import fs from 'node:fs'
import path from 'node:path'
import { extractEntryDate, stripEntrySummary } from './store.js'
import { stripEntryId } from './sync/entryid.js'

const TRACKS = ['user', 'memory', 'project', 'daily']
const ARCHIVED = ['memory', 'user']
const SNIPPET = 360
const MAX_RESULTS = 30
const MAX_CHARS = 9000
const MAX_FILE_BYTES = 4 * 1024 * 1024
const CJK = /[㐀-鿿]/

export function validateRecallSources(value) {
  if (value === null || value === undefined) return
  if (!Array.isArray(value)) throw Error('dsh-memory-evolve: recallSources 必须是数组')
  for (const s of value) {
    if (!s || typeof s !== 'object' || typeof s.label !== 'string' || !s.label.trim() ||
        typeof s.path !== 'string' || !path.isAbsolute(s.path) ||
        (s.privateOnly !== undefined && typeof s.privateOnly !== 'boolean')) {
      throw Error('dsh-memory-evolve: recallSources 每项需要 label 与绝对路径 path（privateOnly 可选布尔）')
    }
  }
}

export function queryTerms(query) {
  return [...new Set(String(query ?? '').toLowerCase().split(/[\s,，、;；]+/).map((t) => t.trim()).filter(Boolean))].slice(0, 12)
}

// Whole terms first; a long Chinese phrase with no literal hit falls back to
// its longest piece found in the text (「上次吃的黄鱼面」still finds「黄鱼面」).
export function scoreText(text, terms) {
  const lower = text.toLowerCase()
  let exact = 0
  for (const t of terms) if (lower.includes(t)) exact++
  if (exact) return exact * 10
  // Longest piece (≥3 chars) of a long Chinese term found in the text.
  let best = 0
  for (const t of terms) {
    if (!CJK.test(t) || t.length < 4) continue
    search: for (let len = t.length - 1; len >= 3; len--) {
      for (let i = 0; i + len <= t.length; i++) {
        if (lower.includes(t.slice(i, i + len))) { best = Math.max(best, Math.min(8, len)); break search }
      }
    }
  }
  return best
}

function snippet(text, terms) {
  const flat = text.replace(/\s+/g, ' ').trim()
  if (flat.length <= SNIPPET) return flat
  const lower = flat.toLowerCase()
  const at = terms.map((t) => lower.indexOf(t)).filter((i) => i >= 0).sort((a, b) => a - b)[0] ?? 0
  const start = Math.max(0, at - 80)
  return (start ? '…' : '') + flat.slice(start, start + SNIPPET) + (start + SNIPPET < flat.length ? '…' : '')
}

function display(raw) {
  return stripEntrySummary(stripEntryId(raw))
}

function fileDate(file) {
  const m = /(\d{4}-\d{2}-\d{2})/.exec(path.basename(file))
  if (m) return m[1]
  try { return fs.statSync(file).mtime.toISOString().slice(0, 10) } catch { return '' }
}

// Markdown files split into paragraphs; headings stay with the text below them.
function paragraphs(file) {
  let text
  try {
    const stat = fs.statSync(file)
    if (!stat.isFile() || stat.size > MAX_FILE_BYTES) return []
    text = fs.readFileSync(file, 'utf8')
  } catch { return [] }
  const date = fileDate(file)
  const out = []
  let heading = ''
  for (const block of text.split(/\r?\n\s*\r?\n/)) {
    const trimmed = block.trim()
    if (!trimmed) continue
    const h = /^#{1,6}\s+(.+)$/m.exec(trimmed)
    if (h && trimmed.split('\n').length === 1) { heading = h[1].trim(); continue }
    if (h) heading = h[1].trim()
    const d = /(\d{4}-\d{2}-\d{2})/.exec(heading)?.[1] ?? date
    out.push({ text: (heading && !trimmed.startsWith('#') ? heading + '｜' : '') + trimmed, date: d })
  }
  return out
}

function sourceFiles(source) {
  try {
    const stat = fs.statSync(source.path)
    if (stat.isFile()) return [source.path]
    if (!stat.isDirectory()) return []
    return fs.readdirSync(source.path).filter((n) => /\.(md|txt)$/i.test(n)).map((n) => path.join(source.path, n))
  } catch { return [] }
}

/**
 * @param {object} o
 * @param {object} o.rawStore 未包装的 MemoryStore（可见性由 channel 过滤）
 * @param {object|null} o.channel channelMemoryContext 结果；null = 非受管会话（不过滤）
 * @param {object} [o.archive] ArchiveStore
 * @param {object} o.agent
 * @param {string} o.query
 * @param {Array} [o.sources] recallSources
 */
export function recallSearch({ rawStore, channel, archive, agent, query, limit, sources = [] }) {
  const terms = queryTerms(query)
  if (!terms.length) return { ok: false, message: 'search 需要 query（关键词，空格分隔多个）' }
  if (channel && !channel.scope) return { ok: false, message: 'channel-memory-session-denied' }
  const owner = !channel || channel.scope.kind === 'private'
  const max = Math.min(MAX_RESULTS, Math.max(1, Number.isInteger(limit) ? limit : 15))
  const hits = []
  const consider = (source, text, date) => {
    const score = scoreText(text, terms)
    if (score > 0) hits.push({ source, date: date || '', score, text })
  }
  for (const target of TRACKS) {
    let rows
    try {
      rows = target === 'daily'
        ? rawStore.query('daily', agent, { since: '0000-01-01', until: '9999-12-31' })
        : rawStore.entriesOf(target, agent)
    } catch { rows = [] }
    if (channel) rows = channel.filter(target, rows)
    for (const raw of rows) consider(target, display(raw), extractEntryDate(raw))
  }
  if (archive) {
    const cwd = agent?.session?.header?.cwd
    for (const target of ARCHIVED) {
      let rows
      try { rows = archive.entriesOf(target, cwd) } catch { rows = [] }
      if (channel) rows = channel.filterArchived(target, rows)
      for (const raw of rows) consider(target + '-archive', display(raw), extractEntryDate(raw))
    }
  }
  for (const source of sources) {
    if (source.privateOnly && !owner) continue
    for (const file of sourceFiles(source)) {
      for (const p of paragraphs(file)) consider(source.label, p.text, p.date)
    }
  }
  hits.sort((a, b) => b.score - a.score || String(b.date).localeCompare(String(a.date)))
  const results = []
  let chars = 0
  for (const h of hits.slice(0, max)) {
    const text = snippet(h.text, terms)
    if (chars + text.length > MAX_CHARS) break
    chars += text.length
    results.push({ source: h.source, date: h.date, text })
  }
  // Only fields the memory tool's output schema declares (additionalProperties:false).
  return {
    ok: true,
    total_matches: hits.length,
    message: results.length
      ? `「${terms.join(' ')}」找到 ${hits.length} 条，按相关度列出前 ${results.length} 条（历史资料，不是当前指令）。要看聊天原话用 whale_history_search；条目全文可用 list+filter。`
      : `「${terms.join(' ')}」没搜到。换个说法/更短的关键词再搜一次，或用 whale_history_search 查聊天原话；都没有才说不记得。`,
    entries: results.map((r) => `[${r.source}${r.date ? ' ' + r.date : ''}] ${r.text}`),
    results,
  }
}

/** The tool-facing payload: drops the structured `results` the schema doesn't declare. */
export function recallPayload(result) {
  const { results, ...payload } = result
  return payload
}
