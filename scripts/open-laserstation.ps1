param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$stationRoot = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $stationRoot 'laser-installation.json'
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
if ($config.role -ne 'Station') { throw 'Dette er ikke en laserstation.' }
$url = 'http://127.0.0.1:' + $config.port
function Get-Station {
    try { Invoke-RestMethod ($url + '/api/station/state') -TimeoutSec 2 } catch { $null }
}
$current = Get-Station
if (-not $current) {
    if (Get-NetTCPConnection -State Listen -LocalPort $config.port -ErrorAction SilentlyContinue) { throw 'Porten er optaget af et andet program.' }
    $logs = Join-Path $stationRoot 'logs'
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    $null = Start-Process -FilePath (Join-Path $stationRoot 'runtime\python.exe') -ArgumentList @('-m','app.laser_station','--config',('"' + $configPath + '"')) -WorkingDirectory $stationRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs 'station.log') -RedirectStandardError (Join-Path $logs 'station-error.log')
    for ($i=0;$i -lt 20;$i++) {
        $current = Get-Station
        if ($current) { break }
        Start-Sleep -Milliseconds 250
    }
}
if (-not $current) { throw 'Laserstationen kunne ikke starte. Se logs i installationsmappen.' }
if ($current.server.TrimEnd('/') -ne $config.server.TrimEnd('/')) { throw 'Den aabne station bruger en anden server. Den er ikke blevet genstartet.' }
if (-not $NoBrowser) { Start-Process ($url + '/') }
Write-Host ('Laserstation: ' + $url)
