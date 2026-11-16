# local-dsh-wxauto · 小鲸的微信通道

DeepSeek Harness 插件。把 DSH 里的养鱼主会话（`bili-whale`）和若干群会话接到一个微信 4.x 桌面账号上。
安装、配置和排错请看仓库根目录的 [README](../../README.md)、[INSTALL](../../INSTALL.md) 和 [docs/](../../docs)。

## 它怎么工作

```
微信 4.x 桌面版
  │  ① 数据库文件一变（每 0.7 秒看一次 mtime）
  ▼
vault/capture.py ── yichen-skills 读取模块（安装时下载）── 解密本账号聊天库 → WhaleData/snapshots
  │  ② 增量导入 WhaleData/vault/whale.sqlite（历史、去重、游标）
  ▼
native_*_catalog.py ── 新文字 / 引用 / 表情 / 图片 / 语音 → 收件箱（epoch + 基线，绝不重放旧消息）
  │  ③ 合并连续消息（私聊 1.2s，群 1.8s）；图片先解密原图并用 modlens 看完再一起交给模型
  ▼
DSH 会话（同一份人设和 Memory Evolve 记忆）── 回复
  │  ④ worker.py（wxauto4）按聊天串行发送：文字分段、表情包、@、原生语音条
  ▼
微信
```

- **收**：文字、引用、表情走本地快照（原生），比 UI 轮询快且不会漏；UI 读取只作为兜底和启用前的过渡。
- **图**：`native-images.mjs` 用本机 kvcomm 密钥 + wxid 解开 `.dat` 原图（wxgf/HEVC 用 ffmpeg 转 JPG），再交给 modlens（推荐 Gemini）。
- **语音**：收到的语音条用 SenseVoice 识别；要发语音时用 BreezeBlue 合成，经 VB-CABLE 按住说话发出原生语音条。
- **记忆**：`memory-view.mjs` 把跨会话记忆按“用户 → 关键 → 普通、新的优先”注入索引；完整内容用 `memory` 工具的 `search` 查。
- **主动搭话**：`dsh-speak-up` 掷中后，这里把那一轮的回复投递到对应私聊/群。
- **抖音/小红书**：`tikhub-reader.mjs` 注册 `social_video_read` 工具（只读，默认关闭）。

## 安全边界

- 只处理配置的主人私聊和已登记的群；群里的人不能代替主人授权任何操作。
- 加好友、建群等社交动作需要主人在私聊里回复一次性批准码。
- 不开网络端口；Node 和 Python 进程之间走私有 stdin/stdout。
- 微信数据库密钥只在内存里使用，不落盘；快照只保留最近 50 份。
- 所有 `*.local.json`、快照、媒体都在数据目录里，不在插件目录、不进 git。

## 开发

```powershell
# Node 单元测试（需要 Python 的用例：设 WHALE_TEST_PYTHON 指向 .venv-wxauto 的 python.exe）
node --test *.test.mjs
# Python 单元测试
& $env:WHALE_TEST_PYTHON -X utf8 -m unittest discover -p "test*.py"
```

`*.integration.test.mjs` 和少数 `test_worker_*` 用例依赖作者本机的开发目录布局，在干净环境里会失败，可以忽略。

更细的技术文档：`DATA-PATHS.md`、`GROUP-TEXT-RUNTIME.md`、`ATTACHMENT-API.md`、`SOCIAL-*.md`、`SINGING_RUNTIME.md`、`SONG-GUIDE-API.md`、`sync-manager.README.md`、`vault/README.md`。
