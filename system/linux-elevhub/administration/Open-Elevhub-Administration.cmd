@echo off
setlocal
set "SCRIPT=%~dp0Elevhub-Administration.ps1"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
endlocal
