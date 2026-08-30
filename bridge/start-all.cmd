@echo off
setlocal
title DSH Voice Launcher
rem One-click launcher for the full stack:
rem   1. voice bridge (venv-speech python, port 8765, models lazy-load)
rem   2. dsh web (the GUI at http://127.0.0.1:3080)  -- requires %DSH_HARNESS%
rem   3. browser
rem The bridge runs as its own minimized console (shows model load progress),
rem independent of dsh web, so restarting the GUI does not kill it.
rem
rem Required environment before running:
rem   DSH_HARNESS  = path to the deepseek-harness source tree
rem                 (e.g. set DSH_HARNESS=C:\dev\deepseek-harness)
rem If DSH_HARNESS is not set (or missing), only the bridge starts.

set "REPO_ROOT=%~dp0.."
set "BRIDGE_PY=%REPO_ROOT%\venv-speech\Scripts\python.exe"

if not exist "%BRIDGE_PY%" (
    echo [ERROR] venv-speech python not found: %BRIDGE_PY%
    echo         Create it first, see README.md "??" section.
    pause
    exit /b 1
)

rem --- start the voice bridge in its own minimized window (skip if already up) ---
cd /d "%REPO_ROOT%\bridge"
>nul 2>&1 curl -s -m 2 "http://127.0.0.1:8765/api/health" && (
    echo [OK] voice bridge already running at :8765 (skipping duplicate start)
    goto :bridge_ok
)
if not exist "%REPO_ROOT%\models\silero-vad\silero_vad_v4.jit" (
    echo [WARN] silero-vad model missing: %REPO_ROOT%\models\silero-vad\silero_vad_v4.jit
)
if not exist "%REPO_ROOT%\models\funasr" (
    echo [WARN] funasr model dir missing: %REPO_ROOT%\models\funasr
)
echo Starting voice bridge on http://127.0.0.1:8765 ...
start "voice-bridge" /min "%BRIDGE_PY%" -m uvicorn voice_bridge:app --host 127.0.0.1 --port 8765

rem --- wait for the bridge health endpoint (up to 30s) ---
echo Waiting for bridge health ...
for /l %%i in (1,1,30) do (
    >nul 2>&1 curl -s "http://127.0.0.1:8765/api/health" && goto :bridge_ok
    timeout /t 1 /nobreak >nul
)
echo [WARN] bridge did not answer within 30s; continuing anyway.
:bridge_ok

rem --- start NapCatQQ (QQ two-way bridge). SKIP with: start-all.cmd /nq ---
if /I "%~1"=="nq" goto :skip_napcat
echo.
echo [NapCat] Starting QQ bridge (closes running QQ, injects the bot account)...
start "napcat-bridge" /min cmd /c ""%~dp0start-napcat.cmd""
:skip_napcat

rem --- start dsh web if the harness tree is configured ---
if "%DSH_HARNESS%"=="" goto :no_harness
if not exist "%DSH_HARNESS%\package.json" goto :no_harness
echo Starting DSH Web from %DSH_HARNESS% ...
cd /d "%DSH_HARNESS%"
rem Skip if 3080 is already serving (e.g. a manual pnpm dsh web is running)
>nul 2>&1 curl -s -m 2 "http://127.0.0.1:3080" && goto :web_up
rem start in its OWN window so closing this launcher never kills dsh web
start "dsh-web" /min cmd /c "cd /d ""%DSH_HARNESS%"" && set OPENAI_BASE_URL=https://apihub.agnes-ai.com/v1 && set OPENAI_API_KEY= && pnpm dsh web"
:web_up
echo Waiting for DSH Web :3080 ...
for /l %%i in (1,1,60) do (
    >nul 2>&1 curl -s -m 2 "http://127.0.0.1:3080" && goto :web_ready
    timeout /t 1 /nobreak >nul
)
echo [WARN] 3080 did not answer within 60s; opening browser anyway.
:web_ready
start http://127.0.0.1:3080
goto :done

:no_harness
echo.
echo [NOTE] %DSH_HARNESS% is not set or missing, so DSH Web was NOT started.
echo        Set the environment variable DSH_HARNESS to your deepseek-harness
echo        source tree and re-run this script to launch the GUI too.

:done
echo All services started. This window can be closed.
pause


