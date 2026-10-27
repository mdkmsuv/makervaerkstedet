[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('Install','Update','ResumeInstall')][string]$Action,
    [string]$PackageRoot,
    [string]$ConnectionFile,
    [string]$InstallPath = "$env:LOCALAPPDATA\MakervaerkstedetLaserstation",
    [string]$PlatesPath,
    [string]$BeamStudioPath,
    [string]$FluxApiPath,
    [ValidateRange(1024,65535)][int]$Port = 8790,
    [switch]$CheckOnly,
    [switch]$NoStart,
    [switch]$NoShortcut,
    [switch]$NoTrust,
    [switch]$FinishSetup
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $PackageRoot) { $PackageRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }
$PackageRoot = [IO.Path]::GetFullPath($PackageRoot)
$InstallPath = [IO.Path]::GetFullPath($InstallPath)
if ($Action -eq 'Install' -and -not $ConnectionFile -and -not $FinishSetup) {
    $ConnectionFile = (Read-Host 'Vælg laser-station-connection.json fra skolens server').Trim().Trim('"')
    if (-not $ConnectionFile) { throw 'Vælg skolens forbindelsesfil for at installere laserstationen.' }
}
$python = Join-Path $PackageRoot 'runtime\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Brug den udpakkede Makerværkstedet-skolepakke med runtime\python.exe.' }
$arguments = @('-B','-m','app.laser_station_install','install','--package-root',$PackageRoot,'--install-root',$InstallPath,'--action',$Action)
foreach ($pair in @(@('ConnectionFile','--connection-file'),@('PlatesPath','--plates'),@('BeamStudioPath','--beam-studio'),@('FluxApiPath','--flux-api'))) {
    $value = Get-Variable -Name $pair[0] -ValueOnly
    if ($value) { $arguments += @($pair[1], [string]$value) }
}
if ($PSBoundParameters.ContainsKey('Port')) { $arguments += @('--port', [string]$Port) }
if ($CheckOnly) { $arguments += '--check-only' }
if ($FinishSetup) {
    if ($Action -ne 'Update' -or $CheckOnly -or $ConnectionFile -or $PlatesPath -or $BeamStudioPath -or $FluxApiPath -or $PSBoundParameters.ContainsKey('Port')) { throw 'FinishSetup bruges alene med -Action Update og eksisterende InstallPath; den geninstallerer ikke programmet.' }
} else {
    Push-Location -LiteralPath $PackageRoot
    try {
        & $python @arguments
        if ($LASTEXITCODE -ne 0) { throw 'Laserstationen blev ikke installeret/opdateret. Se forklaringen ovenfor. En journalført afbrydelse af ren installation genoptages særskilt med -Action ResumeInstall.' }
    } finally { Pop-Location }
}
if ($CheckOnly) { return }
$active = Get-Content -LiteralPath (Join-Path $InstallPath 'active.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($active.kind -ne 'makervaerkstedet-laserstation' -or $active.version_directory -notmatch '^versions/[a-f0-9]{32}$') { throw 'Stationsversionen kunne ikke kontrolleres.' }
$version = Join-Path $InstallPath $active.version_directory
$program = Join-Path $version 'program'
Push-Location -LiteralPath $program
try {
    $infoText = & (Join-Path $program 'runtime\python.exe') -B -m app.laser_station_install info --install-root $InstallPath
    if ($LASTEXITCODE -ne 0) { throw 'Den installerede stations offentlige forbindelse kunne ikke valideres.' }
    $info = ($infoText -join "`n") | ConvertFrom-Json
    $connection = $info.connection
} finally { Pop-Location }
$launcher = Join-Path $version 'program\deployment\school-setup\Open-LaserStation.ps1'
try {
if (-not $NoTrust) {
    # Only the already validated public CA is trusted, for this Windows user.
    # No private key, machine-wide certificate store, admin task or firewall rule.
    $pem = [string]$connection.caCertificatePem
    $encoded = ($pem -replace '-----BEGIN CERTIFICATE-----','' -replace '-----END CERTIFICATE-----','' -replace '\s','')
    $certificate = New-Object Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList @(,[Convert]::FromBase64String($encoded))
    if ($certificate.HasPrivateKey) { throw 'Forbindelsesfilen må ikke indeholde en privat nøgle.' }
    $store = New-Object Security.Cryptography.X509Certificates.X509Store('Root','CurrentUser')
    try { $store.Open('ReadWrite'); $store.Add($certificate) } finally { $store.Close(); $certificate.Dispose() }
}
if (-not $NoShortcut) {
    $shell = New-Object -ComObject WScript.Shell
    $desktop = [Environment]::GetFolderPath('Desktop')
    $shortcut = $shell.CreateShortcut((Join-Path $desktop 'Laserstation.lnk'))
    $shortcut.TargetPath = 'powershell.exe'
    $shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $launcher + '" -InstallPath "' + $InstallPath + '"'
    $shortcut.WorkingDirectory = $InstallPath
    $shortcut.Save()
    $stopShortcut = $shell.CreateShortcut((Join-Path $desktop 'Stop laserstation.lnk'))
    $stopShortcut.TargetPath = 'powershell.exe'
    $stopLauncher = Join-Path $version 'program\deployment\school-setup\Stop-LaserStation.ps1'
    $stopShortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $stopLauncher + '" -InstallPath "' + $InstallPath + '"'
    $stopShortcut.WorkingDirectory = $InstallPath
    $stopShortcut.Save()
    $control = $shell.CreateShortcut((Join-Path $desktop 'Makerværkstedet.url'))
    $control.TargetPath = ([string]$connection.server_url).TrimEnd('/') + '/kontrolpanel'
    $control.Save()
}
} catch {
    throw ('Programmet er installeret og valgt som version ' + $active.release + '. Færdiggørelse af CA/genveje fejlede: ' + $_.Exception.Message + '. Kør Setup-LaserStation.ps1 -Action Update -FinishSetup -InstallPath "' + $InstallPath + '" for kun at færdiggøre brugeropsætningen.')
}
Write-Host ('Laserstation klar: ' + $connection.school_name + ' | ' + $active.release)
Write-Host 'Kø, skoleopsætning og lærerlogin findes fortsat på skolens centrale server.'
if ($NoTrust) { Write-Host 'Browserens CA-tillid er ikke ændret. Laserstationen bruger stadig den validerede offentlige CA.' }
if (-not $NoStart) { & $launcher -InstallPath $InstallPath }
