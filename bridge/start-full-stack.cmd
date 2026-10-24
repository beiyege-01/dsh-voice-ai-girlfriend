@echo off
rem ===========================================================================
rem  DSH voice full stack - one-click launcher (double-click this file)
rem  Forwards all arguments to start-full-stack.ps1.
rem    -NoDsh      voice stack only (OmniVoice + bridge)
rem    -V013       use dsh 0.1.3 (profile web-v013, :3080) instead of latest
rem    -Rc8        use the old rc.8 harness (profile web, :3080)
rem    -Port 3092  DSH web port
rem    -NoBrowser  do not open the browser
rem    -WithQQ     also start NapCat (QQ bridge)
rem  Docker Desktop must already be running (the script only checks it).
rem ===========================================================================
setlocal
title DSH Voice Full Stack
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-full-stack.ps1" %*
echo.
pause
