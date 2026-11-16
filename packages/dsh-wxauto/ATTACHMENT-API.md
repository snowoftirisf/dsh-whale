# Ordinary attachments: integration contract

The worker and Node tool now integrate these helpers. This document distinguishes
offline implementation checks from live WeChat acceptance; neither successful
unit tests nor a library method signature proves a real file was delivered.

## Receive

After the existing message watcher qualifies a fresh `type == 'file'` row and
checks the sender, call:

```python
download_file(message, chat_received_files_directory,
    ensure_chat=lambda: monitor.current() == exact_chat_name,
    media_root=data_root)
```

`FileMessage.download(dir_path=unique_staging_directory, timeout=30)` is the sole
UI operation. Call from the existing single wxauto UI queue, never from an
additional thread. Installed wxauto4 41.1.7 class inspection confirms this
signature; no WeChat instance was created for that check. Real 4.2 file-message
acceptance remains pending. No fallback to arbitrary returned paths or global
clipboard reads is permitted.

Success returns `path`, migration-safe `relativePath`, sanitized `name`, actual
`size`, `sha256`, `source='wechat-file-download'`, `mediaType='file'`, and
`contentStatus='unknown'`. Preserve the exact chat and sender on the surrounding
event. The default limit is **100 MiB**, independent of the existing 20 MiB image
limit. Attachment content is unknown until an explicit reader succeeds; filename
and extension do not establish its contents. Oversized, failed and expired files
must still yield an incoming-message event marked unavailable, not disappear.

The downloaded bytes are copied to an exclusive UUID-prefixed filename. Only a
file inside the operation's staging directory is accepted. Source and parent
symlinks, hardlinks, Windows reparse points, UNC paths and traversal are rejected. Failed
staging directories are retained for diagnosis; the helper does not recursively
delete paths returned by wxauto.
An SDK sidecar or cleanup failure leaves staging for diagnosis, sets
`stagingCleanupPending=true`, and does not discard a successfully verified copy.

## Send

`whale_send_attachment` currently accepts `filePath` and sends the distinct
`send-attachment` IPC operation. The trusted worker, rather than model arguments,
supplies allowed roots and registrations. The existing `send-file` meme-root
policy remains separate. A future attachment-ID facade may resolve IDs through
the vault; the current tool does not implement such a facade or file-content reader.

Immediately before `wx.SendFiles`, call:

```python
attachment_file(filepath, received_root=current_chat_received_files_directory,
    outbox_root=current_chat_outbox_directory,
    received_paths=registered_received_paths_for_current_chat)
```

Received paths require registration in addition to containment; generated files
must be under the current chat's outbox. Another chat's attachment and arbitrary
disk paths are denied. Empty or over-limit files and all linked paths are denied.
An attachment received as a file may be any extension; it is never executed.
The registry derives actual SHA-256 and size at registration, checks any supplied
identity, and rechecks received bytes before granting resend access. Missing,
modified or legacy hashless receipts do not authorize resend. Use
`registry.received_paths(target, filepath)` to avoid hashing unrelated files.

Keep existing begin/end-reply locking, exact destination checks, claim-before-
send and uncertain-outcome handling. Only delivery acknowledgement may mark the
outgoing attachment as sent. Keep native audio on `send-voice`; audio attachments
must not replace a requested native voice bubble.

The installed SDK can return `None` after actually sending a file. Only ordinary
`send-attachment` now has a passive readback alternative: snapshot the exact chat
and old message-row IDs before the single SDK call, then require one new visible
self file row with the complete basename, stable on two reads in the same view.
The observed binding is a childless `mmui::ChatBubbleItemView` ListItem with the
four-line label `文件 / basename / size / 微信电脑版` (newline separated).
There is no substring match, avatar parsing, UI input, retry or generalization to
meme/image sends. Explicit SDK failure is not overridden. Missing observer data
preserves the old single SDK attempt; a void return without evidence is unknown.
ACK `renderedFileEvidence` is sanitized and retained through the outgoing journal
in vault delivery detail. It proves a locally rendered self file row, not the
recipient's downloaded bytes. The real observed old row was outside the viewport,
so that probe establishes label structure, not successful live direction/readback.

## Media and history boundaries

Images retain their separate 20 MiB/40-million-pixel policy. The current SDK's
actual `ImageMessage.download(self, dir_path=None, original=False)` signature
supports the existing explicit `original=True` request. Successful download means
the client returned an image after that request, not proof that the sender's
camera original was available. The fallback is explicitly labelled
`source='visible-thumbnail'`; animated emotion capture remains visible sampled
frames, not a full original GIF. Attachment changes do not alter these branches.

Incoming native voice now has a separate raw-source path: after the native inbox
baseline and local ASR readiness handshake succeed, wxauto emits observation
hints without invoking `VoiceMessage.to_text`. The native inbox resolves an exact
incoming server/chat identity against an authenticated audio snapshot, retains
the SILK source and decoded audio, and uses local CPU SenseVoice for transcription.
The shared vault records the raw message, audio attachments and separate ASR
evidence atomically. A hint alone is not an incoming user message or proof that
ASR succeeded. If the startup handshake is unavailable, the existing SDK
`to_text` path remains the fallback; that fallback does not preserve raw audio.
Fresh end-to-end raw-voice acceptance must be established from an actual new
voice, its source/ASR/history evidence and final reply, not inferred from these
offline checks or a historical voice reference.

The history service records the surrounding message and indexes `images` and
`files` through the existing shared vault client. Index failure is logged and
does not falsely claim content was read. There is no second vault writer here.

History search/context reads keep `attachments.path` as the canonical relative
data-root path and derive `absolutePath` plus `metadata.path` from the current
vault root. `metadata.relativePath` follows that canonical path; historical
absolute paths are never used as a fallback. Missing, invalid or linked paths
have a non-available `pathStatus` and no usable absolute path. Stored DB metadata
and original files are not rewritten by a read. `available` means only that a
regular unlinked file is present at that location, not that its content has been
re-read or that resend is authorized. Current-chat registry and hash checks still
run immediately before sending. `whale_history_message` reads text and attachment
counts; use history search/context to retrieve these attachment descriptors.

## Offline verification and live acceptance

`python -m unittest test_attachments test_attachment_integration test_incoming_images test_message_watch`
previously ran 84 tests; two OS link-creation permission tests were skipped on this host.
Reparse and path-denial tests still run. This suite uses fake wxauto rows and
SendFiles calls, with temporary workspace data only.

Smallest pending owner-private acceptance:

1. Owner sends one tiny UTF-8 text file with a unique filename and known bytes.
   Observe the real 4.2 file row, one incoming event, exact retained SHA/size,
   durable receipt, and one history attachment. Do not infer text from its name.
2. On explicit test authorization, send that registered file back only to the
   same owner private chat. Observe the real file bubble and recipient download
   SHA/size, plus worker claim/ACK and history delivery state. A truthy SDK return
   alone does not prove the recipient's downloaded bytes.
3. Reject a different chat's copy or a mutated registered file using offline
   tests; do not send intentional bad files to real chats. A small image with
   legible text and a short native voice may separately verify image provenance
   and transcription after upgrade. Raw voice preservation has a separate native
   inbox acceptance procedure and is not proven by this ordinary-file test.

The original offline review performed no WeChat UI operation, send, GPU use,
production data write, or vault process startup. Subsequent live evidence follows.

## Actual owner-private file round trip

The first two new owner text files were downloaded, registered, read by the model
and actually received back on the owner's phone, but the installed SDK returned
`None` and the adapter recorded `send-not-confirmed`. The second fresh attempt
captured `sdkResult.type=NoneType`; this was a void return, not an explicit SDK
failure. Neither unknown operation was retried or retroactively upgraded.

After passive file-row readback was integrated, a third distinct received path
completed real turn 127. `whale_send_attachment` returned sent:true; one matching
outgoing operation is sent and contains the two-observation rendered-file-row
evidence in its durable delivery detail. The owner confirmed receiving the file
and a normal reply. Incoming and outgoing attachment records retain the same
6-byte SHA256; the phone's downloaded hash was not independently measured.

Live evidence: `artifacts/third-owner-attachment-live-audit-20261102T095007Z-df51fa3f.json`;
recipient feedback and unchanged old unknown receipts:
`artifacts/third-owner-attachment-recipient-confirmed-20261102.json`.
This verifies the fresh owner-private ordinary TXT path, not group attachments,
all file sizes, generic image ACKs, or recipient byte verification. Current
readback tests: 10 passed; worker attachment integration: 16 passed; complete Node
adapter suite: 235 tests, 232 passed and 3 host skips. No observed-row helper calls
SDK message parsers, resolves avatars, clicks a row, scrolls, or sends again.
