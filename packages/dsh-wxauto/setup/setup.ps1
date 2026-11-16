<#
.SYNOPSIS
  dsh-whale 一键安装：Python 环境、微信读取模块、ffmpeg、语音模型，然后写本机配置。

.DESCRIPTION
  在 DeepSeek Harness 里装好插件之后运行（DSH 的 agent 会替你跑）：

    powershell -ExecutionPolicy Bypass -File "<插件目录>\setup\setup.ps1" -Owner "主人微信名" -InstallMissing

  所有东西都装在本机：
    数据（聊天快照、记忆、配置、密钥）  %USERPROFILE%\WhaleData
    Python 环境和模型                  %USERPROFILE%\WhaleData\runtime
  不会上传任何聊天记录或密钥。

.PARAMETER Owner        小鲸这个微信号里看到的主人备注/昵称/微信号（第一次必填）
.PARAMETER Groups       允许小鲸说话的群名，逗号分隔（可选）
.PARAMETER GeminiKey    Google AI Studio key，看图用（可选，推荐）
.PARAMETER BreezeKey    BreezeBlue key（brz_…），说话用（可选）
.PARAMETER BreezeVoice  BreezeBlue 声音 ID（voc_…）
.PARAMETER TikhubToken  TikHub token，抖音/小红书只读（可选）
.PARAMETER NoVoice      不装语音（不需要 VB-CABLE 和语音模型）
.PARAMETER InstallMissing  缺 Python 3.12 / ffmpeg 时用 winget 自动装（用户级）
.PARAMETER SkipConfigure   只装环境，不写配置
#>
param(
  [string]$Owner,
  [string]$Groups,
  [string]$DataRoot = $(if ($env:WHALE_DATA_ROOT) { $env:WHALE_DATA_ROOT } else { Join-Path $env:USERPROFILE 'WhaleData' }),
  [string]$DshProfile = $(if ($env:DSH_PROFILE) { $env:DSH_PROFILE } else { 'desktop' }),
  [string]$GeminiKey,
  [string]$BreezeKey,
  [string]$BreezeVoice,
  [string]$TikhubToken,
  [switch]$NoVoice,
  [switch]$InstallMissing,
  [switch]$SkipConfigure
)
$ErrorActionPreference = 'Stop'
$Plugin = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $DataRoot 'runtime'
$DshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }
function Step($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Ok($t) { Write-Host "  ✓ $t" -ForegroundColor Green }
function Warn($t) { Write-Host "  ! $t" -ForegroundColor Yellow }
function Die($t) { Write-Host "  ✗ $t" -ForegroundColor Red; exit 1 }

Step '检查系统'
if ($env:OS -ne 'Windows_NT') { Die '只支持 Windows（微信 4.x 桌面版 + UI 自动化）' }
if (-not [Environment]::Is64BitOperatingSystem) { Die '需要 64 位 Windows' }
$wx = Get-Process -Name 'Weixin','WeChat' -ErrorAction SilentlyContinue
if ($wx) { Ok '微信正在运行' } else { Warn '微信没在运行：配置那一步需要先登录小鲸用的微信号' }
New-Item -ItemType Directory -Force $Runtime | Out-Null

Step 'Node（用 DSH 自带的）'
$Node = Join-Path $DshHome 'dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe'
if (-not (Test-Path $Node)) { $Node = (Get-Command node -ErrorAction SilentlyContinue).Source }
if (-not $Node) { Die '找不到 Node：请先安装并打开一次 DeepSeek Harness' }
Ok $Node

Step 'Python 3.12'
function Find-Python312 {
  $py = Get-Command py -ErrorAction SilentlyContinue
  if ($py) { try { $p = & py -3.12 -c 'import sys;print(sys.executable)' 2>$null; if ($LASTEXITCODE -eq 0 -and $p) { return $p.Trim() } } catch {} }
  $cands = @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", "$env:ProgramFiles\Python312\python.exe")
  $cands += @(Get-Command python -All -ErrorAction SilentlyContinue | Where-Object { $_.Source -notmatch 'WindowsApps' } | ForEach-Object { $_.Source })
  foreach ($c in $cands) {
    if (-not (Test-Path $c)) { continue }
    try { $v = & $c -c 'import sys,struct;print(sys.version_info[:2]==(3,12) and struct.calcsize(\"P\")==8)' 2>$null } catch { continue }
    if ($v -and $v.Trim() -eq 'True') { return $c }
  }
  return $null
}
$Base = Find-Python312
if (-not $Base -and $InstallMissing) {
  Warn '没找到 Python 3.12，用 winget 装（用户级）'
  winget install --id Python.Python.3.12 -e --scope user --accept-source-agreements --accept-package-agreements --silent | Out-Host
  $Base = Find-Python312
}
if (-not $Base) { Die '需要 CPython 3.12 x64（微信快照读取只认 3.12）。加 -InstallMissing 自动装，或从 python.org 装 3.12' }
Ok $Base

Step '小鲸的 Python 环境 (.venv-wxauto)'
$Venv = Join-Path $Runtime '.venv-wxauto'
$Py = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path $Py)) { & $Base -m venv $Venv; if ($LASTEXITCODE) { Die '创建 venv 失败' } }
& $Py -m pip install --disable-pip-version-check -q -r (Join-Path $Plugin 'requirements.txt'); if ($LASTEXITCODE) { Die 'pip 安装失败（网络？可以设 PIP_INDEX_URL 用国内镜像）' }
Ok 'wxauto4 / zstandard 等已安装'

Step '微信本地读取模块（从 yichen-skills 上游下载并校验）'
& $Node (Join-Path $PSScriptRoot 'fetch-vendor.mjs') --plugin $Plugin; if ($LASTEXITCODE) { Die '下载或校验失败；可以稍后重跑，或设置代理' }

Step 'ffmpeg（看视频、转图片用）'
$ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
$biliFf = Join-Path (Split-Path $Plugin) 'dsh-bili-whale\tools\lib\ffmpeg.mjs'
if (-not $ff -and (Test-Path $biliFf)) {
  $p = & $Node -e "import('file:///' + process.argv[1].replace(/\\/g,'/')).then(m=>console.log(m.ffmpegPath()||'')).catch(()=>{})" $biliFf 2>$null
  if ($p -and (Test-Path $p.Trim())) { $ff = [pscustomobject]@{ Source = $p.Trim() } }
}
if (-not $ff -and $InstallMissing) {
  winget install --id Gyan.FFmpeg -e --scope user --accept-source-agreements --accept-package-agreements --silent | Out-Host
  $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
  $ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
}
if ($ff) { Ok $ff.Source } else { Warn '没有 ffmpeg：图片原图转换和 B 站看画面会失败。加 -InstallMissing 或手动装后重开 DSH' }

if (-not $NoVoice) {
  Step '语音（收：SenseVoice 识别；发：VB-CABLE 虚拟声卡 + BreezeBlue 合成）'
  $VVenv = Join-Path $Runtime '.venv-whale-voice'
  $VPy = Join-Path $VVenv 'Scripts\python.exe'
  if (-not (Test-Path $VPy)) { & $Base -m venv $VVenv }
  & $VPy -m pip install --disable-pip-version-check -q -r (Join-Path $Plugin 'requirements-voice.txt'); if ($LASTEXITCODE) { Die '语音环境 pip 安装失败' }
  $Models = Join-Path $Runtime 'voice-data\models'
  $files = @(
    @{ dir = 'sensevoice-int8'; name = 'model.int8.onnx'; url = 'https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/model.int8.onnx' },
    @{ dir = 'sensevoice-int8'; name = 'tokens.txt'; url = 'https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/tokens.txt' },
    @{ dir = 'vad'; name = 'silero_vad.onnx'; url = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx' }
  )
  foreach ($f in $files) {
    $dest = Join-Path (Join-Path $Models $f.dir) $f.name
    if (Test-Path $dest) { continue }
    New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
    Write-Host "  下载 $($f.dir)/$($f.name) …"
    $mirror = if ($env:HF_ENDPOINT -and $f.url -like 'https://huggingface.co/*') { $f.url.Replace('https://huggingface.co', $env:HF_ENDPOINT.TrimEnd('/')) } else { $f.url }
    curl.exe -fL --retry 3 -o "$dest.part" $mirror; if ($LASTEXITCODE) { Remove-Item "$dest.part" -ErrorAction SilentlyContinue; Die "下载失败：$($f.name)（国内可以先设 `$env:HF_ENDPOINT='https://hf-mirror.com'）" }
    Move-Item "$dest.part" $dest
  }
  Ok '语音识别模型就绪'
  $cable = Get-CimInstance Win32_SoundDevice -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'VB-Audio|CABLE' }
  if ($cable) { Ok 'VB-CABLE 已安装' } else { Warn '没检测到 VB-CABLE：发原生语音条需要它。到 https://vb-audio.com/Cable/ 下载安装（需要管理员，装完重启电脑）' }
}

if ($SkipConfigure) { Write-Host "`n环境装好了。之后运行 configure：`n  & '$Py' -X utf8 '$PSScriptRoot\configure.py' --owner '主人微信名'"; exit 0 }

Step '写本机配置'
$argsList = @('-X', 'utf8', (Join-Path $PSScriptRoot 'configure.py'), '--data-root', $DataRoot, '--runtime-root', $Runtime, '--plugin', $Plugin, '--dsh-profile', $DshProfile)
if ($Owner) { $argsList += @('--owner', $Owner) }
if ($Groups) { $argsList += @('--groups', $Groups) }
if ($GeminiKey) { $argsList += @('--gemini-key', $GeminiKey) }
if ($BreezeKey) { $argsList += @('--breeze-key', $BreezeKey) }
if ($BreezeVoice) { $argsList += @('--breeze-voice', $BreezeVoice) }
if ($TikhubToken) { $argsList += @('--tikhub-token', $TikhubToken) }
if ($NoVoice) { $argsList += '--no-voice' }
& $Py @argsList
if ($LASTEXITCODE) { exit $LASTEXITCODE }

Step '体检'
& $Node (Join-Path $PSScriptRoot 'doctor.mjs')
