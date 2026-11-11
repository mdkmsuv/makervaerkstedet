param([switch]$SchoolNetwork)
$ErrorActionPreference = 'Stop'
$laserRoot = Split-Path -Parent $PSScriptRoot
$laserRuntime = Join-Path $laserRoot '.runtime'
New-Item -ItemType Directory -Force -Path $laserRuntime | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $laserRoot 'web\vendor\laser.js'))) {
    throw 'Byg editoren først: npm ci og npm run build:laser'
}
$laserStudentBind = if ($SchoolNetwork) { '0.0.0.0' } else { '127.0.0.1' }
$laserExisting = Get-NetTCPConnection -State Listen -LocalPort 8766,8767 -ErrorAction SilentlyContinue
if ($laserExisting) {
    Write-Host 'En tjeneste bruger allerede port 8766 eller 8767. Den er ikke blevet genstartet.'
} else {
    $laserPython = (Get-Command python -ErrorAction Stop).Source
    $laserProcess = Start-Process -FilePath $laserPython -ArgumentList @('-m','app.laser_server','--host',$laserStudentBind) -WorkingDirectory $laserRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $laserRuntime 'laser-server.log') -RedirectStandardError (Join-Path $laserRuntime 'laser-server-error.log')
    $laserProcess.Id | Set-Content -LiteralPath (Join-Path $laserRuntime 'laser-server.pid')
    Write-Host 'Laserværksted starter.'
}
Write-Host 'Elevside på denne computer: http://127.0.0.1:8766/'
Write-Host 'Lærerside kun på denne computer: http://127.0.0.1:8767/'
Write-Host ('Lærerkoden ligger i ' + (Join-Path $laserRoot 'data\laser\laererkode.txt'))
if ($SchoolNetwork) { Write-Host 'Elevcomputere bruger servercomputerens lokale adresse med port 8766. Lærersiden forbliver lokal.' }
