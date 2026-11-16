# wxauto social backend: implemented gate and remaining live work

## Read-only friend-search helper attempt,07:58 UTC

The explicitly bounded helper attempt refused before any navigation/input with
`probe-existing-owned-popup-must-be-closed`. No request, message, account match
or search completion was claimed. The failure is preserved in
`artifacts/friend-search-helper-dfa22fe640c44f71bd0b335df9e6696b.json`.
A subsequent top-level read through wxauto (no clicks or closures) found the
supposed QWidget/WorkerW/Qt icon/tray windows were UIA IsOffscreen=false but
wxauto.uia.IsWindowVisible=false. Only the main window was actually visible.
This diagnoses an overly broad probe inventory, not a user dialog needing
closure. The recursive legacy observer also hit a UIA COM exception; no result
was fabricated. Harness was restored before further development, and the
production receiver returned ready. The helper still has no live pass or social
capability binding from this attempt.

## Dynamic-group scope synchronization,07:26 UTC

The dispatcher, draft queue, backend navigation scope and backend plan queue now
receive the same private, validated active-group set from the serialized worker.
Pending registration and suspension revoke group scope, including suspended fixed
groups; reenable restores it without rewriting plans, hashes, approvals or owner
identity. An invalid account/native binding cannot change this set. No new model
operation or social action binding is exposed. A synchronization failure stops
the worker before a success acknowledgement.

The99 related social/worker/voice/attachment regressions passed. The updated
Python source was loaded using the existing single-plugin switch, waiting for
old receiving/writer processes to exit before enable. Electron43332 and Host46932
were retained; the three groups and speech warmup returned ready. This fixes
scope consistency; the five unverified concrete social workflows below remain
unavailable until their own live acceptance.

The reload overlapped owner speak-up turn120. Its actual own native-history
message has one exact text/sender/time audit match, but the adapter's worker
receipt remains attempted. No sent receipt was fabricated, source rows were not
merged, and the message was not resent. See
`artifacts/proactive-reload-native-observation-20261102.json`.

## November 2 candidate correctness follow-up

The text-only Moments candidate no longer excludes a matching post merely
because Qt reused an old row's RuntimeId. Confirmation still requires exactly
one matching entry plus the existing strict creation-time or retained-head
semantic proof. Three additional offline regressions cover a genuinely new
stable post reusing an old RuntimeId, refusal without semantic proof, and the
still-unproved empty-to-first-post transition. All 23 module tests passed.
This fixes candidate bookkeeping only: no timeline reader, live receipt or
production action binding was added, and no post was published.

## Contact forms observed before submission

`artifacts/contact-forms-live-20261102/acceptance.json` records real observations
of the quick-action menu, group-creation SessionPickerWindow, AddFriendWindow,
existing owner contact profile and ContactsManagerWindow. No group/request/
acceptance/contact change was performed. The picker showed zero selected and
one eligible friend. The contact manager showed one friend; the main contacts
list's four entries also include self and service accounts. These are observed
lists, not an independently audited account-wide contact inventory.

The actual search button is `mmui::XButton`; installed SearchNewFriendWnd.search
still looked for XOutlineButton and failed before clicking. An exact own-ID
readback followed by one physical actual-button search returned not-found. This
is a negative search sample, not proof that public aliases/native IDs are
interchangeable or that sending a request works. Existing profile identity must
be read from its labelled same-row value; a shallow whole-window tree initially
missed the field. New pure read-only helpers and offline replay tests are in
`SOCIAL-CONTACT-READERS.md`; they do not register an action binding.

`social_friend_search.py` now supplies an original, narrowly scoped replacement
for that broken search step. It accepts an already-open verified AddFriendWindow,
requires the matching process and foreground handle, selects the actual XButton,
preserves unrelated input and reads the exact account back before clicking once.
It supports account aliases and international phone queries. Changed controls,
focus loss or ambiguous selectors prevent dispatch; an exception during clicking
returns unknown and the same instance cannot retry. It never applies, accepts,
infers a matching account or claims remote success. The caller must still apply
the client/version gate, independently inspect the result and close/restore its
owned form. Seven offline behavioral tests pass. The selectors are from real
observations, but this new helper itself has not yet received live acceptance;
it remains unregistered and does not alter the running chat service.

Qt exposed Invoke for the picker Cancel but it did not close the window.
Focusing the actual form's search edit, confirming its foreground handle, then
physically clicking Cancel through wxauto did close it. Own add-friend and
manager windows were also closed explicitly. No pending request card was
observed, so applicant identity, pending state and acceptance controls still
require a real sample. None of these observations enables final social actions.

## Scoped Windows OCR observation, November 2

The optional local `tools/windows-ocr/Invoke-ImageOcr.ps1` prototype uses the
already installed Chinese Windows OCR engine on wxauto-generated PNGs. It does
not replace photo understanding or register a social binding. Full-size existing
screenshots missed the announcement label; measured 150% preprocessing found it.
`anchors.py` converts coordinates using the actual source/decoded dimensions on
each axis and rejects duplicate/missing labels, rotation and invalid bounds.

An actual paused-adapter probe captured only the verified `测试群` group-info
panel (375×834), read `群公告` and `群主未设置`, and rechecked the target, panel
runtime/bounds, foreground window and exact PNG hash before each wxauto click.
Neither clicking the label nor the empty placeholder exposed an announcement
editor (137 observed nodes, no announcement/dialog/editor class). This confirms
the displayed empty-placeholder text, not management permission or edit support.
No announcement was changed. The owned sidebar closed, the private chat was
restored, and the component resumed ready/native-voice with history sync 887/0
at 19:02:44Z. The probe session terminated normally. Details:
`artifacts/group-announcement-ocr-live-20261102/acceptance.json`.

The OCR engine remains fallible. Do not infer a role from missing UI or enable
announcement writing from this observation. Concrete editing/readback still
needs a real editable sample and verified group permission.

`social_wxauto_backend.py` is an actual runtime version/receipt/target gate. It
does not claim the social UI workflows are implemented. With no observed action
bindings it reports all actions unverified and performs no UI input.

## Inspected installed interfaces

Class-only inspection of installed **wxauto4 41.1.7** found no public
`PublishMoment`, `SetGroupAnnouncement`, `CreateGroup`, `AddNewFriend` or
`GetNewFriends` on `WeChat`/`Chat`. It does provide the underlying
`SearchNewFriendWnd.search/apply`, `SelectContactWnd.search/confirm`, and bundled
UIA. Those methods alone cannot establish complete target identity, unchanged
privacy, group permissions or post-action success, so none is enabled as a
production social binding.

The local source fork declares version 40.1.1 and WeChat 4.0.5 support. It has no
LICENSE file and no license declaration in its pyproject; installed package
metadata also has no License field. No fork code was copied. This is an interface
inspection finding, not a determination of the upstream project's full licensing.

## Implemented runtime gates

- `running_client(wx)` reads the PID of the **already-bound** wxauto control,
  obtains that process's actual `Weixin.exe`, reads its file version, hashes its
  bytes and checks that the file did not change during hashing. It neither starts
  nor upgrades WeChat. No version is inferred from the installer filename.
- Local receipt `<dataRoot>/config/social-live-acceptance.json` must match the
  actual executable version and SHA256. Each action must name a passed binding
  fingerprint and hash-verified before/after UIA observation files under dataRoot.
- An `ActionBinding` contains original, observed Python callbacks and an
  implementation id. Its fingerprint covers callback sources, module files and
  the actual loaded callback code (`marshal.dumps(code, 2)`). Updating a file
  while an old callback remains loaded cannot make that old callback inherit
  the new code's receipt. Changing implementation bytes invalidates its receipt. Opaque callback source
  failures do not enable an action. Selector/config revisions must also be
  included in the implementation id/fingerprint when future bindings are added.
- The backend requires a persisted **claimed** immutable social plan with the
  same action and full parameters. Management actions also require the stored
  owner-private approval message. Drafts cannot be executed by calling the
  backend directly.
- Announcement access uses `wx.ChatWith(group, exact=True)` and twice verifies
  exact group name/type. Before update it rereads the old announcement and
  compares it with the old body already backed up by `SocialActions`.
- Before/after callbacks must return fresh version-matching UIA evidence for the
  exact parameter hash; a native method's truthy result is never confirmation.
  Missing post-action evidence becomes `unknown`, never automatic retry.
- Social navigation now runs inside a `finally` recovery scope. It records the
  original allowlisted chat and type, or uses only the configured owner fallback
  when that original state is unavailable. Recovery refreshes the wxauto chat
  binding without reseeding message history. Restoration failure retains the
  original confirmed/unknown action result, marks `chat_binding_failed`, blocks
  further social actions, and makes the worker emit `connection-blocked` and
  stop instead of continuing to monitor or send through a stale binding.

The worker constructs
`WxautoSocialBackend(wx, config, bindings=observed_bindings)` and passes it to
`SocialDispatcher` through the observed-binding loader. This integration does
not supply a live-ready workflow by itself. Do not set `liveVerified` from installation success, class
inspection, test receipts or selector guesses. There is deliberately no function
in this module that fabricates a production acceptance receipt.

## Current logged-in observations and remaining evidence

WeChat **4.2.0.12 is installed and logged in**; it is not awaiting QR login.
Harness was restarted after the social recovery integration and reported ready
at **2026-11-01 18:47:51 UTC** (2026-11-02 02:47:51 Asia/Shanghai). This is
worker-loading evidence, not successful social-action acceptance.

The unregistered text-only `social_moments_binding.py` candidate has observed
composer navigation, exact draft write/readback, current privacy summary, and
owned-draft cancellation/discard controls. It does not register a production
publish binding. Own-profile UIA observation identifies the account by its actual
WeChat ID; opening its album establishes navigation provenance, not a verified
post author or a complete timeline reader.

`artifacts/own-album-patterns-live-20261102.json` records five own-album nodes,
including base cells and the camera/publish entry, with `GetScrollPattern=None`.
No actual post, empty-state marker, head boundary, stable post identity or
creation-time evidence has been observed. The SNS schema-only snapshot also
contains zero rows and did not read post content; it cannot certify remote
timeline emptiness. The candidate's top-boundary proof requires an existing old
head-post anchor, so an empty-to-first-post transition cannot pass that branch.
A real first-post reader or another independently observed semantic proof is
still needed; camera/base cells must not be fabricated into post IDs.

The embedded own-album tab's **visible, nonzero-size** `ImageButton` named
`关闭`, with parent class `Tab` and reload/menu siblings, supported one observed
`GetInvokePattern().Invoke()`. `AlbumListView` disappeared and main-window width
changed from 1986 to 1320, as recorded in
`artifacts/own-album-close-invoke-live-20261102.json`; no post was published.
The zero-size `DropDownTabRowView` button was not used. A residual same-process
`QWidget` then triggered the strict modal guard; after confirming the album was
gone, the root agent restored the main window and restarted Harness. This
single close observation does not establish a general modal-dismissal binding
or permission to close arbitrary user drafts.

| Workflow | Evidence and exact controls to observe before writing a binding |
|---|---|
| Publish moment | Text draft entry/readback/privacy and owned discard observed; still needs a real own-post timeline reader, first-post proof, exact post identity and fresh success evidence. Image/video publication is unobserved |
| Set announcement | Group settings entry, exact group identity and management permission, readable old body, edit/save controls, saved body readback |
| Create group | Actual creation entry, unique contact search identities, complete selected-member review, confirmation, newly created group and members |
| Add friend | Search entry and exact account identity, verification/remark/tag fields, send-request control, request-submitted evidence |
| Accept friend | Stable request identity plus applicant/verification text, exact accept/finish controls, unchanged permission options, added-contact evidence |

Observed draft/album controls remain candidate evidence; no complete workflow
has acquired a production live-acceptance binding. Every tested receipt
in `test_social_wxauto_backend.py` is generated only in a temporary directory and
explicitly uses synthetic UI controls; those tests prove gating contracts, not
real end-to-end WeChat compatibility. The loaded-code fingerprint regression,
success/error `finally` recovery, unchanged confirmed/unknown outcome on recovery
failure, and worker shutdown after restoration failure are covered by isolated
backend/worker tests. No synthetic receipt is a substitute for actual action
before/after evidence.
