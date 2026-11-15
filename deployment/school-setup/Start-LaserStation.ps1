param(
    [string]$ConfigPath,
    [Parameter(Mandatory)][string]$Root,
    [switch]$ActiveGeneration,
    [string]$InstallationId,
    [string]$ExpectedSelectionSha256,
    [switch]$OpenBrowser,
    [switch]$NoBrowser
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-LocalPort([object]$port) {
    if ($port -is [bool] -or $port -isnot [int] -and $port -isnot [long] -or $port -lt 1 -or $port -gt 65535) {
        throw 'Laserstationens port skal være et helt tal mellem 1 og 65535.'
    }
    return [int]$port
}

function Test-ArgumentPair([string[]]$items, [string]$flag, [string]$value) {
    for ($i = 0; $i -lt $items.Count - 1; $i++) {
        if ($items[$i] -eq $flag -and $items[$i + 1] -eq $value) { return $true }
    }
    return $false
}

function Assert-GenerationCommandCoherence([object]$command, [string]$Root) {
    $expectedExecute = [IO.Path]::GetFullPath((Join-Path $Root 'runtime\python.exe'))
    $actualExecute = [IO.Path]::GetFullPath([string]$command.Execute)
    $expectedWorkingDirectory = [IO.Path]::GetFullPath($Root)
    $actualWorkingDirectory = [IO.Path]::GetFullPath([string]$command.WorkingDirectory)
    if ($actualExecute -ine $expectedExecute) { throw 'Aktiv generationsstart returnerede en forkert runtime.' }
    if ($actualWorkingDirectory -ine $expectedWorkingDirectory) { throw 'Aktiv generationsstart returnerede en forkert arbejdsmappe.' }
    $formatter = Get-Command ConvertTo-GenerationCommandLine -ErrorAction SilentlyContinue
    if ($null -eq $formatter) { throw 'Aktiv generationsstart mangler sin kommandoformatter.' }
    $expectedArguments = [string](& $formatter.Name -Values @($command.ArgumentList))
    if ([string]$command.Arguments -cne $expectedArguments) { throw 'Aktiv generationsstart har uoverensstemmende kommandoargumenter.' }
}

function Get-ActiveCommand([string]$Root, [string]$InstallationId, [string]$Entry, [object[]]$Arguments, [string]$ExpectedSelection) {
    $helper = Join-Path $Root 'server\generation-startup.ps1'
    if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) { throw 'Aktiv generationsstart mangler sin starthelper.' }
    . $helper
    $parameters = @{ InstallRoot=$Root; InstallationId=$InstallationId; Entry=$Entry; Arguments=$Arguments }
    if (-not [string]::IsNullOrWhiteSpace($ExpectedSelection)) { $parameters.ExpectedSelection = $ExpectedSelection }
    try { $command = Get-GenerationCommand @parameters } catch {
        throw ('Aktiv generationsstart kunne ikke bygges: ' + $_.Exception.Message)
    }
    $properties = if ($null -eq $command) { @() } else { @($command.PSObject.Properties.Name) }
    if ($null -eq $command -or @('Execute', 'ArgumentList', 'Arguments', 'WorkingDirectory' | Where-Object { $properties -notcontains $_ }).Count) {
        throw 'Aktiv generationsstart returnerede en ugyldig kommando.'
    }
    $list = @($command.ArgumentList | ForEach-Object { [string]$_ })
    if (-not ($list -contains '--active') -or
        -not (Test-ArgumentPair $list '--expected-installation-id' $InstallationId) -or
        -not (Test-ArgumentPair $list '--entry' $Entry) -or
        -not (Test-ArgumentPair $list '--runtime-root' $Root)) {
        throw 'Aktiv generationsstart returnerede ikke den forventede faste kommando.'
    }
    if (-not [string]::IsNullOrWhiteSpace($ExpectedSelection) -and
        -not (Test-ArgumentPair $list '--expected-selection' $ExpectedSelection)) {
        throw 'Aktiv generationsstart er ikke bundet til samme generationsvalg.'
    }
    if ($Entry -eq 'laser-station' -and -not (Test-ArgumentPair $list '--runtime-role' 'server')) { throw 'Alt-i-én-starten mangler den forventede serverrolle.' }
    Assert-GenerationCommandCoherence $command $Root
    if (-not (Test-Path -LiteralPath ([string]$command.Execute) -PathType Leaf)) { throw 'Aktiv generationsstartens runtime mangler.' }
    return $command
}

function Get-ActiveLaserUrl([string]$Root, [string]$InstallationId, [string]$ExpectedSelection) {
    $command = Get-ActiveCommand $Root $InstallationId 'school-admin' @('laser-url') $ExpectedSelection
    $out = [IO.Path]::GetTempFileName(); $err = [IO.Path]::GetTempFileName()
    try {
        $process = Start-Process -FilePath ([string]$command.Execute) -ArgumentList ([string]$command.Arguments) -WorkingDirectory ([string]$command.WorkingDirectory) `
            -RedirectStandardOutput $out -RedirectStandardError $err -Wait -PassThru -WindowStyle Hidden
        if ($process.PSObject.Properties.Name -contains 'ExitCode' -and $process.ExitCode -ne 0) { throw 'Generationsforespørgslen fejlede.' }
        try { $payload = Get-Content -LiteralPath $out -Raw -Encoding UTF8 | ConvertFrom-Json } catch { throw 'Generationsforespørgslen returnerede ugyldig JSON.' }
        if ($null -eq $payload -or -not ($payload.PSObject.Properties.Name -contains 'laser')) { throw 'Generationsforespørgslen mangler laser-feltet.' }
        if ($null -eq $payload.laser -or [string]::IsNullOrWhiteSpace([string]$payload.laser)) { return $null }
        $uri = [Uri]$payload.laser
        if ($uri.Scheme -notin @('http', 'https') -or -not $uri.IsLoopback -or $uri.Port -lt 1 -or $uri.Port -gt 65535) { throw 'Generationsforespørgslen returnerede en ugyldig laser-URL.' }
        return [string]$uri
    } finally {
        Remove-Item -LiteralPath $out, $err -Force -ErrorAction SilentlyContinue
    }
}

function Wait-StationReady([string]$Url, [object]$Config, [object]$Process, [string]$ErrorLog, [int[]]$ExpectedPids) {
    $expectedServer = 'http://127.0.0.1:8767'
    if ($null -ne $Config.PSObject.Properties['server']) { $expectedServer = [string]$Config.server }
    $machine = 'fbb2'
    if ($null -ne $Config.PSObject.Properties['machine']) { $machine = [string]$Config.machine }
    $stationId = 'laser-' + $machine
    if ($null -ne $Config.PSObject.Properties['station_id']) { $stationId = [string]$Config.station_id }
    $deadline = [DateTime]::UtcNow.AddSeconds(120)
    do {
        if ($null -ne $Process -and $Process.HasExited) { throw ('Laserstationen stoppede under start. Se ' + $ErrorLog) }
        $state = $null
        try { $state = Invoke-RestMethod -Uri ($Url.TrimEnd('/') + '/api/station/state') -TimeoutSec 1 } catch { }
        if ($null -ne $state) {
            if ($null -eq $state.PSObject.Properties['server'] -or $null -eq $state.PSObject.Properties['stationId']) { throw 'Porten svarer ikke som en laserstation.' }
            if (([string]$state.server).TrimEnd('/') -cne $expectedServer.TrimEnd('/')) { throw 'Porten bruges af en station med en anden server.' }
            if ([string]$state.stationId -cne $stationId) { throw 'Porten bruges af en anden laserstation.' }
            $health = $null
            try { $health = Invoke-RestMethod -Uri ($Url.TrimEnd('/') + '/api/runtime/health') -TimeoutSec 1 } catch { }
            if ($null -eq $health) { Start-Sleep -Milliseconds 250; continue }
            if ($null -eq $health.PSObject.Properties['service'] -or $null -eq $health.PSObject.Properties['pid'] -or
                $health.service -cne 'laser_station' -or ($health.pid -isnot [int] -and $health.pid -isnot [long]) -or $ExpectedPids -notcontains [int]$health.pid) { throw 'Porten ejes ikke af den forventede stationsproces.' }
            return
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw ('Laserstationen svarer ikke efter 120 sekunder. Se ' + $ErrorLog)
}

$launcherLog = $null
try {
    $logDir = Join-Path $env:LOCALAPPDATA 'Elevhub\Laserstation'
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $launcherLog = Join-Path $logDir ('launcher-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + $PID + '.log')
$Root = [IO.Path]::GetFullPath($Root)
$control = Join-Path $Root 'control'
$controlEntry = @(Get-ChildItem -LiteralPath $Root -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq 'control' })
if (-not $ActiveGeneration -and $controlEntry.Count) { throw 'Aktiv generationsstyring er til stede; legacy-start er afvist.' }

if ($ActiveGeneration) {
    if ($PSBoundParameters.ContainsKey('ConfigPath')) { throw '-ConfigPath må ikke bruges sammen med -ActiveGeneration.' }
    if ($InstallationId -notmatch '^[0-9a-fA-F]{32}$') { throw 'InstallationId skal være 32 hex-tegn.' }
    if (-not [string]::IsNullOrWhiteSpace($ExpectedSelectionSha256) -and $ExpectedSelectionSha256 -notmatch '^[0-9a-fA-F]{64}$') { throw 'ExpectedSelectionSha256 skal være 64 hex-tegn.' }
    $leaseHelper = Join-Path $Root 'server\generation-startup.ps1'
    if (-not (Test-Path -LiteralPath $leaseHelper -PathType Leaf)) { throw 'Aktiv generationsstart mangler sin starthelper.' }
    . $leaseHelper
    if ($null -eq (Get-Command New-GenerationLease -ErrorAction SilentlyContinue) -or
        $null -eq (Get-Command Close-GenerationLease -ErrorAction SilentlyContinue)) {
        throw 'Aktiv generationsstart mangler sin leasehelper.'
    }
    $leaseParameters = @{ InstallRoot=$Root; InstallationId=$InstallationId }
    if (-not [string]::IsNullOrWhiteSpace($ExpectedSelectionSha256)) { $leaseParameters.ExpectedSelection = $ExpectedSelectionSha256 }
    $lease = New-GenerationLease @leaseParameters
    try {
    $snapshot = $lease.Snapshot
    if ([string]$snapshot.installation_id -ine $InstallationId -or
        [IO.Path]::GetFullPath([string]$snapshot.runtime_root) -ine $Root -or
        [string]::IsNullOrWhiteSpace([string]$snapshot.selection_sha256)) { throw 'Den aktive generation passer ikke til laserstationen.' }
    if (-not [string]::IsNullOrWhiteSpace($ExpectedSelectionSha256) -and [string]$snapshot.selection_sha256 -ine $ExpectedSelectionSha256) { throw 'Generationsvalget ændredes før laserstart.' }
    $selectionHash = [string]$snapshot.selection_sha256
    $station = Get-ActiveCommand $Root $InstallationId 'laser-station' @('--runtime-role', 'server') $selectionHash
    $python = [string]$station.Execute
    $urls = Get-ActiveLaserUrl $Root $InstallationId $selectionHash
    if ($null -eq $urls) { throw 'Den aktive generation har ingen lokal laserstation.' }
    $activeConfigPath = Join-Path ([string]$snapshot.data_root) 'laserstation\laser-installation.json'
    $config = Get-Content -LiteralPath $activeConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($null -eq $config -or $config -isnot [pscustomobject]) { throw 'Laserstationens konfiguration skal være et JSON-objekt.' }
    $port = 8790
    if ($null -ne $config.PSObject.Properties['port']) { $port = Assert-LocalPort $config.port }
    if (([Uri]$urls).Port -ne $port) { throw 'Generationsforespørgslens port matcher ikke laserstationens konfiguration.' }
    $process = $null
    $stderr = $launcherLog
    $processes = @(Get-CimInstance Win32_Process)
    $activeArgumentsPattern = [regex]::Escape([string]$station.Arguments)
    $stationEntryPattern = '(?:^|\s|")laser-station(?:\s|$|")'
    $legacy = @($processes | Where-Object {
        $_.ExecutablePath -eq $python -and
        (([string]$_.CommandLine) -match '\bapp[./\\]laser_station\b' -or
         (([string]$_.CommandLine) -match 'app[./\\]generation_runtime\.py' -and ([string]$_.CommandLine) -match $stationEntryPattern)) -and
        ([string]$_.CommandLine) -notmatch $activeArgumentsPattern
    })
    if ($legacy.Count) { throw 'En legacy-laserstation bruger stadig denne runtime; luk den før generationsstart.' }
    $active = @($processes | Where-Object {
        $_.ExecutablePath -eq $python -and $_.CommandLine -match '--active' -and
        ([string]$_.CommandLine) -match $activeArgumentsPattern
    })
    if (-not $active.Count) {
        $logDir = Join-Path $env:LOCALAPPDATA 'Elevhub\Laserstation'; New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
        $stdout = Join-Path $logDir "station-$stamp.out.log"; $stderr = Join-Path $logDir "station-$stamp.err.log"
        $process = Start-Process -FilePath $python -ArgumentList ([string]$station.Arguments) -WorkingDirectory ([string]$station.WorkingDirectory) -WindowStyle Hidden `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    }
    $expectedPids = if ($null -ne $process) { @([int]$process.Id) } else { @($active | ForEach-Object { [int]$_.ProcessId }) }
    Wait-StationReady $urls $config $process $stderr $expectedPids
    if ($OpenBrowser -and -not $NoBrowser) { Start-Process $urls }
    } finally {
        Close-GenerationLease -Lease $lease
    }
    return
}

if ([string]::IsNullOrEmpty($ConfigPath)) { throw '-ConfigPath er påkrævet i legacy-tilstand.' }
$ConfigPath = [IO.Path]::GetFullPath($ConfigPath)
try { $config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { throw 'Laserstationens konfiguration kunne ikke læses.' }
if ($null -eq $config -or $config -isnot [pscustomobject]) { throw 'Laserstationens konfiguration skal være et JSON-objekt.' }
$port = 8790
if ($null -ne $config.PSObject.Properties['port']) { $port = $config.port }
$port = Assert-LocalPort $port
$python = Join-Path $Root 'runtime\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or -not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) { throw 'Laserstationens runtime eller konfiguration mangler.' }
$logDir = Join-Path $env:LOCALAPPDATA 'Elevhub\Laserstation'; New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$existing = @(Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -eq $python -and $_.CommandLine -match '\bapp\.laser_station\b' -and
    $_.CommandLine -match [regex]::Escape($ConfigPath)
})
$process = $null
$stderr = $launcherLog
if (-not $existing.Count) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $stdout = Join-Path $logDir "station-$stamp.out.log"; $stderr = Join-Path $logDir "station-$stamp.err.log"
    $arguments = '-B -m app.laser_station --runtime-role server --config "{0}"' -f $ConfigPath
    $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
}
$url = 'http://127.0.0.1:{0}/' -f $port
$expectedPids = if ($null -ne $process) { @([int]$process.Id) } else { @($existing | ForEach-Object { [int]$_.ProcessId }) }
Wait-StationReady $url $config $process $stderr $expectedPids
if ($OpenBrowser -and -not $NoBrowser) { Start-Process $url }
} catch {
    $message = 'Laserstationen kunne ikke åbnes. ' + $_.Exception.Message
    if ($launcherLog) {
        try { [IO.File]::WriteAllText($launcherLog, $message + [Environment]::NewLine, [Text.UTF8Encoding]::new($false)) } catch { }
        $message += "`nLog: " + $launcherLog
    }
    if ($OpenBrowser -and -not $NoBrowser) {
        try { $shell = New-Object -ComObject WScript.Shell; $null = $shell.Popup($message, 0, 'Laserstationen kunne ikke åbnes', 16) } catch { Write-Warning $message }
    }
    throw $message
}
