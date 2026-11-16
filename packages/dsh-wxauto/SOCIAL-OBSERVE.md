# Fixed-purpose social observation

`POST /local-dsh-wxauto/social/observe` accepts only
`{"purpose":"friend-search-owner"}`. The host's current webServer owns the
port; discover it from the running Host process after each normal restart.
Listener ownership alone is insufficient: verify a known read-only plugin
endpoint on that port. A renderer/browser listener can return 404 despite
belonging to the same Electron process tree.

The route requires a loopback peer and the existing connection service's
`requestRejection(req)` admission. Missing/throwing admission fails closed.
It does not read credentials, bypass authentication, or expose a generic RPC.
JSON is limited to 256 bytes. Concurrent requests receive 409 instead of
duplicating navigation. No model tool is registered for this diagnostic.

The Harness **微信连接** settings section offers **检查好友搜索** through
`/api/local-dsh-wxauto/social/observe`. The installed official connection
service requires `/api/` paths and enforces an authenticated owner principal
before invoking registered native fetch handlers. Desktop same-origin fetch
is also authenticated by the official desktop forwarding layer; this plugin
never obtains the session cookie. The `/api/` HTTP registration keeps the
same requestRejection gate as the old route. Native and HTTP transports share
one in-flight guard and the same fixed-purpose action. An unauthenticated
script receiving 401 is expected, not grounds for loosening the gate.

The button does not run on page open, never automatically retries, and locks
after an unconfirmed outcome. Safe observation results/errors are recorded
under `status.local.json.socialObservation` for acceptance review.

## Plan-bound candidate lookup

The separate `friend-search-plan` purpose accepts exactly `purpose`, `planId`
and `hash`. The worker reads the owner-origin `add_friend` plan and rechecks
its digest, pending/approved status and expiry before navigation, dispatch
and completion. Query text cannot come from the HTTP body. A safe literal
query may begin with `_`; this does not assert that WeChat permits that
identifier, and the existing strict trusted-owner identity reader is unchanged.

The settings page loads the one prepared pointer from
`config/friend-lookup.local.json` via owner-only GET
`/api/local-dsh-wxauto/social/friend-lookup-plan`. IDs/hashes stay out of the
product UI; it displays the actual account and greeting for review. This GET
performs no UI navigation or approval. The subsequent **核对好友资料** button
only searches the bound plan. Profile identity must match its literal account;
known profile button labels are observed but never clicked.

The pointer is prepared by `tools/prepare_friend_lookup_plan.py` after an
explicit user target. Preparing or looking up a pending plan does not approve
it or send an approval notice. First clicking an add-contact button can itself
change friendship state, so this read-only path must never click it.

The result distinguishes a matched ID from missing, ambiguous, invalid,
mismatching or unsupported ID evidence. Only `identityEvidence=matched` can
produce `profileIdentityMatched=true`; seeing a profile or an add-contact
button is insufficient. Within the original three-second read deadline,
content-only control changes may be reread after verifying the same form,
PID/HWND/runtime identity and foreground. This never redispatches the search.
Window/foreground changes or multiple results still refuse immediately;
continuous content instability times out. Known control error codes survive
sanitization without exporting names or profile text.

## Owner-approved initial contact acceptance

Two fixed owner-only POST routes extend the prepared plan:

- `/api/local-dsh-wxauto/social/friend-lookup-plan/approval` claims the existing
  one-shot private notice and sends it literally to the configured owner.
- `/api/local-dsh-wxauto/social/friend-lookup-plan/acceptance` accepts only the
  same prepared `planId/hash` after actual owner-private approval. It enters
  `social-acceptance` on the original UI/delivery queue; no model tool exposes it.

This entrance is a first-contact experiment, not a verified general add-friend
capability. It durably claims the approved plan and rechecks the actual query,
display name/avatar, form identity, geometry and observed add-contact button.
An unavailable public ID can use the explicitly owner-reviewed query/name
basis; a readable contradictory/ambiguous/invalid ID always refuses. The first
add-contact click may itself change friendship state, so it requires the same
real private approval as sending a request. Only one click is attempted.

Unknown application forms are observed, never guessed or submitted. The
initial-contact entrance always declines to produce confirmed/passed evidence
or enable ordinary social bindings. A claimed/unknown plan cannot dispatch
again. The prepared test's private approval is acknowledged without immediately
running the still-unverified ordinary social-execute action.

Owned-form cleanup occurs before chat restoration, including preflight and
click exceptions. Uncertain cleanup sets the existing fatal flag and blocks
further UI work. If input was attempted, safe bounded window metadata can be
saved under `backups/social-initial-contact/<planId>/<uuid>.json` as
`unverified-ui-observation`. It is explicitly not an acceptance receipt.
Metadata excludes the main chat tree and raw names, IDs or input text; the
owner response exposes only the artifact's relative path and SHA. Diagnostic
write failure never changes an unknown outcome or authorizes a retry.

Approval cards use two different intent-bound eight-character numbers. The
owner replies with just the approve number or just the reject number; the
trusted worker resolves the intent before Node consumes it. A bare legacy
shared number is never sufficient. Existing explicit commands remain
compatible, but a new intent token paired with the opposite verb refuses.
Ordinary eight-character conversation text without a trusted worker binding
continues through the normal chat flow.

Each claimed card has its own persisted `noticeId` and outgoing send ID.
Replacing the one already-sent legacy card requires explicit user-requested
maintenance while the old worker is stopped. It retires the old number,
clears any queued legacy grants through normal app shutdown, leaves the fixed
parameters/hash and pending status unchanged, and permits one replacement
notice claim. This is not a retry of an unknown delivery or an approval.

The owner target is derived from trusted configuration. Both social-execute
and social-observe join the existing Node delivery tail; Python must also
reject observation during an active reply or unverified independent-window
mode. Only the already-connected wxauto instance may navigate the UI.

Observation can enter the add-friend search and search the configured owner;
it cannot submit a friend request, accept a request, send a message, publish,
or create a group. Return values project only booleans, identity/control
hashes, known state enums, version and control class/type. Chat names,
public IDs, result text, file paths and contact lists are omitted.

The actual original chat must be restored, not a convenient fallback. An
unverified restoration must stop the UI worker under the existing
social-chat-restore-failed guard. Neither successful navigation nor matching
profile text proves search-response freshness or grants a live social write
capability. All five unverified write bindings remain disabled.

Before marking this feature loaded, verify actual normal Host reload and a
fresh fixed-purpose request through the host admission gate. Isolated HTTP,
dispatcher and recovery tests alone do not prove the installed WeChat UI
workflow succeeded. Do not repeat an unknown-outcome observation blindly.
