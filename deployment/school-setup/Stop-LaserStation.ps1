param([string]$InstallPath = "$env:LOCALAPPDATA\MakervaerkstedetLaserstation")
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$InstallPath = [IO.Path]::GetFullPath($InstallPath)
$active = Get-Content -LiteralPath (Join-Path $InstallPath 'active.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($active.kind -ne 'makervaerkstedet-laserstation' -or $active.version_directory -notmatch '^versions/[a-f0-9]{32}$') { throw 'Ukendt stationsinstallation.' }
$program = Join-Path (Join-Path $InstallPath $active.version_directory) 'program'
Push-Location -LiteralPath $program
try {
    $resultText = & (Join-Path $program 'runtime\python.exe') -B -m app.laser_station_install stop --install-root $InstallPath --timeout 15
    if ($LASTEXITCODE -ne 0) { throw 'Laserstationens stop blev ikke bekræftet. Ingen proces er tvangsafsluttet.' }
    $result = ($resultText -join "`n") | ConvertFrom-Json
    if ($result.stopped -ne $true) { throw 'Laserstationens stop blev ikke bekræftet.' }
    Write-Host 'Laserstationen er stoppet. Den kan nu opdateres eller åbnes igen.'
} finally { Pop-Location }
