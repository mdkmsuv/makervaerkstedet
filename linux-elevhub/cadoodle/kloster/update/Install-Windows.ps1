param(
    [string]$Package = (Join-Path $PSScriptRoot 'windows'),
    [string]$InstallRoot = 'C:\Program Files\ElevhubClient\native-apps\cadoodle',
    [string]$StateDir = '',
    [switch]$DryRun,
    [switch]$Rollback
)

$ErrorActionPreference = 'Stop'
if (-not $StateDir) { $StateDir = Join-Path $env:ProgramData 'ElevhubClient\cadoodle-update' }
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$StateDir = [IO.Path]::GetFullPath($StateDir)
$jar = Join-Path $InstallRoot 'app\CaDoodle-Kloster.jar'
$cfg = Join-Path $InstallRoot 'app\CaDoodle.cfg'
$releaseFile = Join-Path $InstallRoot 'runtime\release'
$payload = Join-Path $Package 'CaDoodle-Kloster.jar'
$entry = 'com/commonwealthrobotics/Main.class'

function Fail([string]$Message) { throw $Message }
function Hash([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hasher.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $hasher.Dispose() }
}
function CurrentHash([string]$Path) {
    if (Test-Path -LiteralPath $Path -PathType Leaf) { return (Hash $Path) }
    return $null
}
function EntryHash([string]$Path) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($Path)
    try {
        $member = $archive.GetEntry($entry)
        if ($null -eq $member) { Fail "JAR mangler $entry." }
        $stream = $member.Open()
        $hasher = [Security.Cryptography.SHA256]::Create()
        try { return ([BitConverter]::ToString($hasher.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
        finally { $stream.Dispose(); $hasher.Dispose() }
    }
    finally { $archive.Dispose() }
}
function WriteJsonAtomic($Value, [string]$Path) {
    $stage = Join-Path (Split-Path $Path) ('.' + [IO.Path]::GetFileName($Path) + '.' + [guid]::NewGuid().ToString('N') + '.pending')
    $replaced = $stage + '.replaced'
    try {
        $Value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $stage -Encoding UTF8
        if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($stage, $Path, $replaced) }
        else { [IO.File]::Move($stage, $Path) }
    }
    finally { Remove-Item -LiteralPath $stage,$replaced -Force -ErrorAction SilentlyContinue }
}
function CopyAtomic([string]$Source, [string]$Destination) {
    $parent = Split-Path $Destination
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $stage = Join-Path $parent ('.' + [IO.Path]::GetFileName($Destination) + '.' + [guid]::NewGuid().ToString('N') + '.pending')
    $replaced = $stage + '.replaced'
    try {
        Copy-Item -LiteralPath $Source -Destination $stage
        if ((Hash $stage) -ne (Hash $Source)) { Fail "Staged checksum mismatch: $Destination" }
        if (Test-Path -LiteralPath $Destination) { [IO.File]::Replace($stage, $Destination, $replaced) }
        else { [IO.File]::Move($stage, $Destination) }
    }
    finally { Remove-Item -LiteralPath $stage,$replaced -Force -ErrorAction SilentlyContinue }
}
function AssertStopped {
    $processes = @(Get-CimInstance Win32_Process -Filter "Name='CaDoodle.exe' OR Name='java.exe' OR Name='javaw.exe'")
    $prefix = $InstallRoot.TrimEnd('\') + '\'
    foreach ($process in $processes) {
        $underRoot = $process.ExecutablePath -and $process.ExecutablePath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
        $jarCommand = $process.CommandLine -and ($process.CommandLine.IndexOf($jar, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or $process.CommandLine -match 'CaDoodle-(Kloster|Application)\.jar')
        if ($underRoot -or $jarCommand -or ($process.Name -ieq 'CaDoodle.exe' -and -not $process.ExecutablePath -and -not $process.CommandLine)) {
            Fail 'CaDoodle kører; opdateringen udsættes.'
        }
    }
}

if (-not (Test-Path -LiteralPath $jar -PathType Leaf)) { Fail "Mangler installeret CaDoodle-JAR: $jar" }
if (-not (Test-Path -LiteralPath $cfg -PathType Leaf)) { Fail 'Mangler CaDoodle.cfg.' }
$configuration = Get-Content -LiteralPath $cfg -Raw
if ($configuration -notmatch '(?im)^\s*app\.mainclass\s*=\s*com\.commonwealthrobotics\.Main\s*$') { Fail 'CaDoodle.cfg har forkert mainclass.' }
if ($configuration -notmatch '(?im)^\s*app\.classpath\s*=.*CaDoodle-Kloster\.jar\s*$') { Fail 'CaDoodle.cfg peger ikke på den opdaterede JAR.' }
if (-not (Test-Path -LiteralPath $releaseFile -PathType Leaf)) { Fail 'Mangler bundled runtime/release.' }
$release = Get-Content -LiteralPath $releaseFile -Raw
if ($release -notmatch '\bjava\.logging\b' -or $release -notmatch '\bjdk\.zipfs\b') { Fail 'Bundled runtime mangler java.logging eller jdk.zipfs.' }
$installedEntry = EntryHash $jar
AssertStopped

if ($Rollback) {
    $activePath = Join-Path $StateDir 'active.json'
    if (-not (Test-Path -LiteralPath $activePath)) {
        $markerPath = Join-Path $StateDir 'last-rollback.json'
        if (Test-Path -LiteralPath $markerPath) {
            $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
            if ($marker.target -eq $jar -and $marker.original.target -eq (Hash $jar)) {
                Write-Output 'Allerede rullet tilbage.'
                exit 0
            }
        }
        Fail 'Ingen aktiv opdateringskvittering fundet.'
    }
    $reference = (Get-Content -LiteralPath $activePath -Raw | ConvertFrom-Json).transaction
    if ($reference -notmatch '^transaction-[0-9a-f]{32}$') { Fail 'Ugyldig transaktionsreference.' }
    $transaction = Join-Path $StateDir $reference
    $receipt = Get-Content -LiteralPath (Join-Path $transaction 'receipt.json') -Raw | ConvertFrom-Json
    if ($receipt.schema -ne 1 -or $receipt.target -ne $jar -or $receipt.installed.target -ne (Hash $jar)) { Fail 'Aktuel JAR matcher ikke kvitteringen.' }
    $backup = Join-Path $transaction 'target.jar'
    if ((Hash $backup) -ne $receipt.original.target) { Fail 'Backup er ændret.' }
    if ($DryRun) { Write-Output "Dry-run: rollback $backup -> $jar"; exit 0 }
    $snapshot = Join-Path $StateDir ('.rollback-' + [guid]::NewGuid().ToString('N') + '.jar')
    $markerPath = Join-Path $StateDir 'last-rollback.json'
    Copy-Item -LiteralPath $jar -Destination $snapshot
    if ((Hash $snapshot) -ne $receipt.installed.target) { Fail 'Den aktuelle JAR kunne ikke sikres før rollback.' }
    try {
        CopyAtomic $backup $jar
        if ((Hash $jar) -ne $receipt.original.target) { Fail 'Rollback kunne ikke verificeres.' }
        WriteJsonAtomic @{ target = $jar; original = @{ target = $receipt.original.target } } $markerPath
        Remove-Item -LiteralPath $activePath
    }
    catch {
        $rollbackError = $_
        if (Test-Path -LiteralPath $markerPath -PathType Leaf) { Remove-Item -LiteralPath $markerPath -Force }
        try { CopyAtomic $snapshot $jar }
        catch { Fail "Rollback fejlede, og den opdaterede JAR kunne ikke gendannes; snapshot: $snapshot; fejl: $_" }
        throw $rollbackError
    }
    finally { if ((Hash $jar) -eq $receipt.installed.target -or -not (Test-Path -LiteralPath $activePath)) { Remove-Item -LiteralPath $snapshot -Force -ErrorAction SilentlyContinue } }
    Write-Output "CaDoodle er rullet tilbage med $transaction."
    exit 0
}

$manifestPath = Join-Path $Package 'manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf) -or -not (Test-Path -LiteralPath $payload -PathType Leaf)) { Fail 'Pakken mangler manifest eller JAR.' }
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.schema -ne 1 -or $manifest.product -ne 'CaDoodle' -or $manifest.platform -ne 'windows' -or $manifest.app_version -ne '0.46.094' -or $manifest.payload -ne 'CaDoodle-Kloster.jar') { Fail 'Uforenelig opdateringspakke.' }
if ((Get-Item -LiteralPath $payload).Length -ne $manifest.size -or (Hash $payload) -ne $manifest.sha256) { Fail 'Payloadens størrelse eller checksum matcher ikke manifestet.' }
if ($manifest.compatibility.required_entry -ne $entry -or (EntryHash $payload) -ne $manifest.compatibility.entry_sha256 -or $installedEntry -ne $manifest.compatibility.entry_sha256) { Fail 'CaDoodle-entry er ikke kompatibel med installationen.' }
if ((Hash $jar) -eq $manifest.sha256) { Write-Output 'Allerede opdateret.'; exit 0 }
if ($DryRun) { Write-Output "Dry-run: $payload -> $jar"; exit 0 }

New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
$transaction = Join-Path $StateDir ('transaction-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $transaction | Out-Null
$backup = Join-Path $transaction 'target.jar'
Copy-Item -LiteralPath $jar -Destination $backup
$previousHash = Hash $jar
if ((Hash $backup) -ne $previousHash) { Fail 'Backup kunne ikke verificeres.' }
$receipt = @{ schema = 1; target = $jar; original = @{ target = $previousHash }; installed = @{ target = $manifest.sha256 } }
try {
    CopyAtomic $payload $jar
    if ((Hash $jar) -ne $manifest.sha256) { Fail 'Installeret JAR kunne ikke verificeres.' }
    WriteJsonAtomic $receipt (Join-Path $transaction 'receipt.json')
    WriteJsonAtomic @{ transaction = (Split-Path $transaction -Leaf) } (Join-Path $StateDir 'active.json')
    Remove-Item -LiteralPath (Join-Path $StateDir 'last-rollback.json') -Force -ErrorAction SilentlyContinue
}
catch {
    $installationError = $_
    try { CopyAtomic $backup $jar }
    catch { Fail "Installationen fejlede, og gendannelse fejlede; backup: $transaction; fejl: $_" }
    throw $installationError
}
Write-Output "CaDoodle er opdateret. Backup: $transaction"
