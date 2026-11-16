# dsh-whale · 微信里的小鲸

把 DeepSeek Harness（DSH）里的 agent 接到**你自己的微信**上，做成一个会聊天、会看图、会听语音、会发语音条、会主动找你说话、会自己刷 B 站并记住看过什么的陪伴角色。

> 包含的 `packages/dsh-speak-up` **派生自 <https://github.com/Yuuyuko-uu/dsh-speak-up>，原作者 Yuuyuko-uu**（按其「插件许可 1.2」再分发，改动见该目录 README）；`dsh-bili-whale` 派生自 Alterssss/dsh-bili-whale，`dsh-memory-evolve` 派生自 csyangwen/dsh-memory-evolve（均 MIT）。

> **给 DeepSeek Harness 的话**：如果用户把这个仓库链接发给了你，请按 [INSTALL.md](INSTALL.md) 一步一步帮用户安装和配置。那份文档就是写给你执行的。

---

## ⚠️ 先读这个

- **封号风险**：本项目用 UI 自动化（wxauto4）操作微信桌面版。这违反微信用户协议，**账号可能被限制或封禁**。请用一个**单独的小号**当“小鲸”，别用你的主号。
- **只读你自己的数据**：为了又快又准地收消息、看原图、听语音，插件会在本机读取**正在运行的微信**的数据库密钥，解密**这个小号自己的**聊天库到本机数据目录。只能用于你本人有权访问的账号。
- **数据不出本机**：聊天快照、记忆、日记、配置、所有 API key 都只存在 `%USERPROFILE%\WhaleData`（和 `~/.dsh`）。本仓库不收集任何东西。会联网的只有你自己配置的服务：DeepSeek（DSH 本身）、Gemini（看图）、BreezeBlue（语音合成）、B 站、TikHub（可选）。
- **解密模块不在本仓库里**：读取微信数据库的代码来自 [mcncarl/yichen-skills](https://github.com/mcncarl/yichen-skills)。它的许可证（个人学习与非商业使用）**不允许公开再分发**，所以本仓库只存它的链接和固定版本的哈希。安装脚本会在**你自己的电脑上**从上游下载并逐个校验。请遵守它的许可：**仅个人、非商业使用**。
- 本项目按“现状”提供，没有任何担保。用了就是你自己承担风险。

## 能做什么

| 功能 | 说明 | 需要 |
|---|---|---|
| 💬 私聊 / 群聊 | 主人私聊 + 白名单群；群里自己判断要不要接话，会 @ 人 | 必装 |
| 🖼️ 看图 | 直接解密本地原图（不点 UI），用 Gemini 看；“这个”“再看看”能接上 | Gemini API key（免费额度够用） |
| 🎙️ 听语音 | 本地 SenseVoice 识别语音条 | 自动下载模型（≈230 MB） |
| 🔊 发语音条 | BreezeBlue 云端合成 + 虚拟声卡按住说话发原生语音条 | BreezeBlue key + VB-CABLE |
| 🧠 记忆 | Memory Evolve：跨私聊/群共享的记忆，能检索，不会“忘了” | 必装 |
| 👋 主动搭话 | 按作息掷骰子，私聊和群各自独立决定要不要开口 | 可选 |
| 📺 刷 B 站 | 晚上自己刷视频、看画面、写日记、可以评论/发动态，看过的进记忆 | B 站小号 cookie + ffmpeg |
| 🎵 唱歌 | 本地 Seed-VC 翻唱，用同一个声线 | 显卡 + ≈6 GB 模型，见 [docs/singing.md](docs/singing.md) |
| 📱 抖音 / 小红书 | 通过 TikHub API 只读刷（默认关闭） | TikHub token |

## 需要准备

- Windows 10/11 x64，**微信 4.x 桌面版**（已在 4.2.0.12 验证），登录小鲸用的那个小号，桌面保持解锁
- DeepSeek Harness 桌面版 0.2.1 或 0.2.2，装好后至少打开过一次（0.2.1-rc.5 实机跑过；0.2.2-rc.3 核对过插件用到的全部接口都还在。不用降级）
- [git](https://git-scm.com/download/win)（DSH 从 GitHub 装插件要用）
- 下面这些安装脚本可以自动装：Python 3.12、ffmpeg、语音模型
- 需要你自己申请的（都可以之后再填）：
  - **Gemini API key**：<https://aistudio.google.com/apikey>，看图用
  - **BreezeBlue API key + 声音 ID**：<https://docs.breezeblue.ai/>，说话用
  - **VB-CABLE**：<https://vb-audio.com/Cable/>，发语音条用，需要管理员权限，装完重启电脑
  - **B 站小号 cookie**：在 DSH「设置 → 养鱼」里填
  - **TikHub token**（可选）：<https://docs.tikhub.io/>

## 安装

### 最省事：让 DSH 帮你装

把这个仓库的链接发给 DeepSeek Harness，说“帮我安装这个”。它会读 [INSTALL.md](INSTALL.md)，然后：
1. 装插件；
2. 跑安装脚本；
3. 问你主人的微信名和各个 key；
4. 最后让你重启 DSH、点一个按钮。

### 自己装

见 [INSTALL.md](INSTALL.md)，一共 4 步，每步都有能直接复制的命令。

## 配置与日常使用

- 怎么改配置、每个文件是干嘛的：[docs/configuration.md](docs/configuration.md)
- 出问题先跑体检：`node <插件目录>\setup\doctor.mjs`，然后看 [docs/troubleshooting.md](docs/troubleshooting.md)
- 人设：在 DSH「设置 → 养鱼」里写，模板见 [templates/persona.example.md](templates/persona.example.md)

## 仓库结构

```
packages/
  dsh-wxauto/         微信通道（本项目主体）：收发、图片、语音、快照、记忆视图、TikHub
    setup/            setup.ps1 · configure.py · doctor.mjs · fetch-vendor.mjs
  dsh-bili-whale/     养鱼（fork 自 Alterssss/dsh-bili-whale）：人设、作息、B 站、评论、动态、视频记忆
  dsh-memory-evolve/  记忆（fork 自 csyangwen/dsh-memory-evolve）：加了跨会话记忆检索
  dsh-speak-up/       主动搭话（fork 自 Yuuyuko-uu/dsh-speak-up）：多目标独立掷骰、分会话文案
docs/  templates/
```

## 致谢与许可

- 本项目自己的代码（`packages/dsh-wxauto`、安装脚本、文档）：MIT，见 [LICENSE](LICENSE)。
- `packages/dsh-bili-whale`、`packages/dsh-memory-evolve`：保留原作者的 MIT 许可（各目录 `LICENSE`）。
- `packages/dsh-speak-up`：**Yuuyuko-uu 插件许可 1.2**（目录内 `LICENSE`，原文保留），不是 MIT。
- 第三方组件：见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
- yichen-skills：**不随本仓库分发**，用户自行下载，遵守其个人非商业许可。
