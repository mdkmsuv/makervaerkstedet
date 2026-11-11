@echo off
setlocal
powershell.exe -NoLogo -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0Choose-Setup.ps1" %*
set "result=%errorlevel%"
if not "%result%"=="0" pause
exit /b %result%
