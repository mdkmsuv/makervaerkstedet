# Start (eller stop) hele serversiden i testmiljøet: værkstederne og Elevhub-serveren.
#
#   powershell -ExecutionPolicy Bypass -File "D:\Bambuslicer made easy\linux-elevhub\testmiljoe\vaert\start-server-test.ps1"
#   powershell -ExecutionPolicy Bypass -File "...\start-server-test.ps1" -Stop
#   powershell -ExecutionPolicy Bypass -File "...\start-server-test.ps1" -CheckOnly
#   powershell -ExecutionPolicy Bypass -File "...\start-server-test.ps1" -Konfiguration "...\elevhub-pilot-https.json"
#
# Starter tre baggrundsprocesser i skjulte vinduer, som kører, til de stoppes eller Windows genstartes:
#   printvaerksted-test.py   Printværkstedet: rigtig kø og lagskæring, simuleret printer   127.0.0.1:8765
#   app.laser_server         Designværkstedet (laser og folie): elever 127.0.0.1:8766, lærer 127.0.0.1:8767
#   elevhub.py               Elevindgange og elevhub på 192.168.50.1:8770-8773, lærerpanel på 127.0.0.1:8780
# Alle bruger egne testdata i D:\Elevhub-VM\. Værkstedernes rigtige datamapper bruges ikke.
# Kræver ikke administrator. Elevcomputerne når kun elevindgangene, når trin 6 (firewall) er kørt.

param(
    [switch]$Stop,
    [switch]$CheckOnly,
    [string]$Konfiguration
)

$ErrorActionPreference = 'Stop'

$Projekt = 'D:\Bambuslicer made easy'
$Python  = Join-Path $Projekt '.venv\Scripts\python.exe'   # ikke pythonw: designværkstedet logger til stderr
$Elevhub = Join-Path $Projekt 'linux-elevhub\server\elevhub\elevhub.py'
$Data    = 'D:\Elevhub-VM'

function Resolve-TestConfigurationPath {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return (Join-Path $PSScriptRoot 'elevhub-test.json')
    }
    return $Path
}

function Resolve-TestConfigurationChildPath {
    param(
        [Parameter(Mandatory)][string]$ConfigurationDirectory,
        [Parameter(Mandatory)][string]$Path
    )

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $ConfigurationDirectory $Path))
}

function Read-TestHubConfiguration {
    param([Parameter(Mandatory)][string]$ConfigurationPath)

    $fullPath = [System.IO.Path]::GetFullPath($ConfigurationPath)
    $data = Get-Content -LiteralPath $fullPath -Raw | ConvertFrom-Json
    $usikkerProperty = @($data.PSObject.Properties | Where-Object Name -eq 'usikker_test') | Select-Object -First 1
    if ($null -eq $usikkerProperty) {
        $usikker = $false
    }
    elseif ($usikkerProperty.Value -is [bool]) {
        $usikker = [bool]$usikkerProperty.Value
    }
    else {
        throw 'Konfigurationens usikker_test skal være true eller false.'
    }

    $tls = $data.tls
    if ($usikker -and $null -ne $tls) {
        throw 'Konfigurationen må ikke kombinere usikker_test med TLS.'
    }
    if (-not $usikker -and ($null -eq $tls -or [string]::IsNullOrWhiteSpace([string]$tls.certifikat))) {
        throw 'HTTPS-konfigurationen mangler tls.certifikat.'
    }

    $configurationDirectory = Split-Path -Parent $fullPath
    $certificatePath = ''
    $caPath = ''
    if (-not $usikker) {
        $certificatePath = Resolve-TestConfigurationChildPath -ConfigurationDirectory $configurationDirectory -Path ([string]$tls.certifikat)
        $caPath = Join-Path (Split-Path -Parent $certificatePath) 'ca.crt'
    }

    $elevAddress = [string]$data.elev_adresse
    $hubPort = [int]$data.hub_port
    if ([string]::IsNullOrWhiteSpace($elevAddress) -or $hubPort -lt 1 -or $hubPort -gt 65535) {
        throw 'Konfigurationen mangler en gyldig elev_adresse eller hub_port.'
    }
    $teacherAddress = if ([string]::IsNullOrWhiteSpace([string]$data.laerer_adresse)) { '127.0.0.1' } else { [string]$data.laerer_adresse }
    $teacherPort = [int]$data.laerer_port
    if ($teacherPort -lt 1 -or $teacherPort -gt 65535) { $teacherPort = 8780 }
    $protocol = if ($usikker) { 'http' } else { 'https' }

    [pscustomobject]@{
        Protocol        = $protocol
        HubAddress      = $elevAddress
        HubPort         = $hubPort
        TeacherAddress   = $teacherAddress
        TeacherPort      = $teacherPort
        HealthUrl       = '{0}://{1}:{2}/api/health' -f $protocol, $elevAddress, $hubPort
        CertificatePath = $certificatePath
        CaPath          = $caPath
    }
}

function Normalize-TestPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
    return ([System.IO.Path]::GetFullPath($Path)).TrimEnd('\').ToLowerInvariant()
}

function Normalize-TestCommandLine([string]$CommandLine) {
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return '' }
    # Win32_Process kan variere i whitespace og slash-retning, men ikke i de konkrete argumenter.
    return (($CommandLine.Trim() -replace '\s+', ' ').Replace('/', '\').ToLowerInvariant())
}

function Quote-TestArgument([string]$Value) {
    return '"' + $Value + '"'
}

function New-TestServerConfiguration {
    param(
        [string]$ProjectRoot,
        [string]$PythonPath,
        [string]$ElevhubPath,
        [string]$ConfigurationPath,
        [string]$DataRoot
    )

    $allowedExecutables = @([System.IO.Path]::GetFullPath($PythonPath))
    # En Windows kan en venv-python være en launcher, som reelt kører base-Python.
    $venvRoot = Split-Path (Split-Path $PythonPath -Parent) -Parent
    $venvConfig = Join-Path $venvRoot 'pyvenv.cfg'
    if (Test-Path -LiteralPath $venvConfig -PathType Leaf) {
        $homeLine = Get-Content -LiteralPath $venvConfig | Where-Object { $_ -match '^\s*home\s*=\s*(.+?)\s*$' } | Select-Object -First 1
        if ($homeLine -and ($homeLine -match '^\s*home\s*=\s*(.+?)\s*$')) {
            $homePython = Join-Path $Matches[1].Trim() 'python.exe'
            if (Test-Path -LiteralPath $homePython -PathType Leaf) {
                $allowedExecutables += [System.IO.Path]::GetFullPath($homePython)
            }
        }
    }

    $runtime = Read-TestHubConfiguration -ConfigurationPath $ConfigurationPath

    [pscustomobject]@{
        ProjectRoot       = [System.IO.Path]::GetFullPath($ProjectRoot)
        PythonPath        = [System.IO.Path]::GetFullPath($PythonPath)
        AllowedExecutables = @($allowedExecutables | Sort-Object -Unique)
        ElevhubPath       = [System.IO.Path]::GetFullPath($ElevhubPath)
        ConfigurationPath = [System.IO.Path]::GetFullPath($ConfigurationPath)
        DataRoot          = [System.IO.Path]::GetFullPath($DataRoot)
        Protocol          = $runtime.Protocol
        HubAddress        = $runtime.HubAddress
        HubPort           = $runtime.HubPort
        TeacherAddress    = $runtime.TeacherAddress
        TeacherPort       = $runtime.TeacherPort
        HealthUrl         = $runtime.HealthUrl
        CertificatePath   = $runtime.CertificatePath
        CaPath            = $runtime.CaPath
    }
}

function New-TestProcessDefinitions {
    param([Parameter(Mandatory)]$Configuration)

    $printScript = Join-Path $PSScriptRoot 'printvaerksted-test.py'
    $printData = Join-Path $Configuration.DataRoot 'printvaerksted-test'
    $designData = Join-Path $Configuration.DataRoot 'designvaerksted-test'
    $elevhubData = Join-Path $Configuration.DataRoot 'elevhub-test'
    $python = Quote-TestArgument $Configuration.PythonPath

    @(
        [pscustomobject]@{
            Navn = 'Printværkstedet'
            Kommando = "$python $(Quote-TestArgument $printScript) --data $(Quote-TestArgument $printData) --port 8765"
            Mappe = $PSScriptRoot
            Executables = $Configuration.AllowedExecutables
            Porte = @(8765)
            Tjek = 'http://127.0.0.1:8765/api/health'
            HealthPython = $Configuration.PythonPath
            CaPath = ''
        }
        [pscustomobject]@{
            Navn = 'Designværkstedet'
            Kommando = "$python -m app.laser_server --host 127.0.0.1 --port 8766 --teacher-host 127.0.0.1 --teacher-port 8767 --data $(Quote-TestArgument $designData)"
            Mappe = $Configuration.ProjectRoot
            Executables = $Configuration.AllowedExecutables
            Porte = @(8766, 8767)
            Tjek = 'http://127.0.0.1:8766/api/laser/health'
            HealthPython = $Configuration.PythonPath
            CaPath = ''
        }
        [pscustomobject]@{
            Navn = 'Elevhub-serveren'
            Kommando = "$python $(Quote-TestArgument $Configuration.ElevhubPath) --konfiguration $(Quote-TestArgument $Configuration.ConfigurationPath) --data $(Quote-TestArgument $elevhubData)"
            Mappe = Split-Path $Configuration.ElevhubPath
            Executables = $Configuration.AllowedExecutables
            Porte = @(8770, 8771, 8772, 8773, 8780)
            Tjek = $Configuration.HealthUrl
            HealthPython = $Configuration.PythonPath
            CaPath = $Configuration.CaPath
        }
    )
}

function Test-ProcessIdentity {
    param(
        [Parameter(Mandatory)]$Process,
        [Parameter(Mandatory)]$Definition
    )

    if ($null -eq $Process.CommandLine) { return $false }
    if (($Process.PSObject.Properties.Name -contains 'Name') -and ($Process.Name -notin @('python.exe', 'pythonw.exe'))) { return $false }
    if ($Process.PSObject.Properties.Name -contains 'ExecutablePath') {
        $actualExecutable = [string]$Process.ExecutablePath
        if (-not [string]::IsNullOrWhiteSpace($actualExecutable)) {
            $allowed = @($Definition.Executables | ForEach-Object { Normalize-TestPath $_ })
            if ((Normalize-TestPath $actualExecutable) -notin $allowed) { return $false }
        }
    }
    return (Normalize-TestCommandLine $Process.CommandLine) -eq (Normalize-TestCommandLine $Definition.Kommando)
}

function Get-PythonProcessSnapshot {
    @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'")
}

function Get-OwnedTestProcesses {
    param(
        [Parameter(Mandatory)]$Definition,
        [object[]]$Snapshot = (Get-PythonProcessSnapshot)
    )

    $candidates = @($Snapshot | Where-Object { Test-ProcessIdentity $_ $Definition })
    @($candidates | Where-Object {
        $parent = $_
        $children = @($candidates | Where-Object {
            $hasParent = $_.PSObject.Properties.Name -contains 'ParentProcessId'
            $hasExecutable = $_.PSObject.Properties.Name -contains 'ExecutablePath'
            $parentExecutable = $parent.PSObject.Properties.Name -contains 'ExecutablePath'
            $hasParent -and $hasExecutable -and $parentExecutable -and
                [int]$_.ParentProcessId -eq [int]$parent.ProcessId -and
                (Normalize-TestPath $_.ExecutablePath) -ne (Normalize-TestPath $parent.ExecutablePath)
        })
        # En venv-launcher og dens base-Python-child er én logisk server.
        # Uafhængige dubletter og uklare processer beholdes og afvises senere.
        $children.Count -eq 0
    })
}

function Get-AllOwnedTestProcesses {
    param(
        [Parameter(Mandatory)]$Definitions,
        [object[]]$Snapshot = (Get-PythonProcessSnapshot)
    )

    @($Definitions | ForEach-Object { Get-OwnedTestProcesses $_ $Snapshot })
}

function Get-ListeningPortSnapshot {
    param([int[]]$Ports)

    if (-not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
        throw 'Kan ikke kontrollere portkonflikter: Get-NetTCPConnection findes ikke på denne Windows-installation.'
    }

    @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
        Where-Object { $_.LocalPort -in $Ports })
}

function Find-PortConflicts {
    param(
        [object[]]$Listeners,
        [int[]]$Ports,
        [int[]]$OwnedProcessIds = @()
    )

    $owned = @($OwnedProcessIds | ForEach-Object { [int]$_ })
    @($Listeners | Where-Object {
        $port = [int]$_.LocalPort
        $processId = if ($_.OwningProcess -ne $null) { [int]$_.OwningProcess } elseif ($_.ProcessId -ne $null) { [int]$_.ProcessId } else { -1 }
        ($port -in $Ports) -and ($processId -notin $owned)
    })
}

function Assert-TestFiles {
    param([Parameter(Mandatory)]$Configuration)

    $required = @(
        @{ Label = 'Python'; Path = $Configuration.PythonPath },
        @{ Label = 'Elevhub-script'; Path = $Configuration.ElevhubPath },
        @{ Label = 'Elevhub-konfiguration'; Path = $Configuration.ConfigurationPath },
        @{ Label = 'Printværksted-script'; Path = (Join-Path $PSScriptRoot 'printvaerksted-test.py') }
    )
    if ($Configuration.Protocol -eq 'https') {
        $required += @(
            @{ Label = 'Elevhub-certifikat'; Path = $Configuration.CertificatePath },
            @{ Label = 'Elevhub-CA'; Path = $Configuration.CaPath }
        )
    }
    foreach ($item in $required) {
        if (-not (Test-Path -LiteralPath $item.Path -PathType Leaf)) {
            throw "$($item.Label) findes ikke: $($item.Path)"
        }
    }
}

function Test-SameProcessInstance {
    param(
        [Parameter(Mandatory)]$Expected,
        [Parameter(Mandatory)]$Current
    )

    if ([int]$Expected.ProcessId -ne [int]$Current.ProcessId) { return $false }
    foreach ($property in @('CreationDate')) {
        $hasExpected = $Expected.PSObject.Properties.Name -contains $property
        $hasCurrent = $Current.PSObject.Properties.Name -contains $property
        if ($hasExpected -and $hasCurrent) {
            $expectedValue = [string]$Expected.$property
            $currentValue = [string]$Current.$property
            if ($expectedValue -and $currentValue -and $expectedValue -ne $currentValue) { return $false }
        }
    }
    return $true
}

function Stop-OwnedTestProcesses {
    param(
        [object[]]$Processes,
        [Parameter(Mandatory)]$Definitions,
        [scriptblock]$SnapshotProvider = { Get-PythonProcessSnapshot },
        [scriptblock]$StopProcess = { param($ProcessId) Stop-Process -Id $ProcessId -Force -ErrorAction Stop }
    )

    foreach ($process in @($Processes | Sort-Object ProcessId -Unique)) {
        $definition = @($Definitions | Where-Object { Test-ProcessIdentity $process $_ }) | Select-Object -First 1
        if ($null -eq $definition) {
            Write-Warning "PID $($process.ProcessId) blev ikke stoppet: den matcher ikke længere en kendt testkommando."
            continue
        }
        $snapshot = @(& $SnapshotProvider)
        $current = @($snapshot | Where-Object { [int]$_.ProcessId -eq [int]$process.ProcessId })
        if ($current.Count -ne 1 -or -not (Test-SameProcessInstance $process $current[0]) -or -not (Test-ProcessIdentity $current[0] $definition)) {
            Write-Warning "PID $($process.ProcessId) blev ikke stoppet: PID, CreationDate eller kommando ændrede sig."
            continue
        }

        $toStop = [System.Collections.Generic.List[object]]::new()
        $toStop.Add($current[0])
        $changed = $true
        while ($changed) {
            $changed = $false
            foreach ($candidate in @($snapshot | Where-Object { Test-ProcessIdentity $_ $definition })) {
                if (@($toStop | Where-Object { [int]$_.ProcessId -eq [int]$candidate.ProcessId }).Count -gt 0) { continue }
                $isChild = @($toStop | Where-Object {
                    $candidate.PSObject.Properties.Name -contains 'ParentProcessId' -and
                        [int]$candidate.ParentProcessId -eq [int]$_.ProcessId
                }).Count -gt 0
                $isParent = @($toStop | Where-Object {
                    $_.PSObject.Properties.Name -contains 'ParentProcessId' -and
                        [int]$_.ParentProcessId -eq [int]$candidate.ProcessId
                }).Count -gt 0
                $differentExecutable = @($toStop | Where-Object {
                    ($candidate.PSObject.Properties.Name -contains 'ExecutablePath') -and
                        ($_.PSObject.Properties.Name -contains 'ExecutablePath') -and
                        (Normalize-TestPath $candidate.ExecutablePath) -ne (Normalize-TestPath $_.ExecutablePath)
                }).Count -gt 0
                if (($isChild -or $isParent) -and $differentExecutable) {
                    $toStop.Add($candidate)
                    $changed = $true
                }
            }
        }
        foreach ($ownedProcess in @($toStop | Sort-Object ProcessId -Unique)) {
            & $StopProcess ([int]$ownedProcess.ProcessId)
        }
    }
}

function Wait-OwnedProcessRecord {
    param(
        [Parameter(Mandatory)]$Definition,
        [Parameter(Mandatory)][int]$ProcessId,
        [int]$Seconds = 10
    )

    for ($i = 1; $i -le $Seconds; $i++) {
        $current = @(Get-PythonProcessSnapshot | Where-Object { [int]$_.ProcessId -eq $ProcessId -and (Test-ProcessIdentity $_ $Definition) })
        if ($current.Count -eq 1) { return $current[0] }
        Start-Sleep -Seconds 1
    }
    throw "Den nye $($Definition.Navn)-proces kunne ikke verificeres med PID $ProcessId; den blev ikke stoppet automatisk."
}

function Wait-TestHealth {
    param(
        [Parameter(Mandatory)]$Definition,
        [int]$Seconds = 30
    )

    $healthScript = @'
import http.client
import ssl
import sys

scheme, host, port, path, cafile = sys.argv[1:]
port = int(port)
if scheme == 'https':
    context = ssl.create_default_context(cafile=cafile)
    connection = http.client.HTTPSConnection(host, port, context=context, timeout=2)
elif scheme == 'http':
    connection = http.client.HTTPConnection(host, port, timeout=2)
else:
    raise SystemExit('ukendt healthcheck-protokol')
try:
    connection.request('GET', path)
    response = connection.getresponse()
    response.read()
    if response.status < 200 or response.status >= 400:
        raise SystemExit('HTTP-status {}'.format(response.status))
finally:
    connection.close()
'@
    $lastHealthError = 'endnu ikke kontrolleret'
    for ($i = 1; $i -le $Seconds; $i++) {
        try {
            $uri = [System.Uri]$Definition.Tjek
            if ($uri.Scheme -eq 'https' -and [string]::IsNullOrWhiteSpace([string]$Definition.CaPath)) {
                throw 'HTTPS-healthcheck mangler CA-fil.'
            }
            # Windows PowerShell 5.1 drops an empty trailing native argument.
            # Python always receives five values: HTTP uses this sentinel,
            # while HTTPS receives the validated absolute CA path.
            $caArgument = if ($uri.Scheme -eq 'https') { [string]$Definition.CaPath } else { '-' }
            $arguments = @(
                '-c', $healthScript,
                $uri.Scheme,
                $uri.Host,
                ([string]$uri.Port),
                $uri.PathAndQuery,
                $caArgument
            )
            & $Definition.HealthPython @arguments
            if ($LASTEXITCODE -ne 0) { throw "Healthcheck-processen returnerede $LASTEXITCODE." }
            return
        }
        catch {
            $lastHealthError = $_.Exception.Message
            if (@(Get-OwnedTestProcesses $Definition).Count -eq 0) {
                throw "$($Definition.Navn) afsluttede under opstart. Sidste healthcheck: $lastHealthError"
            }
            Start-Sleep -Seconds 1
        }
    }

    $current = @(Get-OwnedTestProcesses $Definition)
    if ($current.Count -eq 0) {
        throw "$($Definition.Navn) afsluttede under opstart. Ingen proces med den forventede testkommando findes længere."
    }
    throw "$($Definition.Navn) svarede ikke på $($Definition.Tjek) efter $Seconds sekunder. Sidste healthcheck: $lastHealthError"
}

function Invoke-TestServer {
    param(
        [switch]$Stop,
        [switch]$CheckOnly,
        [string]$ConfigurationPath
    )

    if ($Stop -and $CheckOnly) {
        throw '-Stop og -CheckOnly kan ikke bruges samtidig.'
    }

    if ([string]::IsNullOrWhiteSpace($ConfigurationPath)) {
        $ConfigurationPath = Resolve-TestConfigurationPath
    }
    $configuration = New-TestServerConfiguration -ProjectRoot $Projekt -PythonPath $Python -ElevhubPath $Elevhub -ConfigurationPath $ConfigurationPath -DataRoot $Data
    $definitions = @(New-TestProcessDefinitions $configuration)
    Assert-TestFiles $configuration
    $snapshot = @(Get-PythonProcessSnapshot)
    $owned = @(Get-AllOwnedTestProcesses $definitions $snapshot)

    if ($Stop) {
        Stop-OwnedTestProcesses -Processes $owned -Definitions $definitions
        Write-Host ("Stoppet: {0} identificerede testproces(ser)." -f $owned.Count) -ForegroundColor Green
        return
    }

    $ports = @($definitions | ForEach-Object { $_.Porte }) | Sort-Object -Unique
    $listeners = @(Get-ListeningPortSnapshot $ports)
    $ownedIds = @($owned | ForEach-Object { [int]$_.ProcessId })
    $conflicts = @(Find-PortConflicts $listeners $ports $ownedIds)
    if ($conflicts.Count -gt 0) {
        $details = $conflicts | ForEach-Object {
            $processId = if ($_.OwningProcess -ne $null) { $_.OwningProcess } else { $_.ProcessId }
            "port $($_.LocalPort), PID $processId"
        }
        throw ('Port(e) er allerede i brug af andre tjenester: ' + ($details -join '; ') + '. Ingen proces blev stoppet eller startet.')
    }

    if ($CheckOnly) {
        Write-Host 'Kontrol OK: testfiler, testkommandoer og testporte er klar; ingen proces blev startet.' -ForegroundColor Green
        return
    }

    $startedProcesses = @()
    try {
        foreach ($definition in $definitions) {
            $current = @(Get-OwnedTestProcesses $definition $snapshot)
            if ($current.Count -gt 1) {
                throw "$($definition.Navn) findes flere gange med samme testkommando ($($current.Count) processer). Stop dem med -Stop og prøv igen."
            }
            if ($current.Count -eq 0) {
                # Win32_Process.Create med ShowWindow=0 holder processen skjult og løsrevet fra dette PowerShell-vindue.
                $skjult = New-CimInstance -ClassName Win32_ProcessStartup -Property @{ ShowWindow = [uint16]0 } -ClientOnly
                $result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
                    CommandLine = $definition.Kommando
                    CurrentDirectory = $definition.Mappe
                    ProcessStartupInformation = $skjult
                }
                if ($result.ReturnValue -ne 0) {
                    throw "Kunne ikke starte $($definition.Navn) (Win32-fejlkode $($result.ReturnValue)). Kommando: $($definition.Kommando)"
                }
                $startedProcesses += Wait-OwnedProcessRecord -Definition $definition -ProcessId ([int]$result.ProcessId)
                $snapshot = @(Get-PythonProcessSnapshot)
            }
            Write-Host "$($definition.Navn) starter; kontrollerer healthcheck..."
            Wait-TestHealth $definition
            Write-Host "$($definition.Navn) kører." -ForegroundColor Green
        }
    }
    catch {
        # Ryd kun processer op, som denne kørsel selv oprettede.
        Stop-OwnedTestProcesses -Processes $startedProcesses -Definitions $definitions -StopProcess { param($ProcessId) Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue }
        throw
    }

    Write-Host ''
    Write-Host ('  Lærerpanel (kun på denne pc):   {0}://{1}:{2}/' -f $configuration.Protocol, $configuration.TeacherAddress, $configuration.TeacherPort)
    Write-Host ('  Elevcomputerne (testnettet):    {0}://{1}:{2}, centrale elevindgange 8770-8773' -f $configuration.Protocol, $configuration.HubAddress, $configuration.HubPort)
    Write-Host '  Printværkstedets lærerside:     http://localhost:8765/laerer'
    Write-Host '  Designværkstedets lærerside:    http://localhost:8767/'
}

# Dot-sourcing bruges af den rene testfil og må ikke starte eller stoppe noget.
if ($MyInvocation.InvocationName -ne '.') {
    Invoke-TestServer -Stop:$Stop -CheckOnly:$CheckOnly -ConfigurationPath (Resolve-TestConfigurationPath $Konfiguration)
}
