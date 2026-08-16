@echo off
setlocal
title NapCat QQ Bridge Launcher
rem Start NapCatQQ (injects the QQ client, exposes OneBot on 127.0.0.1:3000
rem + WebUI on 6099, and connects its WebSocket client to the voice bridge).
rem
rem NOTE: this closes ALL running QQ processes first (NapCat injection needs
rem the QQ client stopped). If your main QQ is logged in on this PC, it will
rem be signed out here — use the phone QQ for your main account.

set "NAPCAT_DIR=D:\QQ\NapCat\napcat"
set "BRIDGE=http://127.0.0.1:8765"

if not exist "%NAPCAT_DIR%\launcher-win10-user.bat" (
    echo [ERROR] NapCat not found: %NAPCAT_DIR%
    pause
    exit /b 1
)

echo [1/3] Closing all QQ processes (NapCat injection requires QQ stopped)...
taskkill /IM QQ.exe /F >nul 2>&1
timeout /t 3 /nobreak >nul

echo [2/3] Launching NapCat (injects QQ 9.9.32, waits for login)...
start "napcat-launcher" cmd /c "cd /d "%NAPCAT_DIR%" && launcher-win10-user.bat"
echo       NapCat takes ~15-30s to boot. Waiting for OneBot :3000 ...
set "READY="
for /l %%i in (1,1,60) do (
    >nul 2>&1 curl -s -m 2 -X POST "http://127.0.0.1:3000/get_login_info" -H "Content-Type: application/json" -d "{}" && set "READY=1" && goto :ready
    timeout /t 1 /nobreak >nul
)
echo [WARN] OneBot :3000 did not answer within 60s.
echo        Open http://127.0.0.1:6099 (token in napcat\config\webui.json)
echo        and make sure the HTTP server (3000) is enabled, then re-run.
goto :done

:ready
echo [3/3] OneBot :3000 is up.
echo       If the bridge (%BRIDGE%) is running, NapCat's WebSocket client
echo       reconnects automatically and QQ two-way chat is live.
echo       WebUI: http://127.0.0.1:6099

:done
echo.
echo Done. This window can be closed.
rem No pause here: this script is launched in its own window by
rem start-dsh-voice.cmd (which must NOT block waiting for a keypress).
if /I "%~1"=="-p" pause
