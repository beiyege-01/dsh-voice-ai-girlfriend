# OmniVoice (WSL2) 一键启动 —— 新手向说明
#
# 一句话：这个脚本会在 WSL2 里把 OmniVoice 语音引擎跑起来（监听 9877），
#         再把 Windows 这边的语音桥接到它上面。你只要双击 cmd 或跑一条命令。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File bridge\start-omnivoice-wsl.ps1
#   powershell ... -File bridge\start-omnivoice-wsl.ps1 -Model /mnt/e/models/OmniVoice-bf16
#
# 它做 5 件事（每步都打印结果，看到绿色 [OK] 就是这步过了）：
#   第 1 步  确认你有 WSL2（Windows 里的 Linux 子系统）
#   第 2 步  拿到 WSL 的内部 IP
#   第 3 步  第一次运行：把仓库自带的两个服务脚本装进 WSL 的 ~/omnivoice-wsl
#   第 4 步  启动 OmniVoice，并等它就绪（第一次 1~3 分钟；没本地模型会先下载约 10GB）
#   第 5 步  把地址写进 bridge-config.json，再拉起语音桥
#
# ⚠ 本脚本不写死用户名和盘符（2026-11-02 改），别人 clone 下来直接能跑。

param(
  [string]$Model = "",                  # 模型路径。留空 = 自动找本地，找不到就从网上下
  [string]$Distro = "Ubuntu-22.04"      # 你的 WSL 发行版名字（wsl -l -v 可以看）
)

$ErrorActionPreference = "Continue"

function Show-Header {
  Write-Host ""
  Write-Host "  ============================================================" -ForegroundColor Cyan
  Write-Host "   OmniVoice 语音引擎  一键启动（WSL2）" -ForegroundColor Cyan
  Write-Host "  ============================================================" -ForegroundColor Cyan
  Write-Host "   它会帮你：检查环境 -> 装文件 -> 起服务 -> 接通语音桥" -ForegroundColor DarkGray
  Write-Host "   全程不需要你懂 Linux，看到绿色 [OK] 就行。" -ForegroundColor DarkGray
  Write-Host ""
}

function Show-Fail($title, $lines) {
  Write-Host ""
  Write-Host "  [失败] $title" -ForegroundColor Red
  Write-Host ""
  foreach ($l in $lines) { Write-Host "    $l" -ForegroundColor Yellow }
  Write-Host ""
}

function Test-Port($h, $p, $timeoutMs = 1500) {
  try {
    $c = New-Object System.Net.Sockets.TcpClient
    $r = $c.BeginConnect($h, $p, $null, $null)
    $ok = $r.AsyncWaitHandle.WaitOne($timeoutMs)
    if ($ok -and $c.Connected) { $c.Close(); return $true }
    $c.Close()
  } catch {}
  return $false
}

# HTTP 探活更可靠（WSL2 端口转发有时 TCP 抢跑失败，但网页已经能打开）
function Test-VoiceHttp($h) {
  try {
    $r = Invoke-WebRequest ("http://" + $h + ":9877/") -TimeoutSec 3 -UseBasicParsing
    return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500)
  } catch { return $false }
}

function Find-VoiceHost($ip) {
  foreach ($h in @("127.0.0.1", $ip)) { if (Test-VoiceHttp $h) { return $h } }
  foreach ($h in @("127.0.0.1", $ip)) { if (Test-Port $h 9877) { return $h } }
  return $null
}

Show-Header

$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = (Get-Location).Path }
$cfgPath = Join-Path $ScriptDir "bridge-config.json"

# ------------------------- 第 1 步：WSL -------------------------
Write-Host "  [第 1 步 / 共 5 步] 检查 WSL2（Windows 里的 Linux 子系统）" -ForegroundColor White
& wsl.exe -d $Distro -- true 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  Show-Fail "没有可用的 WSL2 发行版「$Distro」" @(
    "1) 先看自己装了哪些：  wsl -l -v",
    "2) 一个都没有？以管理员打开 PowerShell 执行：  wsl --install -d Ubuntu-22.04",
    "3) 装完重启电脑，再回来跑本脚本",
    "4) 发行版名字不一样？  powershell -File bridge\start-omnivoice-wsl.ps1 -Distro 你的名字"
  )
  exit 1
}
Write-Host "        [OK] 找到 $Distro" -ForegroundColor Green

# ------------------------- 第 2 步：IP -------------------------
Write-Host "  [第 2 步 / 共 5 步] 获取 WSL 的内部 IP" -ForegroundColor White
$ip = ((& wsl.exe -d $Distro -- bash -lc "hostname -I") 2>$null) -split "\s+" |
      Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' } | Select-Object -First 1
if (-not $ip) {
  Write-Host "        [提醒] 取不到 WSL IP，改用 127.0.0.1（一般也能用）" -ForegroundColor Yellow
  $ip = "127.0.0.1"
}
Write-Host "        [OK] WSL IP = $ip" -ForegroundColor Green

# ------------------------- 第 3 步：装文件 -------------------------
Write-Host "  [第 3 步 / 共 5 步] 检查 WSL 里的服务脚本（~/omnivoice-wsl）" -ForegroundColor White
$haveDemo = ((& wsl.exe -d $Distro -- bash -lc 'test -f ~/omnivoice-wsl/demo_flashinfer.py && echo yes || echo no') 2>$null) -match 'yes'

if ($haveDemo) {
  Write-Host "        [OK] 服务脚本已就位，跳过" -ForegroundColor Green
} else {
  $srcDir = $null
  foreach ($cand in @((Join-Path $ScriptDir "omnivoice"),
                      (Join-Path $ScriptDir "bridge\omnivoice"),
                      (Join-Path (Split-Path $ScriptDir -Parent) "bridge\omnivoice"))) {
    if (Test-Path (Join-Path $cand "demo_flashinfer.py")) { $srcDir = $cand; break }
  }
  if ($srcDir) {
    $winPath = (Resolve-Path $srcDir).Path
    $drive = $winPath.Substring(0, 1).ToLower()
    $rest = ($winPath.Substring(2) -replace '\\', '/')
    $wslSrc = "/mnt/$drive$rest"
    Write-Host "        第一次运行，正在从仓库自动安装…（来源：$wslSrc）" -ForegroundColor DarkGray
    & wsl.exe -d $Distro -- bash -lc "mkdir -p ~/omnivoice-wsl && cp -f '$wslSrc'/demo_flashinfer.py '$wslSrc'/start_server.sh ~/omnivoice-wsl/ && chmod +x ~/omnivoice-wsl/start_server.sh && echo ok" 2>$null | Out-Null
    $haveDemo = ((& wsl.exe -d $Distro -- bash -lc 'test -f ~/omnivoice-wsl/demo_flashinfer.py && echo yes || echo no') 2>$null) -match 'yes'
    if ($haveDemo) { Write-Host "        [OK] 已装到 ~/omnivoice-wsl/" -ForegroundColor Green }
    else {
      Show-Fail "自动安装失败" @(
        "手动复制这两个文件到 WSL 的 ~/omnivoice-wsl/ 目录：",
        "  bridge/omnivoice/demo_flashinfer.py",
        "  bridge/omnivoice/start_server.sh",
        "然后执行：chmod +x ~/omnivoice-wsl/start_server.sh"
      )
    }
  } else {
    Write-Host "        [提醒] 仓库里没找到 bridge/omnivoice/demo_flashinfer.py（你可能只下了部分文件）" -ForegroundColor Yellow
    Write-Host "               建议重新完整 clone 仓库，或手动把这两份文件放进 ~/omnivoice-wsl/" -ForegroundColor Yellow
  }
}

# 顺手查一下 Python 环境（没有就先告诉用户怎么建，省得后面报一堆红字）
$hasVenv = ((& wsl.exe -d $Distro -- bash -lc 'test -x ~/omnivoice-wsl/.venv/bin/python && echo yes || echo no') 2>$null) -match 'yes'
if (-not $hasVenv) {
  Write-Host ""
  Write-Host "  [提醒] WSL 里还没有 OmniVoice 的 Python 环境（~/.venv）" -ForegroundColor Yellow
  Write-Host "         这一步只需做一次，在 WSL 终端里依次执行：" -ForegroundColor Yellow
  Write-Host "           cd ~/omnivoice-wsl" -ForegroundColor DarkGray
  Write-Host "           uv venv --python 3.12 && source .venv/bin/activate" -ForegroundColor DarkGray
  Write-Host "           uv pip install torch --index-url https://download.pytorch.org/whl/cu130" -ForegroundColor DarkGray
  Write-Host "           uv pip install flashinfer-python flashinfer-jit-cache omnivoice" -ForegroundColor DarkGray
  Write-Host "         装完再重新跑本脚本。" -ForegroundColor Yellow
}

# ------------------------- 第 4 步：起服务 -------------------------
Write-Host "  [第 4 步 / 共 5 步] 启动 OmniVoice（端口 9877）" -ForegroundColor White
$reachHost = $ip
$cur = Find-VoiceHost $ip
if ($cur) {
  $reachHost = $cur
  Write-Host "        [OK] 已经在运行了（${reachHost}:9877），不重复启动" -ForegroundColor Green
} else {
  # 模型：参数 > 环境变量 > 本地常见路径 > 在线下载
  if (-not $Model) {
    if ($env:OMNIVOICE_MODEL) { $Model = $env:OMNIVOICE_MODEL }
    elseif ((& wsl.exe -d $Distro -- bash -lc 'test -d /mnt/e/llama-models/models/OmniVoice-bf16 && echo y') 2>$null) { $Model = "/mnt/e/llama-models/models/OmniVoice-bf16" }
    elseif ((& wsl.exe -d $Distro -- bash -lc 'test -d /mnt/d/models/OmniVoice-bf16 && echo y') 2>$null) { $Model = "/mnt/d/models/OmniVoice-bf16" }
    else { $Model = "k2-fsa/OmniVoice" }
  }
  if ($Model -eq "k2-fsa/OmniVoice") {
    Write-Host ""
    Write-Host "  [注意] 没找到本地模型，将从 HuggingFace 下载 k2-fsa/OmniVoice" -ForegroundColor Yellow
    Write-Host "    首次约 10GB，看网速 10~60 分钟；只下这一次，之后都在本地。" -ForegroundColor Yellow
    Write-Host "    下载太慢可设镜像：`$env:HF_ENDPOINT='https://hf-mirror.com' 再重跑。" -ForegroundColor Yellow
    Write-Host "    已有本地模型可直接指定：-Model /你的/模型/路径（跳过下载）" -ForegroundColor Yellow
    Write-Host ""
  } else {
    Write-Host "        使用模型：$Model" -ForegroundColor DarkGray
  }

  # WSL 里已经有进程（可能正在加载）就别再拉一个，否则吃双份显存
  $wslRunning = ((& wsl.exe -d $Distro -- bash -lc "pgrep -f demo_flashinfer.py >/dev/null && echo yes") 2>$null) -match 'yes'
  if ($wslRunning) {
    Write-Host "        WSL 里已有 OmniVoice 进程（可能还在加载），不再重复启动，等待端口就绪…" -ForegroundColor Yellow
  } else {
    Write-Host "        启动中…（下面要等 1~3 分钟，别关窗口）" -ForegroundColor DarkGray
    $wslArgs = "-d $Distro -- bash -lc `"cd ~/omnivoice-wsl && export CUDA_HOME=`$HOME/omnivoice-wsl/.venv/lib/python3.12/site-packages/nvidia/cuda_nvcc && exec .venv/bin/python demo_flashinfer.py --model $Model --port 9877 --no-asr --ip 0.0.0.0 --graph-buckets 5,10,15,20,30`""
    Start-Process -FilePath "wsl.exe" -ArgumentList $wslArgs -WindowStyle Hidden
  }

  Write-Host "        等待服务就绪（最长 150 秒）…" -ForegroundColor DarkGray
  for ($i = 0; $i -lt 75; $i++) {
    Start-Sleep -Seconds 2
    $cur = Find-VoiceHost $ip
    if ($cur) { $reachHost = $cur; break }
    if ($i % 10 -eq 9) { Write-Host "          还在加载…已等 $((($i + 1) * 2)) 秒" -ForegroundColor DarkGray }
  }
  if ($cur) {
    Write-Host "        [OK] OmniVoice 就绪（$reachHost:9877）" -ForegroundColor Green
  } else {
    Show-Fail "等了 150 秒服务还没起来" @(
      "1) 先看日志最后 30 行：  wsl -d $Distro -- tail -30 ~/omnivoice-wsl/server.log",
      "2) 常见原因：模型还在下载（网络慢）、显存不够（关掉别的占显存的程序）、",
      "   Python 环境没装好（看上面第 3 步的提醒）",
      "3) 修好后重新跑本脚本即可"
    )
  }
}

# ------------------------- 第 5 步：接桥 -------------------------
Write-Host "  [第 5 步 / 共 5 步] 记住地址 + 启动语音桥（8765）" -ForegroundColor White
if (Test-Path $cfgPath) {
  try {
    $cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $cfg.omnivoice) {
      $cfg | Add-Member -NotePropertyName omnivoice -NotePropertyValue ([pscustomobject]@{}) -Force
    }
    $old = $cfg.omnivoice.base
    $newBase = "http://$reachHost" + ":9877"
    if ($old -ne $newBase) {
      $cfg.omnivoice.base = $newBase
      # 必须用无 BOM 的 UTF-8，否则 Python 读配置会报 Unexpected UTF-8 BOM
      $json = $cfg | ConvertTo-Json -Depth 10
      [System.IO.File]::WriteAllText($cfgPath, $json, (New-Object System.Text.UTF8Encoding($false)))
      Write-Host "        [OK] 已把地址写进 bridge-config.json：$newBase" -ForegroundColor Green
    } else {
      Write-Host "        [OK] 地址没变：$newBase" -ForegroundColor Green
    }
  } catch {
    Write-Host "        [提醒] 写配置失败：$_" -ForegroundColor Yellow
  }
} else {
  Write-Host "        [提醒] 没找到 $cfgPath" -ForegroundColor Yellow
  Write-Host "               首次使用请先复制一份：cp bridge\bridge-config.example.json bridge\bridge-config.json" -ForegroundColor Yellow
}

function Get-PortPid($port) {
  try {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.OwningProcess }
  } catch {}
  return $null
}

$bridgePid = Get-PortPid 8765
if ($bridgePid) {
  Write-Host "        [OK] 语音桥已在运行（PID $bridgePid）" -ForegroundColor Green
} else {
  Write-Host "        语音桥没在跑，启动它…" -ForegroundColor DarkGray
  $startCmd = Join-Path $ScriptDir "start-bridge.cmd"
  if (Test-Path $startCmd) { Start-Process -FilePath $startCmd -WorkingDirectory $ScriptDir -WindowStyle Minimized }
  Start-Sleep -Seconds 6
  if (Get-PortPid 8765) { Write-Host "        [OK] 语音桥已就绪" -ForegroundColor Green }
  else { Write-Host "        [提醒] 语音桥没起来，手动双击 start-bridge.cmd 试试" -ForegroundColor Yellow }
}

Write-Host ""
Write-Host "  ============================================================" -ForegroundColor Cyan
Write-Host "   全部完成" -ForegroundColor Green
Write-Host "  ============================================================" -ForegroundColor Cyan
Write-Host "   OmniVoice 语音引擎 : http://${reachHost}:9877"
Write-Host "   语音桥            : http://127.0.0.1:8765"
Write-Host ""
Write-Host "   想确认真的好了？浏览器打开： http://127.0.0.1:9877/" -ForegroundColor DarkGray
Write-Host "   能出现一个网页（含「语音克隆」字样）就说明引擎正常。" -ForegroundColor DarkGray
Write-Host ""
Write-Host "   下次重启电脑后，直接再跑本脚本即可（已经在跑会自动跳过）。" -ForegroundColor DarkGray
Write-Host ""
