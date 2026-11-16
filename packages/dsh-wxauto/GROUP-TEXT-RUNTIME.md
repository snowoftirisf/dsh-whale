# Native group text runtime

This implementation is independent of owner-private text and voice authority.
The three existing groups were explicitly switched through the authenticated
Harness settings UI after quiet checks. Their original sessions/chats and
304 historical baseline texts were preserved without replay. A real restart
restored the same epochs/markers with recoveryGuardVersion2 and active3.
See the independent first-three and restored audits recorded in STATUS.
No fresh group turn or remote reply is inferred from baseline/restoration proof.
Automatic native delegation for authenticated text-containing new-group batches
was loaded on 2026-11-03. Existing three-group recovery, unchanged authority
epochs, sessions and baseline states were independently verified after loading.
A fresh newly-created group conversation is not inferred from that recovery.
Images/files and first batches without unhandled text retain the stated SDK
boundaries. The earlier staged descriptions below are development history.

## New-group admission — loaded, fresh first-group acceptance pending

`artifacts/dev-new-group-text/index.staged.mjs` is the next review entry.
Its dedicated pending-source reader pins the original authenticated unread
snapshot; raw text, rendered text and redacted legacy text remain distinct.
Exact native IDs delegate text/voice to their durable inboxes; the raw unread
queue filters already-owned inputs, including submitted/baseline IDs. Existing
native groups keep their epoch, session and baseline, with no first-admit call.

`NativeGroupTextAdmissionAuthority` is separate from generic quiet commit. It
requires the actual worker's registered-handoff capability, authenticated
catalog, initial queued text claims and matching durable delegation receipt.
Only then does it exclusively create a source marker. Unknown outcomes keep
their evidence and use read-only reconciliation rather than another submit.

Committed monitor restoration uses a separate marker/canonical-route capability
and does not need a fabricated raw claim. Before clearing an unread badge,
the candidate must prove the full native unread set is text/voice; voice also
requires the validated existing native voice inbox baseline. This removes the
invalid assumption that WeChat 4.2 exposes a usable scroll-bottom pattern.
Unsupported media/system unread restoration remains pending before navigation;
normal already-running media reception keeps its original SDK path.

The canonical index/worker are installed; helper modules retain candidate names
for provenance. The source backup and live audits are recorded in
`artifacts/dev-new-group-text/source-state.json`.
First text ownership now follows successful UI confirmation; an authenticated
native-second boundary preserves unseen arrivals during that handoff. A parent
admission barrier prevents model submission until worker enable acknowledgement.
Exactly correlated busy-before-admit acknowledgements now grant a private,
one-use not-issued capability and one durable retry on the same worker. Unknown
acknowledgements never grant retry; restart requires fresh UI confirmation.
Actual load/restoration and a Python-to-Node-to-marker fixture passed. Unsupported
media/system unread restoration remains explicitly pending. Offline fixtures
do not prove a new live group conversation or a fresh voice roundtrip.

## Contract

`groupTextRpcClient({write,routeForGroup,isReady})` accepts only `group-text-status/prepare/abort/commit` and a trusted active native group key. It derives the exact target from `routeForGroup`; responses are correlated by generated operation ID and revalidate that route. There is no group approval/decision operation.

`createNativeGroupTextRuntime` restores existing per-group inbox epochs only after explicit authenticated worker status. UI/preparing/blocked statuses revoke the corresponding processor; a heartbeat cannot initialize or activate an inbox. Each group uses its existing session through `routeForGroup`. `refresh()` performs catalog reads only; captures remain in the single existing scheduler. All messages, including owner-looking approval codes sent in a group, use the group processor without any owner control dispatcher.

`createGroupTextCutover` performs one explicit quiet two-snapshot cutover using the existing exclusive capture lease. Canonical comparison includes raw message identity, native sender numeric identity, unresolved physical envelopes, and all observed native IDs. Historical unresolved rows can remain baseline-held; they do not become queued inputs. Preparing UI messages remain real deferred SDK envelopes in the Python bridge and dirty the transition. Uncertain commit/abort keeps the group paused; no repeated commit, rollback of a native marker, or automatic activation occurs.

The baseline publisher uses object-capability tokens, a group-specific inbox lock, exclusive publication, hash readback, and archive-only discard when a fresh worker explicitly confirms commit is not native. It never replaces an existing authority marker. Readiness checks session activity, actual group queue/buffer, history journals, exact loaded configuration SHA, deferred evidence files, and trusted route identity. The Python prepare guard must separately prove no unprocessed unread group input; a stable database catalog alone cannot prove this.

`group-incoming-submit.mjs` issues one durable reservation/issued permit before the actual model ask. It reuses strict queued-only validation for native group text and voice in the same batch. Partial or unknown submission is not released or retried. Its separate `incoming-group-submissions` folder preserves the existing owner gate's strict historical schema. Payloads are hashes, not chat text.

History stores source `wechat-native-group-text`, the original raw source text and stable native author, and typed group claim/epoch/snapshot metadata. The model-facing event may carry independently verified rendered text. Memory source bindings come only from the current adapter session and canonical group chat; adding a source kind does not grant private memory access. Pending recovery files containing ordinary text for native/preparing/blocked groups are held untouched; voice/media and legacy UI groups retain their prior recovery behavior.

## Explicit owner-local entry

`GET /api/local-dsh-wxauto/group-text-routes` lists only exact current trusted active group key/target pairs after owner admission. No capture, status RPC, marker or activation.

Both cutover endpoints use POST with exactly `{"nativeChatKey":"wechat:<id>@chatroom"}`:

- `/api/local-dsh-wxauto/group-text-cutover` — read-only status/preflight.
- `/api/local-dsh-wxauto/group-text-cutover/activate` — explicit first cutover.

The HTTP path requires loopback plus official owner admission. Native fetch registration uses the existing authenticated owner transport and same-origin checks. Neither endpoint is registered as a model tool. Routes, account, session, filesystem paths, epoch, ticket, and capture commands cannot be supplied by the model/client.

## Integration artifact

`artifacts/dev-group-text/index.staged.mjs` and `index.patch` contain the index integration, kept out of the live index while the listener is active. The staged module resolves all imports without calling `apply`. It connects group hints/status and scans to the existing shared capture/pump, typed durable ask gate, startup restore, worker-exit cleanup, explicit cutover endpoints, and recovery guards. Applying it requires the reviewed Python bridge/worker changes and the root's loading window.

New auto-discovered groups deliberately retain the existing SDK unread admission and UI authority. The current catalog client accepts active routes only; authenticated pending-group exact text delegation is a separate required stage. This code does not claim that new groups already use native text, nor that any live group switch has been accepted. Images/files/emoji remain on their previous SDK paths and may still need separate sender handling.
