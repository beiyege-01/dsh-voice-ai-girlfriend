# ============================================================================
#  DSH 语音全栈 · 一键启动（2026-10-25 更新：DSH 0.2.1）
#
#  一条命令把「重启电脑后需要点好几下」的东西按正确顺序拉起来：
#    [1] Docker / DUIX 数字人容器检查（Docker Desktop 需你提前手动开好）
#    [2] WSL2 OmniVoice TTS（:9877）+ 语音桥接（:8765）—— 复用 start-omnivoice-wsl.ps1
#    [3] 桥接保险：没起来就用带 DEEPSEEK_API_KEY 的方式起；缺 key 时自动重启修复
#    [4] DSH Web（默认最新 0.2.1 / profile web-v021，:3092）+ 自动用带 token 的链接开浏览器
#    [5] 数字人状态检查（开关 / 容器 / 是否需要 docker start）
#    [6] 汇总自检：每条链路 OK / FAIL 一目了然
#
#  用法：
#    start-full-stack.cmd                # 双击即可（自动选最新的 0.2.1；没有则退回 v013）
#    start-full-stack.cmd -NoDsh         # 只起语音栈（OmniVoice + 桥接 + 数字人检查）
#    start-full-stack.cmd -V013          # 用 dsh 0.1.3（profile web-v013，:3080）
#    start-full-stack.cmd -Rc8           # 用旧版 rc.8（profile web，:3080）
#    start-full-stack.cmd -Port 3092     # 指定 DSH 端口
#    start-full-stack.cmd -Harness E:\DSH\deepseek-harness-v021 -Profile web-v021
#    start-full-stack.cmd -NoBrowser     # 不自动开浏览器
#    start-full-stack.cmd -WithQQ        # 额外拉起 NapCat（QQ 双向推送）
#
#  幂等：已经在跑的东西不会重复启动（端口探活为准），可以随时重跑。
# ============================================================================
param(
    [switch]$NoDsh,
    [switch]$NoBrowser,
    [switch]$Rc8,
    [switch]$V013,
    [switch]$WithQQ,
    [switch]$SkipDockerCheck,
    [int]$Port = 0,
    [string]$Harness = '',
    [string]$Profile = ''
)

$ErrorActionPreference = 'Continue'
$Host.UI.RawUI.WindowTitle = 'DSH 语音全栈 一键启动'

# ── 可配置区（换机器只改这里）──────────────────────────────────────────────
$Root          = $PSScriptRoot
$BridgePy      = Join-Path $Root 'venv-speech\Scripts\python.exe'
$OmniScript    = Join-Path $Root 'start-omnivoice-wsl.ps1'
$DockerExe     = 'docker'
$DockerFallback = "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe"
$Container     = 'duix-avatar-gen-video'
$DuiXProbe     = 'http://127.0.0.1:9000/easy/query?code=probe'
$BridgeHealth  = 'http://127.0.0.1:8765/api/health'
$BridgeStatus  = 'http://127.0.0.1:8765/api/dh/status'
$BridgeBalance = 'http://127.0.0.1:8765/api/balance'

# 选 harness / profile / 端口：显式参数 > -Rc8/-V013 > 自动挑最新的 0.2.1 > v013。
$LatestHarness = 'E:\DSH\deepseek-harness-v021'
$LatestProfile = 'web-v021'
$LatestPort    = 3092
$LegacyHarness = 'E:\DSH\deepseek-harness-v013'
$LegacyProfile = 'web-v013'
$LegacyPort    = 3080
$Rc8Harness    = 'E:\DSH\deepseek-harness'
$Rc8Profile    = 'web'
$Rc8Port       = 3080

if ($Rc8) {
    $SelectedHarness = $Rc8Harness; $SelectedProfile = $Rc8Profile; $SelectedPort = $Rc8Port
} elseif ($V013) {
    $SelectedHarness = $LegacyHarness; $SelectedProfile = $LegacyProfile; $SelectedPort = $LegacyPort
} elseif (Test-Path (Join-Path $LatestHarness 'package.json')) {
    $SelectedHarness = $LatestHarness; $SelectedProfile = $LatestProfile; $SelectedPort = $LatestPort
} else {
    $SelectedHarness = $LegacyHarness; $SelectedProfile = $LegacyProfile; $SelectedPort = $LegacyPort
}
if ($Harness -ne '') { $SelectedHarness = $Harness }
if ($Profile -ne '') { $SelectedProfile = $Profile }
if ($Port -gt 0) { $SelectedPort = $Port }
$Harness = $SelectedHarness
$Profile = $SelectedProfile
$DshPort = $SelectedPort
$Result = [ordered]@{}

function Say([string]$text, [string]$color = 'Gray') { Write-Host $text -ForegroundColor $color }
function Step([string]$n, [string]$text) { Write-Host ''; Say "[$n] $text" 'Cyan' }
function Ok([string]$t)   { Say "      [OK]   $t" 'Green' }
function Warn([string]$t) { Say "      [WARN] $t" 'Yellow' }
function Bad([string]$t)  { Say "      [FAIL] $t" 'Red' }

function Test-Port([string]$addr, [int]$port, [int]$timeoutMs = 1500) {
    try {
        $c = New-Object Net.Sockets.TcpClient
        $iar = $c.BeginConnect($addr, $port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne($timeoutMs, $false)
        if ($ok -and $c.Connected) { $c.Close(); return $true }
        $c.Close(); return $false
    } catch { return $false }
}

function Get-Http([string]$url, [int]$timeoutSec = 5) {
    # 用 .NET 显式按 UTF-8 解码：PowerShell 5.1 的 Invoke-WebRequest 对
    # application/json（不带 charset）默认按 ISO-8859-1 解 → 中文全乱码。
    try {
        $req = [Net.HttpWebRequest]::Create($url)
        $req.Timeout = $timeoutSec * 1000
        $req.ReadWriteTimeout = $timeoutSec * 1000
        $resp = $req.GetResponse()
        $sr = New-Object IO.StreamReader($resp.GetResponseStream(), [Text.Encoding]::UTF8)
        $text = $sr.ReadToEnd()
        $sr.Close(); $resp.Close()
        return $text
    } catch { return $null }
}

function Resolve-Docker {
    if (Get-Command $DockerExe -ErrorAction SilentlyContinue) { return $DockerExe }
    if (Test-Path $DockerFallback) { return $DockerFallback }
    return $null
}

Say '============================================================' 'Cyan'
Say '  DSH 语音全栈 · 一键启动' 'Cyan'
Say "  harness = $Harness" 'Cyan'
Say "  profile = $Profile      root = $Root" 'Cyan'
Say '============================================================' 'Cyan'

# ── [1/6] Docker Desktop / DUIX 容器 ───────────────────────────────────────
Step '1/6' '检查 Docker 与 DUIX 数字人容器（Docker Desktop 需已手动开启）'
$docker = Resolve-Docker
$dockerOk = $false
if ($SkipDockerCheck) {
    Warn '按参数跳过 Docker 检查'
} elseif ($null -eq $docker) {
    Bad 'docker CLI 找不到 —— 请确认已安装 Docker Desktop'
    $Result['Docker CLI'] = 'FAIL'
} else {
    $info = & $docker info --format '{{.ServerVersion}}' 2>$null
    if ($LASTEXITCODE -eq 0 -and $info) {
        $dockerOk = $true
        Ok "Docker 引擎在线（$info）"
        $Result['Docker 引擎'] = "OK ($info)"
        $running = (& $docker inspect $Container --format '{{.State.Running}}' 2>$null)
        $exists = (& $docker inspect $Container --format '{{.Name}}' 2>$null)
        if (-not $exists) {
            Bad "容器 $Container 不存在 —— 先跑一次：docker compose -f D:\docker_project\docker-compose-5060ti.yml up -d"
            $Result['DUIX 容器'] = 'FAIL (容器不存在)'
        } elseif ($running -eq 'true') {
            Ok "容器 $Container 已在运行"
            $Result['DUIX 容器'] = 'OK (running)'
        } else {
            Warn "容器 $Container 未运行 —— 尝试 docker start ..."
            & $docker start $Container 2>&1 | Out-Null
            for ($i = 0; $i -lt 60; $i++) {
                Start-Sleep -Seconds 3
                if (Get-Http $DuiXProbe 3) { break }
            }
            if (Get-Http $DuiXProbe 3) {
                Ok 'DUIX 已就绪（:9000 应答正常）'
                $Result['DUIX 容器'] = 'OK (started)'
            } else {
                Warn 'DUIX 尚未就绪（冷启动要加载模型，1-3 分钟）；桥接会继续等它'
                $Result['DUIX 容器'] = 'WARN (starting)'
            }
        }
    } else {
        Bad 'Docker 引擎没应答 —— 请先手动打开 Docker Desktop，等托盘图标变绿后重跑本脚本'
        $Result['Docker 引擎'] = 'FAIL (请手动开 Docker Desktop)'
    }
}

# ── [2/6] WSL2 OmniVoice + 语音桥接 ────────────────────────────────────────
Step '2/6' '启动 WSL2 OmniVoice（:9877）与语音桥接（:8765）'

# 先注入 key：脚本内部启动桥接时会继承本进程环境变量（否则余额徽章 503）
$env:DEEPSEEK_API_KEY = ''
try {
    $k = (Get-ItemProperty 'HKCU:\Environment' -Name DEEPSEEK_API_KEY -ErrorAction Stop).DEEPSEEK_API_KEY
    if ($k) { $env:DEEPSEEK_API_KEY = $k; Ok 'DEEPSEEK_API_KEY 已从注册表注入（值不回显）' }
    else { Warn '注册表里没有 DEEPSEEK_API_KEY —— 余额徽章会 503（其它功能正常）' }
} catch { Warn '读取注册表 DEEPSEEK_API_KEY 失败 —— 余额徽章可能 503' }

if (Test-Path $OmniScript) {
    Say '      交给 start-omnivoice-wsl.ps1 处理（已在运行的服务它会自动跳过）...'
    & powershell -NoProfile -ExecutionPolicy Bypass -File $OmniScript
} else {
    Warn "找不到 $OmniScript —— 跳过 OmniVoice（只保证桥接）"
}

# ── [3/6] 桥接保险：起没起 / 有没有 key ────────────────────────────────────
Step '3/6' '桥接保险检查（进程 + API Key）'
if (-not (Test-Path $BridgePy)) {
    Bad "venv python 不存在：$BridgePy"
    $Result['语音桥接'] = 'FAIL (venv 缺失)'
} else {
    if (-not (Test-Port '127.0.0.1' 8765)) {
        Warn '桥接未运行，正在启动 ...'
        Start-Process -FilePath $BridgePy `
            -ArgumentList '-m', 'uvicorn', 'voice_bridge:app', '--host', '127.0.0.1', '--port', '8765' `
            -WorkingDirectory $Root -WindowStyle Minimized
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Seconds 2
            if (Get-Http $BridgeHealth 3) { break }
        }
    }
    if (Test-Port '127.0.0.1' 8765) {
        Ok '桥接在线（127.0.0.1:8765）'
        $health = Get-Http $BridgeHealth
        if ($health) { Say "      health: $health" 'DarkGray' }
        $bal = Get-Http $BridgeBalance 5
        if ($bal -and $bal -notmatch 'DEEPSEEK_API_KEY not configured') {
            Ok '余额接口可用（API Key 已生效）'
            $Result['语音桥接'] = 'OK (+key)'
        } else {
            Warn '桥接在跑但缺 DEEPSEEK_API_KEY —— 重启桥接让它继承 key ...'
            $lp = (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1)
            if ($lp) { Stop-Process -Id $lp.OwningProcess -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2 }
            Start-Process -FilePath $BridgePy `
                -ArgumentList '-m', 'uvicorn', 'voice_bridge:app', '--host', '127.0.0.1', '--port', '8765' `
                -WorkingDirectory $Root -WindowStyle Minimized
            for ($i = 0; $i -lt 30; $i++) {
                Start-Sleep -Seconds 2
                if (Get-Http $BridgeHealth 3) { break }
            }
            if (Test-Port '127.0.0.1' 8765) { Ok '桥接已重启（带 key）'; $Result['语音桥接'] = 'OK (+key)' }
            else { Bad '桥接重启失败，请手动检查'; $Result['语音桥接'] = 'FAIL' }
        }
    } else {
        Bad '桥接 30 秒内没起来 —— 手动看窗口报错'
        $Result['语音桥接'] = 'FAIL'
    }
}

# ── [4/6] DSH Web ─────────────────────────────────────────────────────────
$dshUrl = "http://127.0.0.1:$DshPort"
Step '4/6' "DSH Web（$Profile @ $DshPort）"
if ($NoDsh) {
    Warn '按参数 -NoDsh 跳过'
    $Result['DSH Web'] = 'SKIP'
} elseif (Test-Port '127.0.0.1' $DshPort) {
    Ok "$DshPort 已在服务（不重复启动，也不会打断当前会话）"
    Say "      直接用现有浏览器标签即可：$dshUrl" 'DarkGray'
    $Result['DSH Web'] = 'OK (已在运行)'
} elseif (-not (Test-Path (Join-Path $Harness 'package.json'))) {
    Bad "harness 不存在：$Harness"
    $Result['DSH Web'] = 'FAIL (harness 缺失)'
} elseif (-not (Test-Path (Join-Path $Harness 'apps\web\dist'))) {
    Bad "前端产物缺失：$Harness\apps\web\dist —— 先跑 pnpm run build:web"
    $Result['DSH Web'] = 'FAIL (dist 缺失)'
} else {
    $pnpm = 'C:\Users\wrpsg\AppData\Roaming\npm\pnpm.cmd'
    if (-not (Test-Path $pnpm)) { $pnpm = 'pnpm' }
    $log = Join-Path $Harness "dsh-web-$Profile.log"
    if (Test-Path $log) { Remove-Item $log -Force -ErrorAction SilentlyContinue }
    Say "      启动中（首次约 10-40 秒）..."
    $cmd = "cd /d `"$Harness`" && set OPENAI_BASE_URL=https://apihub.agnes-ai.com/v1 && set OPENAI_API_KEY= && call `"$pnpm`" dsh --profile $Profile --port $DshPort --no-open >> `"$log`" 2>&1"
    Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $cmd -WindowStyle Minimized
    $up = $false
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Port '127.0.0.1' $DshPort) { $up = $true; break }
    }
    if ($up) {
        Ok "DSH Web 已就绪：$dshUrl"
        $Result['DSH Web'] = 'OK'
        if (-not $NoBrowser) {
            $open = $dshUrl
            if (Test-Path $log) {
                $raw = Get-Content -LiteralPath $log -Raw -ErrorAction SilentlyContinue
                $ms = [regex]::Matches($raw, 'http://[^\s]+token=[A-Za-z0-9_\-]+')
                if ($ms.Count -gt 0) { $open = $ms[$ms.Count - 1].Value }
            }
            Start-Process $open
            Say '      已打开浏览器（带 token 的登录链接）' 'DarkGray'
        }
    } else {
        Bad "$DshPort 90 秒内没应答，日志末尾："
        if (Test-Path $log) { Get-Content -LiteralPath $log -Tail 15 | ForEach-Object { Say "      $_" 'DarkGray' } }
        $Result['DSH Web'] = 'FAIL'
    }
}

# ── [5/6] 数字人状态 ──────────────────────────────────────────────────────
Step '5/6' '数字人（DUIX）链路状态'
$st = Get-Http $BridgeStatus 8
if ($st) {
    try {
        $j = $st | ConvertFrom-Json
        # 数字人开关关着时，桥接不会去碰 DUIX（保持零流量），duix.state 就是
        # unknown —— 这里从脚本侧补一次直连探测，让报告仍然说得清容器在不在。
        $duixState = $j.duix.state
        if ($duixState -eq 'unknown') {
            if (Get-Http $DuiXProbe 4) { $duixState = 'running (脚本探测)' } else { $duixState = 'stopped/unreachable' }
        }
        $seg = "{0}/{1}" -f $j.done_segments, $j.total_segments
        Say ("      enabled={0}  state={1}  段={2}  容器={3} ({4})" -f $j.enabled, $j.state, $seg, $duixState, $j.duix.message) 'Gray'
        if ($j.enabled -and $duixState -like 'running*') {
            Ok '数字人可用（开关开 + 容器就绪）'
            $Result['数字人'] = 'OK'
        } elseif ($j.enabled) {
            Warn "开关是开的，但容器状态=$duixState —— 桥接会自动 docker start，稍等即可"
            $Result['数字人'] = "WARN ($duixState)"
        } else {
            Warn '数字人开关是关的（前端开关或 POST /api/dh/enable 打开）；关着时不占显存'
            $Result['数字人'] = "OFF (容器 $duixState)"
        }
        if ($j.stats) {
            Say ("      计数：submits={0} rejected={1} segment_submits={2} status_polls={3}" -f `
                $j.stats.submits, $j.stats.rejected, $j.stats.segment_submits, $j.stats.status_polls) 'DarkGray'
        }
    } catch { Warn '状态解析失败' }
} else {
    Warn '读不到 /api/dh/status（桥接未就绪）'
    $Result['数字人'] = 'UNKNOWN'
}

# ── [6/6] 汇总 ────────────────────────────────────────────────────────────
Step '6/6' '汇总自检'
$omniIp = '127.0.0.1'
try { $omniIp = (Get-Content (Join-Path $Root 'bridge-config.json') -Raw -Encoding UTF8 | ConvertFrom-Json).omnivoice.base } catch {}
$omniOk = $false
try {
    $mb = [regex]::Match([string]$omniIp, '://([^:/]+)')
    if ($mb.Success) { $omniOk = Test-Port $mb.Groups[1].Value 9877 2000 }
} catch {}
if ($omniOk) { $Result['OmniVoice TTS'] = "OK ($omniIp)" } else { $Result['OmniVoice TTS'] = "FAIL ($omniIp)" }

foreach ($k in $Result.Keys) {
    $v = [string]$Result[$k]
    if ($v -like 'OK*') { Say ("  {0,-16} {1}" -f $k, $v) 'Green' }
    elseif ($v -like 'FAIL*') { Say ("  {0,-16} {1}" -f $k, $v) 'Red' }
    else { Say ("  {0,-16} {1}" -f $k, $v) 'Yellow' }
}
Say ''
Say '完成。UI 里说话即走 OmniVoice 朗读；数字人开关打开时回复会生成口播视频。' 'Cyan'
Say '（本窗口可以关闭；桥接与 DSH 都在各自的独立窗口里跑。）' 'DarkGray'
