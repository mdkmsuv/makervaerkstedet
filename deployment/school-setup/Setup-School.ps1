[CmdletBinding()]
param(
    [string]$PackageRoot,
    [string]$SchoolName,
    [string]$Role,
    [string]$RoleIp,
    [string]$StudentSubnet,
    [string]$Machine = 'fbb2',
    [string]$Theme,
    [string]$DataRoot = "$env:ProgramData\ElevhubServer",
    [ValidateRange(1,65535)][int]$PrintBackendPort = 8765,
    [ValidateRange(1,65535)][int]$DesignBackendPort = 8766,
    [ValidateRange(1,65535)][int]$TeacherBackendPort = 8767,
    [ValidateRange(1,65535)][int]$TeacherPort = 8780,
    [switch]$AcceptInstall,
    [switch]$CheckOnly,
    [switch]$Resume,
    [switch]$GenerationLayout,
    [switch]$RemoteLaserStation,
    [string]$RecoveryBackup = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $PackageRoot) { $PackageRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $CheckOnly -and -not $isAdmin) {
    $forward = [ordered]@{}
    foreach ($entry in $PSBoundParameters.GetEnumerator()) {
        if ($entry.Key -eq 'CheckOnly') { continue }
        $forward[$entry.Key] = if ($entry.Value -is [Management.Automation.SwitchParameter]) { [bool]$entry.Value } else { [string]$entry.Value }
    }
    $payload = @{script=$PSCommandPath;parameters=$forward} | ConvertTo-Json -Depth 5 -Compress
    $blob = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($payload))
    $command = '$p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(''' + $blob + ''')) | ConvertFrom-Json; $a=@{}; foreach($x in $p.parameters.PSObject.Properties){$a[$x.Name]=$x.Value}; & $p.script @a'
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $elevated = Start-Process powershell.exe -Verb RunAs -ArgumentList @('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand',$encoded) -Wait -PassThru
    exit $elevated.ExitCode
}

function Ask([string]$Label, [string]$Current = '') {
    $suffix = if ($Current) { " [$Current]" } else { '' }
    $value = Read-Host "$Label$suffix"
    if ([string]::IsNullOrWhiteSpace($value)) { return $Current }
    return $value.Trim()
}

function Assert-SchoolDataPath([string]$Path) {
    $expected = [IO.Path]::GetFullPath((Join-Path $env:ProgramData 'ElevhubServer'))
    if ($Path -ine $expected) { throw 'Skoleopsætningen kræver den faste datamappe under ProgramData.' }
    $cursor = $Path
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Reparse point er ikke tilladt: $cursor" }
        }
        $cursor = Split-Path -Parent $cursor
    }
    if (Test-Path -LiteralPath $Path) {
        foreach ($item in @(Get-ChildItem -LiteralPath $Path -Force -Recurse)) {
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Reparse point er ikke tilladt: $($item.FullName)" }
        }
    }
}

function Assert-GenerationCommandCoherence([object]$command, [string]$Root) {
    $expectedExecute = [IO.Path]::GetFullPath((Join-Path $Root 'runtime\python.exe'))
    if ([IO.Path]::GetFullPath([string]$command.Execute) -ine $expectedExecute) { throw 'Generationskommandoet bruger en forkert runtime.' }
    if ([IO.Path]::GetFullPath([string]$command.WorkingDirectory) -ine [IO.Path]::GetFullPath($Root)) { throw 'Generationskommandoet bruger en forkert arbejdsmappe.' }
    $formatter = Get-Command ConvertTo-GenerationCommandLine -ErrorAction SilentlyContinue
    if ($null -eq $formatter) { throw 'Generationsstart mangler sin kommandoformatter.' }
    $expectedArguments = [string](& $formatter.Name -Values @($command.ArgumentList))
    if ([string]$command.Arguments -cne $expectedArguments) { throw 'Generationskommandoets argumenter stemmer ikke med argumentlisten.' }
}
if ($AcceptInstall -and @(@($SchoolName,$Role,$RoleIp,$StudentSubnet,$Theme) | Where-Object { [string]::IsNullOrWhiteSpace($_) }).Count) {
    throw 'AcceptInstall kræver SchoolName, Role, RoleIp, StudentSubnet og Theme som eksplicitte parametre.'
}
$schoolPorts = @($PrintBackendPort,$DesignBackendPort,$TeacherBackendPort,$TeacherPort)
if (@($schoolPorts | Select-Object -Unique).Count -ne $schoolPorts.Count -or @($schoolPorts | Where-Object { $_ -in @(8770,8771,8772,8773,8774,8790) }).Count) {
    throw 'Serverportene skal være forskellige og må ikke bruge skolens gateway-, enrollment- eller laserstationporte.'
}
if (-not $SchoolName) { $SchoolName = Ask "1/5 Skolens navn (denne computer: $env:COMPUTERNAME)" }
if (-not $Role) { $Role = Ask '2/5 Rolle: 1 = Windows VM/server, 2 = stationær makerspace' '2' }
if ($Role -in @('1','vm','VM')) { $Role = 'vm' } elseif ($Role -in @('2','makerspace','MakerSpace')) { $Role = 'makerspace' } else { throw 'Vælg 1 (Windows VM/server) eller 2 (stationær makerspace).' }
. (Join-Path $PSScriptRoot 'school-layout.ps1')
if ($RemoteLaserStation -and $Role -ne 'vm') { throw 'Separat lasercomputer kræver rollen central server/VM.' }
if (-not $RoleIp) { $RoleIp = Ask '3/5 Styreenhedens IPv4-adresse' }
if (-not $StudentSubnet) { $StudentSubnet = Ask '3/5 Elevsubnet (CIDR)' }
if (-not $Theme) { $Theme = Ask '4/5 Tema: 1 = Rød, 2 = Gul, 3 = Grøn' '3' }
if ($Theme -in @('1','red')) { $Theme = 'red' } elseif ($Theme -in @('2','yellow')) { $Theme = 'yellow' } elseif ($Theme -in @('3','green')) { $Theme = 'green' } else { throw 'Vælg 1 (rød), 2 (gul) eller 3 (grøn).' }
if (($Role -eq 'makerspace' -or $RemoteLaserStation) -and -not $PSBoundParameters.ContainsKey('Machine') -and -not $CheckOnly -and -not $AcceptInstall) { $Machine = Ask '4/5 Lasermodel: 1 = Beambox II, 2 = HEXA' '1' }
if ($Machine -in @('1','fbb2')) { $Machine = 'fbb2' } elseif ($Machine -in @('2','fhexa1')) { $Machine = 'fhexa1' } else { throw 'Vælg 1 (Beambox II) eller 2 (HEXA).' }
if ([string]::IsNullOrWhiteSpace($SchoolName) -or $SchoolName.Length -gt 80 -or @($SchoolName.ToCharArray() | Where-Object { [int][char]$_ -lt 32 -or [int][char]$_ -eq 127 }).Count) { throw 'SchoolName skal være 1-80 tegn uden kontroltegn.' }
$ip = $null
if (-not [Net.IPAddress]::TryParse($RoleIp, [ref]$ip) -or $ip.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or [Net.IPAddress]::IsLoopback($ip) -or $ip.Equals([Net.IPAddress]::Any) -or $ip.Equals([Net.IPAddress]::Broadcast) -or $ip.IsIPv6Multicast -or $ip.GetAddressBytes()[0] -ge 224) { throw 'RoleIp skal være en aktiv unicast IPv4-adresse.' }
$parts = $StudentSubnet.Split('/'); $subnetIp = $null; $prefix = 0
if ($parts.Count -ne 2 -or -not [Net.IPAddress]::TryParse($parts[0], [ref]$subnetIp) -or $subnetIp.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or -not [int]::TryParse($parts[1], [ref]$prefix) -or $prefix -lt 8 -or $prefix -gt 32 -or [Net.IPAddress]::IsLoopback($subnetIp)) { throw 'StudentSubnet skal være et IPv4-elevnet med CIDR-maske fra /8 til /32.' }
$firstOctet = $subnetIp.GetAddressBytes()[0]; if ($firstOctet -ge 224 -and $firstOctet -le 239) { throw 'StudentSubnet må ikke være multicast.' }
if ($subnetIp.Equals([Net.IPAddress]::Any) -or $subnetIp.Equals([Net.IPAddress]::Broadcast)) { throw 'StudentSubnet må ikke være uspecificeret eller broadcast.' }
if ($prefix -lt 32) {
    $bytes = $subnetIp.GetAddressBytes(); $network = [byte[]]::new(4)
    for ($n = 0; $n -lt 4; $n++) { $remaining = $prefix - ($n * 8); $mask = if ($remaining -ge 8) { 255 } elseif ($remaining -le 0) { 0 } else { 256 - [math]::Pow(2, 8 - $remaining) }; $network[$n] = [byte]($bytes[$n] -band [int]$mask) }
    if ([Net.IPAddress]::new($network).ToString() -ne $subnetIp.ToString()) { throw 'StudentSubnet skal være den kanoniske netværksadresse, fx 10.20.30.0/24.' }
}
$teacherAddress = Get-SchoolTeacherAddress $Role $RoleIp ([bool]$RemoteLaserStation)
if (-not $CheckOnly) {
    Write-Host "5/5 Opsummering: $SchoolName | rolle=$Role | server=$RoleIp | elevnet=$StudentSubnet | tema=$Theme | laser=$Machine"
    if (-not $AcceptInstall) {
        $go = Ask 'Start installationen nu? (ja/nej)' 'ja'
        if ($go -notmatch '^(?i:ja|j|yes|y)$') { Write-Output 'Installationen blev annulleret før ændringer.'; exit 0 }
    }
}
$PackageRoot = [IO.Path]::GetFullPath($PackageRoot); $DataRoot = [IO.Path]::GetFullPath($DataRoot)
Assert-SchoolDataPath $DataRoot
# Installed administrative wrappers live beside the fixed runtime, while the
# manifest belongs to immutable program sources. Only first-install resume is
# supported here; later generations require the update transaction tooling.
if (-not (Test-Path -LiteralPath (Join-Path $PackageRoot 'elevhub-package.json') -PathType Leaf) -and
    @(Get-ChildItem -LiteralPath $PackageRoot -Force | Where-Object Name -eq 'control').Count) {
    $fixedRoot = [IO.Path]::GetFullPath((Join-Path $env:ProgramFiles 'ElevhubServer'))
    if ($PackageRoot -ine $fixedRoot) { throw 'Skoleopsætningen kræver den faste installationsmappe.' }
    $setupOwner = Get-Content -LiteralPath (Join-Path $DataRoot 'installation-owner.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    . (Join-Path $fixedRoot 'server\generation-startup.ps1')
    if ((Get-InstallationStartupMode $setupOwner $fixedRoot) -ne 'generation-v1') { throw 'Den installerede opstartsprotokol er ugyldig.' }
    # Resolve the immutable program location only.  The shared lease below is
    # acquired before reading the selected record or changing school data.
    $PackageRoot = Join-Path $fixedRoot 'program'
}
$manifest = Join-Path $PackageRoot 'elevhub-package.json'; $python = Join-Path $PackageRoot 'runtime\python.exe'; $serverInstall = Join-Path $PackageRoot 'server\install.ps1'; $enrollment = Join-Path $PackageRoot 'deployment\school-setup\enrollment.py'
if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) { throw 'Pakken mangler elevhub-package.json.' }
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Pakken mangler runtime\python.exe.' }
if (-not (Test-Path -LiteralPath $serverInstall -PathType Leaf)) { throw 'Pakken mangler server\install.ps1.' }
if (-not (Test-Path -LiteralPath $enrollment -PathType Leaf)) { throw 'Pakken mangler deployment\school-setup\enrollment.py.' }
$verifier = Join-Path $PackageRoot 'server\verify.py'
if (-not (Test-Path -LiteralPath $verifier -PathType Leaf)) { throw 'Pakken mangler server\verify.py.' }
# Verify every packaged file before installation, including the read-only check.
Push-Location -LiteralPath $PackageRoot
try {
& $python -B $verifier --package-root $PackageRoot --role-ip $RoleIp
if ($LASTEXITCODE -ne 0) { throw 'Pakkens integritetskontrol fejlede. Pak ud igen fra den verificerede udgivelse.' }
& $python -B -c 'import app.server, app.laser_server, app.laser_station, app.boxes_generator; from boxes.generators.abox import ABox'
if ($LASTEXITCODE -ne 0) { throw 'Pakkens runtimeafhængigheder kunne ikke indlæses.' }
if ($GenerationLayout) {
    $generationInitializer = Join-Path $PackageRoot 'server\generation_install.py'
    if (-not (Test-Path -LiteralPath $generationInitializer -PathType Leaf)) { throw 'Pakken mangler den fælles generationsinstallation.' }
    & $python -B $generationInitializer --package-root $PackageRoot --check-package
    if ($LASTEXITCODE -ne 0) { throw 'Pakken bestod ikke generationskontrollen.' }
}
if ($Role -eq 'makerspace') { & $python -B -c 'import app.laser_station, app.laser_machines' ; if ($LASTEXITCODE -ne 0) { throw 'Laserstationens runtimeafhængigheder mangler i pakken.' } }
} finally { Pop-Location }
$firewallGuards = Join-Path $PackageRoot 'server\school-uninstall-guards.ps1'
if (-not (Test-Path -LiteralPath $firewallGuards -PathType Leaf)) { throw 'Pakken mangler den fælles firewall-adressekontrol.' }
. $firewallGuards
if ($RecoveryBackup) {
    if (-not $GenerationLayout) { throw 'Værtsgendannelse kræver generationslayout.' }
    $recoveryRuntime = [IO.Path]::GetFullPath((Join-Path $env:ProgramFiles 'ElevhubServer'))
    $recoveryPlanRaw = & $python -B -m app.school_recovery plan --source $RecoveryBackup --package-root $PackageRoot --runtime-root $recoveryRuntime --data-root $DataRoot
    if ($LASTEXITCODE -ne 0) { throw 'Backupen kunne ikke klargøres til en erstatningsvært.' }
    $recoveryPlan = $recoveryPlanRaw | ConvertFrom-Json
    Assert-SchoolLaserProfile $recoveryPlan.school_profile ([bool]$RemoteLaserStation) $Machine
    foreach ($pair in @(@{key='school_name';value=$SchoolName},@{key='role';value=$Role},@{key='theme';value=$Theme},@{key='server';value=$RoleIp},@{key='student_subnet';value=$StudentSubnet})) {
        if ([string]$recoveryPlan.school_profile.($pair.key) -cne [string]$pair.value) { throw 'Værtsgendannelse kræver backupens oprindelige skoleprofil og LAN.' }
    }
    if ([string]$recoveryPlan.school.teacherAddress -cne $teacherAddress) { throw 'Værtsgendannelse kræver backupens oprindelige læreradresse.' }
    foreach ($pair in @(@{key='printBackendPort';value=$PrintBackendPort},@{key='designBackendPort';value=$DesignBackendPort},@{key='teacherBackendPort';value=$TeacherBackendPort},@{key='teacherPort';value=$TeacherPort})) {
        if ([int]$recoveryPlan.school.($pair.key) -ne $pair.value) { throw 'Værtsgendannelse kræver backupens oprindelige serverporte.' }
    }
}
if ($CheckOnly) {
    Write-Output (@{checkOnly=$true; package=$PackageRoot; school_name=$SchoolName; role=$Role; server=$RoleIp; student_subnet=$StudentSubnet; theme=$Theme; enrollment_port=8774; print_backend_port=$PrintBackendPort; design_backend_port=$DesignBackendPort; teacher_backend_port=$TeacherBackendPort; teacher_port=$TeacherPort; teacher_address=$teacherAddress; remote_laser_station=[bool]$RemoteLaserStation} | ConvertTo-Json -Compress)
    exit 0
}
if (-not $isAdmin) { throw 'Kør opsætningen fra en administratorkonsol.' }
$null = Get-Command ssh-keygen.exe -ErrorAction Stop
if ($RecoveryBackup) {
    & $python -B -m app.school_recovery prepare --source $RecoveryBackup --package-root $PackageRoot --runtime-root $recoveryRuntime --data-root $DataRoot
    if ($LASTEXITCODE -ne 0) { throw 'Den isolerede værtsgendannelse fejlede; kopier og startspærrer bevares.' }
    $Resume = [Management.Automation.SwitchParameter]::new($true)
}
$ownerMarker = Join-Path $DataRoot 'installation-owner.json'; $resumeExisting = $Resume.IsPresent
$installedRoot = [IO.Path]::GetFullPath((Join-Path $env:ProgramFiles 'ElevhubServer'))
$existingProfile = $null
$resumeNeedsServer = $false
if ($resumeExisting) {
    if (-not (Test-Path -LiteralPath $ownerMarker -PathType Leaf)) { throw 'Resume kræver en eksisterende, ejet ElevhubServer-installation.' }
    $owner = Get-Content -LiteralPath $ownerMarker -Raw -Encoding UTF8 | ConvertFrom-Json
    $ownerGenerationLayout = ($owner.PSObject.Properties.Name -contains 'startupProtocol' -and $owner.startupProtocol -eq 'generation-v1')
    if ($owner.PSObject.Properties.Name -contains 'generationLayout') { $ownerGenerationLayout = [bool]$owner.generationLayout }
    if ([string]::IsNullOrWhiteSpace([string]$owner.ownerId) -or $ownerGenerationLayout -ne [bool]$GenerationLayout -or
        $owner.packageManifestSha256 -ne (Get-FileHash -LiteralPath (Join-Path $PackageRoot 'integrity.json') -Algorithm SHA256).Hash.ToLowerInvariant()) {
        throw 'Resume matcher ikke installationens ejerskab, layout og verificerede pakke.'
    }
    foreach ($port in @(@{name='printBackendPort';expected=$PrintBackendPort}, @{name='designBackendPort';expected=$DesignBackendPort}, @{name='teacherBackendPort';expected=$TeacherBackendPort}, @{name='teacherPort';expected=$TeacherPort})) {
        if ($owner.PSObject.Properties.Name -contains $port.name -and [int]$owner.($port.name) -ne $port.expected) {
            throw 'Resume matcher ikke skoleopsætningens serverporte.'
        }
    }
    if ($owner.PSObject.Properties.Name -contains 'externalBackends' -and @($owner.externalBackends).Count -gt 0) {
        throw 'Skoleopsætningen understøtter ikke genoptagelse med eksterne backends.'
    }
    if ($owner.schemaVersion -ne 1 -or $owner.product -ne 'ElevhubServer' -or $owner.state -ne 'installed' -or
        $owner.installRoot -ne $installedRoot -or $owner.dataRoot -ne $DataRoot -or
        $owner.roleIp -ne $RoleIp -or $owner.studentSubnet -ne $StudentSubnet -or
        $owner.teacherAddress -ne $teacherAddress -or $owner.taskName -ne 'ElevhubServer' -or
        -not (Test-Path -LiteralPath $installedRoot -PathType Container)) {
        if ($owner.schemaVersion -ne 1 -or $owner.product -ne 'ElevhubServer' -or $owner.state -notin @('rolledBack','installing','rollbackFailed','recoveryPrepared') -or
            $owner.installRoot -ne $installedRoot -or $owner.dataRoot -ne $DataRoot -or
            [string]::IsNullOrWhiteSpace([string]$owner.ownerId) -or $owner.roleIp -ne $RoleIp -or
            $owner.studentSubnet -ne $StudentSubnet -or $owner.teacherAddress -ne $teacherAddress -or
            $owner.taskName -ne 'ElevhubServer' -or ($owner.state -eq 'rolledBack' -and (Test-Path -LiteralPath $installedRoot))) {
            throw 'Resume matcher ikke den ejede serverinstallation og dens netværk.'
        }
        $resumeNeedsServer = $true
    }
    $existingProfilePath = Join-Path $DataRoot 'school.json'
    if (Test-Path -LiteralPath $existingProfilePath -PathType Leaf) {
        $existingProfile = Get-Content -LiteralPath $existingProfilePath -Raw -Encoding UTF8 | ConvertFrom-Json
        Assert-SchoolLaserProfile $existingProfile ([bool]$RemoteLaserStation) $Machine
        if ($existingProfile.schemaVersion -ne 1 -or $existingProfile.school_name -cne $SchoolName -or
            $existingProfile.theme -cne $Theme -or $existingProfile.role -cne $Role -or
            $existingProfile.server -cne $RoleIp -or $existingProfile.student_subnet -cne $StudentSubnet) {
            throw 'Resume må ikke ændre skolens navn, rolle, tema eller netværk. Brug den oprindelige opsætning.'
        }
        foreach ($port in @(@{name='print_backend_port';expected=$PrintBackendPort;default=8765}, @{name='design_backend_port';expected=$DesignBackendPort;default=8766}, @{name='teacher_backend_port';expected=$TeacherBackendPort;default=8767}, @{name='teacher_port';expected=$TeacherPort;default=8780})) {
            $recordedPort = if ($existingProfile.PSObject.Properties.Name -contains $port.name) { [int]$existingProfile.($port.name) } else { $port.default }
            if ($recordedPort -ne $port.expected) { throw 'Resume må ikke ændre skolens serverporte.' }
        }
    }
}
if (-not $resumeExisting) {
    if (Test-Path -LiteralPath $ownerMarker) { throw 'Der findes allerede en Elevhub-installation. Brug -Resume efter en afbrudt opsætning.' }
    $installArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$serverInstall,
        '-PackageRoot',$PackageRoot,'-RoleIp',$RoleIp,'-StudentSubnet',$StudentSubnet,
        '-TeacherAddress',$teacherAddress,'-DataRoot',$DataRoot,
        '-PrintBackendPort',$PrintBackendPort,'-DesignBackendPort',$DesignBackendPort,'-TeacherBackendPort',$TeacherBackendPort,'-TeacherPort',$TeacherPort)
    if ($GenerationLayout) { $installArgs += '-GenerationLayout' }
    if ($RecoveryBackup) { $installArgs += @('-RecoveryBackup',$RecoveryBackup) }
    & powershell.exe @installArgs
    if ($LASTEXITCODE -ne 0) { throw 'Elevhub-serverinstallationen fejlede.' }
} elseif ($resumeNeedsServer) {
    $installArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$serverInstall,
        '-PackageRoot',$PackageRoot,'-RoleIp',$RoleIp,'-StudentSubnet',$StudentSubnet,
        '-TeacherAddress',$teacherAddress,'-DataRoot',$DataRoot,'-Resume',
        '-PrintBackendPort',$PrintBackendPort,'-DesignBackendPort',$DesignBackendPort,'-TeacherBackendPort',$TeacherBackendPort,'-TeacherPort',$TeacherPort)
    if ($GenerationLayout) { $installArgs += '-GenerationLayout' }
    if ($RecoveryBackup) { $installArgs += @('-RecoveryBackup',$RecoveryBackup) }
    & powershell.exe @installArgs
    if ($LASTEXITCODE -ne 0) { throw 'Genoptagelse af Elevhub-serverinstallationen fejlede.' }
}
$owner = Get-Content -LiteralPath $ownerMarker -Raw -Encoding UTF8 | ConvertFrom-Json
$startupHelper = Join-Path $installedRoot 'server\generation-startup.ps1'
$startupMode = 'legacy'
$generationId = $null
if (Test-Path -LiteralPath $startupHelper -PathType Leaf) {
    . $startupHelper
    $startupMode = Get-InstallationStartupMode -Owner $owner -InstallRoot $installedRoot
} elseif ($owner.PSObject.Properties.Name -contains 'startupProtocol' -or
    @(Get-ChildItem -LiteralPath $installedRoot -Force | Where-Object Name -eq 'control').Count) {
    throw 'Installationen mangler sin faste startup-helper.'
}
if ($GenerationLayout -and $startupMode -ne 'generation-v1') {
    throw 'GenerationLayout kræver en eksisterende generation-v1 installation.'
}
$generationLease = $null
$generationSnapshot = $null
try {
if ($startupMode -eq 'generation-v1') {
    $generationId = Get-GenerationInstallationId -Owner $owner
    # Keep one shared selection lease through the inspect and every mutation
    # below.  The snapshot is the sole source of the selected pair.
    $generationLease = New-GenerationLease -InstallRoot $installedRoot -InstallationId $generationId
    $generationSnapshot = $generationLease.Snapshot
    if ($generationSnapshot.revision -ne 1 -or [IO.Path]::GetFullPath([string]$generationSnapshot.data_root) -ine $DataRoot -or
        [IO.Path]::GetFullPath([string]$generationSnapshot.runtime_root) -ine $installedRoot -or
        [IO.Path]::GetFullPath([string]$generationSnapshot.program_root) -ine (Join-Path $installedRoot 'program') -or
        [string]$generationSnapshot.installation_id -ine $generationId -or
        [string]::IsNullOrWhiteSpace([string]$generationSnapshot.selection_sha256)) {
        throw 'Den aktive generationsmarkør matcher ikke den valgte skoledata.'
    }
}
$installedPython = Join-Path $installedRoot 'runtime\python.exe'; $installedEnrollment = Join-Path $installedRoot 'deployment\school-setup\enrollment.py'
$security = Join-Path $DataRoot 'security'; $sshRoot = Join-Path $DataRoot 'admin-ssh'; New-Item -ItemType Directory -Force -Path $DataRoot,$security,$sshRoot | Out-Null
$privateKey = Join-Path $sshRoot 'id_ed25519'; $publicKey = "$privateKey.pub"
& icacls.exe $sshRoot /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'SSH-mappen kunne ikke beskyttes før nøgleoprettelse.' }
$hasPrivate = Test-Path -LiteralPath $privateKey -PathType Leaf; $hasPublic = Test-Path -LiteralPath $publicKey -PathType Leaf
if ($hasPrivate -ne $hasPublic -or ($resumeExisting -and $null -ne $existingProfile -and -not $hasPrivate)) { throw 'Administrationsnøglen er ufuldstændig. Resume må ikke erstatte en eksisterende nøgle.' }
if (-not $hasPrivate) {
    $psi = [Diagnostics.ProcessStartInfo]::new(); $psi.FileName = (Get-Command ssh-keygen.exe -ErrorAction Stop).Source; $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $psi.Arguments = '-q -t ed25519 -N "" -C elevhub-school-admin -f "' + $privateKey + '"'
    $proc = [Diagnostics.Process]::Start($psi); $proc.WaitForExit(); if ($proc.ExitCode -ne 0) { throw 'ed25519-nøglen kunne ikke oprettes.' }
}
& icacls.exe $privateKey /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Den private SSH-nøgle kunne ikke beskyttes.' }
$public = (Get-Content -LiteralPath $publicKey -Raw).Trim(); if ($public -notmatch '^ssh-ed25519\s+\S+(\s+.*)?$') { throw 'Den oprettede public key er ugyldig.' }
$derived = [string](& ssh-keygen.exe -y -f $privateKey 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $derived -or (($derived -split '\s+')[0..1] -join ' ') -cne (($public -split '\s+')[0..1] -join ' ')) { throw 'Den private og offentlige administrationsnøgle passer ikke sammen.' }
$profile = [ordered]@{schemaVersion=1;school_name=$SchoolName;theme=$Theme;role=$Role;server=$RoleIp;student_subnet=$StudentSubnet;admin_public_key=$public;print_backend_port=$PrintBackendPort;design_backend_port=$DesignBackendPort;teacher_backend_port=$TeacherBackendPort;teacher_port=$TeacherPort}
if ($RemoteLaserStation) {
    $profile['remote_laser_station'] = $true
    $profile['laser_machine'] = $Machine
    $profile['laser_station_id'] = 'laser-' + $Machine
}
$profilePath = Join-Path $DataRoot 'school.json'
if (Test-Path -LiteralPath $profilePath -PathType Leaf) {
    if ($existingProfile.admin_public_key -cne $public) { throw 'Resume må ikke ændre skolens administrationsnøgle.' }
} else {
    $tmp = "$profilePath.tmp"; [IO.File]::WriteAllText($tmp, ($profile | ConvertTo-Json -Depth 5) + "`n", [Text.UTF8Encoding]::new($false)); Move-Item -LiteralPath $tmp -Destination $profilePath
}
& icacls.exe $profilePath /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' '*S-1-5-19:R' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Skoleprofilen kunne ikke beskyttes.' }
$taskName = 'ElevhubSchoolEnrollment'
if ($startupMode -eq 'generation-v1') {
    # The scheduled task must resolve the current generation on each launch.
    # The lease still protects setup itself; only one-shot commands are
    # pinned to its selection hash.
    $enrollmentCommand = Get-GenerationCommand -InstallRoot $installedRoot -InstallationId $generationId -Entry 'enrollment' -Arguments @('serve')
    $enrollmentExecute = [string]$enrollmentCommand.Execute
    $enrollmentArgumentList = @($enrollmentCommand.ArgumentList)
    $enrollmentArgs = [string]$enrollmentCommand.Arguments
    $enrollmentWorkingDirectory = [string]$enrollmentCommand.WorkingDirectory
    Assert-GenerationCommandCoherence $enrollmentCommand $installedRoot
} else {
    $enrollmentExecute = $installedPython
    $enrollmentArgumentList = @('-B', $installedEnrollment, 'serve', '--data-root', $DataRoot)
    $enrollmentArgs = '-B "{0}" serve --data-root "{1}"' -f $installedEnrollment,$DataRoot
    $enrollmentWorkingDirectory = $installedRoot
}
$action = New-ScheduledTaskAction -Execute $enrollmentExecute -Argument $enrollmentArgs -WorkingDirectory $enrollmentWorkingDirectory
$principal = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    if ($existingTask.Description -ne 'Elevhub skole-enrollment' -or @($existingTask.Actions).Count -ne 1 -or
        $existingTask.Actions[0].Execute -ne $enrollmentExecute -or $existingTask.Actions[0].WorkingDirectory -ne $enrollmentWorkingDirectory -or
        $existingTask.Actions[0].Arguments -ne $enrollmentArgs -or
        $existingTask.Principal.UserId -notin @('S-1-5-18','SYSTEM','NT AUTHORITY\SYSTEM')) { throw 'Enrollment-opgaven er ændret eksternt; Resume afvises.' }
} else { Register-ScheduledTask -TaskName $taskName -Action $action -Trigger (New-ScheduledTaskTrigger -AtStartup) -Principal $principal -Settings $settings -Description 'Elevhub skole-enrollment' | Out-Null }
$existingRule = Get-NetFirewallRule -Name 'ElevhubSchoolEnrollment-8774' -ErrorAction SilentlyContinue
if ($existingRule) {
    $ports = $existingRule | Get-NetFirewallPortFilter; $addresses = $existingRule | Get-NetFirewallAddressFilter; $program = $existingRule | Get-NetFirewallApplicationFilter
    $remoteAddress = if (@($addresses.RemoteAddress).Count -eq 1) { [string]@($addresses.RemoteAddress)[0] } else { '' }
    if ($existingRule.Description -ne 'ElevhubSchoolEnrollment' -or $existingRule.Direction -ne 'Inbound' -or
        $existingRule.Action -ne 'Allow' -or $existingRule.Enabled -ne 'True' -or $existingRule.Profile -ne 'Any' -or
        $ports.Protocol -notin @('TCP',6) -or [string]$ports.LocalPort -ne '8774' -or
        @($addresses.LocalAddress).Count -ne 1 -or @($addresses.LocalAddress)[0] -ne $RoleIp -or
        @($addresses.RemoteAddress).Count -ne 1 -or -not (Test-FirewallAddressEquivalent -Actual $remoteAddress -Expected $StudentSubnet) -or
        $program.Program -ne $installedPython) { throw 'Enrollment-firewallreglen er ændret eksternt; Resume afvises.' }
} else { New-NetFirewallRule -Name 'ElevhubSchoolEnrollment-8774' -DisplayName 'Elevhub school enrollment TCP 8774' -Direction Inbound -Action Allow -Enabled True -Profile Any -Protocol TCP -LocalAddress $RoleIp -LocalPort 8774 -RemoteAddress $StudentSubnet -Program $installedPython -Description 'ElevhubSchoolEnrollment' | Out-Null }
Start-ScheduledTask -TaskName $taskName
if ($Role -eq 'makerspace') {
    $laserRoot = Join-Path $DataRoot 'laserstation'; $plates = Join-Path $DataRoot 'Laserplader'; New-Item -ItemType Directory -Force -Path $laserRoot,$plates | Out-Null
    $laserConfig = [ordered]@{role='Station';server=('http://127.0.0.1:' + $TeacherBackendPort);plates=$plates;port=8790;machine=$Machine}
    & icacls.exe $laserRoot /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-19:(OI)(CI)M' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Laserstationens konfigurationsmappe kunne ikke beskyttes.' }
    & icacls.exe $plates /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-19:(OI)(CI)M' '*S-1-5-32-545:(OI)(CI)M' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Laserpladernes mappe kunne ikke åbnes for lokale brugere.' }
    $laserConfigPath = Join-Path $laserRoot 'laser-installation.json'
    if (Test-Path -LiteralPath $laserConfigPath -PathType Leaf) {
        $oldLaser = Get-Content -LiteralPath $laserConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($oldLaser.role -ne 'Station' -or $oldLaser.server -ne $laserConfig.server -or
            $oldLaser.plates -ne $plates -or $oldLaser.port -ne 8790 -or $oldLaser.machine -ne $Machine) {
            throw 'Resume må ikke ændre den eksisterende laserstation. Vælg samme lasermodel.'
        }
    } else {
        [IO.File]::WriteAllText($laserConfigPath, ($laserConfig | ConvertTo-Json -Depth 5) + "`n", [Text.UTF8Encoding]::new($false))
    }
    & icacls.exe $laserConfigPath /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' '*S-1-5-19:M' '*S-1-5-32-545:R' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Laserstationens konfiguration kunne ikke låses for lokale brugere.' }
    $laserScript = Join-Path $installedRoot 'deployment\school-setup\Start-LaserStation.ps1'
    if ($startupMode -eq 'generation-v1') {
        # Resolve the current generation at each launch; the running process
        # itself remains pinned to the lease selection hash.
        $laserArgs = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Root "{1}" -ActiveGeneration -InstallationId "{2}"' -f $laserScript,$installedRoot,$generationId
    } else {
        $laserArgs = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -ConfigPath "{1}" -Root "{2}"' -f $laserScript,$laserConfigPath,$installedRoot
    }
    $startup = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\Startup'; New-Item -ItemType Directory -Force -Path $startup | Out-Null
    $shell = New-Object -ComObject WScript.Shell; $shortcut = $shell.CreateShortcut((Join-Path $startup 'Elevhub Laserstation.lnk')); $shortcut.TargetPath = 'powershell.exe'; $shortcut.Arguments = $laserArgs; $shortcut.WorkingDirectory = $installedRoot; $shortcut.Save()
    $browserShortcut = $shell.CreateShortcut((Join-Path $env:Public 'Desktop\Elevhub Laserstation.lnk'))
    $browserShortcut.TargetPath = 'powershell.exe'; $browserShortcut.Arguments = $laserArgs + ' -OpenBrowser'; $browserShortcut.WorkingDirectory = $installedRoot; $browserShortcut.Save()
}
$publicDir = Join-Path $env:Public 'Documents\Elevvaerkstedet'; New-Item -ItemType Directory -Force -Path $publicDir | Out-Null
$connection = Join-Path $publicDir 'school-connection.json'; if (Test-Path -LiteralPath $connection) { $connection = Join-Path $publicDir ('school-connection-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json') }
if ($startupMode -eq 'generation-v1') {
    $connectionCommand = Get-GenerationCommand -InstallRoot $installedRoot -InstallationId $generationId -Entry 'enrollment' -ExpectedSelection $generationSnapshot.selection_sha256 -Arguments @('connection','--output',$connection)
    Assert-GenerationCommandCoherence $connectionCommand $installedRoot
    & $connectionCommand.Execute @($connectionCommand.ArgumentList)
} else {
    & $installedPython -B $installedEnrollment connection --data-root $DataRoot --output $connection
}
if ($LASTEXITCODE -ne 0) { throw 'school-connection.json kunne ikke eksporteres.' }
if ($RemoteLaserStation) {
    $laserConnection = Join-Path $publicDir 'laser-station-connection.json'
    if (Test-Path -LiteralPath $laserConnection) { $laserConnection = Join-Path $publicDir ('laser-station-connection-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.json') }
    $laserConnectionValue = New-LaserStationConnection $SchoolName $RoleIp $TeacherPort (Get-Content -LiteralPath (Join-Path $security 'ca.crt') -Raw -Encoding ASCII) $Machine
    [IO.File]::WriteAllText($laserConnection, ($laserConnectionValue | ConvertTo-Json -Depth 4) + "`n", [Text.UTF8Encoding]::new($false))
    Write-Output "Kopiér den offentlige laserforbindelse til lasercomputeren: $laserConnection"
}
foreach ($item in @(
    @{Name='Elevhub skoleopsætning.lnk';Target=(Join-Path $installedRoot 'deployment\school-setup\Start.cmd')},
    @{Name='Elevhub skoleadmin.lnk';Target=(Join-Path $installedRoot 'deployment\school-setup\Manage-School.cmd')},
    @{Name='Makerværkstedet.lnk';Target=(Join-Path $installedRoot 'deployment\school-setup\Open-ControlPanel.cmd');Arguments=('-Port {0} -Address {1}' -f $TeacherPort,$teacherAddress)}
)) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path $env:Public ('Desktop\' + $item.Name)))
    $shortcut.TargetPath = $item.Target
    $shortcut.WorkingDirectory = $installedRoot
    if ($item.ContainsKey('Arguments')) { $shortcut.Arguments = [string]$item.Arguments } else { $shortcut.Arguments = '' }
    $shortcut.Save()
}
Write-Output "Skoleopsætning er klar. Profil: $profilePath. Privat nøgle: $privateKey. Forbindelse: $connection"
} finally {
    if ($null -ne $generationLease) { Close-GenerationLease -Lease $generationLease }
}
if (-not $AcceptInstall) { Read-Host 'Tryk Enter for at lukke opsætningen' | Out-Null }
