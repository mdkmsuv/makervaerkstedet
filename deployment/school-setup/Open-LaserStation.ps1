param(
    [string]$InstallPath = "$env:LOCALAPPDATA\MakervaerkstedetLaserstation",
    [switch]$NoBrowser
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$launcherLog = $null
try {
$InstallPath = [IO.Path]::GetFullPath($InstallPath)
$logs = Join-Path $InstallPath 'logs'
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$launcherLog = Join-Path $logs ('launcher-' + $stamp + '-' + $PID + '.log')
$active = Get-Content -LiteralPath (Join-Path $InstallPath 'active.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($active.kind -ne 'makervaerkstedet-laserstation' -or $active.version_directory -notmatch '^versions/[a-f0-9]{32}$') { throw 'Ukendt stationsinstallation.' }
$version = Join-Path $InstallPath $active.version_directory
$program = Join-Path $version 'program'
$configPath = Join-Path $version 'laser-installation.json'
function Read-CurrentConfiguration {
    $selected = Get-Content -LiteralPath (Join-Path $InstallPath 'active.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($selected.installation_id -cne $active.installation_id -or $selected.version_directory -cne $active.version_directory) { throw 'Stationsversionen ændredes under start. Åbn genvejen igen.' }
    $script:config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (($config.port -isnot [int] -and $config.port -isnot [long]) -or $config.port -lt 1 -or $config.port -gt 65535) { throw 'Laserstationens port er ugyldig.' }
    $script:url = 'http://127.0.0.1:' + $config.port + '/'
}
function Read-OwnRunningDescriptor {
    $path = Join-Path $InstallPath 'running.json'
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    try { $running = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json } catch {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
        throw 'Laserstationens lokale startkontrol kunne ikke læses.'
    }
    if ($running -isnot [pscustomobject] -or @($running.PSObject.Properties).Count -ne 6 -or
        ($running.format -isnot [int] -and $running.format -isnot [long]) -or $running.format -ne 1 -or $running.kind -cne 'makervaerkstedet-laserstation-running' -or
        [IO.Path]::GetFullPath([string]$running.root) -ine $InstallPath -or
        $running.installation_id -cne $active.installation_id -or
        $running.nonce -cnotmatch '^[a-f0-9]{32}$') { throw 'Stationskontrollen tilhører ikke den valgte installation. Stop stationen, og prøv igen.' }
    # An old version's crash record is never evidence for this HTTP response.
    # run_station may clean it only after obtaining the shared lifetime lease.
    if ($running.version_directory -cne $active.version_directory) { return $null }
    return $running
}
function Find-OwnStationProcesses {
    $rootArgument = '--install-root\s+(?:"' + [regex]::Escape($InstallPath) + '"|' + [regex]::Escape($InstallPath) + ')(?:\s|$)'
    return @(Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -ieq (Join-Path $program 'runtime\python.exe') -and
        [string]$_.CommandLine -match '\bapp\.laser_station_install\s+run(?:\s|$)' -and
        [string]$_.CommandLine -match $rootArgument
    })
}
function Read-StationState {
    if ($null -eq (Read-OwnRunningDescriptor)) { return $null }
    $owners = @(Find-OwnStationProcesses)
    if (-not $owners.Count) { return $null }
    try { $state = Invoke-RestMethod -Uri ($url + 'api/station/state') -TimeoutSec 1 } catch { return $null }
    if ($null -eq $state) { return $null }
    if ($null -eq $state.PSObject.Properties['server'] -or $null -eq $state.PSObject.Properties['stationId']) { throw 'Porten svarer ikke som en laserstation.' }
    if (([string]$state.server).TrimEnd('/') -cne ([string]$config.server).TrimEnd('/')) { throw 'Porten bruges af en station med en anden server.' }
    if ([string]$state.stationId -cne [string]$config.station_id) { throw 'Porten bruges af en anden laserstation.' }
    try { $health = Invoke-RestMethod -Uri ($url + 'api/runtime/health') -TimeoutSec 1 } catch { return $null }
    if ($null -eq $health -or $null -eq $health.PSObject.Properties['service'] -or $null -eq $health.PSObject.Properties['pid'] -or
        $health.service -cne 'laser_station' -or ($health.pid -isnot [int] -and $health.pid -isnot [long]) -or $health.pid -lt 1) { throw 'Porten svarer ikke med laserstationens procesidentitet.' }
    if (@($owners | Where-Object { [int]$_.ProcessId -eq [int]$health.pid }).Count -ne 1) { throw 'Porten ejes ikke af denne stationsinstallation.' }
    return $state
}
Read-CurrentConfiguration
$state = Read-StationState
if (-not $state) {
    $stderr = Join-Path $logs ($stamp + '.error.log')
    $process = $null
    if (-not @(Find-OwnStationProcesses).Count) {
        $arguments = '-B -m app.laser_station_install run --install-root "' + $InstallPath + '"'
        $process = Start-Process -FilePath (Join-Path $program 'runtime\python.exe') -ArgumentList $arguments -WorkingDirectory $program -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs ($stamp + '.log')) -RedirectStandardError $stderr
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(120)
    do {
        if ($null -ne $process -and $process.HasExited) { throw ('Laserstationen stoppede under start. Se ' + $stderr) }
        Read-CurrentConfiguration
        $state = Read-StationState
        if ($state) { break }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
}
if (-not $state) { throw ('Laserstationen svarer ikke efter 120 sekunder. Se ' + $stderr) }
Read-CurrentConfiguration
# The shortcut opens the start page: laser station, 3D print and administration.
# Headless callers use -NoBrowser; a desktop shortcut reports browser errors.
if (-not $NoBrowser) {
    $startPage = $url + 'makervaerkstedet.html'
    try { Start-Process $startPage }
    catch { throw ('Laserstationen kører, men browseren kunne ikke åbnes automatisk. Åbn adressen selv i en browser: ' + $startPage) }
}
} catch {
    $message = 'Laserstationen kunne ikke åbnes. ' + $_.Exception.Message
    if ($launcherLog) {
        try { [IO.File]::WriteAllText($launcherLog, $message + [Environment]::NewLine, [Text.UTF8Encoding]::new($false)) } catch { }
        $message += "`nLog: " + $launcherLog
    }
    if (-not $NoBrowser) {
        try { $shell = New-Object -ComObject WScript.Shell; $null = $shell.Popup($message, 0, 'Laserstationen kunne ikke åbnes', 16) } catch { Write-Warning $message }
    }
    throw $message
}
