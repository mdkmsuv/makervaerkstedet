param([switch]$Demo, [switch]$LocalOnly)
$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectPath
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Host 'Gor Printvaerksted klar. Dette tager kun tid foerste gang.'
    python -m venv (Join-Path $projectPath '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 eller nyere skal installeres.' }
}
& $pythonPath -m pip install -r (Join-Path $projectPath 'requirements.txt') --disable-pip-version-check --quiet
if ($LASTEXITCODE -ne 0) { throw 'Programdelene kunne ikke installeres.' }
if (-not (Test-Path -LiteralPath (Join-Path $projectPath 'web\vendor\viewer.js'))) {
    npm.cmd ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw '3D-visningen kunne ikke installeres. Installer Node.js.' }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw '3D-visningen kunne ikke bygges.' }
}
$portNumber = if ($Demo) { 8766 } else { 8765 }
$bindAddress = if ($Demo -or $LocalOnly) { '127.0.0.1' } else { '0.0.0.0' }
$localUrl = "http://localhost:$portNumber"
$launchArguments = @('-m', 'app.desktop', '--no-window')
if ($Demo) { $launchArguments += '--demo' }
if ($LocalOnly) { $launchArguments += '--local-only' }
& $pythonPath @launchArguments
if ($LASTEXITCODE -ne 0) { throw 'Programmet kunne ikke starte. Se datamappens logfiler.' }
Start-Process $localUrl
