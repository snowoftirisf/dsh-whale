# Observed contact readers (read-only)

`social_contact_observation.py` defines read-only readers only. It does not initialize
wxauto, navigate, type, invoke, cancel, select contacts, submit, register an
ActionBinding or create a production acceptance receipt.

```python
read_contact_profile(control_root, expected_display_name,
                     expected_wechat_id=None, replay=False)
read_group_creation_picker(control_root, replay=False)
read_friend_search_result(window, expected_account, client_pid=trusted_pid,
                          window_handle=trusted_handle, foreground=read_foreground,
                          replay=False)
```

The caller supplies an already-observed wxauto UIA control. Both readers capture
its subtree twice, with bounded depth/nodes, and reject changed runtime IDs,
properties or ancestry. A matching runtime ID is stability evidence only, never
an account/contact identity. Results contain `readOnly=True`,
`actionAuthorized=False`, `liveAcceptance=False` and source `wxauto-uia` (or
`wxauto-uia-offline-replay` when explicitly replaying).

The contact reader requires one actual `ProfileHeaderUi`, its uniquely identified
display-name field and avatar name both equal to the expected display name.
Rows repeat the same AutomationIds for region and WeChat ID. Consequently the
reader requires one `WeChat ID:` key and exactly one ProfileTextView **in that
same row**, not an arbitrary descendant. Empty, duplicate, cross-row or malformed
values fail closed. An optional trusted expected ID must match exactly. Without
it, `identityMatched=False`: the returned `wechatId` is only a candidate requiring
trusted resolution and exact-plan approval before any action. It does not prove
friendship/request status. This result contains private identity values for the
local caller; do not publish them in status logs or general final messages.

The picker reader accepts only the observed exact title `WeChat发起群聊` and
SessionPickerWindow class. It requires the observed search edit, exact
`sp_choice_contact_list` zero-selected label, cancel XOutlineButton and complete
XButton. It refuses a forwarding/“发送给” picker and a nonzero/unknown selected
count. Blank or named checkbox controls never become contact identities: returned
`contacts=[]`, `contactsResolved=False`. Control presence does not validate
Invoke/Click behavior, cancellation or group creation. No control object that
could subsequently perform an action is returned.

Evidence is the root's actual contact-forms observations. Tests replay the
existing `owner-profile-header.json` and `controls-004.json` as inert read-only
ReplayControl trees, explicitly tagged offline; they create no duplicate fixture
or live acceptance. Synthetic tests independently cover incorrect identities,
region confusion, duplicate/missing fields, control changes, incorrect picker
purpose/classes and selected counts. Run:

`python -m unittest discover -s plugins/dsh-wxauto -p test_social_contact_observation.py`

Six tests pass on this workspace, including actual-artifact offline replay. The
artifact replay test is skipped when those local observations are not distributed.
Final group creation, applicant request identity, search-result application forms
and acceptance/completion readback remain unimplemented and unverified here.

The root subsequently called both readers on actual wxauto controls using an
actual SDK wrapper: existing-owner profile matched the previously observed
WeChat ID, and the real group-creation picker reported zero selected contacts.
The owned picker was physically cancelled after foreground confirmation and the
SDK chat cache restored to owner private. The actual SDK-backed native focus
method also passed without recording, audio or messages. Evidence:
`artifacts/contact-readers-live-acceptance-20261102.json`. This is real read-only
UI acceptance; `liveAcceptance=False` in a reader result still intentionally
denotes no final social action authorization or capability receipt.

## Current search-result reader (not live accepted yet)

The new reader accepts an already-open AddFriendWindow only. Its actual class,
type, title and AutomationId, trusted PID/handle, current foreground and complete
runtime identity must agree before and after both snapshots. Search ValuePattern
must equal the exact expected account in both snapshots. Hidden or foreign-process
evidence, ambiguous search controls, multiple negatives/profiles and a contradictory
negative-plus-profile tree fail closed. It reads no pattern setter or action API.

The returned state is only `no-match-observed`, `profile-observed` or `unknown`.
`profile-observed` means one visible ProfileHeaderUi exists, not a resolved target;
use the separate strict identity reader before any future authorization. This
reader returns querySHA256 rather than the account or search ValuePattern; control
evidence excludes Names, query values and profile contents. Current ownership and
query matching do **not** establish response freshness: `newSearchCompleted`,
`responseFreshnessVerified`, `accountMatched`, `applicationSubmitted` and
`friendshipConfirmed` are always false. Re-reading an unchanged old negative
never turns it into a newly completed lookup. No dispatch token, elapsed time or
changed RuntimeId alone is promoted into remote response proof.

Ten module tests now pass: the original six plus four new multi-case tests for
the three observation states, old-negative persistence, exact window/query binding,
focus/ownership/visibility failures, changing controls and actual negative-tree
replay from controls-008.json. That original inventory does not contain PID/HWND
or ValuePattern values: replay uses explicitly synthetic values for those fields
and remains tagged offline. It does not certify the past query's exact value.

Still needed live: invoke this new reader on the caller-owned real AddFriendWindow
with real PID/handle/foreground and actual query ValuePattern, without apply or
accept. Any claim that a **new** lookup completed additionally needs independently
observed query-correlated response transitions; no such completion inference has
been implemented or tested here. No social binding or production receipt is added.
