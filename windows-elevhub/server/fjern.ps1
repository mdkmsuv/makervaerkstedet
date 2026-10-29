[CmdletBinding(SupportsShouldProcess, ConfirmImpact='High')]
param(
    [string]$InstallRoot = "$env:ProgramFiles\ElevhubServer",
    [string]$DataRoot = "$env:ProgramData\ElevhubServer"
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

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
function Assert-Administrator {
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Kør fjernelsen som administrator.' }
}
function Assert-NoReparseTree([string]$Path) {
    $items = @((Get-Item -LiteralPath $Path -Force)) + @(Get-ChildItem -LiteralPath $Path -Force -Recurse)
    foreach ($item in $items) { if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Fjernelse afviser reparse point: $($item.FullName)" } }
}
function Write-JsonAtomic([string]$Path, [object]$Value) {
    $temp="$Path.tmp"; [IO.File]::WriteAllText($temp,($Value|ConvertTo-Json -Depth 20)+"`n",[Text.UTF8Encoding]::new($false)); Move-Item -LiteralPath $temp -Destination $Path -Force
}
. (Join-Path $PSScriptRoot 'school-uninstall-guards.ps1')
. (Join-Path $PSScriptRoot 'generation-startup.ps1')

function Get-TaskPrincipalSid([object]$Identity) {
    $value = [string]$Identity
    if ($value -match '^S-\d(?:-\d+)+$') { return $value }
    try {
        $account = [Security.Principal.NTAccount]::new($value)
        return $account.Translate([Security.Principal.SecurityIdentifier]).Value
    } catch {
        # Task Scheduler may expose the built-in account in the host language.
        # An unresolved name stays rejected; only the translated SID is trusted.
        return $null
    }
}

function Get-TaskPrincipalSidFromTask([object]$Task) {
    $principal = if ($null -ne $Task -and $Task.PSObject.Properties.Name -contains 'Principal') { $Task.Principal } else { $null }
    $principalText = if ($null -ne $principal -and $principal.PSObject.Properties.Name -contains 'UserId') { [string]$principal.UserId } else { '' }
    return Get-TaskPrincipalSid $principalText
}

function Get-TaskShortcutNames([string]$CanonicalName) {
    $names = [Collections.Generic.List[string]]::new()
    $names.Add($CanonicalName)
    # The sealed no-BOM installer was read as the local ANSI code page by
    # Windows PowerShell 5.1. Keep only this exact derived legacy spelling.
    $legacyName = [Text.Encoding]::Default.GetString([Text.Encoding]::UTF8.GetBytes($CanonicalName))
    if ($legacyName -cne $CanonicalName) { $names.Add($legacyName) }
    return $names.ToArray()
}

Assert-Administrator
$InstallRoot=Full $InstallRoot; $DataRoot=Full $DataRoot
$expectedInstall=Full "$env:ProgramFiles\ElevhubServer"; $expectedData=Full "$env:ProgramData\ElevhubServer"
if ($InstallRoot -ne $expectedInstall -or $DataRoot -ne $expectedData) { throw 'Fjernelse må kun målrette Elevhubs faste mapper.' }
$markerPath=Join-Path $DataRoot 'installation-owner.json'
if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) { throw 'Ejerskabsmarkøren mangler; intet fjernes.' }
Assert-PlainPath $DataRoot; Assert-PlainPath $markerPath; Assert-PlainPath $InstallRoot
$marker=Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
try { $null=[Guid]::Parse([string]$marker.ownerId) } catch { throw 'Ejerskabsmarkørens ejer-id er ugyldigt.' }
if ($marker.schemaVersion -ne 1 -or $marker.product -ne 'ElevhubServer' -or $marker.installRoot -ne $InstallRoot -or $marker.dataRoot -ne $DataRoot -or $marker.taskName -ne 'ElevhubServer') { throw 'Ejerskabsmarkøren er ugyldig.' }
$startupMode=Get-InstallationStartupMode -Owner $marker -InstallRoot $InstallRoot
$generation=$startupMode -eq 'generation-v1'
$anchorDataRoot=$DataRoot; $anchorMarkerPath=$markerPath; $retainGenerations=$false; $generationPlan=$null
$selectionSha256=$null
function Get-GenerationRemovalPlan {
    $python=Join-Path $InstallRoot 'runtime\python.exe'
    $raw=& $python -B (Join-Path $InstallRoot 'server\provision.py') generation-uninstall-plan --runtime-root $InstallRoot --anchor-data-root $anchorDataRoot
    if ($LASTEXITCODE -ne 0) { throw 'Generationsvalget eller installationsejeren matcher ikke; Windows-integration og alle data er bevaret.' }
    return (($raw -join "`n") | ConvertFrom-Json)
}
if ($generation -and (Test-Path -LiteralPath $InstallRoot -PathType Container)) {
    $generationPlan=Get-GenerationRemovalPlan
    $retainGenerations=[bool]$generationPlan.retainGenerations
    $DataRoot=Full ([string]$generationPlan.dataRoot); Assert-PlainPath $DataRoot
    $markerPath=Join-Path $DataRoot 'installation-owner.json'; Assert-PlainPath $markerPath
    $marker=Get-Content -LiteralPath $markerPath -Raw -Encoding UTF8 | ConvertFrom-Json
}
function Assert-InitialGenerationRemoval {
    if (-not (Test-Path -LiteralPath $InstallRoot -PathType Container)) { return }
    if ($retainGenerations) { return }
    # The source manifest is bound to the protected installation owner. Later
    # revisions, relocated data and unknown files are deliberately retained.
    $python=Join-Path $InstallRoot 'runtime\python.exe'
    $source=if ($generation) { Join-Path $InstallRoot 'program' } else { $InstallRoot }
    & $python -B (Join-Path $InstallRoot 'server\provision.py') check-initial-install-tree --package-root $source --runtime-root $InstallRoot --data-root $DataRoot --uninstall
    if ($LASTEXITCODE -ne 0) { throw 'Generationsfjernelse kræver det uændrede første generationsvalg; alle programmer og data er bevaret.' }
}
Assert-InitialGenerationRemoval
if ($generation -and (Test-Path -LiteralPath $InstallRoot -PathType Container)) { $selectionSha256=(Get-FileHash -LiteralPath (Join-Path $InstallRoot 'control\active-generation.json') -Algorithm SHA256).Hash }
$markerRoleIp = Get-InstalledRoleIp $marker $DataRoot
$markerTeacherAddress = if ($marker.PSObject.Properties.Name -contains 'teacherAddress') { [string]$marker.teacherAddress } else { $markerRoleIp }
$roleAddress = $null; try { $roleAddress = [Net.IPAddress]::Parse($markerRoleIp) } catch { throw 'Ejerskabsmarkørens serveradresse er ugyldig.' }
if ($roleAddress.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or $roleAddress.Equals([Net.IPAddress]::Any) -or [Net.IPAddress]::IsLoopback($roleAddress)) { throw 'Ejerskabsmarkørens serveradresse er ugyldig.' }
$markerTeacherPort = if ($marker.PSObject.Properties.Name -contains 'teacherPort') { [int]$marker.teacherPort } else { 8780 }
$markerStudentSubnet = Get-InstalledStudentSubnet $marker $DataRoot $markerRoleIp
$expectedFirewallPorts = @(8770,8771,8772,8773)
if ($markerTeacherAddress -ne '127.0.0.1') { $expectedFirewallPorts += $markerTeacherPort }
$expectedFirewallNames=@($expectedFirewallPorts | ForEach-Object { "ElevhubServer-$_" })
if (@($marker.firewallNames).Count -ne $expectedFirewallNames.Count -or @(Compare-Object -ReferenceObject $expectedFirewallNames -DifferenceObject @($marker.firewallNames)).Count -ne 0) { throw 'Ejerskabsmarkørens firewall-liste er ugyldig.' }
if ($marker.caThumbprint -and [string]$marker.caThumbprint -notmatch '^[A-Fa-f0-9]{40,64}$') { throw 'Ejerskabsmarkørens certifikat-id er ugyldigt.' }
if ($marker.caThumbprint) {
    $caPath=Join-Path $DataRoot 'security\ca.crt'; Assert-PlainPath $caPath
    $retainedCa=[Security.Cryptography.X509Certificates.X509Certificate2]::new($caPath)
    try { if ($retainedCa.Thumbprint.ToUpperInvariant() -ne ([string]$marker.caThumbprint).ToUpperInvariant()) { throw 'Den bevarede CA matcher ikke installationen; intet fjernes.' } } finally { $retainedCa.Dispose() }
}
if (Test-Path -LiteralPath $InstallRoot) { Assert-NoReparseTree $InstallRoot }
$task=Get-ScheduledTask -TaskName $marker.taskName -ErrorAction SilentlyContinue
if ($task) {
    $expectedPython=Join-Path $InstallRoot 'runtime\python.exe'; $expectedBootstrap=Join-Path $InstallRoot 'server\bootstrap.py'
    $roleArgument = if ($marker.PSObject.Properties.Name -contains 'roleIp') { ' --role-ip "{0}" --teacher-port "{1}" --teacher-address "{2}"' -f $markerRoleIp,[int]$marker.teacherPort,$markerTeacherAddress } else { '' }
    $expectedArguments='-B "{0}" --package-root "{1}" --data-root "{2}"{3}' -f $expectedBootstrap,$InstallRoot,$DataRoot,$roleArgument
    if ($generation) { $expectedArguments=(Get-GenerationCommand -InstallRoot $InstallRoot -InstallationId (Get-GenerationInstallationId $marker) -Entry server -Arguments @('--role-ip',$markerRoleIp,'--teacher-port',[string]$marker.teacherPort,'--teacher-address',$markerTeacherAddress)).Arguments }
    if ($task.Description -ne "ElevhubServer owner=$($marker.ownerId)" -or @($task.Actions).Count -ne 1 -or $task.Actions[0].Execute -ne $expectedPython -or $task.Actions[0].WorkingDirectory -ne $InstallRoot -or $task.Actions[0].Arguments -ne $expectedArguments -or (Get-TaskPrincipalSidFromTask $task) -cne 'S-1-5-19') { throw 'Den planlagte opgave er ændret eksternt; intet fjernes.' }
}
foreach ($name in @($marker.firewallNames)) {
    $rule=Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
    if ($rule) {
        $port=[int]($name -replace '^ElevhubServer-',''); $pf=$rule | Get-NetFirewallPortFilter; $af=$rule | Get-NetFirewallAddressFilter; $app=$rule | Get-NetFirewallApplicationFilter
        $remote=@($af.RemoteAddress); $local=@($af.LocalAddress); $remoteOk=$false
        if ($remote.Count -eq 1) { $remoteOk=Test-FirewallAddressEquivalent -Actual ([string]$remote[0]) -Expected $markerStudentSubnet }
        if ($rule.Description -ne "ElevhubServer owner=$($marker.ownerId)" -or $rule.Direction -ne 'Inbound' -or $rule.Action -ne 'Allow' -or $rule.Enabled -ne 'True' -or $rule.Profile -ne 'Any' -or $pf.Protocol -notin @('TCP',6) -or [string]$pf.LocalPort -ne [string]$port -or $local.Count -ne 1 -or $local[0] -ne $markerRoleIp -or -not $remoteOk -or $app.Program -ne (Join-Path $InstallRoot 'runtime\python.exe')) { throw "Firewallreglen $name er ændret eksternt; intet fjernes." }
    }
}
$school=$null; $schoolTask=$null; $schoolFirewall=$null; $schoolShortcuts=@(); $schoolPublicFiles=@()
$schoolPath=Join-Path $DataRoot 'school.json'
if (Test-Path -LiteralPath $schoolPath -PathType Leaf) {
    Assert-PlainPath $schoolPath
    $school=Get-Content -LiteralPath $schoolPath -Raw | ConvertFrom-Json
    if ($school.schemaVersion -ne 1 -or $school.server -ne $markerRoleIp -or $school.student_subnet -ne $markerStudentSubnet -or $school.role -notin @('vm','makerspace')) {
        throw 'Skoleprofilen passer ikke til den installerede server; intet fjernes.'
    }
    $enrollment=Join-Path $InstallRoot 'deployment\school-setup\enrollment.py'
    $schoolTask=Get-ScheduledTask -TaskName 'ElevhubSchoolEnrollment' -ErrorAction SilentlyContinue
    if ($schoolTask) {
        $expectedArgs='-B "{0}" serve --data-root "{1}"' -f $enrollment,$DataRoot
        if ($generation) { $expectedArgs=(Get-GenerationCommand -InstallRoot $InstallRoot -InstallationId (Get-GenerationInstallationId $marker) -Entry enrollment -Arguments @('serve')).Arguments }
        if ($schoolTask.Description -ne 'Elevhub skole-enrollment' -or @($schoolTask.Actions).Count -ne 1 -or
            $schoolTask.Actions[0].Execute -ne (Join-Path $InstallRoot 'runtime\python.exe') -or
            $schoolTask.Actions[0].WorkingDirectory -ne $InstallRoot -or $schoolTask.Actions[0].Arguments -ne $expectedArgs -or
            (Get-TaskPrincipalSidFromTask $schoolTask) -cne 'S-1-5-18') {
            throw 'Skolens enrollment-opgave er ændret eksternt; intet fjernes.'
        }
    }
    $schoolFirewall=Get-NetFirewallRule -Name 'ElevhubSchoolEnrollment-8774' -ErrorAction SilentlyContinue
    if ($schoolFirewall) {
        $pf=$schoolFirewall | Get-NetFirewallPortFilter; $af=$schoolFirewall | Get-NetFirewallAddressFilter; $app=$schoolFirewall | Get-NetFirewallApplicationFilter
        $remote=@($af.RemoteAddress); $local=@($af.LocalAddress)
        $schoolRemoteOk=$false
        if ($remote.Count -eq 1) { $schoolRemoteOk=Test-FirewallAddressEquivalent -Actual ([string]$remote[0]) -Expected $markerStudentSubnet }
        if ($schoolFirewall.Description -ne 'ElevhubSchoolEnrollment' -or $schoolFirewall.Direction -ne 'Inbound' -or
            $schoolFirewall.Action -ne 'Allow' -or $schoolFirewall.Enabled -ne 'True' -or $schoolFirewall.Profile -ne 'Any' -or
            $pf.Protocol -notin @('TCP',6) -or [string]$pf.LocalPort -ne '8774' -or
            $local.Count -ne 1 -or $local[0] -ne $markerRoleIp -or -not $schoolRemoteOk -or
            $app.Program -ne (Join-Path $InstallRoot 'runtime\python.exe')) {
            throw 'Skolens firewallregel er ændret eksternt; intet fjernes.'
        }
    }
    $desktop=Join-Path $env:Public 'Desktop'
    foreach ($item in @(@{Name='Elevhub skoleopsætning.lnk';Target=(Join-Path $InstallRoot 'deployment\school-setup\Start.cmd')},
                       @{Name='Elevhub skoleadmin.lnk';Target=(Join-Path $InstallRoot 'deployment\school-setup\Manage-School.cmd')},
                       @{Name='Elevhub kontrolpanel.lnk';Target=(Join-Path $InstallRoot 'deployment\school-setup\Open-ControlPanel.cmd');Arguments=('-Port {0} -Address {1}' -f $markerTeacherPort,$markerTeacherAddress);CompatibleArguments=@('-Port {0}' -f $markerTeacherPort)},
                       @{Name='Makerværkstedet.lnk';Target=(Join-Path $InstallRoot 'deployment\school-setup\Open-ControlPanel.cmd');Arguments=('-Port {0} -Address {1}' -f $markerTeacherPort,$markerTeacherAddress);CompatibleArguments=@('-Port {0}' -f $markerTeacherPort)})) {
        $expectedShortcutArguments = if ($item.ContainsKey('Arguments')) { [string]$item.Arguments } else { '' }
        $compatibleShortcutArguments = if ($item.ContainsKey('CompatibleArguments')) { @($item.CompatibleArguments) } else { @() }
        foreach ($shortcutName in (Get-TaskShortcutNames $item.Name)) {
            $path=Join-Path $desktop $shortcutName
            if (Get-OwnedShortcut $path $item.Target $InstallRoot $expectedShortcutArguments -CompatibleArguments $compatibleShortcutArguments) { $schoolShortcuts+= $path }
        }
    }
    if ($school.role -eq 'makerspace') {
        $startup=Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\Startup\Elevhub Laserstation.lnk'
        $script=Join-Path $InstallRoot 'deployment\school-setup\Start-LaserStation.ps1'
        $config=Join-Path $DataRoot 'laserstation\laser-installation.json'
        $expectedArgs='-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -ConfigPath "{1}" -Root "{2}"' -f $script,$config,$InstallRoot
        if ($generation) { $expectedArgs='-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Root "{1}" -ActiveGeneration -InstallationId "{2}"' -f $script,$InstallRoot,(Get-GenerationInstallationId $marker) }
        if (Get-OwnedShortcut $startup 'powershell.exe' $InstallRoot $expectedArgs) { $schoolShortcuts+= $startup }
        $makerShortcut=Join-Path $desktop 'Elevhub Laserstation.lnk'
        if (Get-OwnedShortcut $makerShortcut 'powershell.exe' $InstallRoot ($expectedArgs + ' -OpenBrowser')) { $schoolShortcuts+= $makerShortcut }
    }
    $publicDir=Join-Path $env:Public 'Documents\Elevvaerkstedet'
    if (Test-Path -LiteralPath $publicDir -PathType Container) {
        Assert-PlainPath $publicDir
        $ca=(Get-Content -LiteralPath (Join-Path $DataRoot 'security\ca.crt') -Raw).Trim()
        foreach ($file in @(Get-ChildItem -LiteralPath $publicDir -File | Where-Object { $_.Name -eq 'school-connection.json' -or $_.Name -match '^school-connection-[0-9]{8}-[0-9]{6}\.json$' })) {
            Assert-PlainPath $file.FullName
            try {
                $public=Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json
                if ($public.schemaVersion -eq 1 -and $public.kind -eq 'elevhub-school-connection' -and
                    $public.server -eq $school.server -and $public.admin_public_key -eq $school.admin_public_key -and
                    $public.caCertificatePem.Trim() -eq $ca) { $schoolPublicFiles+= $file.FullName }
            } catch { } # A user-owned or changed file is never deleted.
        }
    }
}
if (-not $PSCmdlet.ShouldProcess('ElevhubServer-programmer, opgave, firewallregler og den importerede lokale CA','Fjern')) { return }

# Remove the demand-only restart task before taking the offline removal locks;
# its own validator holds the same maintenance locks and refuses foreign state.
$restartSetup=Join-Path $InstallRoot 'server\configure-restart.ps1'
if(Test-Path -LiteralPath $restartSetup -PathType Leaf){& $restartSetup -Mode Remove}
if ($school -and $school.role -eq 'makerspace') {
    $stationConfig=Join-Path $DataRoot 'laserstation\laser-installation.json'
    $stationPython=Join-Path $InstallRoot 'runtime\python.exe'
    $generationStationLines=@()
    if ($generationPlan) {
        $stationArguments=(Get-GenerationCommand -InstallRoot $InstallRoot -InstallationId (Get-GenerationInstallationId $marker) -ExpectedSelection ([string]$generationPlan.selectionSha256) -Entry 'laser-station' -Arguments @()).Arguments
        $generationStationLines=@(('"{0}" {1}' -f $stationPython,$stationArguments),('{0} {1}' -f $stationPython,$stationArguments))
    }
    $stationProcesses=@(Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -eq $stationPython -and
        (($_.CommandLine -match '\bapp\.laser_station\b' -and $_.CommandLine -match [regex]::Escape($stationConfig)) -or
         ($generationStationLines -contains ([string]$_.CommandLine).Trim()))
    })
    foreach ($process in $stationProcesses) { Stop-Process -Id $process.ProcessId -ErrorAction Stop }
}
if ($schoolTask) {
    Stop-ScheduledTask -TaskName 'ElevhubSchoolEnrollment' -ErrorAction SilentlyContinue
    $deadline=[DateTime]::UtcNow.AddSeconds(20)
    do { Start-Sleep -Milliseconds 250; $schoolTask=Get-ScheduledTask -TaskName 'ElevhubSchoolEnrollment' -ErrorAction SilentlyContinue } until (-not $schoolTask -or $schoolTask.State -ne 'Running' -or [DateTime]::UtcNow -ge $deadline)
    if ($schoolTask -and $schoolTask.State -eq 'Running') { throw 'Enrollment-processen kunne ikke stoppes; intet slettes.' }
}
if ($task) {
    Stop-ScheduledTask -TaskName $marker.taskName -ErrorAction SilentlyContinue
    $deadline=[DateTime]::UtcNow.AddSeconds(20)
    do { Start-Sleep -Milliseconds 250; $task=Get-ScheduledTask -TaskName $marker.taskName -ErrorAction SilentlyContinue } until (-not $task -or $task.State -ne 'Running' -or [DateTime]::UtcNow -ge $deadline)
    if ($task -and $task.State -eq 'Running') { throw 'Serverprocessen kunne ikke stoppes; intet slettes.' }
}
$deadline=[DateTime]::UtcNow.AddSeconds(10)
do { $ownedProcesses=@(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($InstallRoot+'\',[StringComparison]::OrdinalIgnoreCase) }); if ($ownedProcesses.Count) { Start-Sleep -Milliseconds 250 } } until ($ownedProcesses.Count -eq 0 -or [DateTime]::UtcNow -ge $deadline)
if ($ownedProcesses.Count) { throw 'Et Elevhub-procestræ kører stadig; programmappen bevares.' }
Assert-InitialGenerationRemoval
$removalLocks=New-OfflineRemovalLocks $InstallRoot $DataRoot ($generation -and (Test-Path -LiteralPath $InstallRoot -PathType Container)) @($anchorDataRoot)
try {
if ($selectionSha256 -and ((Get-FileHash -LiteralPath (Join-Path $InstallRoot 'control\active-generation.json') -Algorithm SHA256).Hash -cne $selectionSha256 -or (Test-Path -LiteralPath (Join-Path $InstallRoot 'control\selection-transition.json')))) { throw 'Generationsvalget blev ændret før fjernelsen; alle programmer og data er bevaret.' }
if ($generationPlan -and ((Get-FileHash -LiteralPath $anchorMarkerPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $generationPlan.anchorOwnerSha256 -or (Get-FileHash -LiteralPath $markerPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $generationPlan.selectedOwnerSha256)) { throw 'Installationsejeren blev ændret før fjernelsen; alle programmer og data er bevaret.' }
if ($schoolTask) { Unregister-ScheduledTask -TaskName 'ElevhubSchoolEnrollment' -Confirm:$false }
if ($schoolFirewall) { Remove-NetFirewallRule -Name 'ElevhubSchoolEnrollment-8774' }
if ($task) { Unregister-ScheduledTask -TaskName $marker.taskName -Confirm:$false }
foreach ($name in @($marker.firewallNames)) { if (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue) { Remove-NetFirewallRule -Name $name } }
if ($marker.caThumbprint) {
    $certificate=Get-ChildItem -LiteralPath "Cert:\LocalMachine\Root\$($marker.caThumbprint)" -ErrorAction SilentlyContinue
    if ($certificate) { Remove-Item -LiteralPath $certificate.PSPath -Force }
}
foreach ($path in $schoolShortcuts) { Remove-Item -LiteralPath $path -Force }
foreach ($path in $schoolPublicFiles) { Remove-Item -LiteralPath $path -Force }
if (-not $retainGenerations -and (Test-Path -LiteralPath $InstallRoot)) { Assert-NoReparseTree $InstallRoot; Remove-Item -LiteralPath $InstallRoot -Recurse -Force }
$marker.state='removed'; $marker | Add-Member -NotePropertyName removedUtc -NotePropertyValue ([DateTime]::UtcNow.ToString('o')) -Force
Write-JsonAtomic $markerPath $marker
if ($anchorMarkerPath -ine $markerPath) {
    $anchorMarker=Get-Content -LiteralPath $anchorMarkerPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $anchorMarker.state='removed'; $anchorMarker | Add-Member -NotePropertyName removedUtc -NotePropertyValue $marker.removedUtc -Force
    Write-JsonAtomic $anchorMarkerPath $anchorMarker
}
if ($retainGenerations) { Write-Output "ElevhubServers Windows-integration er fjernet. Alle programgenerationer, kontrolkvitteringer, data og nøgler er bevaret som arkiv; fast runtime ligger i $InstallRoot og de sidst valgte data i $DataRoot." }
else { Write-Output "ElevhubServer-programmerne er fjernet. Brugerdata og ejerskabsmarkøren er bevaret i $DataRoot." }
} finally { foreach ($handle in $removalLocks) { $handle.Dispose() } }
