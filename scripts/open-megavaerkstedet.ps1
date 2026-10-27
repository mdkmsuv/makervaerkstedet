param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$stationRoot = Split-Path -Parent $PSScriptRoot
# Reuse the existing launcher's identity/port checks. No machine job is sent.
& (Join-Path $PSScriptRoot 'open-laserstation.ps1') -NoBrowser
if (-not $?) { throw 'Laserstationen kunne ikke startes.' }
$config = Get-Content -LiteralPath (Join-Path $stationRoot 'laser-installation.json') -Raw | ConvertFrom-Json
$url = 'http://127.0.0.1:' + $config.port + '/megavaerkstedet.html'
if (-not $NoBrowser) { Start-Process $url }
Write-Host (('Makerv' + [char]0x00E6 + 'rkstedet: ') + $url)
