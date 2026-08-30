@echo off
rem OmniVoice (WSL2 FlashInfer) 一键拉起 - 重启电脑后双击本文件即可
rem 会依次: 启动 WSL2 -> 检测/拉起 OmniVoice 服务 -> 检测/拉起 voice bridge
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-omnivoice-wsl.ps1"
echo.
pause
