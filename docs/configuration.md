# 配置说明

所有配置都在你自己电脑上，**不在仓库里**。`setup/configure.py` 会帮你生成；想手动改也可以，改完重启 DeepSeek Harness 生效。

## 文件在哪

| 文件 | 内容 | 谁写的 |
|---|---|---|
| `%USERPROFILE%\.dsh\dsh-whale.json` | 只有一行：数据目录在哪 | configure.py |
| `WhaleData\config\wxauto.local.json` | 微信通道主配置（见下） | configure.py |
| `WhaleData\config\group-routes.local.json` | 已登记的群 → 会话 | 插件自动 |
| `WhaleData\config\native-images.local.json` | `enabled`：是否直接解密原图看图 | configure.py |
| `WhaleData\config\breezeblue.local.json` | BreezeBlue `apiKey` | configure.py `--breeze-key` |
| `WhaleData\config\tikhub.local.json` | TikHub `token`、`enabled` | configure.py `--tikhub-token` |
| `WhaleData\voice\voice.local.json` | 声音：`backend: "breezeblue"`、`breezeVoiceId` | configure.py `--breeze-voice` |
| `%USERPROFILE%\.modlens\config.json` | 看图：`provider: "gemini-api"`、`providers.gemini-api.apiKey` | configure.py `--gemini-key` |
| `%USERPROFILE%\.dsh\bili-whale.config.json` | 养鱼：人设、作息、B 站、分享规则 | DSH 设置 → 养鱼 |
| `%USERPROFILE%\.bili-ai\credentials.json` | B 站 cookie | DSH 设置 → 养鱼 |
| `%USERPROFILE%\.dsh\speak-up-config.json` | 主动搭话：目标会话、频率、文案 | DSH 设置 → 主动搭话 |
| `%USERPROFILE%\.dsh\profiles\desktop\cordis.patch.yml` | 各插件的高级参数（记忆检索范围等） | 手动 |

`WhaleData` 默认是 `%USERPROFILE%\WhaleData`，可以用 `-DataRoot` / `--data-root` 改。

## wxauto.local.json 里常改的字段

```jsonc
{
  "target": "主人微信名",          // 小鲸微信里看到的主人备注/昵称，必须一字不差
  "groups": ["群1", "群2"],        // 允许说话的群（群名一字不差）；空数组 = 只私聊
  "receiveImages": true,           // 看图
  "receiveVoice": true,            // 听语音、发语音条
  "receiveFiles": true,            // 收文件
  "autoGroups": {"enabled": true}, // 被拉进新群后自动登记
  "capture": {"intervalSeconds": 300} // 兜底快照间隔；平时由数据库变动即时触发
}
```

不要手动改的：`accountId`、`chatKeys`、`capture.sourceId/accountUsername`、`nativeVoiceSources`。这些是身份绑定，改错会导致收不到消息。想换主人，就用新名字重跑 `configure.py --owner`。

## 换声音

在 BreezeBlue 控制台选一个声音，拿到 `voc_...`：

```powershell
& "$env:USERPROFILE\WhaleData\runtime\.venv-wxauto\Scripts\python.exe" -X utf8 "<插件目录>\setup\configure.py" --breeze-voice voc_xxx
```

## 记忆检索范围

`dsh-memory-evolve` 的 `memory` 工具多了 `search` 动作。默认只搜记忆本身；想让她也能搜 B 站阅读报告和日记，在 `cordis.patch.yml` 的 `dsh-memory-evolve` 配置里加：

```yaml
    recallSources:
      [
        { "label": "看过的视频（B站阅读报告）", "path": "C:\\Users\\<你>\\.dsh\\whale-background-reports" },
        { "label": "日记", "path": "C:\\Users\\<你>\\.dsh\\whale-diary.md", "privateOnly": true }
      ]
```

`label` 是给模型看的来源名，`path` 必须是绝对路径（文件或目录都行），`privateOnly: true` 表示只在主人私聊里能搜到。

## B 站评论和动态

默认允许小鲸在夜间自主刷视频时评论（每晚最多 3 条）、发动态（每天最多 1 条），都会先 `--dry` 预演再 `--go`。不想要的话，在 `%USERPROFILE%\.dsh\bili-whale.config.json` 的 `share` 里加 `"autoComment": false` 和／或 `"autoDynamic": false`（保存即生效，下一晚起不再提示她评论/发动态）。
