# Rene verifikationer for start-server-test.ps1.
# Kør med: powershell -ExecutionPolicy Bypass -File .\test-start-server-test.ps1
# Testene dot-sourcer kun funktionerne og starter/stopper ingen servere.

$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'start-server-test.ps1'

$tokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$parseErrors) | Out-Null
if ($parseErrors.Count -gt 0) {
    throw ('PowerShell-parseren fandt fejl: ' + (($parseErrors | ForEach-Object Message) -join '; '))
}

. $scriptPath

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "FEJL: $Message" }
}

function Assert-Equal($Actual, $Expected, [string]$Message) {
    if ($Actual -ne $Expected) {
        throw "FEJL: $Message (fik '$Actual', forventede '$Expected')"
    }
}

$configuration = New-TestServerConfiguration `
    -ProjectRoot 'D:\Bambuslicer made easy' `
    -PythonPath 'D:\Bambuslicer made easy\.venv\Scripts\python.exe' `
    -ElevhubPath 'D:\Bambuslicer made easy\linux-elevhub\server\elevhub\elevhub.py' `
    -ConfigurationPath 'D:\Bambuslicer made easy\linux-elevhub\testmiljoe\vaert\elevhub-test.json' `
    -DataRoot 'D:\Elevhub-VM'
$definitions = @(New-TestProcessDefinitions $configuration)

$defaultConfigurationPath = Resolve-TestConfigurationPath
$customConfigurationPath = [System.IO.Path]::GetTempFileName()
$customConfigurationData = Get-Content -LiteralPath $defaultConfigurationPath -Raw | ConvertFrom-Json
$customConfigurationData.usikker_test = $false
$customConfigurationData | Add-Member -MemberType NoteProperty -Name tls -Value ([pscustomobject]@{
    certifikat = 'sikkerhed\server.crt'
    noegle = 'sikkerhed\server.key'
}) -Force
$customConfigurationData | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $customConfigurationPath -Encoding UTF8
Assert-Equal $defaultConfigurationPath (Join-Path $PSScriptRoot 'elevhub-test.json') 'Standardkonfigurationen skal være elevhub-test.json'
Assert-Equal (Resolve-TestConfigurationPath $customConfigurationPath) $customConfigurationPath 'En separat konfigurationssti skal kunne vælges'

Assert-Equal $definitions.Count 3 'Der skal være tre testserver-definitioner'
Assert-Equal (($definitions | ForEach-Object { $_.Porte } | Sort-Object -Unique) -join ',') '8765,8766,8767,8770,8771,8772,8773,8780' 'De centrale testporte skal bevares; lokale elevapps bruger loopback-serviceporte'
Assert-True ($configuration.AllowedExecutables.Count -ge 1) 'Der skal være mindst én tilladt Python executable'
foreach ($definition in $definitions) {
    Assert-True ($definition.Kommando -match '--data') "$($definition.Navn) skal have en eksplicit testdatasti"
    Assert-True ($definition.Kommando -match [regex]::Escape('D:\Elevhub-VM')) "$($definition.Navn) skal pege på D:\Elevhub-VM"
}

try {
    $customConfiguration = New-TestServerConfiguration `
        -ProjectRoot 'D:\Bambuslicer made easy' `
        -PythonPath 'D:\Bambuslicer made easy\.venv\Scripts\python.exe' `
        -ElevhubPath 'D:\Bambuslicer made easy\linux-elevhub\server\elevhub\elevhub.py' `
        -ConfigurationPath $customConfigurationPath `
        -DataRoot 'D:\Elevhub-VM'
    $customElevhub = @(New-TestProcessDefinitions $customConfiguration | Where-Object Navn -eq 'Elevhub-serveren')
    Assert-Equal $customConfiguration.Protocol 'https' 'TLS-konfigurationen skal vælge HTTPS'
    Assert-True ($customConfiguration.CertificatePath -match '\\sikkerhed\\server\.crt$') 'TLS-konfigurationen skal finde certifikatet fra tls.certifikat'
    Assert-True ($customConfiguration.CaPath -match '\\sikkerhed\\ca\.crt$') 'TLS-healthcheck skal bruge ca.crt ved siden af certifikatet'
    Assert-True ($customElevhub[0].Tjek -match '^https://') 'Elevhub-healthcheck skal bruge HTTPS ved TLS'
    Assert-True ($customElevhub[0].Kommando -match [regex]::Escape($customConfigurationPath)) 'Elevhub-kommandoen skal bruge den valgte konfigurationssti'
    Assert-True ($customElevhub[0].Kommando -match [regex]::Escape('D:\Elevhub-VM\elevhub-test')) 'Alternativ konfiguration må ikke ændre testdata'
}
finally {
    Remove-Item -LiteralPath $customConfigurationPath -Force -ErrorAction SilentlyContinue
}

$print = $definitions[0]
$launcherExecutable = @($configuration.AllowedExecutables | Where-Object { (Normalize-TestPath $_) -ne (Normalize-TestPath $configuration.PythonPath) } | Select-Object -First 1)
Assert-True ($launcherExecutable.Count -eq 1) 'pyvenv.cfg skal bidrage med base-Python som tilladt launcher-executable'
$launcherParent = [pscustomobject]@{ Name = 'python.exe'; ExecutablePath = $configuration.PythonPath; CommandLine = $print.Kommando; ProcessId = 200; ParentProcessId = 1; CreationDate = '2026-09-19 09:00:00' }
$launcherChild = [pscustomobject]@{ Name = 'python.exe'; ExecutablePath = $launcherExecutable[0]; CommandLine = $print.Kommando; ProcessId = 201; ParentProcessId = 200; CreationDate = '2026-09-19 09:00:01' }
$ownedLauncherPair = @(Get-OwnedTestProcesses -Definition $print -Snapshot @($launcherParent, $launcherChild))
Assert-Equal $ownedLauncherPair.Count 1 'En verificeret venv parent-child skal tælle som én logisk testserver'
Assert-Equal $ownedLauncherPair[0].ProcessId 201 'Den verificerede child-proces skal repræsentere launcher-parret'
$independentDuplicate = [pscustomobject]@{ Name = 'python.exe'; ExecutablePath = $launcherExecutable[0]; CommandLine = $print.Kommando; ProcessId = 202; ParentProcessId = 999; CreationDate = '2026-09-19 09:00:02' }
Assert-Equal @(Get-OwnedTestProcesses -Definition $print -Snapshot @($launcherParent, $launcherChild, $independentDuplicate)).Count 2 'Uafhængige dubletter skal fortsat afvises'
$sameCommand = [pscustomobject]@{ Name = 'python.exe'; ExecutablePath = $configuration.PythonPath; CommandLine = $print.Kommando; ProcessId = 101; CreationDate = '2026-09-19 08:00:00' }
$launcherCommand = [pscustomobject]@{ Name = 'python.exe'; ExecutablePath = $launcherExecutable[0]; CommandLine = $print.Kommando; ProcessId = 106; CreationDate = '2026-09-19 08:00:01' }
$sameCommandDifferentCase = [pscustomobject]@{ Name = 'python.exe'; ExecutablePath = $configuration.PythonPath; CommandLine = $print.Kommando.ToUpperInvariant(); ProcessId = 102; CreationDate = '2026-09-19 08:00:02' }
$realServer = [pscustomobject]@{
    Name = 'python.exe'
    ExecutablePath = $configuration.PythonPath
    CommandLine = '"D:\Bambuslicer made easy\.venv\Scripts\python.exe" "D:\Bambuslicer made easy\app\laser_server.py" --data "C:\ProgramData\Designvaerksted"'
    ProcessId = 103
}
$wrongInterpreter = [pscustomobject]@{ Name = 'python.exe'; ExecutablePath = 'C:\Python\python.exe'; CommandLine = $print.Kommando.Replace($configuration.PythonPath, 'C:\Python\python.exe'); ProcessId = 104; CreationDate = '2026-09-19 08:00:04' }
$wrongExecutable = [pscustomobject]@{ Name = 'other.exe'; ExecutablePath = 'C:\Python\other.exe'; CommandLine = $print.Kommando; ProcessId = 105; CreationDate = '2026-09-19 08:00:05' }
$missingExecutableField = [pscustomobject]@{ Name = 'python.exe'; CommandLine = $print.Kommando; ProcessId = 107; CreationDate = '2026-09-19 08:00:06' }

Assert-True (Test-ProcessIdentity $sameCommand $print) 'Den præcise testkommando skal genkendes'
Assert-True (Test-ProcessIdentity $launcherCommand $print) 'Venv-launcherens base-Python skal accepteres'
Assert-True (Test-ProcessIdentity $sameCommandDifferentCase $print) 'Kommandoer må være ufølsomme over for Windows-casing'
Assert-True (-not (Test-ProcessIdentity $realServer $print)) 'En rigtig app-server med samme Python skal afvises'
Assert-True (-not (Test-ProcessIdentity $wrongInterpreter $print)) 'En anden Python-interpreter skal afvises'
Assert-True (-not (Test-ProcessIdentity $wrongExecutable $print)) 'En anden executable skal afvises'
Assert-True (Test-ProcessIdentity $missingExecutableField $print) 'Manglende ExecutablePath må ikke gøre matching usikkert ud over kommandokravet'

$listeners = @(
    [pscustomobject]@{ LocalPort = 8765; OwningProcess = 501 },
    [pscustomobject]@{ LocalPort = 8766; OwningProcess = 101 },
    [pscustomobject]@{ LocalPort = 9999; OwningProcess = 502 }
)
$conflicts = @(Find-PortConflicts $listeners @(8765, 8766) @(101))
Assert-Equal $conflicts.Count 1 'Kun en fremmed listener på en testport skal være konflikt'
Assert-Equal $conflicts[0].OwningProcess 501 'Konflikten skal rapportere den fremmede PID'
Assert-Equal (Find-PortConflicts $listeners @(8765, 8766) @(501, 101)).Count 0 'Ejede testprocesser skal ikke give portkonflikt'

$stopCalls = [System.Collections.Generic.List[int]]::new()
$reusedPid = [pscustomobject]@{ Name = 'python.exe'; ExecutablePath = $configuration.PythonPath; CommandLine = $print.Kommando; ProcessId = 101; CreationDate = '2026-09-19 09:00:00' }
Stop-OwnedTestProcesses -Processes @($sameCommand) -Definitions $definitions -SnapshotProvider { @($reusedPid) } -StopProcess { param($ProcessId) $stopCalls.Add([int]$ProcessId) }
Assert-Equal $stopCalls.Count 0 'En genbrugt PID med anden CreationDate må ikke stoppes'
$stopCalls.Clear()
Stop-OwnedTestProcesses -Processes @($sameCommand) -Definitions $definitions -SnapshotProvider { @($sameCommand) } -StopProcess { param($ProcessId) $stopCalls.Add([int]$ProcessId) }
Assert-Equal $stopCalls.Count 1 'Samme PID, CreationDate og kommando skal kunne stoppes'
Assert-Equal $stopCalls[0] 101 'Det validerede PID skal sendes til stop-funktionen'
$stopCalls.Clear()
Stop-OwnedTestProcesses -Processes @($ownedLauncherPair) -Definitions $definitions -SnapshotProvider { @($launcherParent, $launcherChild) } -StopProcess { param($ProcessId) $stopCalls.Add([int]$ProcessId) }
Assert-Equal $stopCalls.Count 2 'Et verificeret launcher-par skal stoppes samlet'
Assert-Equal ($stopCalls -join ',') '200,201' 'Launcher-parent og child skal stoppes i PID-rækkefølge'

$oppositeFlagsRejected = $false
try { Invoke-TestServer -Stop -CheckOnly } catch { $oppositeFlagsRejected = $_.Exception.Message -match 'kan ikke bruges samtidig' }
Assert-True $oppositeFlagsRejected 'Modstridende -Stop og -CheckOnly skal afvises før procesadgang'

$scriptText = Get-Content -Raw -LiteralPath $scriptPath
Assert-True ($scriptText -notmatch 'elevindgang\.py') 'Scriptet må ikke stoppe ældre elevindgang-processer bredt'
Assert-True ($scriptText -notmatch 'Kendetegn') 'Scriptet må ikke bruge bredt proces-kendetegn som ejerskab'
Assert-True ($scriptText -notmatch 'Start-Process') 'Teststarten skal fortsat bruge den skjulte CIM-start'
Assert-True ($scriptText -match 'create_default_context\(cafile=cafile\)') 'HTTPS-healthcheck skal validere med den lokale CA'
Assert-True ($scriptText -notmatch 'SkipCertificateCheck') 'HTTPS-healthcheck må ikke springe certifikatkontrollen over'

Write-Host 'OK: parser, proces-ejerskab, launcher-par, TLS-konfiguration, isolerede datastier, porte og portkonflikter er verificeret.' -ForegroundColor Green
