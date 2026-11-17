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
- **对方收到的语音条是几秒空白**：VB-CABLE 有两端，插件把声音放进 `CABLE Input`，微信要从 `CABLE Output` 录。到 Windows「声音设置 → 输入」把默认录音设备设成 **CABLE Output**（或者在微信设置里把麦克风选成它）。插件不会替你改系统音频设备，「发送成功」只代表按住说话的动作做完了。
- **语音没发出来但文字到了**：语音合成失败（例如缺参考音频、合成服务没起来）时，那一段会改成文字发出，不会整条丢掉。原因记在 `status.local.json` 的 `lastVoiceFallback` 里。

## 插件更新后不工作了

更新会替换插件目录，下载的微信读取模块也会一起没掉。重跑 `setup.ps1`，配置和数据都不会丢。

## 微信更新后不工作了

UI 自动化和数据库读取都和微信版本有关。已验证的版本是 4.2.0.12。如果新版本不兼容：
- 先在体检里看是哪一项失败；
- 回到已验证版本，或者等 wxauto4 / yichen-skills 上游更新。

## 状态一直是 wechat-not-ready / expected-one-main-window

插件要找到**正好一个**微信主窗口（UI 自动化类名 `mmui::MainWindow`）。按顺序排查：

1. 微信要用普通权限启动：托盘右键「退出」，从开始菜单打开（不要从管理员 PowerShell 里启动，安装程序装完自动拉起的微信也要重开一次）。
2. 关掉微信里所有弹窗和独立聊天窗口，主窗口不要最小化。
3. 跑只读探测，把输出原样发出来求助（它只列窗口，不点击、不写文件）：

```powershell
$p = "$env:USERPROFILE\.dsh\profiles\desktop\node_modules\local-dsh-wxauto"
& "$env:USERPROFILE\WhaleData\runtime\.venv-wxauto\Scripts\python.exe" -X utf8 "$p\setup\wx-window-probe.py"
```

正常时第一段里会有一行 `uia='mmui::MainWindow'`。

## 群里不回 / 群一直是 blocked

群不用手动设置：在群里 @ 她，插件会自动登记这个群、建群会话，并切到原生文字接收。设置页的「减少头像取名操作」按钮一般用不到。

- **不要手改** `WhaleData\config\group-routes.local.json`、`wxauto.local.json` 里的 `groupSessions` / `chatKeys`，也不要手建 `~/.dsh/sessions` 下的会话目录。切到原生文字后，插件会持续核对群的会话 ID 和聊天 ID；和登记的不一致，就会为了安全把这个群停成 `blocked`，不再收消息。
- 旧版本在主人还没和小鲸私聊过时，建群会话会报 `session/not-found`；新版本已修，更新插件即可。
- 群已经变成 `blocked`：设置页 → 群聊文字接收 → 选中这个群 → 「读取群接收状态」，会显示**被停的原因**（例如「会话或聊天编号和切换时登记的不一致」）。先按原因处理（比如把手改过的文件恢复原样），再点「**复位该群**」：这个群的原生接收记录会整体移到 `WhaleData\config\native-group-text-reset\` 备份（不删除），群回到界面接收，之后在群里 @ 她会重新自动切换。
- 如果原因是「群登记文件读不懂」（`registry-unverified`），复位按钮会拒绝：这时要先修好 `group-routes.local.json`，或者把整个 `WhaleData\config\native-group-text\` 移走备份后完全退出 DSH 再打开。
- 提 issue 时把原因代码（括号里那串英文）一起贴上。

## 磁盘越来越大

每次抓取会生成一份解密快照，插件只保留最新 50 份和正在使用的，其余自动删除。`WhaleData\snapshots` 正常在几百 MB 以内。

## 想把她停掉

在 `WhaleData\config\wxauto.local.json` 里把 `enabled` 设成 `false`，然后重启 DSH；或者直接卸载，见 INSTALL.md。
