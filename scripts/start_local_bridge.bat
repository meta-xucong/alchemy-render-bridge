@echo off
setlocal
start "Alchemy Render Bridge Status" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_local_bridge.ps1"
