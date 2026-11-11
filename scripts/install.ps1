param([string]$InstallDirectory, [string]$ShortcutDirectory, [string]$PrinterSettings,
    [string]$DataDirectory, [string]$MigrateFrom,
    [ValidateSet('Boot','Logon','None')][string]$Autostart = 'Boot',
    [string]$TaskName = 'Printvaerksted', [int]$Port = 8765, [switch]$Quiet)
$ErrorActionPreference = 'Stop'
$packagePath = Split-Path -Parent $PSScriptRoot
$machine = $Autostart -eq 'Boot'
if (-not $InstallDirectory) { $InstallDirectory = if ($machine) { Join-Path $env:ProgramFiles 'Printvaerksted' } else { Join-Path $env:LOCALAPPDATA 'Printvaerksted' } }
if (-not $DataDirectory) { $DataDirectory = if ($machine) { Join-Path $env:ProgramData 'Printvaerksted\data' } else { Join-Path $InstallDirectory 'data' } }
if (-not $MigrateFrom -and $machine) { $MigrateFrom = Join-Path $env:LOCALAPPDATA 'Printvaerksted' }
$installPath = [IO.Path]::GetFullPath($InstallDirectory)
$dataPath = [IO.Path]::GetFullPath($DataDirectory)
if ($Port -lt 1024 -or $Port -gt 65535 -or $TaskName -notmatch '^Printvaerksted[\w-]*$') { throw 'Ugyldigt portnummer eller opgavenavn.' }
if ($machine -and -not $installPath.StartsWith($env:ProgramFiles.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Opstart foer login kraever programfiler i Program Files.' }
if ($installPath -eq $dataPath -or $installPath.StartsWith($dataPath.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Programfiler og data skal ligge i hver sin mappe.' }
if ($packagePath.StartsWith($installPath.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Installationsmappen maa ikke indeholde installationspakken.' }
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($machine -and -not $isAdmin) {
    $elevationArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'),'-InstallDirectory',('"' + $installPath + '"'),'-DataDirectory',('"' + $dataPath + '"'),'-MigrateFrom',('"' + $MigrateFrom + '"'),'-TaskName',$TaskName,'-Port',"$Port")
    if ($PrinterSettings) { $elevationArgs += @('-PrinterSettings',('"' + [IO.Path]::GetFullPath($PrinterSettings) + '"')) }
    if ($ShortcutDirectory) { $elevationArgs += @('-ShortcutDirectory',('"' + [IO.Path]::GetFullPath($ShortcutDirectory) + '"')) }
    if ($Quiet) { $elevationArgs += '-Quiet' }
    Write-Host 'Windows skal godkende installation og automatisk opstart foer login.'
    $elevated = Start-Process powershell.exe -ArgumentList $elevationArgs -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    if ($elevated.ExitCode -ne 0) {
        $errorLog = Join-Path $dataPath 'installation.log'
        if (Test-Path -LiteralPath $errorLog) {
            Write-Host "Fejl fra administratorinstallationen (log: $errorLog):"
            Get-Content -LiteralPath $errorLog -Tail 45 | ForEach-Object { Write-Host $_ }
        }
        throw "Installationen blev ikke gennemfoert (kode $($elevated.ExitCode)). Log: $errorLog. Hvis loggen mangler: hoejreklik START.cmd og vaelg Koer som administrator for at se fejlen."
    }
    exit 0
}
$transcriptStarted = $false
try {
# Include integrity checks and other early failures in the diagnostic log.
$null = New-Item -ItemType Directory -Path $dataPath -Force
$installationLog = Join-Path $dataPath 'installation.log'
Start-Transcript -Path $installationLog -Append | Out-Null
$transcriptStarted = $true
Write-Host "Installationslog: $installationLog"
if ($installPath.TrimEnd('\') -eq $packagePath.TrimEnd('\')) { throw 'Vaelg en anden installationsmappe end den udpakkede pakke.' }
if (-not [Environment]::Is64BitOperatingSystem) { throw 'Denne pakke kraever 64-bit Windows.' }
$markerName = 'printvaerksted-package.json'
$sourceMarker = Join-Path $packagePath $markerName
if (-not (Test-Path -LiteralPath $sourceMarker)) { throw 'Udpak hele installationspakken foerst.' }
$packagePython = Join-Path $packagePath 'runtime\python.exe'
Write-Host '1/5 Kontrollerer installationspakken. Det kan tage et par minutter...'
Push-Location $packagePath
try {
    & $packagePython -m app.installation verify-files
    if ($LASTEXITCODE -ne 0) { throw 'Pakken kunne ikke kontrolleres. Se fejlen ovenfor; ved manglende eller beskadigede filer skal hele Printvaerksted-mappen kopieres igen.' }
} finally { Pop-Location }
if ((Test-Path -LiteralPath $installPath) -and (Get-ChildItem -LiteralPath $installPath -Force | Select-Object -First 1)) {
    $markerPath = Join-Path $installPath $markerName
    if (-not (Test-Path -LiteralPath $markerPath)) { throw 'Mappen indeholder andre filer. Vaelg en tom mappe.' }
    if ((Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json).app -ne 'printvaerksted') { throw 'Mappen tilhoerer ikke Printvaerksted.' }
}
$installLocks = @()
try {
. (Join-Path $PSScriptRoot 'install-background.ps1')
Prepare-PrintInstallation
$null = New-Item -ItemType Directory -Path $installPath -Force
Write-Host '2/5 Installerer Printvaerksted og Bambu Studio...'
foreach ($item in @('app','web','runtime','tools','licenses','scripts','LAES MIG.txt','WINDOWS-KLAR.txt','SKOLEPAKKE.txt','README.md','OPSAETNING.md','TEKNISK.md','KLARGOERING-PA-SKOLEN.md', 'OPSTART-TEST.md','H1-PRINTTEST.md','BAMBU-INTEGRATION-ANMODNING.md','integrity.json',$markerName,'Aabn Printvaerksted.cmd','Proev demo.cmd','Stop Printvaerksted.cmd')) {
    Copy-Item -LiteralPath (Join-Path $packagePath $item) -Destination $installPath -Recurse -Force
}
Write-Host '3/5 Klargoer slicing og printerindstillinger...'
# The official Bambu distribution includes the offline Microsoft runtime installer.
$vcKey = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64' -ErrorAction SilentlyContinue
if (-not $vcKey -or $vcKey.Installed -ne 1 -or [version]($vcKey.Version.TrimStart('v')) -lt [version]'14.44.35211') {
    Write-Host 'Windows skal muligvis godkende Microsofts programdele til Bambu Studio.'
    $vcInstaller = Join-Path $installPath 'tools\prerequisites\vc_redist.x64.exe'
    $vcProcess = Start-Process -FilePath $vcInstaller -ArgumentList '/install /passive /norestart' -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    if ($vcProcess.ExitCode -notin @(0,1638,3010)) { throw 'Microsofts programdele kunne ikke installeres. Koer installationen igen.' }
}
if (-not $PrinterSettings) {
    $candidate = Join-Path $packagePath 'Skolens-printere-privat.json'
    if (Test-Path -LiteralPath $candidate) { $PrinterSettings = $candidate }
}
$installedPython = Join-Path $installPath 'runtime\python.exe'
Push-Location $installPath
try {
    $configureArgs = @('-m','app.installation','configure')
    if ($PrinterSettings) { $configureArgs += @('--printer-settings',[IO.Path]::GetFullPath($PrinterSettings)) }
    & $installedPython @configureArgs
    if ($LASTEXITCODE -ne 0) { throw 'Printerindstillingerne kunne ikke klargoeres.' }
    Write-Host '4/5 Pladen skal kontrolleres og frigives manuelt af laereren.'
} finally { Pop-Location }
Write-Host '5/5 Opretter genveje...'
if ($machine) {
    Write-Host 'Aabner Printvaerksted for computere paa samme lokale netvaerk...'
    . (Join-Path $PSScriptRoot 'lan-access.ps1')
    Enable-PrintLan $installPath $Port $TaskName
    Write-PrintNetworkInfo $installPath $Port
}
$shellObject = New-Object -ComObject WScript.Shell
$displayName = 'Printv' + [char]0x00e6 + 'rksted'
if ($ShortcutDirectory) {
    $desktopPath = [IO.Path]::GetFullPath($ShortcutDirectory)
    $menuPath = Join-Path $desktopPath 'Start-menu'
} elseif ($machine) {
    $desktopPath = [Environment]::GetFolderPath('CommonDesktopDirectory')
    $menuPath = Join-Path ([Environment]::GetFolderPath('CommonPrograms')) $displayName
} else {
    $desktopPath = [Environment]::GetFolderPath('DesktopDirectory')
    $menuPath = Join-Path ([Environment]::GetFolderPath('Programs')) $displayName
}
$null = New-Item -ItemType Directory -Path $desktopPath -Force
$null = New-Item -ItemType Directory -Path $menuPath -Force
function Add-PrintShortcut([string]$destination, [string]$arguments, [string]$description) {
    $shortcut = $shellObject.CreateShortcut($destination)
    $shortcut.TargetPath = Join-Path $installPath 'runtime\pythonw.exe'
    $shortcut.Arguments = '-m app.desktop' + $arguments
    $shortcut.WorkingDirectory = $installPath
    $shortcut.IconLocation = (Join-Path $installPath 'web\app.ico') + ',0'
    $shortcut.Description = $description
    $shortcut.Save()
}
Add-PrintShortcut (Join-Path $desktopPath "$displayName.lnk") '' 'Aabn skolens 3D-printvaerksted'
Add-PrintShortcut (Join-Path $menuPath "$displayName.lnk") '' 'Aabn skolens 3D-printvaerksted'
Add-PrintShortcut (Join-Path $menuPath "$displayName - demo.lnk") ' --demo' 'Afproev uden fysiske printere'
Add-PrintShortcut (Join-Path $menuPath "Stop $displayName.lnk") ' --stop' 'Stop koesystemet. Et fysisk print fortsaetter.'
$readmeShortcut = $shellObject.CreateShortcut((Join-Path $menuPath 'Kom godt i gang.lnk'))
$readmeShortcut.TargetPath = Join-Path $installPath 'LAES MIG.txt'
$readmeShortcut.Save()
if ($machine) {
    $networkShortcut = $shellObject.CreateShortcut((Join-Path $menuPath ($displayName + ' - netvaerksadresse.lnk')))
    $networkShortcut.TargetPath = Join-Path $installPath 'NETVAERK.txt'
    $networkShortcut.Save()
}
if ($Autostart -ne 'None') {
    . (Join-Path $PSScriptRoot 'autostart.ps1')
    Register-PrintBackground $installPath $TaskName $Autostart
}
if ($legacySource -and (Test-Path -LiteralPath (Join-Path $legacySource 'printvaerksted-package.json')) -and $legacySource -ne $installPath) {
    Push-Location $installPath
    try {
        & $installedPython -m app.installation redirect-legacy --source $legacySource
        if ($LASTEXITCODE -ne 0) { throw 'Gamle genveje kunne ikke knyttes til den faelles koe.' }
    } finally { Pop-Location }
}
} finally {
    foreach ($stream in $installLocks) { $stream.Dispose() }
}
if ($Autostart -ne 'None') {
    Start-ScheduledTask -TaskName $TaskName
    Push-Location $installPath
    try {
        & $installedPython -m app.desktop --no-window
        if ($LASTEXITCODE -ne 0) { throw 'Baggrundsstart kunne ikke bekraeftes. Se datamappens logfiler.' }
    } finally { Pop-Location }
}
Write-Host "Installeret: $installPath"
Write-Host "Genvej oprettet paa skrivebordet: $displayName"
if (-not $Quiet) {
    Add-Type -AssemblyName System.Windows.Forms
    $null = [Windows.Forms.MessageBox]::Show("Installationen er faerdig. Windows-opstart: $Autostart. Koe og pausevalg er bevaret. En ny koe er pauset. Medfoelgende printeroplysninger er indlaest ved ny installation. Aabn Printvaerksted via skrivebordsgenvejen.", $displayName)
}
} catch {
    Write-Host ('INSTALLATIONSFEJL: ' + $_.Exception.Message)
    Write-Host $_.InvocationInfo.PositionMessage
    if ($installationLog) { Write-Host "Log: $installationLog" }
    throw
} finally {
    if ($transcriptStarted) { Stop-Transcript | Out-Null }
}
