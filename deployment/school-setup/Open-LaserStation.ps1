param(
    [string]$InstallPath = "$env:LOCALAPPDATA\MakervaerkstedetLaserstation",
    [switch]$NoBrowser
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$InstallPath = [IO.Path]::GetFullPath($InstallPath)
$active = Get-Content -LiteralPath (Join-Path $InstallPath 'active.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($active.kind -ne 'makervaerkstedet-laserstation' -or $active.version_directory -notmatch '^versions/[a-f0-9]{32}$') { throw 'Ukendt stationsinstallation.' }
$version = Join-Path $InstallPath $active.version_directory
$program = Join-Path $version 'program'
$config = Get-Content -LiteralPath (Join-Path $version 'laser-installation.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$url = 'http://127.0.0.1:' + $config.port + '/'
function Read-StationState {
    try { Invoke-RestMethod ($url + 'api/station/state') -TimeoutSec 2 } catch { return $null }
}
$state = Read-StationState
if (-not $state) {
    $logs = Join-Path $InstallPath 'logs'
    New-Item -ItemType Directory -Path $logs -Force | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $arguments = '-B -m app.laser_station_install run --install-root "' + $InstallPath + '"'
    $process = Start-Process -FilePath (Join-Path $program 'runtime\python.exe') -ArgumentList $arguments -WorkingDirectory $program -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs ($stamp + '.log')) -RedirectStandardError (Join-Path $logs ($stamp + '.error.log'))
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        $state = Read-StationState
        if ($state) { break }
        if ($process.HasExited) { throw ('Laserstationen kunne ikke starte. Se ' + $logs) }
        Start-Sleep -Milliseconds 250
    }
}
if (-not $state) { throw 'Laserstationen svarer ikke endnu. Se stationens logs.' }
if (([string]$state.server).TrimEnd('/') -ne ([string]$config.server).TrimEnd('/')) { throw 'Porten bruges af en station med en anden server.' }
if ([string]$state.stationId -ne [string]$config.station_id) { throw 'Porten bruges af en anden laserstation.' }
if (-not $NoBrowser) { Start-Process $url }
