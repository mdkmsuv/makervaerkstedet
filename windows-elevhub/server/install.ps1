[CmdletBinding()]
param(
    [string]$PackageRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$InstallRoot = "$env:ProgramFiles\ElevhubServer",
    [string]$DataRoot = "$env:ProgramData\ElevhubServer",
    [string]$RoleIp = '',
    [string]$StudentSubnet = '',
    [string]$TeacherAddress = '',
    [int]$TeacherPort = 8780,
    [int]$PrintBackendPort = 8765,
    [int]$DesignBackendPort = 8766,
    [int]$TeacherBackendPort = 8767,
    [switch]$UseExistingPrintBackend,
    [switch]$GenerationLayout,
    [switch]$Resume,
    [switch]$CheckOnly,
    [string]$RecoveryBackup = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$TaskName = 'ElevhubServer'
$FirewallPorts = @(8770, 8771, 8772, 8773)
$LocalPorts = @(8765, 8766, 8767)
$PortBindings = @()
$ExpectedInstall = [IO.Path]::GetFullPath("$env:ProgramFiles\ElevhubServer")
$ExpectedData = [IO.Path]::GetFullPath("$env:ProgramData\ElevhubServer")
. (Join-Path $PSScriptRoot 'generation-startup.ps1')
. (Join-Path $PSScriptRoot 'school-uninstall-guards.ps1')

function Full([string]$Path) { [IO.Path]::GetFullPath($Path) }
function Get-InstalledRoleIp([object]$Owner, [string]$ExistingDataRoot) {
    # Only retained metadata may supply a legacy address. Package templates,
    # current NICs and a school-specific constant are never fallback evidence.
    Assert-PlainPath $ExistingDataRoot
    $candidates = [Collections.Generic.List[string]]::new()
    if ($null -ne $Owner.PSObject.Properties['roleIp']) {
        if ([string]::IsNullOrWhiteSpace([string]$Owner.roleIp)) { throw 'Installationsejerens serveradresse er tom.' }
        $candidates.Add([string]$Owner.roleIp)
    }
    $configPath = Join-Path $ExistingDataRoot 'konfiguration.json'
    if (Test-Path -LiteralPath $configPath -PathType Leaf) {
        Assert-PlainPath $configPath
        $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($null -eq $config.PSObject.Properties['elev_adresse'] -or [string]::IsNullOrWhiteSpace([string]$config.elev_adresse)) { throw 'Den bevarede serverkonfiguration mangler elev_adresse.' }
        $candidates.Add([string]$config.elev_adresse)
    }
    $normalized = @()
    foreach ($candidate in $candidates) {
        $address = $null
        if (-not [Net.IPAddress]::TryParse($candidate,[ref]$address) -or
            $address.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or
            $candidate -cne $address.ToString() -or $address.Equals([Net.IPAddress]::Any) -or
            [Net.IPAddress]::IsLoopback($address) -or $address.GetAddressBytes()[0] -ge 224) { throw 'Den bevarede serveradresse er ugyldig.' }
        $normalized += $address.ToString()
    }
    $unique = @($normalized | Select-Object -Unique)
    if ($unique.Count -ne 1) { throw 'Serveradressen kan ikke bestemmes entydigt fra installationsejer og bevaret konfiguration.' }
    return [string]$unique[0]
}
function Get-InstalledStudentSubnet([object]$Owner, [string]$ExistingDataRoot, [string]$ExistingRoleIp) {
    $value = ''
    foreach ($key in @('studentSubnet','roleNetwork')) {
        if ($null -ne $Owner.PSObject.Properties[$key] -and -not [string]::IsNullOrWhiteSpace([string]$Owner.$key)) { $value=[string]$Owner.$key; break }
    }
    if (-not $value) {
        $schoolPath = Join-Path $ExistingDataRoot 'school.json'
        if (Test-Path -LiteralPath $schoolPath -PathType Leaf) {
            Assert-PlainPath $schoolPath
            $school = Get-Content -LiteralPath $schoolPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($school.schemaVersion -ne 1 -or $school.server -cne $ExistingRoleIp) { throw 'Skolekonfigurationens serveradresse matcher ikke installationen.' }
            $value = [string]$school.student_subnet
        }
    }
    $parts = $value.Split('/'); $address = $null
    if ($parts.Count -ne 2 -or $parts[1] -notmatch '^(?:[1-9]|[12][0-9]|3[0-2])$' -or
        -not [Net.IPAddress]::TryParse($parts[0],[ref]$address) -or
        $address.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or $parts[0] -cne $address.ToString()) { throw 'Elevnettet kan ikke bestemmes sikkert fra bevarede installationsmetadata.' }
    return $value
}
function Assert-Port([int]$Port, [string]$Name) { if ($Port -lt 1 -or $Port -gt 65535) { throw "$Name er ikke en gyldig TCP-port." } }
function Get-RoleNetwork([string]$Ip) {
    $address = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop | Where-Object { $_.IPAddress -eq $Ip -and $_.AddressState -eq 'Preferred' } | Select-Object -First 1)
    if ($address.Count -eq 0) { throw "Denne computer har ikke den aktive serveradresse $Ip." }
    $prefix = [int]$address[0].PrefixLength
    if ($prefix -lt 1 -or $prefix -gt 32) { throw "Serveradressen $Ip har et ugyldigt netværk." }
    $bytes = [Net.IPAddress]::Parse($Ip).GetAddressBytes(); $network = [byte[]]::new(4)
    for ($index = 0; $index -lt 4; $index++) {
        $remaining = $prefix - ($index * 8); $mask = if ($remaining -ge 8) { 255 } elseif ($remaining -le 0) { 0 } else { 256 - [math]::Pow(2, 8 - $remaining) }
        $network[$index] = [byte]($bytes[$index] -band [int]$mask)
    }
    return "$([Net.IPAddress]::new($network))/$prefix"
}
function Assert-NotReparse([string]$Path) {
    $cursor = Get-Item -LiteralPath $Path -Force
    while ($null -ne $cursor) {
        if (($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Reparse point er ikke tilladt: $($cursor.FullName)" }
        $cursor = if ($cursor -is [IO.DirectoryInfo]) { $cursor.Parent } else { $cursor.Directory }
    }
}
function Assert-NoReparseTree([string]$Path) {
    $items=@((Get-Item -LiteralPath $Path -Force))+@(Get-ChildItem -LiteralPath $Path -Force -Recurse)
    foreach($item in $items){ if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint)-ne 0){ throw "Reparse point er ikke tilladt: $($item.FullName)" } }
}
function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Kør installationen som administrator.' }
    if (-not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess) { throw 'ElevhubServer kræver 64-bit Windows og 64-bit PowerShell.' }
}
function Assert-RoleHost {
    try { $parsed = [Net.IPAddress]::Parse($RoleIp) } catch { throw "Ugyldig serveradresse: $RoleIp" }
    if ($parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or $parsed.Equals([Net.IPAddress]::Any) -or [Net.IPAddress]::IsLoopback($parsed)) { throw 'Serverrollen skal bruge en aktiv IPv4-adresse.' }
    $script:RoleNetwork = Get-RoleNetwork $RoleIp
}
function Assert-StudentSubnet {
    if ([string]::IsNullOrWhiteSpace($StudentSubnet)) { $script:StudentSubnet = $script:RoleNetwork; return }
    $parts = $StudentSubnet.Split('/')
    $address = $null; $prefix = 0
    if ($parts.Count -ne 2 -or -not [Net.IPAddress]::TryParse($parts[0], [ref]$address) -or $address.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or -not [int]::TryParse($parts[1], [ref]$prefix) -or $prefix -lt 1 -or $prefix -gt 32) { throw 'StudentSubnet skal være et IPv4-net med maske, fx 10.20.30.0/24.' }
    $script:StudentSubnet = $StudentSubnet
}
function Assert-TeacherAddress {
    if ([string]::IsNullOrWhiteSpace($TeacherAddress)) { $script:TeacherAddress = $RoleIp }
    try { $parsed = [Net.IPAddress]::Parse($TeacherAddress) } catch { throw "Ugyldig læreradresse: $TeacherAddress" }
    if ($parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or $parsed.Equals([Net.IPAddress]::Any) -or ($TeacherAddress -ne '127.0.0.1' -and $TeacherAddress -ne $RoleIp)) { throw 'Lærerens adresse skal være serveradressen eller 127.0.0.1.' }
    if ($TeacherAddress -ne '127.0.0.1') { $script:FirewallPorts += $TeacherPort }
}
function Assert-PortsFree {
    foreach ($binding in $PortBindings) {
        $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Parse($binding.Address), [int]$binding.Port)
        try { $listener.Start() } catch { throw "Port $($binding.Port) er allerede i brug på $($binding.Address)." } finally { try { $listener.Stop() } catch {} }
    }
}
function Assert-ExistingPrintBackend {
    # Only inspect the existing service. Its process, queue, data and tasks
    # are outside this installer's ownership, including during rollback.
    $request = [Net.HttpWebRequest]::Create("http://127.0.0.1:$PrintBackendPort/api/health")
    $request.Proxy = $null
    $request.AllowAutoRedirect = $false
    $request.Timeout = 8000
    $request.ReadWriteTimeout = 8000
    $response = $null
    try {
        $response = $request.GetResponse()
        if ([int]$response.StatusCode -ne 200) { throw 'Print-backend svarede ikke HTTP 200.' }
        $reader = [IO.StreamReader]::new($response.GetResponseStream())
        try { $health = $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() }
        if ($health.ok -isnot [bool] -or -not $health.ok -or $health.demo -isnot [bool] -or $health.demo -or $health.service -ne 'printvaerksted') {
            throw 'Den eksisterende backend er ikke et aktivt Printvaerksted uden demo.'
        }
    } finally { if ($null -ne $response) { $response.Dispose() } }
}
function Invoke-Icacls([string]$Path, [string[]]$Arguments) {
    & "$env:SystemRoot\System32\icacls.exe" $Path @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "ACL kunne ikke sikres på $Path." }
}
function Write-JsonAtomic([string]$Path, [object]$Value) {
    $temp = "$Path.tmp"
    [IO.File]::WriteAllText($temp, ($Value | ConvertTo-Json -Depth 20) + "`n", [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temp -Destination $Path -Force
}
function Assert-OwnedProgramTree {
    & $pythonSource -B (Join-Path $PackageRoot 'server\provision.py') check-initial-install-tree --package-root $PackageRoot --runtime-root $InstallRoot --data-root $DataRoot
    if ($LASTEXITCODE -ne 0) { throw 'Oprydning afviser ukendte eller ændrede programfiler; alle mapper er bevaret.' }
}
function Assert-InstallSpace {
    $packageBytes=[long]0; $fixedBytes=[long]0
    $fixedApp=@('__init__.py','backup_databases.py','backup_guard.py','data_contract.py','database_migrations.py',
                'generation_activation.py','generation_host.py','generation_lock.py','generation_materialization.py',
                'generation_relocation.py','generation_rollback.py','generation_runtime.py','generation_selection.py',
                'generation_transaction.py','installation_backup.py','installation_update.py','instance.py',
                'release_compatibility.py','school_maintenance.py','school_maintenance_host.py','school_recovery.py','school_tls.py')
    foreach ($file in @(Get-ChildItem -LiteralPath $PackageRoot -File -Recurse -Force)) {
        $packageBytes += $file.Length
        $relative=$file.FullName.Substring($PackageRoot.Length+1)
        if ($GenerationLayout -and ($relative -match '^(runtime|server|deployment)\\' -or ($relative -match '^app\\' -and $fixedApp -contains $file.Name))) { $fixedBytes += $file.Length }
    }
    # Reserve space for journals, TLS and initial database work in addition to
    # the exact copied bytes. This is a preflight estimate, not a quota.
    $programVolume=[IO.Path]::GetPathRoot($InstallRoot); $dataVolume=[IO.Path]::GetPathRoot($DataRoot)
    $programRequired=$packageBytes+$fixedBytes+64MB
    if ($programVolume -eq $dataVolume) { $programRequired += 128MB }
    if ([IO.DriveInfo]::new($programVolume).AvailableFreeSpace -lt $programRequired) { throw "Der er ikke nok ledig plads til programmerne. Der kræves mindst $([math]::Ceiling($programRequired/1MB)) MiB før installationen kan skrive data." }
    if ($programVolume -ne $dataVolume -and [IO.DriveInfo]::new($dataVolume).AvailableFreeSpace -lt 128MB) { throw 'Der kræves mindst 128 MiB ledig plads på datadrevet før installation.' }
}
function Copy-FixedAdministrativeAppFiles([string]$ProgramRoot, [string]$InstallRoot, [object]$IntegrityManifest) {
    $fixedAppCore = @('__init__.py','backup_databases.py','backup_guard.py','data_contract.py','database_migrations.py',
                      'generation_activation.py','generation_host.py','generation_lock.py','generation_materialization.py',
                      'generation_relocation.py','generation_rollback.py','generation_runtime.py','generation_selection.py',
                      'generation_transaction.py','installation_backup.py','installation_update.py','instance.py',
                      'release_compatibility.py','school_maintenance.py','school_maintenance_host.py')
    $fixedAppOptional = @('school_recovery.py','school_tls.py')
    foreach ($name in $fixedAppCore + $fixedAppOptional) {
        $sourcePath = Join-Path $ProgramRoot "app\$name"
        if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
            $declaredPath = 'app/' + $name
            $declared = $IntegrityManifest.files.PSObject.Properties.Name -contains $declaredPath
            if ($fixedAppOptional -contains $name -and -not $declared) { continue }
            throw "Pakken mangler den faste administrative app-fil: $declaredPath"
        }
        Copy-Item -LiteralPath $sourcePath -Destination (Join-Path $InstallRoot 'app')
    }
}
function Get-TaskPrincipalSid([object]$Identity) {
    $value = [string]$Identity
    if ($value -match '^S-\d(?:-\d+)+$') { return $value }
    try {
        $account = New-Object Security.Principal.NTAccount($value)
        return $account.Translate([Security.Principal.SecurityIdentifier]).Value
    } catch {
        # Windows may expose the built-in account in the host language. Keep
        # the two canonical English spellings as a closed fallback; every
        # other unresolved identity remains rejected below.
        if ($value -ieq 'LOCAL SERVICE' -or $value -ieq 'NT AUTHORITY\LOCAL SERVICE') { return 'S-1-5-19' }
        return $null
    }
}
function Assert-OwnedScheduledTask {
    param(
        [Parameter(Mandatory)]$Task,
        [Parameter(Mandatory)][string]$ExpectedDescription,
        [Parameter(Mandatory)][string]$ExpectedExecute,
        [Parameter(Mandatory)][string]$ExpectedWorkingDirectory,
        [Parameter(Mandatory)][string]$ExpectedArguments
    )
    $mismatches = @()
    $description = if ($Task.PSObject.Properties.Name -contains 'Description') { [string]$Task.Description } else { '' }
    if ($description -cne $ExpectedDescription) { $mismatches += 'description' }
    $actions = @()
    if ($Task.PSObject.Properties.Name -contains 'Actions') { $actions = @($Task.Actions) }
    if ($actions.Count -ne 1) {
        $mismatches += 'actions'
    } else {
        $action = $actions[0]
        $execute = if ($action.PSObject.Properties.Name -contains 'Execute') { [string]$action.Execute } else { '' }
        $workingDirectory = if ($action.PSObject.Properties.Name -contains 'WorkingDirectory') { [string]$action.WorkingDirectory } else { '' }
        $arguments = if ($action.PSObject.Properties.Name -contains 'Arguments') { [string]$action.Arguments } else { '' }
        if ($execute -ine $ExpectedExecute) { $mismatches += 'execute' }
        if ($workingDirectory -ine $ExpectedWorkingDirectory) { $mismatches += 'workingDirectory' }
        # Keep the command-line comparison exact; only Windows path casing is
        # normalized above because Task Scheduler treats paths case-insensitively.
        if ($arguments -cne $ExpectedArguments) { $mismatches += 'arguments' }
    }
    $principal = if ($Task.PSObject.Properties.Name -contains 'Principal') { $Task.Principal } else { $null }
    $principalText = if ($null -ne $principal -and $principal.PSObject.Properties.Name -contains 'UserId') { [string]$principal.UserId } else { '' }
    $principalSid = Get-TaskPrincipalSid $principalText
    if ($principalSid -cne 'S-1-5-19') { $mismatches += 'principal' }
    if ($mismatches.Count) {
        throw "Rollback afviser en planlagt opgave, som er ændret eksternt. Felter: $($mismatches -join ', '); principalSid=$principalSid."
    }
}
function Remove-OwnedResources([string]$MarkerPath) {
    if (-not (Test-Path -LiteralPath $MarkerPath -PathType Leaf)) { return }
    $marker = Get-Content -LiteralPath $MarkerPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $rollbackOwnerId = [Guid]::Empty
    if ($marker.schemaVersion -ne 1 -or $marker.product -ne 'ElevhubServer' -or $marker.installRoot -ne $InstallRoot -or $marker.dataRoot -ne $DataRoot -or $marker.taskName -ne $TaskName -or
        $marker.state -notin @('installing','rollbackFailed','rolledBack') -or -not [Guid]::TryParse([string]$marker.ownerId,[ref]$rollbackOwnerId) -or $rollbackOwnerId -eq [Guid]::Empty -or
        @($marker.firewallNames).Count -ne $FirewallPorts.Count -or @(Compare-Object -ReferenceObject @($FirewallPorts | ForEach-Object { "ElevhubServer-$_" }) -DifferenceObject @($marker.firewallNames)).Count) { throw 'Rollback afviser en ændret eller ufuldstændig installationsejer.' }
    $rollbackSucceeded = $false
    $rollbackLocks = @()
    try {
        # All filesystem and resource identities are checked before stopping
        # anything. A retained queue/key under Program Files is not program data.
        Assert-NotReparse $DataRoot; Assert-NoReparseTree $DataRoot
        if (Test-Path -LiteralPath $InstallRoot) { Assert-OwnedProgramTree }
        $task = Get-ScheduledTask -TaskName $marker.taskName -ErrorAction SilentlyContinue
        $expectedPython=Join-Path $InstallRoot 'runtime\python.exe'; $expectedBootstrap=Join-Path $InstallRoot 'server\bootstrap.py'
        $markerRoleIp = Get-InstalledRoleIp $marker $DataRoot
        $markerTeacherAddress = if ($marker.PSObject.Properties.Name -contains 'teacherAddress') { [string]$marker.teacherAddress } else { $markerRoleIp }
        $roleArgument = if ($marker.PSObject.Properties.Name -contains 'roleIp') { ' --role-ip "{0}" --teacher-port "{1}" --teacher-address "{2}"' -f $markerRoleIp,[int]$marker.teacherPort,$markerTeacherAddress } else { '' }
        $expectedArguments='-B "{0}" --package-root "{1}" --data-root "{2}"{3}' -f $expectedBootstrap,$InstallRoot,$DataRoot,$roleArgument
        if ((Get-InstallationStartupMode -Owner $marker -InstallRoot $InstallRoot) -eq 'generation-v1') {
            $expectedArguments=(Get-GenerationCommand -InstallRoot $InstallRoot -InstallationId (Get-GenerationInstallationId $marker) -Entry server -Arguments @('--role-ip',$markerRoleIp,'--teacher-port',[string]$marker.teacherPort,'--teacher-address',$markerTeacherAddress)).Arguments
        }
        if ($task) {
            Assert-OwnedScheduledTask -Task $task -ExpectedDescription "ElevhubServer owner=$($marker.ownerId)" -ExpectedExecute $expectedPython -ExpectedWorkingDirectory $InstallRoot -ExpectedArguments $expectedArguments
        }
        foreach ($name in @($marker.firewallNames)) {
            $rule=Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
            if ($rule) {
                $port=[int]($name -replace '^ElevhubServer-',''); $pf=$rule|Get-NetFirewallPortFilter; $af=$rule|Get-NetFirewallAddressFilter; $app=$rule|Get-NetFirewallApplicationFilter
                $remote=@($af.RemoteAddress); $local=@($af.LocalAddress); $expectedNetwork = Get-InstalledStudentSubnet $marker $DataRoot $markerRoleIp; $actualRemote=(@($remote | ForEach-Object { [string]$_ }) -join ', '); $remoteOk=$remote.Count -eq 1 -and (Test-FirewallAddressEquivalent -Actual ([string]$remote[0]) -Expected $expectedNetwork)
                if ($rule.Description -ne "ElevhubServer owner=$($marker.ownerId)" -or $rule.Direction -ne 'Inbound' -or $rule.Action -ne 'Allow' -or $rule.Enabled -ne 'True' -or $rule.Profile -ne 'Any' -or $pf.Protocol -notin @('TCP',6) -or [string]$pf.LocalPort -ne [string]$port -or $local.Count -ne 1 -or $local[0] -ne $markerRoleIp -or -not $remoteOk -or $app.Program -ne $expectedPython) { throw "Rollback afviser firewallreglen $name, som er ændret eksternt. Forventet RemoteAddress=$expectedNetwork; faktisk=$actualRemote." }
            }
        }
        if (Test-Path -LiteralPath $InstallRoot) { Assert-NotReparse $InstallRoot; Assert-NoReparseTree $InstallRoot }
        if ($marker.caThumbprint) {
            $caPath = Join-Path $DataRoot 'security\ca.crt'
            $retainedCa = [Security.Cryptography.X509Certificates.X509Certificate2]::new($caPath)
            try {
                if ($retainedCa.Thumbprint.ToUpperInvariant() -ne ([string]$marker.caThumbprint).ToUpperInvariant()) { throw 'Rollback afviser en ændret lokal CA.' }
            } finally { $retainedCa.Dispose() }
        }
        if ($task) {
            Stop-ScheduledTask -TaskName $marker.taskName -ErrorAction SilentlyContinue
            $deadline=[DateTime]::UtcNow.AddSeconds(20)
            do { $owned=@(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($InstallRoot+'\',[StringComparison]::OrdinalIgnoreCase) }); if ($owned.Count) { Start-Sleep -Milliseconds 250 } } until ($owned.Count -eq 0 -or [DateTime]::UtcNow -ge $deadline)
            if ($owned.Count) { throw 'Rollback kunne ikke stoppe Elevhubs procestræ.' }
            Unregister-ScheduledTask -TaskName $marker.taskName -Confirm:$false
        }
        if (Test-Path -LiteralPath $InstallRoot) { Assert-OwnedProgramTree }
        $generationLockPresent=(Get-InstallationStartupMode -Owner $marker -InstallRoot $InstallRoot) -eq 'generation-v1' -and (Test-Path -LiteralPath (Join-Path $InstallRoot 'control\generation.lock') -PathType Leaf)
        $rollbackLocks=New-OfflineRemovalLocks $InstallRoot $DataRoot $generationLockPresent
        foreach ($name in @($marker.firewallNames)) {
            $rule = Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
            if ($rule) { Remove-NetFirewallRule -Name $name }
        }
        if ($marker.caThumbprint) {
            $certificate = Get-ChildItem -LiteralPath "Cert:\LocalMachine\Root\$($marker.caThumbprint)" -ErrorAction SilentlyContinue
            if ($certificate) { Remove-Item -LiteralPath $certificate.PSPath -Force }
        }
        if ((Test-Path -LiteralPath $InstallRoot) -and (Full $InstallRoot) -eq $ExpectedInstall) {
            $owned=@(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($InstallRoot+'\',[StringComparison]::OrdinalIgnoreCase) })
            if ($owned.Count) { throw 'Rollback afviser at slette en mappe med kørende processer.' }
            Assert-NotReparse $InstallRoot; Assert-NoReparseTree $InstallRoot
            Remove-Item -LiteralPath $InstallRoot -Recurse -Force
        }
        $rollbackSucceeded = $true
    } finally {
        foreach ($handle in $rollbackLocks) { $handle.Dispose() }
        $marker.state = if ($rollbackSucceeded) { 'rolledBack' } else { 'rollbackFailed' }
        $marker | Add-Member -NotePropertyName rolledBackUtc -NotePropertyValue ([DateTime]::UtcNow.ToString('o')) -Force
        Write-JsonAtomic $MarkerPath $marker
    }
}

$PackageRoot = Full $PackageRoot; $InstallRoot = Full $InstallRoot; $DataRoot = Full $DataRoot
Assert-Port $PrintBackendPort 'Print-backendport'; Assert-Port $DesignBackendPort 'Design-backendport'; Assert-Port $TeacherBackendPort 'Lærer-backendport'
Assert-Port $TeacherPort 'Lærerport'
$LocalPorts = @($PrintBackendPort, $DesignBackendPort, $TeacherBackendPort)
if ($UseExistingPrintBackend) { $LocalPorts = @($DesignBackendPort, $TeacherBackendPort) }
$RoleNetwork = $null
if ($InstallRoot -ne $ExpectedInstall -or $DataRoot -ne $ExpectedData) { throw 'Denne pilot bruger kun de faste mapper under Program Files og ProgramData.' }
if ([string]::IsNullOrWhiteSpace($RoleIp)) {
    if (-not $Resume) { throw 'Vælg serverens faktiske adresse med -RoleIp; en ny installation har ingen standardadresse.' }
    $existingOwnerPath = Join-Path $DataRoot 'installation-owner.json'
    if (-not (Test-Path -LiteralPath $existingOwnerPath -PathType Leaf)) { throw 'Resume kræver en bevaret installationsejer og en entydig serveradresse.' }
    Assert-PlainPath $DataRoot; Assert-PlainPath $existingOwnerPath
    $existingOwner = Get-Content -LiteralPath $existingOwnerPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($existingOwner.schemaVersion -ne 1 -or $existingOwner.product -ne 'ElevhubServer' -or $existingOwner.installRoot -ne $InstallRoot -or $existingOwner.dataRoot -ne $DataRoot) { throw 'Resume afviser en fremmed installationsejer.' }
    $RoleIp = Get-InstalledRoleIp $existingOwner $DataRoot
}
if (-not (Test-Path -LiteralPath $PackageRoot -PathType Container)) { throw 'Pakkemappen findes ikke.' }
Assert-NotReparse $PackageRoot
$pythonSource = Join-Path $PackageRoot 'runtime\python.exe'
if (-not (Test-Path -LiteralPath $pythonSource -PathType Leaf)) { throw 'Pakken mangler den bundne Python-runtime.' }
& $pythonSource -B (Join-Path $PackageRoot 'server\verify.py') --package-root $PackageRoot --role-ip $RoleIp
if ($LASTEXITCODE -ne 0) { throw 'Pakkeverifikation fejlede.' }
if ($GenerationLayout) {
    if ($UseExistingPrintBackend) { throw 'Fælles generationsvalg understøtter endnu ikke en ekstern printinstallation.' }
    $initializer = Join-Path $PackageRoot 'server\generation_install.py'
    if (-not (Test-Path -LiteralPath $initializer -PathType Leaf)) { throw 'Pakken mangler første generationsinstallation.' }
    & $pythonSource -B $initializer --package-root $PackageRoot --check-package
    if ($LASTEXITCODE -ne 0) { throw 'Pakken understøtter ikke fælles generationsvalg.' }
}
$packageManifest = Get-Content -LiteralPath (Join-Path $PackageRoot 'elevhub-package.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$integrityManifest = Get-Content -LiteralPath (Join-Path $PackageRoot 'integrity.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($packageManifest.PSObject.Properties.Name -contains 'requiresGenerationLayout') {
    if ($packageManifest.requiresGenerationLayout -isnot [bool]) { throw 'Pakkens requiresGenerationLayout skal være boolsk.' }
    if ($packageManifest.requiresGenerationLayout -and -not $GenerationLayout) { throw 'Denne pakke kræver -GenerationLayout.' }
}
$releaseManifestSha256 = $null
if (Test-Path -LiteralPath (Join-Path $PackageRoot 'makerspace-release.json') -PathType Leaf) { $releaseManifestSha256 = (Get-FileHash -LiteralPath (Join-Path $PackageRoot 'makerspace-release.json') -Algorithm SHA256).Hash.ToLowerInvariant() }
if ($packageManifest.PSObject.Properties.Name -contains 'externalPrintBackendRequired') {
    if ($packageManifest.externalPrintBackendRequired -and -not $UseExistingPrintBackend) {
        throw 'Denne gatewaypakke kraever -UseExistingPrintBackend og et eksisterende Printvaerksted.'
    }
}

if ($RecoveryBackup) {
    if (-not $GenerationLayout -or $UseExistingPrintBackend) { throw 'Værtsgendannelse kræver det samlede generationslayout.' }
    $recoveryPlanRaw = & $pythonSource -B -m app.school_recovery plan --source $RecoveryBackup --package-root $PackageRoot --runtime-root $InstallRoot --data-root $DataRoot
    if ($LASTEXITCODE -ne 0) { throw 'Backupens værtsgendannelse bestod ikke den skrivefri kontrol.' }
    $recoveryPlan = $recoveryPlanRaw | ConvertFrom-Json
    $recoverySchool = $recoveryPlan.school
    foreach ($pair in @(@{key='roleIp';value=$RoleIp},@{key='studentSubnet';value=$StudentSubnet},@{key='teacherAddress';value=$TeacherAddress},
                         @{key='teacherPort';value=$TeacherPort},@{key='printBackendPort';value=$PrintBackendPort},
                         @{key='designBackendPort';value=$DesignBackendPort},@{key='teacherBackendPort';value=$TeacherBackendPort})) {
        if ([string]$recoverySchool.($pair.key) -cne [string]$pair.value) { throw 'Værtsgendannelse kræver backupens uændrede LAN-adresser og porte.' }
    }
}
if ($CheckOnly) {
    Write-Output 'Pakkens integritet, rolle og skrivefri validering er OK. Værtskontroller er ikke kørt i CheckOnly.'
    exit 0
}

Assert-Administrator; Assert-RoleHost; Assert-StudentSubnet
Assert-TeacherAddress

if (@(@($PrintBackendPort, $DesignBackendPort, $TeacherBackendPort, 8770, 8771, 8772, 8773, $TeacherPort) | Select-Object -Unique).Count -ne 8) { throw 'Serverens TCP-porte skal vaere forskellige.' }
$PortBindings = @($LocalPorts | ForEach-Object { [pscustomobject]@{ Address='127.0.0.1'; Port=$_ } }) + @($FirewallPorts | ForEach-Object { [pscustomobject]@{ Address=$RoleIp; Port=$_ } })
# The teacher endpoint must be free and become ready even when loopback-only.
if ($TeacherAddress -eq '127.0.0.1') { $PortBindings += [pscustomobject]@{ Address=$TeacherAddress; Port=$TeacherPort } }
if ($RecoveryBackup) {
    if ($recoverySchool.roleNetwork -cne $RoleNetwork) { throw 'Erstatningsværten skal tilhøre backupens oprindelige LAN.' }
    if (-not (Test-Path -LiteralPath $DataRoot)) {
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { throw 'Værtsgendannelse afviser en eksisterende serveropgave.' }
        foreach ($port in $FirewallPorts) { if (Get-NetFirewallRule -Name "ElevhubServer-$port" -ErrorAction SilentlyContinue) { throw 'Værtsgendannelse afviser eksisterende firewallregler.' } }
        Assert-PortsFree; Assert-InstallSpace
        & $pythonSource -B -m app.school_recovery prepare --source $RecoveryBackup --package-root $PackageRoot --runtime-root $InstallRoot --data-root $DataRoot
        if ($LASTEXITCODE -ne 0) { throw 'Den isolerede værtsgendannelse kunne ikke klargøres; bevarede kopier må ikke startes.' }
    }
    $Resume = $true
}
if ($UseExistingPrintBackend) { Assert-ExistingPrintBackend }
if (Test-Path -LiteralPath $InstallRoot) {
    if (-not $Resume) { throw 'Installationsmappen findes allerede; afbryder uden overwrite.' }
}
# Inspect existing ancestors before creating anything beneath the fixed paths.
Assert-NotReparse (Split-Path -Parent $InstallRoot)
Assert-NotReparse (Split-Path -Parent $DataRoot)
if (Test-Path -LiteralPath $DataRoot) {
    if (-not $Resume) { throw 'Datamappen findes allerede; afbryder for at beskytte eksisterende data.' }
    $resumeMarkerPath = Join-Path $DataRoot 'installation-owner.json'
    if (-not (Test-Path -LiteralPath $resumeMarkerPath -PathType Leaf)) { throw 'Resume afvises: datamappen mangler installationsmarkør.' }
    $resumeMarker = Get-Content -LiteralPath $resumeMarkerPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($resumeMarker.schemaVersion -ne 1 -or $resumeMarker.product -ne 'ElevhubServer' -or
        $resumeMarker.state -notin @('rolledBack','installing','rollbackFailed','recoveryPrepared') -or $resumeMarker.installRoot -ne $InstallRoot -or
        $resumeMarker.dataRoot -ne $DataRoot -or [string]::IsNullOrWhiteSpace([string]$resumeMarker.ownerId) -or
        $resumeMarker.taskName -ne $TaskName -or (Get-InstalledRoleIp $resumeMarker $DataRoot) -ne $RoleIp -or
        $resumeMarker.roleNetwork -ne $RoleNetwork -or $resumeMarker.studentSubnet -ne $StudentSubnet -or
        $resumeMarker.teacherAddress -ne $TeacherAddress -or
        $resumeMarker.packageManifestSha256 -ne (Get-FileHash -LiteralPath (Join-Path $PackageRoot 'integrity.json') -Algorithm SHA256).Hash.ToLowerInvariant() -or
        [int]$resumeMarker.printBackendPort -ne $PrintBackendPort -or [int]$resumeMarker.designBackendPort -ne $DesignBackendPort -or
        [int]$resumeMarker.teacherBackendPort -ne $TeacherBackendPort -or [int]$resumeMarker.teacherPort -ne $TeacherPort -or
        [bool]$resumeMarker.generationLayout -ne [bool]$GenerationLayout -or
        @($resumeMarker.externalBackends).Count -ne $(if ($UseExistingPrintBackend) { 1 } else { 0 }) -or
        (@($resumeMarker.externalBackends) -contains 'print') -ne [bool]$UseExistingPrintBackend) {
        throw 'Resume afvises: markøren er ikke en ejet, afbrudt førstegangsinstallation.'
    }
    $resumeOwnerId = [Guid]::Empty
    if (-not [Guid]::TryParse([string]$resumeMarker.ownerId, [ref]$resumeOwnerId) -or $resumeOwnerId -eq [Guid]::Empty) { throw 'Resume afvises: installationens ejer-id er ugyldigt.' }
    if ($GenerationLayout -and ($resumeMarker.PSObject.Properties.Name -notcontains 'releaseManifestSha256' -or $resumeMarker.releaseManifestSha256 -cne $releaseManifestSha256)) { throw 'Resume afvises: generationsudgivelsen matcher ikke den oprindelige installation.' }
    $expectedFirewallNames=@($FirewallPorts | ForEach-Object { "ElevhubServer-$_" })
    if (@($resumeMarker.firewallNames).Count -ne $expectedFirewallNames.Count -or @(Compare-Object -ReferenceObject $expectedFirewallNames -DifferenceObject @($resumeMarker.firewallNames)).Count) { throw 'Resume afvises: markørens firewall-liste matcher ikke installationen.' }
    Assert-NotReparse $DataRoot
    Assert-NoReparseTree $DataRoot
    if ($resumeMarker.state -eq 'recoveryPrepared' -and -not $RecoveryBackup) { throw 'En værtsgendannelse kræver det udtrykkelige -RecoveryBackup-valg.' }
    if (Test-Path -LiteralPath (Join-Path $DataRoot 'school-recovery.json') -PathType Leaf) {
        & $pythonSource -B -m app.school_recovery check --package-root $PackageRoot --runtime-root $InstallRoot --data-root $DataRoot
        if ($LASTEXITCODE -ne 0) { throw 'Den klargjorte værtsgendannelse er ændret; startspærrer bevares.' }
    }
} elseif ($Resume) {
    throw 'Resume afvises: den tidligere installations data er ikke bevaret.'
}
if ($Resume) {
    $resumeSecurityPath = Join-Path $DataRoot 'security'
    $resumeTlsFiles = @('ca.crt','server.crt','server.key','enheder.json') | ForEach-Object { Join-Path $resumeSecurityPath $_ }
    $resumeTlsComplete = @($resumeTlsFiles | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }).Count -eq $resumeTlsFiles.Count
    $resumeTlsJournal = Join-Path $resumeSecurityPath 'tls-initialization.json'
    $resumeTlsJournalPresent = (Test-Path -LiteralPath $resumeTlsJournal -PathType Leaf) -or (Test-Path -LiteralPath "$resumeTlsJournal.tmp" -PathType Leaf)
    if (-not $resumeTlsComplete -and -not $resumeTlsJournalPresent -and
        ((Test-Path -LiteralPath (Join-Path $DataRoot 'konfiguration.json')) -or $resumeMarker.caThumbprint -or
         ((Test-Path -LiteralPath $resumeSecurityPath) -and @(Get-ChildItem -LiteralPath $resumeSecurityPath -Force).Count -gt 0))) {
        throw 'Resume afvises: TLS- og enhedsdata er ufuldstændige uden journal; ingen data blev overskrevet.'
    }
    # Validate every staged/retained identity before ACLs, marker changes or
    # program copies. The provisioner never regenerates an exposed CA/key.
    & $pythonSource -B (Join-Path $PackageRoot 'server\provision.py') init-tls --output-dir $resumeSecurityPath --server-ip $RoleIp --owner-id ([string]$resumeMarker.ownerId) --resume --check
    if ($LASTEXITCODE -ne 0) { throw 'Resume afvises: TLS-journal eller identitet matcher ikke; ingen data blev overskrevet.' }
    if ($resumeMarker.caThumbprint -and $resumeTlsComplete) {
        $retainedCa = [Security.Cryptography.X509Certificates.X509Certificate2]::new((Join-Path $resumeSecurityPath 'ca.crt'))
        try {
            if ($retainedCa.Thumbprint.ToUpperInvariant() -ne ([string]$resumeMarker.caThumbprint).ToUpperInvariant()) {
                throw 'Resume afvises: CA-identiteten er ændret; ingen data blev overskrevet.'
            }
        } finally { $retainedCa.Dispose() }
    }
    $resumeConfigPath = Join-Path $DataRoot 'konfiguration.json'
    if (Test-Path -LiteralPath $resumeConfigPath -PathType Leaf) {
        $resumeConfig = Get-Content -LiteralPath $resumeConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($resumeConfig.elev_adresse -ne $RoleIp -or $resumeConfig.laerer_adresse -ne $TeacherAddress -or
            [int]$resumeConfig.laerer_port -ne $TeacherPort -or
            [int]$resumeConfig.backend_ports.print -ne $PrintBackendPort -or
            [int]$resumeConfig.backend_ports.design -ne $DesignBackendPort -or
            [int]$resumeConfig.backend_ports.teacher -ne $TeacherBackendPort -or
            $resumeConfig.tls.certifikat -ne (Join-Path $resumeSecurityPath 'server.crt') -or
            $resumeConfig.tls.noegle -ne (Join-Path $resumeSecurityPath 'server.key') -or
            $resumeConfig.tls.ca -ne (Join-Path $resumeSecurityPath 'ca.crt') -or
            $resumeConfig.enhedsregister -ne (Join-Path $resumeSecurityPath 'enheder.json')) {
            throw 'Resume afvises: den bevarede konfiguration matcher ikke installationen; ingen data blev overskrevet.'
        }
    }
    if ($resumeMarker.state -in @('installing','rollbackFailed') -or (Test-Path -LiteralPath $InstallRoot)) {
        # A hard crash bypasses the catch handler. Finish the same guarded
        # rollback before checking ports or recreating first-install resources.
        # Never adopt an installed marker or a later generation selection.
        if (Test-Path -LiteralPath $InstallRoot) { Assert-OwnedProgramTree }
        Remove-OwnedResources $resumeMarkerPath
        $resumeMarker = Get-Content -LiteralPath $resumeMarkerPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($resumeMarker.state -ne 'rolledBack') { throw 'Resume kunne ikke afslutte den ejede rollback.' }
    }
}
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { throw 'Der findes allerede en opgave med navnet ElevhubServer.' }
foreach ($port in $FirewallPorts) { if (Get-NetFirewallRule -Name "ElevhubServer-$port" -ErrorAction SilentlyContinue) { throw "Der findes allerede en firewallregel ElevhubServer-$port." } }
Assert-PortsFree
Assert-InstallSpace

$ownerId = [Guid]::NewGuid().ToString('D'); $markerPath = Join-Path $DataRoot 'installation-owner.json'; $createdDataRoot = $false
$marker = [ordered]@{ schemaVersion=1; product='ElevhubServer'; ownerId=$ownerId; state='installing'; createdUtc=[DateTime]::UtcNow.ToString('o'); installRoot=$InstallRoot; dataRoot=$DataRoot; taskName=$TaskName; roleIp=$RoleIp; roleNetwork=$RoleNetwork; studentSubnet=$StudentSubnet; teacherAddress=$TeacherAddress; teacherPort=$TeacherPort; printBackendPort=$PrintBackendPort; designBackendPort=$DesignBackendPort; teacherBackendPort=$TeacherBackendPort; generationLayout=[bool]$GenerationLayout; firewallNames=@($FirewallPorts | ForEach-Object { "ElevhubServer-$_" }); caThumbprint=$null; packageManifestSha256=(Get-FileHash -LiteralPath (Join-Path $PackageRoot 'integrity.json') -Algorithm SHA256).Hash.ToLowerInvariant() }
if ($Resume) {
    $ownerId = [string]$resumeMarker.ownerId
    $marker = [ordered]@{}
    foreach ($property in $resumeMarker.PSObject.Properties) { $marker[$property.Name] = $property.Value }
    $marker.state = 'installing'; $marker.resumedUtc = [DateTime]::UtcNow.ToString('o')
    $marker.installRoot = $InstallRoot; $marker.dataRoot = $DataRoot; $marker.roleIp = $RoleIp
    $marker.roleNetwork = $RoleNetwork; $marker.studentSubnet = $StudentSubnet; $marker.teacherAddress = $TeacherAddress
    $marker.teacherPort = $TeacherPort; $marker.firewallNames = @($FirewallPorts | ForEach-Object { "ElevhubServer-$_" })
    $marker.packageManifestSha256 = (Get-FileHash -LiteralPath (Join-Path $PackageRoot 'integrity.json') -Algorithm SHA256).Hash.ToLowerInvariant()
}
$marker.externalBackends = @()
$marker.releaseManifestSha256 = $releaseManifestSha256
if ($GenerationLayout) { $marker.startupProtocol = 'generation-v1' }
if ($UseExistingPrintBackend) { $marker.externalBackends = @('print') }
try {
    if (-not $Resume) { New-Item -ItemType Directory -Path $DataRoot | Out-Null; $createdDataRoot = $true }
    Invoke-Icacls $DataRoot @('/inheritance:r','/grant:r','*S-1-5-18:(OI)(CI)F','*S-1-5-32-544:(OI)(CI)F','*S-1-5-19:(RX)')
    Assert-NotReparse $DataRoot
    # The service must be able to hold the lifetime lock and read the
    # protocol marker, while the data-root itself remains non-writable.
    $instanceLockPath = Join-Path $DataRoot 'instance.lock'
    $protocolPath = Join-Path $DataRoot 'backup-protocol.json'
    if (-not (Test-Path -LiteralPath $instanceLockPath -PathType Leaf)) { New-Item -ItemType File -Path $instanceLockPath | Out-Null }
    Invoke-Icacls $instanceLockPath @('/inheritance:r','/grant:r','*S-1-5-18:F','*S-1-5-32-544:F','*S-1-5-19:M')
    if (-not (Test-Path -LiteralPath (Join-Path $DataRoot 'school-recovery.json'))) { Write-JsonAtomic $protocolPath ([ordered]@{ protocol=1; role='server' }) }
    Invoke-Icacls $protocolPath @('/inheritance:r','/grant:r','*S-1-5-18:F','*S-1-5-32-544:F','*S-1-5-19:R')
    Write-JsonAtomic $markerPath $marker

    $securityPath = Join-Path $DataRoot 'security'
    $tlsFiles = @('ca.crt','server.crt','server.key','enheder.json') | ForEach-Object { Join-Path $securityPath $_ }
    $tlsPresent = @($tlsFiles | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
    $tlsArguments = @('-B',(Join-Path $PackageRoot 'server\provision.py'),'init-tls','--output-dir',$securityPath,'--server-ip',$RoleIp,'--owner-id',$ownerId)
    if ($Resume) { $tlsArguments += '--resume' }
    & $pythonSource @tlsArguments
    if ($LASTEXITCODE -ne 0) { throw 'TLS-oprettelse eller genoptagelse fejlede.' }
    $security = $securityPath
    Invoke-Icacls $security @('/inheritance:r','/grant:r','*S-1-5-18:(OI)(CI)F','*S-1-5-32-544:(OI)(CI)F','*S-1-5-19:(OI)(CI)RX')
    # The retained CA signer is an administrator-only recovery secret.  The
    # service may read the public CA and leaf key, but must never read ca.key
    # or a staged journal that still contains the CA private key.
    foreach ($privateName in @('ca.key','tls-initialization.json','tls-initialization.json.tmp','tls-renewal.json','tls-renewal-receipt.json')) {
        $privatePath = Join-Path $security $privateName
        if (Test-Path -LiteralPath $privatePath -PathType Leaf) {
            Assert-NotReparse $privatePath
            Invoke-Icacls $privatePath @('/inheritance:r','/grant:r','*S-1-5-18:F','*S-1-5-32-544:F')
        }
    }
    # A replacement-host backup already contains the administration identity.
    # Protect it before any service starts, including before Setup-School's
    # later enrollment steps can tighten its own ACLs.
    $retainedAdminSsh = Join-Path $DataRoot 'admin-ssh'
    if (Test-Path -LiteralPath $retainedAdminSsh -PathType Container) {
        Assert-NotReparse $retainedAdminSsh
        Invoke-Icacls $retainedAdminSsh @('/inheritance:r','/grant:r','*S-1-5-18:(OI)(CI)F','*S-1-5-32-544:(OI)(CI)F')
    }
    Invoke-Icacls (Join-Path $security 'enheder.json') @('/inheritance:r','/grant:r','*S-1-5-18:F','*S-1-5-32-544:F','*S-1-5-19:R')
    $enhederLockPath = Join-Path $security 'enheder.lock'
    if (-not (Test-Path -LiteralPath $enhederLockPath -PathType Leaf)) { New-Item -ItemType File -Path $enhederLockPath | Out-Null }
    Invoke-Icacls $enhederLockPath @('/inheritance:r','/grant:r','*S-1-5-18:F','*S-1-5-32-544:F','*S-1-5-19:M')
    $runtimeFolders = @('logs','hub','designvaerksted')
    if (-not $UseExistingPrintBackend) { $runtimeFolders += @('printvaerksted','local-ai') }
    foreach ($name in $runtimeFolders) {
        $path = Join-Path $DataRoot $name; New-Item -ItemType Directory -Force -Path $path | Out-Null
        Invoke-Icacls $path @('/inheritance:r','/grant:r','*S-1-5-18:(OI)(CI)F','*S-1-5-32-544:(OI)(CI)F','*S-1-5-19:(OI)(CI)M')
    }

    New-Item -ItemType Directory -Path $InstallRoot | Out-Null; Assert-NotReparse $InstallRoot
    Invoke-Icacls $InstallRoot @('/inheritance:r','/grant:r','*S-1-5-18:(OI)(CI)F','*S-1-5-32-544:(OI)(CI)F','*S-1-5-32-545:(OI)(CI)RX','*S-1-5-19:(OI)(CI)RX')
    $programRoot = $InstallRoot
    if ($GenerationLayout) {
        $programRoot = Join-Path $InstallRoot 'program'
        New-Item -ItemType Directory -Path $programRoot | Out-Null
    }
    Get-ChildItem -LiteralPath $PackageRoot -Force | Copy-Item -Destination $programRoot -Recurse -Force
    if ($GenerationLayout) {
        # Fixed runtime and administrative entry points, outside immutable sources.
        foreach ($folder in @('runtime','server','deployment')) {
            Copy-Item -LiteralPath (Join-Path $programRoot $folder) -Destination $InstallRoot -Recurse
        }
        New-Item -ItemType Directory -Path (Join-Path $InstallRoot 'app') | Out-Null
        Copy-FixedAdministrativeAppFiles $programRoot $InstallRoot $integrityManifest
    }
    & (Join-Path $programRoot 'runtime\python.exe') -B (Join-Path $programRoot 'server\verify.py') --package-root $programRoot --role-ip $RoleIp
    if ($LASTEXITCODE -ne 0) { throw 'Den kopierede installation bestod ikke integritetskontrollen.' }

    $runtimeConfig = Join-Path $DataRoot 'konfiguration.json'
    # A failed readiness check may already have accepted school configuration.
    # Replay must retain that exact file, including school-specific extensions.
    if (-not $Resume -or -not (Test-Path -LiteralPath $runtimeConfig -PathType Leaf)) {
        $baseConfig = Get-Content -LiteralPath (Join-Path $programRoot 'hub\konfiguration.windows.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $baseConfig.elev_adresse = $RoleIp; $baseConfig.laerer_adresse = $TeacherAddress; $baseConfig.laerer_port = $TeacherPort
        $baseConfig | Add-Member -NotePropertyName external_backends -NotePropertyValue @($marker.externalBackends) -Force
        if ($null -eq $baseConfig.backend_ports) { $baseConfig | Add-Member -NotePropertyName backend_ports -NotePropertyValue ([pscustomobject]@{}) }
        $baseConfig.backend_ports.print = $PrintBackendPort; $baseConfig.backend_ports.design = $DesignBackendPort; $baseConfig.backend_ports.teacher = $TeacherBackendPort
        foreach ($app in @($baseConfig.apps)) {
            if ($app.id -eq '3dprint') { $app.bagved = "127.0.0.1:$PrintBackendPort" }
            elseif ($app.id -in @('laser','folie')) { $app.bagved = "127.0.0.1:$DesignBackendPort" }
        }
        if ($UseExistingPrintBackend) {
            if ($baseConfig.PSObject.Properties.Name -contains 'makerspace_printers') {
                $baseConfig.PSObject.Properties.Remove('makerspace_printers')
            }
        } else {
            $baseConfig | Add-Member -NotePropertyName makerspace_printers -NotePropertyValue ([pscustomobject]@{
                backend = "127.0.0.1:$PrintBackendPort"
                key_file = Join-Path (Join-Path $DataRoot 'printvaerksted') 'makerspace-bridge.key'
            }) -Force
        }
        $baseConfig.tls.certifikat = Join-Path $security 'server.crt'; $baseConfig.tls.noegle = Join-Path $security 'server.key'; $baseConfig.tls.ca = Join-Path $security 'ca.crt'; $baseConfig.enhedsregister = Join-Path $security 'enheder.json'
        Write-JsonAtomic $runtimeConfig $baseConfig
    }
    Invoke-Icacls $runtimeConfig @('/inheritance:r','/grant:r','*S-1-5-18:F','*S-1-5-32-544:F','*S-1-5-19:R')
    $localAiIncluded = $true
    if ($packageManifest.PSObject.Properties.Name -contains 'localAiIncluded') {
        if ($packageManifest.localAiIncluded -isnot [bool]) { throw 'Pakkens localAiIncluded skal være boolsk.' }
        $localAiIncluded = [bool]$packageManifest.localAiIncluded
    }
    Write-JsonAtomic (Join-Path $InstallRoot 'installation.json') ([ordered]@{data_root=$DataRoot;port=$PrintBackendPort;autostart='Boot';ai_enabled=$localAiIncluded})
    if ($GenerationLayout) {
        $installationId = Get-GenerationInstallationId ([pscustomobject]$marker)
        if (Test-Path -LiteralPath (Join-Path $DataRoot 'school-recovery.json') -PathType Leaf) {
            & $pythonSource -B -m app.school_recovery release --package-root $PackageRoot --runtime-root $InstallRoot --data-root $DataRoot
            if ($LASTEXITCODE -ne 0) { throw 'Værtsgendannelsen kunne ikke frigives efter pakke-, identitets- og rettighedskontrollen.' }
        }
        & (Join-Path $programRoot 'runtime\python.exe') -B (Join-Path $programRoot 'server\generation_install.py') --package-root $programRoot --runtime-root $InstallRoot --data-root $DataRoot --installation-id $installationId
        if ($LASTEXITCODE -ne 0) { throw 'Første fælles generationsvalg kunne ikke oprettes.' }
        $checkCommand = Get-GenerationCommand -InstallRoot $InstallRoot -InstallationId $installationId -Entry server -Arguments @('--check','--installed','--role-ip',$RoleIp,'--teacher-port',[string]$TeacherPort,'--teacher-address',$TeacherAddress)
        $checkArguments = $checkCommand.ArgumentList
        & $checkCommand.Execute @checkArguments
    } else {
        & (Join-Path $InstallRoot 'runtime\python.exe') -B (Join-Path $InstallRoot 'server\bootstrap.py') --package-root $InstallRoot --data-root $DataRoot --check --installed --role-ip $RoleIp --teacher-port $TeacherPort --teacher-address $TeacherAddress
    }
    if ($LASTEXITCODE -ne 0) { throw 'Runtime-konfigurationen bestod ikke den skrivefri kontrol.' }

    $caPath=Join-Path $security 'ca.crt'; $caCertificate=[Security.Cryptography.X509Certificates.X509Certificate2]::new($caPath)
    try { $marker.caThumbprint=$caCertificate.Thumbprint.ToUpperInvariant() } finally { $caCertificate.Dispose() }
    Write-JsonAtomic $markerPath $marker
    $imported = Import-Certificate -FilePath $caPath -CertStoreLocation 'Cert:\LocalMachine\Root'
    if (-not $imported -or $imported.Thumbprint.ToUpperInvariant() -ne $marker.caThumbprint) { throw 'Den lokale CA kunne ikke importeres med det forventede fingeraftryk.' }

    $python = Join-Path $InstallRoot 'runtime\python.exe'; $bootstrap = Join-Path $InstallRoot 'server\bootstrap.py'
    $arguments = '-B "{0}" --package-root "{1}" --data-root "{2}" --role-ip "{3}" --teacher-port "{4}" --teacher-address "{5}"' -f $bootstrap,$InstallRoot,$DataRoot,$RoleIp,$TeacherPort,$TeacherAddress
    if ($GenerationLayout) {
        $arguments = (Get-GenerationCommand -InstallRoot $InstallRoot -InstallationId $installationId -Entry server -Arguments @('--role-ip',$RoleIp,'--teacher-port',[string]$TeacherPort,'--teacher-address',$TeacherAddress)).Arguments
    }
    $action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $InstallRoot
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'S-1-5-19' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 12 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "ElevhubServer owner=$ownerId" | Out-Null
    foreach ($port in $FirewallPorts) { New-NetFirewallRule -Name "ElevhubServer-$port" -DisplayName "Elevhub server TCP $port" -Direction Inbound -Action Allow -Enabled True -Profile Any -Protocol TCP -LocalAddress $RoleIp -LocalPort $port -RemoteAddress $StudentSubnet -Program $python -Description "ElevhubServer owner=$ownerId" | Out-Null }

    # Initial administrator-created sources/control files must have an owner
    # recognized by the offline maintenance ACL contract.
    Invoke-Icacls $InstallRoot @('/setowner','*S-1-5-32-544','/T')
    Invoke-Icacls $DataRoot @('/setowner','*S-1-5-32-544','/T')
    if (Test-Path -LiteralPath (Join-Path $DataRoot 'school-recovery.json') -PathType Leaf) {
        & $pythonSource -B -m app.school_recovery started --package-root $PackageRoot --runtime-root $InstallRoot --data-root $DataRoot
        if ($LASTEXITCODE -ne 0) { throw 'Værtsgendannelsens verificerede opstart kunne ikke journalføres.' }
    }
    Start-ScheduledTask -TaskName $TaskName
    $readinessTimeoutSeconds = 180
    $deadline = [DateTime]::UtcNow.AddSeconds($readinessTimeoutSeconds); $open = $false
    do {
        Start-Sleep -Milliseconds 500; $open = $true
        foreach ($binding in $PortBindings) { $client=[Net.Sockets.TcpClient]::new(); try { $client.Connect([string]$binding.Address,[int]$binding.Port) } catch { $open=$false } finally { $client.Dispose() }; if (-not $open) { break } }
    } until ($open -or [DateTime]::UtcNow -ge $deadline)
    if (-not $open) { throw "Serverens porte blev ikke klar inden for $readinessTimeoutSeconds sekunder." }
    foreach ($port in $FirewallPorts) {
        $request=[Net.HttpWebRequest]::Create("https://$RoleIp`:$port/"); $request.AllowAutoRedirect=$false; $request.Timeout=10000; $request.Proxy=$null
        try { $response=$request.GetResponse() } catch [Net.WebException] { if ($null -eq $_.Exception.Response) { throw }; $response=$_.Exception.Response }
        try { if ([int]$response.StatusCode -ge 500) { throw "TLS-sundhedstjek på port $port svarede HTTP $([int]$response.StatusCode)." } } finally { $response.Dispose() }
    }
    if ($UseExistingPrintBackend) { Assert-ExistingPrintBackend }
    $marker.state='installed'; $marker.installedUtc=[DateTime]::UtcNow.ToString('o'); Write-JsonAtomic $markerPath $marker
    if (Test-Path -LiteralPath (Join-Path $DataRoot 'school-recovery.json') -PathType Leaf) {
        & $pythonSource -B -m app.school_recovery complete --package-root $PackageRoot --runtime-root $InstallRoot --data-root $DataRoot
        if ($LASTEXITCODE -ne 0) { throw 'Værtsgendannelsens afslutningskvittering kunne ikke skrives.' }
    }
    Write-Output "ElevhubServer er installeret. Data og nøgler ligger beskyttet i $DataRoot."
} catch {
    $failure=$_
    $rollbackComplete=$false
    try { Remove-OwnedResources $markerPath; $rollbackComplete=$true } catch { [Console]::Error.WriteLine("Automatisk rollback fejlede: $($_.Exception.Message)") }
    # TLS keys and possibly already accepted jobs survive a failed readiness
    # check. Rollback owns programs/tasks, never deletion of created school data.
    if ($createdDataRoot -and (Test-Path -LiteralPath $DataRoot)) {
        [Console]::Error.WriteLine("Installationsdata er bevaret til undersøgelse/gendannelse: $DataRoot")
    }
    throw $failure
}

# The server is installed and healthy before the optional administrator action
# is enabled. A failure here must not roll back a working installation or data.
try {
    & (Join-Path $InstallRoot 'server\configure-restart.ps1') -Mode Enable
} catch {
    Write-Warning ('Serveren er installeret, men genstartsvalget kunne ikke aktiveres. Vælg skoleadministrationens menu 13 for at prøve igen. ' + $_.Exception.Message)
}
