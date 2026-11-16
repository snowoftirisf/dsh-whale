# Memory Evolve channel views: integration contract

`memory-view.mjs` is the helper for the opt-in Memory Evolve local integration.
The local fork now connects it via `dsh-memory-evolve/lib/channel-memory.js` and
`lib/index.js`; the feature is off until `channelMemoryPolicy` is configured.
No actual configuration, Markdown memory, session, tool guard, or Harness
compression behavior was changed while developing the integration.

```json
{
  "channelMemoryPolicy": {
    "workspace": "C:/path/to/whale-work",
    "ownerSessionId": "bili-whale",
    "ownerChatId": "stable-vault-owner-chat-id",
    "groups": [{"sessionId":"group-session-id","chatId":"stable-vault-chat-id"}],
    "metadataFile": "<DATA_ROOT>/memory/provenance.json",
    "projectKey": "whale-work",
    "maxIndexChars": 8000
  }
}
```

This is a local paired-plugin integration: distribute both plugin directories
with their existing sibling layout. The production import uses that relative
layout, never an absolute user path. `projectKey` and `maxIndexChars` are optional.
Static config validates the policy; reviewMode is unchanged. Nonmatching
workspaces retain upstream behavior; unrecognized sessions in the managed
workspace are fail-closed. The owner mapping is resolved afresh on every operation
from the trusted runtime `agent.ctx.get('biliBackground').getSessionId()` when that
service exists. This is the same scheduler-owned ID the wxauto adapter uses;
the previous owner ID loses access after rotation. An invalid/throwing resolver
fails owner access closed, and cannot convert a configured group into private.
Without that service the original configured owner mapping remains compatible.
Static owner/group ID collisions are invalid configuration. Other subagents need
an explicit trusted mapping, never a display name or claimed parent in prompts.

The registered `memory` tool supports `action=expand` with target=memory/user/key
and the complete `memory:…` reference in `id`. Mapped sessions' query/archived
outputs are capped at 50 entries / 12000 characters. The original short key IDs
continue working with the same channel filtering.

This provides **context/API filtering**, not hard privacy isolation. A session
that retains general file or shell tools may read the underlying memory files.
Do not claim otherwise or silently restore the removed `chatToolGuard`.

## Trusted identity and portable storage

Create one immutable `Map<sessionId, {kind:'private'|'chat', chatId}>` from the
adapter's confirmed routing configuration, not prompt text or tool arguments.
The owner private session uses `kind:'private'`. Each group uses its stable vault
chat ID, not a mutable display name. Refresh only the owner session from the
trusted companion service on each call; keep its stable ownerChatId unchanged.
An unknown session resolves to `null`: for non-WeChat DSH
sessions preserve the existing behavior by bypassing this opt-in helper entirely.
For an expected WeChat session whose mapping is missing, withhold memory output.

Store metadata at `<dataRoot>/memory/provenance.json`, and include it in the same
backup as the Markdown memory tracks. The file contains provenance, visibility,
and archived old content. It must remain private and must not be attached to chats.
Use a portable logical project ID (e.g. `whale-project`) for `projectKey`; never use
an absolute workspace path. New entries can use existing Memory Evolve `[id:…]`
tags. Existing entries need no rewrite: complete-body SHA256 is the fallback.

`entryIdentity({target, content, projectKey?})` returns `{ref,digest}`. Content must
be the **actual raw persisted entry**, including the original store's date/ID
prefixes, not the pre-stamp content from a model request. Stable IDs are bound to
the body digest so an external edit cannot reuse an old group's visibility.

## Read and index hooks

In Memory Evolve `renderSnapshot()` (`lib/index.js`), replace full memory/user
injection for mapped WeChat sessions with `boundedIndex(entries,state,scope)`.
Pass all relevant entry descriptors and the current metadata state. Its default
budget is 8000 JavaScript characters. It returns `text`, resolvable `refs`, and
local-only counts; inject **only `text`**, never counts/timestamps on every turn.
Daily/project bodies are deliberately excluded. These remain on-demand logs.

The new index is not a second compression system. Continue using Harness's
original compaction. Add a retrieval operation that resolves a ref through
`readMemoryRef(ref,entries,state,scope)`, caps returned characters, and labels the
body as historical data. The ref lookup rejects invisible/unknown entries.

Apply `canReadMemory` / `visibleEntries` consistently to:

- normal memory tool `list` and filtered/time-range queries;
- daily range reads, which bypass `entriesOf()` inside `store.query()`;
- archived `list`, `expand`, search, history, export, and tool-result metadata;
- write targets before replace/remove/archive (guessing a substring must not
  allow a group to edit a different scope's entry).

For archived entries use the metadata captured with that archived version, not
the current version's metadata. Do not return an inaccessible track's total,
earliest date, matches, error snippets, or filenames as collateral output.
The existing `outcomeOnly()` already strips mutation results' complete tracks;
keep that behavior.

Defaults: unclassified legacy entries are private; private owner scope may
inspect all of its account's memory; a group sees its own chat-scoped entries and
explicitly shared entries. A group's new memory defaults to its own scope.
Shared promotion requires the owner and an explicit owner request; do not infer
that authority from a group member's display name or model-supplied boolean.

## Sources and writes

The root controller supplies `trustedMessages: Map<messageId,{chatId,deleted?}>`
from the vault's authorized current input, never from model JSON. Run
`validateSourceRefs(refs,scope,trustedMessages)` before passing sources to the
metadata service. It accepts at most 50 unique references, requires the current
chat, and strips arbitrary source fields. Cross-chat historical use needs a
separate root-authorized resolution step; this helper does not auto-grant it.

The production wrapper now reads `agent.ctx.get('whaleHistory')` at each actual
add/replace. `currentSource(sessionId)` must synchronously return only the active
incoming batch bound by the adapter around its `ask()`:

```js
{chatId: 'stable-vault-chat-id', messageIds: ['vault-message-id'],
 sourceKind: 'wxauto-incoming'}
// or null after the ask has completed / for background turns
```

Configure `ownerChatId` with the real vault owner chat ID when enabling this
source hook. A different chat, historical search source, malformed ID or wrong
source kind is rejected before the Markdown write. No service or a null active
binding is allowed: session-level provenance remains, without inventing message
IDs. Model-supplied IDs are ignored. Replace records the new active input's refs
when available; the prior refs remain in the archived version. If no active
source exists for a background replacement, prior refs are retained. Clearing
the adapter binding prevents later background writes borrowing a stale input.

`createMemoryMetadataService({file})` exposes:

```js
service.read(); // copy of {version:1, revision, entries, archives}
await service.record({entry, scope, sources, summary});
await service.promoteShared({entry, scope, explicitOwnerRequest:true});
await service.archiveAndReplace({before, after, scope, sources, summary,
  apply: async () => {
    // Call existing Memory Evolve write logic, then read back the raw entry.
    // Return that exact raw string, not a success boolean or requested text.
    return persistedRawEntry;
  },
});
```

`record()` must follow an existing successful add with a readback of its raw
entry. Existing private/shared metadata cannot be overwritten by a group.
`archiveAndReplace()` durably saves the complete old raw entry and old metadata
**before** invoking `apply`. It verifies readback before publishing the new
metadata, preserves the previous visibility unless explicitly promoted, and
records `supersedes`. A writer or final metadata failure leaves the old version
in the archive; newly changed unclassified content is private to group readers.

The existing MemoryStore matching/drift checks and Markdown directory lock must
still be used. `before` must be read again under that existing write lock before
replacement, not copied from a stale model request. The metadata service's own
exclusive lock only serializes metadata writers; it does not make arbitrary
Markdown edits transactional. Do not lock the original Markdown directory and
then call a nested store method that tries to reacquire the same lock; integrate
at the store transaction boundary or compare the expected digest in the writer.

The metadata lock is a dedicated `.lock` file, waits up to 5 seconds by default,
and is never deleted merely because another process appears slow. Atomic JSON
writes use a unique same-directory temp file, fsync, and rename. Unknown schema
or corrupt JSON fails explicitly instead of silently resetting provenance.
After a real crash, verify the owning process is gone before removing a stale
lock through an explicit recovery command; do not turn that into automatic retry.

Replace and source updates are silent. User-facing replies should not announce
"recorded", "updated personality", or backup details unless asked.

## Test command

```powershell
$env:TEMP = (Resolve-Path 'voice-data/test-tmp').Path
$env:TMP = $env:TEMP
& '%USERPROFILE%/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe' --test plugins/dsh-wxauto/memory-view.test.mjs
```

Tests cover scope isolation in these APIs, unknown/legacy/external edits,
stable project identity, shared promotion, budgets, ref reads, provenance,
archive-before-write, failure behavior, independent writers, corrupt metadata,
and stale locks. They do not establish OS-level isolation or live integration.

## Restored-copy registered-tool smoke (2026-11-02)

Executed `tools/memory-smoke/isolated-registered.mjs` against the complete
`backup-20261101T175634-49f4798c`, in its own workspace artifact directory.
Evidence: `artifacts/memory-isolated-2026-11-01T18-52-47-385Z-5bd33731/acceptance.json`.
The script copies real backed-up memory files, then rebases `memoryDir`, policy
`workspace`, `metadataFile`, state/suggestion paths and the cwd-derived project
directory before constructing stores. Filesystem write entry points used by
the stores/sidecar assert fixture-only paths. The backup source files' hashes
remain unchanged; no production writer, Harness session, model or UI is started.

It registers the actual exported `memoryTool` through a fixture
`ctx.tools.register`, with real `MemoryStore`, `ArchiveStore` and
`SuggestionQueue`. This tests the registered tool factory without invoking the
whole plugin's timers or services. The original owner and two configured group
sessions exercise add, filtered list/query, ref expansion, replace and archive;
other groups cannot retrieve the sentinels. Trusted owner rotation revokes the
old owner ID. Reconstructing stores, policy and registered tool preserves the
new records and scope decisions. The existing KEY ref also expands successfully.

KEY `add` retains its original suggestion-queue behavior: three fixture
suggestions are queued, the five existing KEY entries are unchanged, and no
approval or promotion is fabricated. Direct-add coverage is memory/user/daily/
project, not a claim that KEY automatically becomes effective in auto review.

All newly bound source message IDs are explicitly **synthetic fixture IDs** in
a fake agent runtime service. They validate provenance plumbing and cross-chat
rejection; they are not real inbound messages, live `beginInput` calls, or owner
approvals. Fifteen synthetic source references are checked. The 20 original
metadata records remain identical; final fixture metadata has 30 active records
and six archive records. This finite coverage cannot prove every future privacy
decision, and tool/context filtering is not an OS/shell isolation boundary.

The copied SQLite file passes `query_only=1` and integrity check, with 1,394
messages, six chat rows and three redirects (not six independent active chats).
This is restored-history integrity/count evidence, not live capture freshness or
a full history-tool test. Persona, voice-config and selected-reference hashes
are preserved, including `indextts2.5` and emotion alpha 0.3; no synthesis or
message delivery occurs. Real incoming-triggered source binding and actual
session-rotation continuity remain distinct live acceptance tasks.

## Subsequent production add/source acceptance

`tools/verify_live_memory_add_sources.mjs` and
`artifacts/live-memory-add-acceptance-20261102.json` now provide separate real
production evidence:12 actual registered add calls (6 owner,6 first group)
correlate with24 active daily/project entries. Every metadata digest matches
active Markdown, argument content matches in memory, updatedAt falls between
the corresponding call/result, and the rendered result is positively successful.
Eighteen entries carry24 exact same-chat incoming references verified by a
read-only SQLite transaction;6 uncited entries remain unattributed. No source
reference is supplied by the verifier or inserted into production.

No replace/expand/archive calls occur in the inspected live logs. The third
session's historical adds do not strictly correlate to current metadata. This
does not replace those pending live checks, prove real rotation, validate global
KEY auto-promotion, or imply OS/shell isolation. No new user message was needed
to obtain this existing-evidence acceptance.
