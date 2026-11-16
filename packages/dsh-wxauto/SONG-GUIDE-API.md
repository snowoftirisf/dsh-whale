# CPU song-guide preparation

Private stdio worker, no network listener/UI/sending/voice approval step. It
prepares bounded model-estimated vocals; it never claims that a separated file
is the correct song, singing rather than speech, or perfectly accompaniment-free.

Launch from the trusted workspace:

```
.venv-seed-vc/Scripts/python.exe plugins/dsh-wxauto/song_guide_runtime.py
```

Set `WHALE_DATA_ROOT` from trusted adapter configuration. The default is
`<DATA_ROOT>`; tests/acceptance must explicitly select a workspace fixture root.
The worker uses `.venv-seed-vc` for pinned yt-dlp 2026.10.26 + portable FFmpeg 7.1
(binary SHA256 2ce797a0f88d7f067180338fb227f7b1928ea727bd9a4d7a1d022f7c52af71a3),
and `.venv-vocal-separator` with CPU Torch 2.0.1 for Demucs. Fixed Demucs source
commit e976d93ecc3865e5757426930257e200846a520a, local htdemucs 955717e8 model
SHA256 8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4.
No model download, GPU, new dependency or authentication/cookie flow is used.

One JSON request per line; output uses the speech client's id/ok/value/code
envelope. `status` and `stop` are supported. For example:

```json
{"id":"job-rpc-1","op":"prepare","sourceUrl":"https://www.youtube.com/watch?v=oec9R5ypf-o","startSeconds":80,"durationSeconds":20,"chatKey":"0123456789abcdef"}
```

`chatKey` is the trusted adapter's SHA256(exact target UTF8) first 16 lowercase
hex characters. It is not a model-facing input. Model tools should expose only
sourceUrl/startSeconds/durationSeconds and add chatKey from the current route.
No target/recipient/ref/voice/path/approval override fields are accepted.

Source URLs are HTTPS Bilibili BV video URLs (optional p), YouTube watch/shorts/
embed, and youtu.be. They are canonicalized; arbitrary domains, local paths,
credentials and nonstandard ports are rejected. The extractor uses only public
access with no cookies, login, paywall or captcha workarounds. Only direct audio
formats on known provider CDN suffixes with public DNS addresses are accepted;
other media protocols/CDNs fail clearly. Calls are argv lists, never shell text.
FFmpeg limits the requested interval and output size (32MiB); returned rate,
actual length, finite/non-silent data, channels and file bounds are checked.

Duration is 1..55s, start 0..86400s. Extraction worker deadline 210s (FFmpeg 150s),
separation 360s. The caller should allow at least 600s for prepare. Timed-out
workers' own process trees are terminated, not unrelated applications. stderr
and upstream logs are discarded; auth/transport/phase failures return static
codes with no URLs containing signed tokens, message text or exception paths.

Successful value contains guideId/jobId (same UUID hex32), trusted chatKey,
canonical sourceUrl, sourceStartSeconds/sourceDurationSeconds, sampleRate,
durationSeconds, guideSha256, dryVocalMethod, listeningAccepted=false,
musicalSourceVerified=false and vocalIsolation=model-estimated. It exposes no
absolute file path or recipient.

The trusted SVC runtime resolves:

```
<DataRoot>/voice/song-guides/<chatKey>/<guideId>/guide.wav
<DataRoot>/voice/song-guides/<chatKey>/<guideId>/metadata.json
```

Metadata is published atomically only after separation and file validation:
schemaVersion=1, kind=whale-song-guide, guideId/jobId/chatKey, guideFile=guide.wav,
guideSha256, sourceAudioSha256, sourceUrl, sourceStartSeconds/sourceDurationSeconds,
slice, actual sampleRate/channels/durationSeconds, createdAt, audioKind=isolated-vocal,
verified=true, dryVocalMethod=demucs-htdemucs, vocalIsolation=model-estimated,
listeningAccepted=false, musicalSourceVerified=false, separator source/model pin,
and sourceExtractor identity. `sourceAudioSha256` hashes ONLY the bounded decoded
mixed source.wav, not the full video. verified means technical preparation only.
It must not be interpreted as manual musical-source or purity acceptance.

Both source.wav and separated guide.wav are cached in that job for provenance.
No failed separation falls back to the mix. Failed jobs have failure.json with
verified=false and no metadata.json success receipt. Generated guide is 44.1kHz
mono FLOAT WAV; only downward linear headroom is applied if necessary, no pitch
shift. SVC must independently check chatKey, guideId, metadata and SHA before use.

Validation: 14 offline interface/failure tests and one actual public YouTube
official-source slice (80–82s) through FFmpeg and CPU Demucs in a workspace-only
data root; 2.0s/44100Hz/mono, complete pipeline 10.55s. The result is in
`voice-data/auditions/song-guide-runtime-verification/result.json`; this is
technical validation, not listening acceptance. A separate actual Bilibili
18.5–20.5s slice completed the same pipeline in 8.75s (speech source, provider
compatibility only, NOT a song claim), recorded in bili-result.json. Both actual
receipts were accepted by SingingRuntime.guide() for schema/hash/audio validation
without rendering or GPU use (svc-schema-validation.json). No production D:
writes or sends.
