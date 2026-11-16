# Group voice transcription layout correction — November 2

## Current production disposition: original audio receiver loaded

The main-window worker now emits native voice observations and bypasses SDK
transcription after the raw-audio coordinator's readiness handshake. Five-database
capture, exact SILK extraction, CPU decode and SenseVoice are loaded; nine old
voices remain baselined, all three groups are active and speech warmup is ready.
See `NATIVE-VOICE-PROGRESS.md`. Genuine new automatic receive/reply acceptance is
still pending. The failed geometry shim remains disabled; the following section
is retained as the earlier rollback investigation, not current raw-mode behavior.

## Historical rollback: experimental geometry disabled

A subsequent genuine new group voice failed the geometry selector with
`VoiceGeometryError`. Reading the previously expanded diagnostic voice was
not proof of a fresh automatic round trip. The visual shim has therefore
been removed from `MainWindowMonitor.transcribe_voice`, which again calls the
original SDK `message.to_text()` for both private and group reception.
Harness was normally restarted to load that rollback; the helper remains
experimental source, not a production receive path. Diagnostic tracing is off.

The user explicitly confirmed the intended architecture is original audio
acquisition followed by local SenseVoice. At that earlier rollback it was not yet wired into WeChat:
the SDK voice class has no download method; the current chat snapshot contains
only voice metadata. A real encrypted `message/media_0.db` exists but is not
in the four-database capture. The next step is a precise media database capture
and verified server-ID/blob mapping, followed by container-specific decoding
into the already-ready CPU recognizer. Do not describe outgoing voice readiness
or UI transcription as proof of this raw-audio chain.

Read-only historical comparison found two successful private transcriptions;
the four captured group voice inputs were failure markers. The earlier v4
group-send acceptance used a text input. Thus that report never established
group ASR success. Evidence:
`artifacts/group-asr-regression-comparison-20261102T055250Z.json` and
`artifacts/group-voice-avatar-guard-live-20261102T055011Z.json`.

The sections below retain the attempted correction and its limited evidence;
they do not override this rollback.

## Observed failure

The genuine group voice test at 05:35 UTC entered the original group session,
but transcription failed. The model received the explicit unknown-content
marker and chose two text replies. A ready outgoing voice capability did not
prove successful incoming transcription. The audit is
`artifacts/group-voice-avatar-guard-live-20261102T053650Z.json`.

The actual SDK trace distinguished two operations:

- Incoming group sender parsing right-clicked `(51,30)` and opened the observed
  at-plus-pat menu. This is the SDK sender lookup, not voice sending.
- `VoiceMessage.to_text` right-clicked `(102,30)` and opened no menu.

A passive wxauto capture showed a 115-pixel-high incoming voice row with its
nickname near y18–34 and its gray voice bubble near y44–98. The fixed y30
transcription click therefore targeted the nickname area. This is separate
from the SDK's intentional sender-avatar menu.

## Change and evidence

`group_voice_transcription.py` keeps the SDK's transcription and menu handling,
but temporarily replaces that message instance's `right_click`. Every SDK menu
step captures the same visible incoming voice row, rechecks chat/type/process,
foreground, runtime identity, bounds and content stability, then uses
`incoming_voice_geometry.py` to locate an interior gray-bubble point. The
expanded transcription bubble is selected for the SDK's later copy step.
Missing, ambiguous, clipped or changing geometry refuses the click. There is
no guessed coordinate fallback, sender replacement or outgoing send.

The original per-instance method is restored on success and failure. The
private voice path is unchanged. Failures now include non-text diagnostic
facts in the incoming event rather than a null media error.

The first diagnostic successfully triggered native transcription, but an
overly strict one-click guard interrupted the SDK's subsequent copy step.
That failed diagnostic is retained in
`artifacts/group-voice-corrected-transcription-live-20261102.json`.
The guard was corrected to allow at most three independently validated SDK
menu steps. A later diagnostic read the same already-expanded voice and
returned 15 transcription characters; both menu clicks targeted `(357,130)`
inside the transcription bubble. Evidence:
`artifacts/group-voice-corrected-transcription-live-20261102T054627Z.json`.
No old input was replayed to the model and no additional reply was sent.

The original passive screenshot path was overwritten by the later expanded
capture. It must not be represented as the original 115-pixel image. The
expanded image has a separate retained copy; unexpanded-layout tests use
explicit synthetic fixtures. The original trace/bounds and first corrected
click remain recorded independently.

Sixty-eight focused geometry/transcription/watcher/delivery tests passed.
The corrected source was loaded in a fresh Harness process; three groups,
history sync and native voice were ready, and resident speech warmup finished.
The diagnostic trace was then disabled. A fresh incoming automatic round trip
after this correction is still being verified.
