# 出问题怎么办

先跑体检，它会逐项告诉你哪里不对、怎么修：

```powershell
node "$env:USERPROFILE\.dsh\profiles\desktop\node_modules\local-dsh-wxauto\setup\doctor.mjs"
```

## 她完全不回

1. 微信登录了吗？桌面是否锁屏？微信窗口是否被最小化到托盘？UI 自动化需要一个**可见、已登录**的主窗口。
2. 体检里「微信连接 ready」是不是 ✓？不是的话**完全退出 DSH 再打开**。第一次启动偶尔会 `initialization-failed`，再重启一次通常就好。
3. 「主人私聊：原生文字接收」没启用：去 设置 → 微信连接 点按钮。
4. `target` 和小鲸微信里显示的主人名字是否一字不差（包括 emoji、空格）。

## 能看到图又说没看到 / 看图很慢

- 体检里看图 provider 要是 `gemini-api`。没有 Gemini key 时会退回很慢的方式，或者干脆看不了。
- `gemini-2.5-flash` 对新 key 关闭了，用默认的 `gemini-flash-latest`。
- 原图是 HEVC（wxgf），需要 ffmpeg 转码；没有 ffmpeg 就只能看缩略图。

## 语音

- **收不到语音**：`receiveVoice` 要是 true，语音识别模型要在 `WhaleData\runtime\voice-data\models` 下（重跑 setup.ps1）。
- **发不出语音条**：需要 VB-CABLE，还要 BreezeBlue key 和声音 ID；体检会提示缺哪个。

## 插件更新后不工作了

更新会替换插件目录，下载的微信读取模块也会一起没掉。重跑 `setup.ps1`，配置和数据都不会丢。

## 微信更新后不工作了

UI 自动化和数据库读取都和微信版本有关。已验证的版本是 4.2.0.12。如果新版本不兼容：
- 先在体检里看是哪一项失败；
- 回到已验证版本，或者等 wxauto4 / yichen-skills 上游更新。

## 磁盘越来越大

每次抓取会生成一份解密快照，插件只保留最新 50 份和正在使用的，其余自动删除。`WhaleData\snapshots` 正常在几百 MB 以内。

## 想把她停掉

在 `WhaleData\config\wxauto.local.json` 里把 `enabled` 设成 `false`，然后重启 DSH；或者直接卸载，见 INSTALL.md。
