# Third-party notices

## Included in this repository (modified forks)

| Directory | Upstream | License |
|---|---|---|
| `packages/dsh-bili-whale` | <https://github.com/Alterssss/dsh-bili-whale> (based on 0.4.6) | MIT, © 2026 ALTERSSSS |
| `packages/dsh-memory-evolve` | <https://github.com/csyangwen/dsh-memory-evolve> (based on 0.1.0) | MIT, © 2026 dsh-external |
| `packages/dsh-speak-up` | <https://github.com/Yuuyuko-uu/dsh-speak-up> (based on 0.2.7), original author **Yuuyuko-uu** | Yuuyuko-uu 插件许可 1.2, kept verbatim in that directory |

Each fork's README lists what was changed.

## Downloaded by the user at install time (NOT included here)

### mcncarl/yichen-skills — WeChat local reader modules
- Upstream: <https://github.com/mcncarl/yichen-skills>, pinned commit `d176934a3b201a6ae0bfe94daafdf67a1d8860e3`
- License: **Personal Learning and Non-Commercial Use License**. It does not allow public redistribution, so this repository ships only `vault/vendor/vendor-lock.json` (paths + SHA256) and a loader. `setup/fetch-vendor.mjs` downloads the 17 files from upstream on the user's own machine and verifies every hash.
- The downloaded files include upstream's own `LICENSE.yichen.txt`, `THIRD_PARTY_NOTICES.md` and the MIT licenses of the code it adapts:
  - [TANGandXUE/wcdb-key-tool](https://github.com/TANGandXUE/wcdb-key-tool) (MIT)
  - [freanocank/wechat-decrypt](https://github.com/freanocank/wechat-decrypt) (MIT)
- Use it for personal, non-commercial purposes only, on accounts you are authorized to access.

### Installed from PyPI / npm / model hubs
- [wxauto4](https://pypi.org/project/wxauto4/) (Cluic) — WeChat 4.x UI automation, installed via pip
- zstandard, pywin32, comtypes, pillow, psutil, httpx, tenacity, pyperclip, colorama — pip
- sherpa-onnx, numpy, scipy, soundfile, sounddevice, pysilk-mod, av — pip (voice, optional)
- SenseVoice int8 model (csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17) and silero VAD — downloaded by setup.ps1
- npm: `@xmanrui/dsh-im`, `@liustack/modlens`, `dsh-meme`, `qrcode`

## Ideas and methods
- WeChat 4 image `.dat` (V2) decoding — key derivation from the local `kvcomm` key file plus wxid, following the method used in yichen-skills' `group_image_media.py`. Reimplemented in `packages/dsh-wxauto/native-images.mjs`.
