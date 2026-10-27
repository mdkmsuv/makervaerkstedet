@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup-LaserStation.ps1" -Action Update %*
set "result=%errorlevel%"
pause
exit /b %result%
