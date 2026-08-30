# OmniVoice (WSL2 FlashInfer) 一键拉起脚本
# 用法: 双击 start-omnivoice-wsl.cmd 或运行本脚本
# 功能: 启动 WSL2 -> 获取 IP -> 更新 bridge 配置 -> 拉起 OmniVoice 服务 -> 拉起 bridge
$ErrorActionPreference = "Continue"
$Host.UI.RawUI.WindowTitle = "OmniVoice WSL2 一键拉起"

Write-Host "=== OmniVoice (WSL2 FlashInfer) 一键拉起 ===" -ForegroundColor Cyan

# [1/5] 确保 WSL2 发行版已启动
Write-Host "[1/5] 启动 WSL2 ..."
wsl -d Ubuntu-22.04 -- true 2>$null
Start-Sleep -Seconds 2

# [2/5] 获取 WSL2 的 eth0 IP（重启后可能变化，动态检测）
$ip = (wsl -d Ubuntu-22.04 -- hostname -I 2>$null | Select-Object -First 1).Trim().Split(' ')[0]
if (-not $ip) {
    Write-Host "      [WARN] 无法获取 WSL2 IP，回退 127.0.0.1" -ForegroundColor Yellow
    $ip = "127.0.0.1"
}
Write-Host "      WSL2 IP: $ip"

# [3/5] 更新 bridge-config.json 的 omnivoice.base（IP 变化时自动同步）
$cfgPath = "D:\speech-to-speech\bridge-config.json"
$baseChanged = $false
try {
    $cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $old = $cfg.omnivoice.base
    $newBase = "http://$ip" + ":9877"
    if ($old -ne $newBase) {
        $baseChanged = $true
        $cfg.omnivoice.base = $newBase
        # 用无 BOM UTF-8 写入（PowerShell 5.1 的 Set-Content -Encoding UTF8 会带 BOM，
        # 导致 bridge 的 json.load 报 Unexpected UTF-8 BOM）
        $json = $cfg | ConvertTo-Json -Depth 10
        [System.IO.File]::WriteAllText($cfgPath, $json, (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "      omnivoice.base: $old -> $newBase (IP 变化，需重启 bridge 生效)"
    } else {
        Write-Host "      omnivoice.base 未变化: $newBase"
    }
} catch {
    Write-Host "      [WARN] 更新 bridge-config 失败: $_" -ForegroundColor Yellow
}

# [4/5] 检测 / 启动 OmniVoice 服务（9877）
function Test-Port($addr, $port) {
    try {
        $c = New-Object Net.Sockets.TcpClient
        $iar = $c.BeginConnect($addr, $port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(2000, $false)
        if ($ok -and $c.Connected) { $c.Close(); return $true }
        $c.Close(); return $false
    } catch { return $false }
}

if (Test-Port $ip 9877) {
    Write-Host "[4/5] OmniVoice 服务已在运行 (${ip}:9877)"
} else {
    Write-Host "[4/5] 启动 OmniVoice 服务（WSL2 隐藏窗口）..."
    $wslArgs = "-d Ubuntu-22.04 -- bash -lc `"cd ~/omnivoice-wsl && export CUDA_HOME=/home/tgkz/omnivoice-wsl/.venv/lib/python3.12/site-packages/nvidia/cuda_nvcc && exec .venv/bin/python demo_flashinfer.py --model /mnt/e/llama-models/models/OmniVoice-bf16 --port 9877 --no-asr --ip 0.0.0.0 --graph-buckets 5,10,15,20,30`""
    Start-Process -FilePath "wsl.exe" -ArgumentList $wslArgs -WindowStyle Hidden
    Write-Host "      等待服务就绪（最长 150s，含模型加载 + CUDA graph）..."
    $ready = $false
    for ($i = 0; $i -lt 75; $i++) {
        Start-Sleep -Seconds 2
        if (Test-Port $ip 9877) { $ready = $true; break }
    }
    if ($ready) { Write-Host "      [OK] OmniVoice 就绪 ($ip:9877)" -ForegroundColor Green }
    else { Write-Host "      [FAIL] 服务未就绪，看 ~/omnivoice-wsl/server.log" -ForegroundColor Red }
}

# [5/5] 检测 / 启动 bridge（8765）；若 IP 变化需重启 bridge 让新配置生效
function Get-PortPid($port) {
    try {
        $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($c) { return $c.OwningProcess }
    } catch {}
    return $null
}

if (Test-Port "127.0.0.1" 8765) {
    if ($baseChanged) {
        Write-Host "[5/5] bridge 在运行但 omnivoice.base 已变化，重启 bridge 生效 ..."
        $pid = Get-PortPid 8765
        if ($pid) { Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2 }
        Start-Process -FilePath "D:\speech-to-speech\venv-speech\Scripts\python.exe" `
            -ArgumentList "-m", "uvicorn", "voice_bridge:app", "--host", "127.0.0.1", "--port", "8765" `
            -WorkingDirectory "D:\speech-to-speech" -WindowStyle Minimized
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Seconds 2
            try { Invoke-WebRequest -Uri "http://127.0.0.1:8765/api/health" -TimeoutSec 2 -UseBasicParsing | Out-Null; break } catch {}
        }
        Write-Host "      bridge 已重启（新配置生效）"
    } else {
        Write-Host "[5/5] bridge 已在运行 (8765)"
    }
} else {
    Write-Host "[5/5] 启动 bridge ..."
    Start-Process -FilePath "D:\speech-to-speech\venv-speech\Scripts\python.exe" `
        -ArgumentList "-m", "uvicorn", "voice_bridge:app", "--host", "127.0.0.1", "--port", "8765" `
        -WorkingDirectory "D:\speech-to-speech" -WindowStyle Minimized
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        try { Invoke-WebRequest -Uri "http://127.0.0.1:8765/api/health" -TimeoutSec 2 -UseBasicParsing | Out-Null; break } catch {}
    }
    Write-Host "      bridge 已启动"
}

# 最终状态
Write-Host ""
Write-Host "=== 最终状态 ===" -ForegroundColor Cyan
try { $r = Invoke-WebRequest -Uri ("http://" + $ip + ":9877/") -TimeoutSec 5 -UseBasicParsing; Write-Host "  OmniVoice : http://${ip}:9877 -> HTTP $($r.StatusCode)" -ForegroundColor Green } catch { Write-Host "  OmniVoice : 不可达 ($_)" -ForegroundColor Red }
try { $h = Invoke-WebRequest -Uri "http://127.0.0.1:8765/api/health" -TimeoutSec 5 -UseBasicParsing; Write-Host "  bridge    : 127.0.0.1:8765 -> $($h.Content)" -ForegroundColor Green } catch { Write-Host "  bridge    : 不可达" -ForegroundColor Red }
Write-Host ""
Write-Host "完成。UI 里说话即走 FlashInfer 加速 TTS。" -ForegroundColor Cyan
