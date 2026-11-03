@echo off
setlocal
pushd "%~dp0.."
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_vps_node_agent.ps1"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo VPS Node Agent exited with code %RC%.
  pause
)
popd
exit /b %RC%
