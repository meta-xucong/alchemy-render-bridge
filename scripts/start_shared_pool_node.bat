@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_shared_pool_node.ps1"
set "BRIDGE_EXIT=%ERRORLEVEL%"
if not "%BRIDGE_EXIT%"=="0" pause
exit /b %BRIDGE_EXIT%
