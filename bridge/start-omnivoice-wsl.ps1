# OmniVoice (WSL2 + FlashInfer) 一键拉起 / 自检脚本
#
# 用法（双击 start-omnivoice-wsl.cmd，或直接跑本脚本）：
#   powershell -ExecutionPolicy Bypass -File bridge\start-omnivoice-wsl.ps1
#   powershell ... -File bridge\start-omnivoice-wsl.ps1 -Model /mnt/e/models/OmniVoice-bf16
#
# 流程:
#   [1/5] 检查 WSL2 发行版
#   [2/5] 取 WSL 内网 IP
#   [3/5] 首次运行: 把仓库自带的服务脚本装进 ~/omnivoice-wsl
#   [4/5] 拉起 / 探测 OmniVoice (:9877)  ← 127.0.0.1 与 WSL IP 两种地址都试
#   [5/5] 同步 bridge-config.json 的 omnivoice.base 并拉起 bridge (:8765)
#
# ⚠ 本脚本不写死用户名 / 盘符: 一切从 $HOME 与 $PSScriptRoot 推导，
#   别人 clone 下来直接能跑（2026-11-02 修）。

param(
  [string]$Model = "",          # 模型路径(HF id 或 WSL 内路径)。空 = 自动挑
  [string]$Distro = "Ubuntu-22.04"
)

$ErrorActionPreference = "Continue"
$Host.UI.RawUI.WindowTitle = "OmniVoice WSL2 一键拉起"
Write-Host "=== OmniVoice (WSL2 FlashInfer) 一键拉起 ===" -ForegroundColor Cyan

$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = (Get-Location).Path }
$cfgPath = Join-Path $ScriptDir "bridge-config.json"

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

# HTTP 探活更可靠（WSL2 端口转发有时 TCP 探测抢跑失败，但 HTTP 已经通）
function Test-VoiceHttp($h) {
  try {
    $r = Invoke-WebRequest ("http://" + $h + ":9877/") -TimeoutSec 3 -UseBasicParsing
    return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500)
  } catch { return $false }
}

# ---------- [1/5] WSL ----------
Write-Host "[1/5] 检查 WSL2 ..."
# 直接探测能否进该发行版（`wsl -l -q` 输出是 UTF-16，正则匹配不可靠）
& wsl.exe -d $Distro -- true 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Host "      [FAIL] 进不去 WSL 发行版 $Distro（wsl -l -v 看看实际名字）" -ForegroundColor Red
  exit 1
}
Write-Host "      [OK] $Distro"

# ---------- [2/5] IP ----------
Write-Host "[2/5] 获取 WSL 内网 IP ..."
$ip = ((& wsl.exe -d $Distro -- bash -lc "hostname -I") 2>$null) -split "\s+" | Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' } | Select-Object -First 1
if (-not $ip) { Write-Host "      [WARN] 取不到 IP，只试 127.0.0.1" -ForegroundColor Yellow; $ip = "127.0.0.1" }
Write-Host "      IP = $ip"

# ---------- [3/5] 首次安装: 部署服务脚本 ----------
Write-Host "[3/5] 检查 ~/omnivoice-wsl ..."
$haveDemo = ((& wsl.exe -d $Distro -- bash -lc 'test -f ~/omnivoice-wsl/demo_flashinfer.py && echo yes || echo no') 2>$null) -match 'yes'

if ($haveDemo) {
  Write-Host "      [OK] 服务脚本已就位"
} else {
  # 仓库里自带一份(bridge/omnivoice/)，直接拷进 WSL，省去手抄
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
    Write-Host "      从仓库部署: $wslSrc → ~/omnivoice-wsl/"
    & wsl.exe -d $Distro -- bash -lc "mkdir -p ~/omnivoice-wsl && cp -f '$wslSrc'/demo_flashinfer.py '$wslSrc'/start_server.sh ~/omnivoice-wsl/ && chmod +x ~/omnivoice-wsl/start_server.sh && echo ok" 2>$null | Out-Null
    $haveDemo = ((& wsl.exe -d $Distro -- bash -lc 'test -f ~/omnivoice-wsl/demo_flashinfer.py && echo yes || echo no') 2>$null) -match 'yes'
    if ($haveDemo) { Write-Host "      [OK] 已部署" -ForegroundColor Green }
    else { Write-Host "      [FAIL] 部署失败，请手动 cp bridge/omnivoice/* ~/omnivoice-wsl/" -ForegroundColor Red }
  } else {
    Write-Host "      [WARN] 仓库里没找到 bridge/omnivoice/demo_flashinfer.py" -ForegroundColor Yellow
    Write-Host "             请把该文件与 start_server.sh 复制到 ~/omnivoice-wsl/ 后再跑本脚本" -ForegroundColor Yellow
  }
}

# ---------- [4/5] 拉起 / 探测 ----------
function Find-VoiceHost() {
  foreach ($h in @("127.0.0.1", $ip)) { if (Test-VoiceHttp $h) { return $h } }
  foreach ($h in @("127.0.0.1", $ip)) { if (Test-Port $h 9877) { return $h } }
  return $null
}

$reachHost = $ip
$cur = Find-VoiceHost
if ($cur) {
  $reachHost = $cur
  Write-Host "[4/5] OmniVoice 已在运行 (${reachHost}:9877)"
} else {
  # 模型路径: 参数 > 环境变量 > 常见本地路径 > HF 仓库 id
  if (-not $Model) {
    if ($env:OMNIVOICE_MODEL) { $Model = $env:OMNIVOICE_MODEL }
    elseif ((& wsl.exe -d $Distro -- bash -lc 'test -d /mnt/e/llama-models/models/OmniVoice-bf16 && echo y') 2>$null) { $Model = "/mnt/e/llama-models/models/OmniVoice-bf16" }
    else { $Model = "k2-fsa/OmniVoice" }
  }
  # 先确认 WSL 里没有在跑的实例（端口没通但进程还在加载时，重拉会吃双份显存）
  $wslRunning = ((& wsl.exe -d $Distro -- bash -lc "pgrep -f demo_flashinfer.py >/dev/null && echo yes") 2>$null) -match 'yes'
  if ($wslRunning) {
    Write-Host "[4/5] WSL 里已有 OmniVoice 进程（可能仍在加载），不再重复拉起，等待端口就绪…"
  } else {
  Write-Host "[4/5] 启动 OmniVoice（模型 $Model）..."
  $wslArgs = "-d $Distro -- bash -lc `"cd ~/omnivoice-wsl && export CUDA_HOME=`$HOME/omnivoice-wsl/.venv/lib/python3.12/site-packages/nvidia/cuda_nvcc && exec .venv/bin/python demo_flashinfer.py --model $Model --port 9877 --no-asr --ip 0.0.0.0 --graph-buckets 5,10,15,20,30`""
  Start-Process -FilePath "wsl.exe" -ArgumentList $wslArgs -WindowStyle Hidden
  Write-Host "      等待就绪（最长 150s，含模型加载 + CUDA graph 预热）..."
  for ($i = 0; $i -lt 75; $i++) {
    Start-Sleep -Seconds 2
    $cur = Find-VoiceHost
    if ($cur) { $reachHost = $cur; break }
  }
  if ($cur) { Write-Host "      [OK] OmniVoice 就绪 (${reachHost}:9877)" -ForegroundColor Green }
  else { Write-Host "      [FAIL] 未就绪，看 ~/omnivoice-wsl/server.log" -ForegroundColor Red }
  }
}

# ---------- [5/5] 同步配置 + 拉起 bridge ----------
Write-Host "[5/5] 同步 bridge-config.json ..."
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
      # 无 BOM UTF-8（PS 5.1 的 Set-Content -Encoding UTF8 会带 BOM → json.load 报错）
      $json = $cfg | ConvertTo-Json -Depth 10
      [System.IO.File]::WriteAllText($cfgPath, $json, (New-Object System.Text.UTF8Encoding($false)))
      Write-Host "      omnivoice.base: $old -> $newBase（桥需重启生效）"
    } else {
      Write-Host "      omnivoice.base 未变化: $newBase"
    }
  } catch {
    Write-Host "      [WARN] 更新 bridge-config 失败: $_" -ForegroundColor Yellow
  }
} else {
  Write-Host "      [WARN] 没找到 $cfgPath（首次运行请先 cp bridge-config.example.json bridge-config.json）" -ForegroundColor Yellow
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
  Write-Host "      bridge 已在运行 (PID $bridgePid)"
} else {
  Write-Host "      启动 bridge (:8765) ..."
  $startCmd = Join-Path $ScriptDir "start-bridge.cmd"
  if (Test-Path $startCmd) { Start-Process -FilePath $startCmd -WorkingDirectory $ScriptDir -WindowStyle Minimized }
  Start-Sleep -Seconds 6
  if (Get-PortPid 8765) { Write-Host "      [OK] bridge 已就绪" -ForegroundColor Green }
  else { Write-Host "      [WARN] bridge 未起来，手动跑 start-bridge.cmd" -ForegroundColor Yellow }
}

Write-Host ""
Write-Host "=== 完成 ===" -ForegroundColor Cyan
Write-Host "  OmniVoice : http://${reachHost}:9877"
Write-Host "  bridge    : http://127.0.0.1:8765"
Write-Host "  自检      : curl http://127.0.0.1:8765/api/balance"
