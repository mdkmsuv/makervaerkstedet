@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Stop-LaserStation.ps1" %*
set "result=%errorlevel%"
pause
exit /b %result%
