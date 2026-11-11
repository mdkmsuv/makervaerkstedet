@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup-LaserStation.ps1" -Action Install %*
set "result=%errorlevel%"
pause
exit /b %result%
