param(
    # Designværkstedets lærerside med laserkøen. På skolen: serverens adresse, fx https://printserver:8767
    [string]$Server = 'http://127.0.0.1:8767',
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$stationRoot = Split-Path -Parent $PSScriptRoot
$stationRuntime = Join-Path $stationRoot '.runtime'
New-Item -ItemType Directory -Force -Path $stationRuntime | Out-Null
$stationUrl = 'http://127.0.0.1:8790/'
$stationExisting = Get-NetTCPConnection -State Listen -LocalPort 8790 -ErrorAction SilentlyContinue
if ($stationExisting) {
    Write-Host 'Laserstationen kører allerede (port 8790). Den er ikke blevet genstartet.'
} else {
    $venvPython = Join-Path $stationRoot '.venv\Scripts\python.exe'
    $stationPython = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { (Get-Command python -ErrorAction Stop).Source }
    $plates = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Laserplader'
    $stationProcess = Start-Process -FilePath $stationPython -ArgumentList @('-m', 'app.laser_station', '--server', $Server, '--plates', ('"' + $plates + '"')) -WorkingDirectory $stationRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $stationRuntime 'laserstation.log') -RedirectStandardError (Join-Path $stationRuntime 'laserstation-error.log')
    $stationProcess.Id | Set-Content -LiteralPath (Join-Path $stationRuntime 'laserstation.pid')
    Write-Host 'Laserstationen starter.'
    Start-Sleep -Seconds 1
}
Write-Host ('Laserstation: ' + $stationUrl)
Write-Host ('Afleveringskø: ' + $Server)
Write-Host ('Laserplader gemmes i ' + (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Laserplader'))
if (-not (Test-Path -LiteralPath 'C:\Program Files\Beam Studio\Beam Studio.exe')) {
    Write-Host 'Beam Studio blev ikke fundet. Pladerne kan gemmes, men ikke åbnes eller tidsberegnes.'
}
if (-not $NoBrowser) { Start-Process $stationUrl }
