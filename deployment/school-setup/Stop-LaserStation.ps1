param(
    [string]$InstallPath = "$env:LOCALAPPDATA\MakervaerkstedetLaserstation",
    [string]$Root,
    [string]$ConfigPath,
    [switch]$NoDialog
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$stopLog = $null
function Confirm-StationStopped {
    $message = 'Laserstationen er stoppet. Du kan nu opdatere den eller åbne den igen.'
    Write-Host $message
    if (-not $NoDialog) {
        try { $shell = New-Object -ComObject WScript.Shell; $null = $shell.Popup($message, 5, 'Laserstationen er stoppet', 64) } catch { Write-Warning $message }
    }
}
try {
if ($Root -or $ConfigPath) {
    if (-not $Root -or -not $ConfigPath -or $PSBoundParameters.ContainsKey('InstallPath')) { throw 'Alt-i-én-stop kræver både -Root og -ConfigPath, uden -InstallPath.' }
    $program = [IO.Path]::GetFullPath($Root)
    $config = [IO.Path]::GetFullPath($ConfigPath)
    $python = Join-Path $program 'runtime\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Laserstationens runtime mangler.' }
    $logs = Join-Path $env:LOCALAPPDATA 'Elevhub\Laserstation'
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    $stopLog = Join-Path $logs ('stop-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + $PID + '.log')
    Push-Location -LiteralPath $program
    try {
        $resultText = & $python -B -m app.laser_station --stop-config $config --stop-timeout 15 2> $stopLog
        if ($LASTEXITCODE -ne 0) { throw ('Laserstationens stop blev ikke bekræftet. Ingen proces er tvangsafsluttet. Se ' + $stopLog) }
    } finally { Pop-Location }
    $result = ($resultText -join "`n") | ConvertFrom-Json
    if ($result.stopped -ne $true) { throw 'Laserstationens stop blev ikke bekræftet.' }
    if ($result.already_stopped -eq $true) {
        $configArgument = '--config\s+(?:"' + [regex]::Escape($config) + '"|' + [regex]::Escape($config) + ')(?:\s|$)'
        $legacy = @(Get-CimInstance Win32_Process | Where-Object {
            $_.ExecutablePath -ieq $python -and [string]$_.CommandLine -match '\bapp\.laser_station\b' -and
            [string]$_.CommandLine -match $configArgument
        })
        if ($legacy.Count) { throw 'En ældre laserstation kører stadig uden lokal stopkontrol. Log af Windows på lasercomputeren, og prøv derefter opdateringen igen. Ingen proces er tvangsafsluttet.' }
    }
    Confirm-StationStopped
    return
}
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
    Confirm-StationStopped
} finally { Pop-Location }
} catch {
    $message = 'Laserstationen kunne ikke stoppes. ' + $_.Exception.Message
    if ($stopLog) { $message += "`nLog: " + $stopLog }
    if (-not $NoDialog) {
        try { $shell = New-Object -ComObject WScript.Shell; $null = $shell.Popup($message, 0, 'Laserstationen kunne ikke stoppes', 16) } catch { Write-Warning $message }
    }
    throw $message
}
