# Unified data paths and copy-only migration

Python: `resolve_data_paths(config)` returns `Path` values. Node:
`dataPaths(config)` returns absolute strings. Both use `config.dataRoot`, default
`<DATA_ROOT>`, and expose `root`, `manifest`, `config`, `vault`, `database`,
`media`, `memory`, `persona`, `voice`, `outbox`, `snapshots`, and `backups`.

`relative_data_path` / `relativeDataPath` produce root-relative forward-slash
paths; `absolute_data_path` / `absoluteDataPath` reverse this after containment
checks. Do not resolve these paths relative to the working directory. Reparse
and symlink roots, traversal, UNC and absolute values in relative-path fields
are rejected. Resolving or reading paths does not create a data root.

The manifest format is `whale-data`, `schemaVersion` 1 or 2, with canonical
relative `paths`; old manifests containing only `database` are accepted. New
roots use schema 2. Existing manifests are never downgraded or silently rewritten.
`initialize_data_root` is the explicit directory-creation entry point; SQLite's
own schema migrations remain the responsibility of `vault.store`.

## Migration

Run the CLI with the wxauto Python environment. Default mode is a read-only plan:

```text
python migrate-data.py --dry-run --data-root <DATA_ROOT>
python migrate-data.py --execute --data-root <DATA_ROOT>
```

`--home` and `--project-root` support test fixtures or explicitly selected source
locations. The CLI copies memories, persona/history, both diaries, workspace
AGENTS/MEMORY, retained wx-media, selected voice settings and the exact referenced
audio, plus adapter config and outgoing claims. It does not copy model weights,
WeChat account directories, login credentials or the original runtime environment.

Every destination is hash-checked. Different preexisting bytes are a conflict;
identical files are retained. Source files are never deleted or edited. The final
receipt contains old absolute path → new absolute path mappings and file hashes.
No active configuration is changed: activation belongs to the root integration.

Exported `config/wxauto.local.json` contains the new data root, image directory and
claim file. It deliberately retains the old workspace and adds
`workspaceMigrationTarget=config/workspace` as an absolute destination hint;
changing the active cwd requires migrating Harness session/project-memory mapping
first. Python/Codex executable and meme runtime directories still need runtime
binding on another computer.

Exported `voice/voice.local.json` uses root-relative `referenceAudio` and
`referenceAudioBase='dataRoot'`. Speech runtime integration must resolve that
reference against `dataRoot`, while model weights remain in their separately
configured model directory. Persona and workspace instruction path references to
the old persona/memory locations are rewritten in the exported copies.

## Ordinary file messages

`receiveFiles=true` enables worker file receive/send. `attachmentDirectory`
defaults to `<dataRoot>/media/attachments`; child folders use the first 16 hex
characters of SHA256(exact target name). `files` metadata accompanies incoming
message events; content is unknown until a reader succeeds.

The worker durably records received files in
`<dataRoot>/config/attachment-receipts.local.json` before publishing them. The
registry uses relative paths and survives restart. Node sends with
`{op:'send-attachment', id, replyId, target, filepath}` inside the existing reply
transaction. The filepath must be registered for this chat or located inside
`<dataRoot>/outbox/<chat_key>`. Root selection and registered paths never come from
model arguments. `send-file` remains restricted to existing meme roots.
