# 唱歌（可选，进阶）

唱歌用本地 **Seed-VC** 做歌声转换：先取原曲，用 Demucs 分离人声，再转成小鲸的声线，最后分段发原生语音条。整个过程在本机显卡上跑。

**门槛**：
- NVIDIA 显卡，8 GB 以上显存，CUDA 12.x；
- 约 6 GB 模型，加两个独立的 Python 3.11 环境；
- 还需要先把语音（VB-CABLE + BreezeBlue）配好，唱歌和说话共用同一个声线参考。

这一套**没有在新机器上做过一键安装验证**，适合愿意折腾的朋友。不装也不影响其他任何功能：她被要求唱歌时会说现在唱不了。

## 目录约定

都在 `WhaleData\runtime`（即 `wxauto.local.json` 的 `runtimeRoot`）下：

```
runtime\
  .venv-seed-vc\           Python 3.11：torch(cu124) + seed-vc 依赖 + yt-dlp
  .venv-vocal-separator\   Python 3.11：demucs
  voice-data\models\seed-vc\    Seed-VC 权重
  voice-data\models\demucs\     Demucs 权重
```

## 步骤概要

1. 装 Python 3.11 x64（`winget install Python.Python.3.11 --scope user`）。
2. 建环境：
   ```powershell
   $rt = "$env:USERPROFILE\WhaleData\runtime"
   py -3.11 -m venv "$rt\.venv-seed-vc"
   & "$rt\.venv-seed-vc\Scripts\python.exe" -m pip install torch==2.4.0 torchaudio==2.4.0 --index-url https://download.pytorch.org/whl/cu124
   & "$rt\.venv-seed-vc\Scripts\python.exe" -m pip install librosa==0.10.2 soundfile==0.12.1 transformers==4.46.3 yt-dlp
   py -3.11 -m venv "$rt\.venv-vocal-separator"
   & "$rt\.venv-vocal-separator\Scripts\python.exe" -m pip install demucs
   ```
3. 按 [Plachtaa/seed-vc](https://github.com/Plachtaa/seed-vc) 的说明，把歌声模型下载到 `voice-data\models\seed-vc`。
4. 声线参考：`WhaleData\voice\singing.local.json` 指向一段 5–10 秒的干净人声。BreezeBlue 用户可以用 BreezeBlue 合成一段，放到 `WhaleData\voice\references\` 里。
5. 重启 DSH，在私聊里说“唱一下 xxx”。

详细的运行时契约见插件里的 `SINGING_RUNTIME.md` 和 `SONG-GUIDE-API.md`。
