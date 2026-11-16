# 安装说明（写给 DeepSeek Harness 的 agent，也写给人）

> **Agent 注意**
> - 按顺序执行，每一步看结果再继续；失败就把错误原文给用户看，不要猜。
> - 需要用户提供的信息（主人微信名、API key）**向用户要**，不要编。
> - key 只通过命令行参数传给 `configure.py`。它会写进用户本机的 `WhaleData\config\*.local.json`，**不要把 key 写进聊天记录以外的任何地方，也不要回显**。
> - 下面所有命令都在 Windows PowerShell 里运行。
> - 需要管理员权限的只有 VB-CABLE（可选），让用户自己装。
> - **DSH 版本**：0.2.1 和 0.2.2 都支持，**不要因为版本号让用户降级或停止安装**。DSH 本身不检查插件版本；`@xmanrui/dsh-im` 的 package.json 里 `dsh.compatibility` 只列到 0.2.1，那只是作者测过的版本，不是限制。只有真的报错（插件加载失败、接口报错）才算不兼容，把错误原文给用户看。

仓库：`github:snowoftirisf/dsh-whale`（下文的 `REPO`）

## 第 0 步：确认前提

```powershell
git --version          # 没有就让用户装 https://git-scm.com/download/win ，装完重开 DSH
Get-Process Weixin,WeChat -ErrorAction SilentlyContinue | Select Name   # 微信 4.x 要在运行并登录小鲸的号
```

告诉用户：这个项目会自动化操作微信，有封号风险，建议用小号；并且会在本机解密这个号自己的聊天库。用户同意后再继续。

## 第 1 步：装插件（7 个）

本仓库 4 个（各自是仓库里的一个子目录）+ npm 上 3 个依赖。逐条运行：

```powershell
dsh plugin --profile desktop add "github:snowoftirisf/dsh-whale#path:/packages/dsh-bili-whale"
dsh plugin --profile desktop add "github:snowoftirisf/dsh-whale#path:/packages/dsh-memory-evolve"
dsh plugin --profile desktop add "github:snowoftirisf/dsh-whale#path:/packages/dsh-speak-up"
dsh plugin --profile desktop add "github:snowoftirisf/dsh-whale#path:/packages/dsh-wxauto"
dsh plugin --profile desktop add @xmanrui/dsh-im@5.9.0 @liustack/modlens@3.27.2 dsh-meme
```

- `dsh` 不在 PATH 里时，用 DSH 设置 → 插件 → 安装，把引号里的内容逐条填进去。
- 报 `ERR_PNPM_GIT_FETCH_FAILED` = 没装 git。
- 如果用户以前装过官方的 `dsh-bili-whale` / `dsh-memory-evolve` / `dsh-speak-up`，上面会把它们换成本仓库的版本（同名、兼容原配置）。

## 第 2 步：跑安装脚本

先向用户要这几样（没有的可以留空，之后再补）：

| 参数 | 问用户 | 必填 |
|---|---|---|
| `-Owner` | “小鲸这个微信号里，主人的备注或昵称是什么？（要和小鲸微信里显示的完全一样）” | ✅ |
| `-Groups` | “允许小鲸在哪些群说话？群名用逗号隔开。” | 否 |
| `-GeminiKey` | “Gemini API key（看图用，https://aistudio.google.com/apikey）” | 推荐 |
| `-BreezeKey` / `-BreezeVoice` | “BreezeBlue 的 key（brz_ 开头）和声音 ID（voc_ 开头）” | 想发语音就要 |
| `-TikhubToken` | “TikHub token（刷抖音/小红书，可选）” | 否 |

然后运行（插件目录就是第 1 步装好的 `local-dsh-wxauto`）：

```powershell
$plugin = (Get-Item "$env:USERPROFILE\.dsh\profiles\desktop\node_modules\local-dsh-wxauto").FullName
powershell -NoProfile -ExecutionPolicy Bypass -File "$plugin\setup\setup.ps1" -InstallMissing -Owner "主人微信名" -Groups "群1,群2" -GeminiKey "..." -BreezeKey "brz_..." -BreezeVoice "voc_..."
```

它会依次：
1. 装 Python 3.12，建 `WhaleData\runtime\.venv-wxauto`；
2. 从 yichen-skills 上游下载微信读取模块，并校验 SHA256；
3. 装 ffmpeg；
4. 装语音环境和识别模型；不要语音就加 `-NoVoice`；
5. 找到小鲸的微信号，拍第一份聊天快照，在联系人里找到主人，写配置；
6. 跑体检。

常见失败：
- **“在微信联系人里没找到”**：让主人先给小鲸发一条消息，或者确认名字一字不差，然后**只重跑这一步**：
  ```powershell
  & "$env:USERPROFILE\WhaleData\runtime\.venv-wxauto\Scripts\python.exe" -X utf8 "$plugin\setup\configure.py" --owner "主人微信名"
  ```
- **下载慢或失败（国内网络）**：先设镜像再重跑：
  ```powershell
  $env:PIP_INDEX_URL='https://pypi.tuna.tsinghua.edu.cn/simple'
  $env:HF_ENDPOINT='https://hf-mirror.com'
  ```
- **没检测到 VB-CABLE**：让用户自己去 https://vb-audio.com/Cable/ 装，装完重启电脑。不装也能聊天，只是不能发语音条。

## 第 3 步：重启 DSH，然后在设置里点按钮

1. 让用户**完全退出** DeepSeek Harness（托盘图标也退出），然后重新打开。
2. 打开 **设置 → 养鱼**：
   - 填 B 站小号的 `SESSDATA`、`bili_jct`、`buvid3`：F12 → Application → Cookies，复制那三个值，不要用 `document.cookie`；
   - 填人设，可以从 `templates/persona.example.md` 改；
   - 点**保存**，再点「依赖体检」。
3. 打开 **设置 → 微信连接**：
   - 主人私聊：先点「读取文字接收状态」，再点 **「切换为原生文字接收」**；
   - 每个群：先在小鲸的微信里被拉进群，或者在群里说句话让她自动登记；然后在「群聊文字接收」里选这个群，点「读取群接收状态」，再点 **「减少头像取名操作」**（意思是切到原生接收）；
   - 切换前别在微信里正在来回发消息，它要等聊天安静几秒才会切。
4. 想让她主动找人说话：**设置 → 主动搭话**，在「目标会话」里勾上主人私聊（和群）。

## 第 4 步：验证

```powershell
node "$plugin\setup\doctor.mjs"
```

全部 ✓ 后，让主人用自己的微信给小鲸发：
- 一句话：应该 5–15 秒内回复；
- 一张图加一句“这是什么”：会在同一轮里看图回答；
- 一条语音（如果装了语音）。

## 更新

插件更新（重新 `add` 同一行）后，插件目录会被替换，**需要重跑第 2 步**（会重新下载读取模块；配置和数据不受影响）。

## 卸载

```powershell
dsh plugin --profile desktop remove local-dsh-wxauto dsh-speak-up dsh-memory-evolve dsh-bili-whale
```

数据在 `%USERPROFILE%\WhaleData`、`%USERPROFILE%\.dsh\dsh-whale.json`、`%USERPROFILE%\.modlens`，需要的话手动删除。
