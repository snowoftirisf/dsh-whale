# Private singing runtime contract

`singing_runtime.py` is an independent private JSONL worker, compatible with the existing `speech-client.mjs` transport. It does not open a port, access WeChat/history, send audio, fetch songs, select a reference from model arguments, or call TTS to read lyrics. Root/Node owns ingestion and actual native-voice delivery.

## Launch and ownership

Use the isolated workspace `.venv-seed-vc/Scripts/python.exe -u plugins/dsh-wxauto/singing_runtime.py`. Trusted environment:

- `WHALE_DATA_ROOT`: canonical data root, default `<DATA_ROOT>`; **tests must override to a workspace temporary fixture**.
- `WHALE_SINGING_ASSET_ROOT`: the workspace containing pinned `sources/seed-vc`, `tools/seed-vc`, and `.venv-seed-vc`. Defaults to the runtime file's workspace ancestor; set explicitly if copying this protocol module to the installed Harness plugin.
- `WHALE_MODEL_ROOT`: optional model parent, default `<assetRoot>/voice-data/models`; singing files remain under its `seed-vc/`.

These values come from trusted application configuration, never model/tool input. Only the Node route derives `chatKey = sha256(exactTargetUTF8).hexdigest()[:16]`. Model-facing tools accept `guideId`, never chatKey/target/reference/file paths.

## Requests and responses

```json
{"id":"uuid","op":"status"}
{"id":"uuid","op":"synthesize","guideId":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","chatKey":"aaaaaaaaaaaaaaaa"}
{"op":"stop"}
```

Success is `{id,ok:true,value}`; failure `{id,ok:false,code}`. Unknown synthesis fields, including `text`, `lyrics`, `guideFile`, `target` or `referenceAudio`, are rejected. Stdout is JSONL only; upstream progress goes to stderr. Processing is serial. Closing/killing this worker never kills WeChat or another inference worker.

`status` reads configuration, pinned source/manifest and model sizes, dependency package metadata, and the fixed reference. **It neither imports torch nor initializes GPU**, and makes no network request or data-root directory. It separately returns `modelsReady`, `modelHashesVerified`, `environmentReady`, `dependenciesReady`, `referenceReady`, `voiceApproved`, `voiceSelected`, `ready`, `cudaRequired:true`, `cudaChecked:false`, `cpuFallback:false`, `gpuLoadedByStatus:false`. Size/presence readiness does not substitute for full model hashing: all weights are hashed before each real synthesis. This distinction is intentional.

## Guide layout and ingestion schema

The trusted prepare job atomically publishes metadata last:

```
<dataRoot>/voice/song-guides/<chatKey16hex>/<guideId32hex>/guide.wav
<dataRoot>/voice/song-guides/<chatKey16hex>/<guideId32hex>/metadata.json
```

Common schema agreed with attachment_api:

```json
{
  "schemaVersion":1,
  "kind":"whale-song-guide",
  "guideId":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "jobId":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "chatKey":"aaaaaaaaaaaaaaaa",
  "sourceUrl":"https://www.example.org/original-song",
  "sourceAudioSha256":"<64 lowercase hex, bounded original mixed WAV>",
  "sourceStartSeconds":80,
  "sourceDurationSeconds":20,
  "guideFile":"guide.wav",
  "guideSha256":"<64 lowercase hex>",
  "audioKind":"isolated-vocal",
  "verified":true,
  "sampleRate":44100,
  "channels":1,
  "durationSeconds":20,
  "dryVocalMethod":"demucs-htdemucs",
  "vocalIsolation":"model-estimated",
  "separator":{"sourceCommit":"...","modelSha256":"...","device":"cpu"},
  "slice":{"startSeconds":80,"durationSeconds":20},
  "createdAt":"ISO8601",
  "listeningAccepted":false,
  "musicalSourceVerified":false
}
```

`verified` means trusted ingestion/file/provenance/numerical processing succeeded. It **does not certify** that the source is actually singing the requested song, contains absolutely no accompaniment, or has been heard/approved. SVC preserves and converts the supplied guide; correct original melody comes from choosing the correct source. This limitation is preserved in output metadata, never replaced by a static “song verified” flag. The original source hash is an ingestion receipt; this runtime independently verifies the actual guide hash.

Current Node preparation limits slices to1–55seconds. The runtime allows1–600seconds for future trusted producers, validates actual mono44.1kHz audio/duration agreement, and rejects silent/nonfinite input. It rejects cross-chat metadata, arbitrary file arguments, path traversal, UNC, symlinks/reparse points and hardlinks. It copies validated guide/reference into a private per-job work directory and checks the copied hashes before inference. The guide sidecar is checked again after rendering.

## One fixed approved actor

The only reference comes from `<dataRoot>/voice/voice.local.json`, identical to the speech runtime. `approved` must be exactly true. Supported reference resolution matches speech: `referenceAudioBase:'dataRoot'` with a safe relative path, or a trusted absolute reference under dataRoot/voice or `.dsh`. The actual reference SHA is used as actor identity; optional `referenceSha256` is checked. No separate singing voice bank/persona is created.

Approval/reference identity is re-read before loading, after backend construction, after inference, before publication and after publication. Revocation/reference change aborts and removes unpublished/generated artifacts. **Node must still recheck approval and route immediately before each native send**, because revocation can occur after a successful runtime response. This module does not send or decide a recipient.

## CUDA loader, license and output

`tools/seed-vc/svc_backend.py` calls the unchanged official GPL-3.0 checkout at commit `51383efd921027683c89e5348211d93ff12ac2a8`; no upstream implementation is copied into the protocol. `runtime-source.lock.json` locks73source/config/license files; the manifest hashes are pinned in this module. Official model revisions and full SHA256 are in `models.lock.json`. Both source and full model hashes are checked before rendering. The upstream GPL license is retained; this is local personal integration, not a claim that a redistributed combined product is MIT-only.

CUDA is mandatory. The previous CPU numerical failure is **not** papered over with a CPU fallback. The actual loader uses the previously tested F0-conditioned44.1kHz SVC,40steps,fp16,CFG0.7,length1.0,zero semitone shift/no automatic F0 adjustment, offline local paths and a blocked Python network interface. It audits every current trainable tensor before loading; only the known10unused legacy Wavenet keys and regenerated8192→16384position buffer are allowed. Model banners cannot corrupt JSONL. After a call, tensor/cache cleanup is attempted; the CUDA process context is only fully released when the private worker exits.

Successful output is under `<dataRoot>/voice/outbox/singing/<renderJobId>/`. Temporary reference/guide/render files are kept outside the sendable outbox under `voice/.singing-work`, then removed. Only final segments and metadata are published. Each segment is at most55seconds, using the existing shared quiet-boundary segmentation. Output is mono44.1kHz PCM24. A controlled `load_models` wrapper attaches the owned shared headroom hook to the verified BigVGAN `conv_post`, before the official final clamp; source files stay unchanged. Gain is uniform and downward only per vocoder forward block, preserving sample timing and pitch. Longer upstream renders can contain multiple blocks, so this is not a promise of one gain across an entire long song. Outer file-level0.95headroom remains as a final numerical guard. `metadata.vocoderHeadroom` reports preclamp peak, gain range and clipping counts. No accompaniment is mixed in.

Default operation retains numeric hook statistics only. Trusted diagnostic environment `WHALE_SINGING_DIAGNOSTIC_DIR` can capture bounded raw arrays only under workspace `voice-data/auditions`; this is not a model argument. Constructors and hooks are restored before diagnostic file I/O, including failure paths.

Return shape:

```json
{"file":"<first segment>","sampleRate":44100,"durationSeconds":112,"backend":"seed-vc-44k-svc",
 "segmentCount":3,"requiresSegments":true,
 "segments":[{"file":"<absolute local WAV>","name":"segment-001.wav","durationSeconds":53,"offsetSeconds":0,"sha256":"..."}],
 "metadata":{"guideId":"...","chatKey":"...","sourceUrl":"...","actorReferenceSha256":"...",
   "musicalSourceVerified":false,"listeningAccepted":false,"audioKind":"voice-converted-guide"},
 "checkpointAudit":{}}
```

**Always deliver the ordered `segments` list**, particularly when `requiresSegments` is true. `file` is only the first segment for existing native-voice shape compatibility; it is not the whole long song. Runtime success is generated audio, not a send receipt. Native delivery and its sent/failed status remain the existing Node/wxauto responsibility.

## Verification and present limit

Interface tests use tiny temporary fixtures and a fake backend; no production voice/config/GPU inference is used. They cover no-GPU status, scoped IDs/files/hashes, forbidden overrides, unapproved/revoked/changed references, changed guide metadata, silent/nonfinite source, wrong output format/timing, network denial, JSONL hygiene and complete≤55second segmentation. Hardlink coverage is skipped only if the host disallows creating a test hardlink.

Real workspace assets/status were also read with an **absent temporary DataRoot**: models/dependencies ready, reference/approval false, torch absent from `sys.modules`, and no fixture directory created. Coordinated real CUDA private-stdio trials then completed using the approved Bili actor copied into an isolated workspace DataRoot and the original Rainie Yang guide80–100seconds. Both model SHA verification and all267current tensor shapes were actually checked. The resulting19.992second mono44.1kHz segment was not sent anywhere. See `voice-data/auditions/singing-runtime-preclamp-acceptance-20261102/REPORT.md` for exact reconstruction hashes, acoustic evidence and limitations. No Droot production synthesis or tool enablement is implied.
