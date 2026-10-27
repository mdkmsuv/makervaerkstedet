#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$IsoPath,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-fA-F]{64}$')][string]$ExpectedSha256,
    [Parameter(Mandatory)][ValidateRange(0, 255)][int]$DiskNumber,
    [Parameter(Mandatory)][string]$ExpectedUniqueId,
    [Parameter(Mandatory)][ValidateRange(1, [UInt64]::MaxValue)][UInt64]$ExpectedSize,
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z]$')][string]$ExpectedDriveLetter,
    # A previously flashed Debian EFI volume has an empty label. Require an
    # explicit value, including empty, and still compare it exactly below.
    [Parameter(Mandatory)][AllowEmptyString()][string]$ExpectedLabel,
    [Parameter(Mandatory)][string]$ReportPath,
    [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$ChunkSize = 4MB

function Write-Report {
    param([System.Collections.IDictionary]$State)
    $full = [IO.Path]::GetFullPath($ReportPath)
    $parent = Split-Path -Parent $full
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    $tmp = "$full.tmp-$([Guid]::NewGuid().ToString('N'))"
    [IO.File]::WriteAllText($tmp, (($State | ConvertTo-Json -Depth 8) + "`n"), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $tmp -Destination $full -Force
}

function Fail {
    param([System.Collections.IDictionary]$State, [string]$Message)
    $State.state = 'error'
    $State.error = $Message
    try { Write-Report $State } catch {}
    throw $Message
}

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Skrivning kræver en administrativ PowerShell-session. Brug -CheckOnly uden elevation.'
    }
}

function Add-RawIoType {
    if ('ElevhubRawIo' -as [type]) { return }
    Add-Type @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class ElevhubRawIo {
    private const uint GENERIC_READ = 0x80000000;
    private const uint GENERIC_WRITE = 0x40000000;
    private const uint FILE_SHARE_READ = 1;
    private const uint FILE_SHARE_WRITE = 2;
    private const uint OPEN_EXISTING = 3;
    private const uint FSCTL_LOCK_VOLUME = 0x00090018;
    private const uint FSCTL_DISMOUNT_VOLUME = 0x00090020;
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    private static extern IntPtr CreateFile(string name, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError=true)]
    private static extern bool DeviceIoControl(IntPtr handle, uint code, IntPtr inBuffer, uint inLength, IntPtr outBuffer, uint outLength, out uint returned, IntPtr overlapped);
    [DllImport("kernel32.dll", SetLastError=true)]
    private static extern bool CloseHandle(IntPtr handle);
    public static IntPtr OpenVolume(string path) {
        IntPtr handle = CreateFile(path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, IntPtr.Zero, OPEN_EXISTING, 0, IntPtr.Zero);
        if (handle == new IntPtr(-1)) throw new Win32Exception(Marshal.GetLastWin32Error(), "Kunne ikke åbne målvolumen.");
        return handle;
    }
    public static void LockAndDismount(IntPtr handle) {
        uint returned;
        if (!DeviceIoControl(handle, FSCTL_LOCK_VOLUME, IntPtr.Zero, 0, IntPtr.Zero, 0, out returned, IntPtr.Zero))
            throw new Win32Exception(Marshal.GetLastWin32Error(), "Kunne ikke låse målvolumen.");
        if (!DeviceIoControl(handle, FSCTL_DISMOUNT_VOLUME, IntPtr.Zero, 0, IntPtr.Zero, 0, out returned, IntPtr.Zero))
            throw new Win32Exception(Marshal.GetLastWin32Error(), "Kunne ikke afmontere målvolumen.");
    }
    public static void CloseVolume(IntPtr handle) { if (handle != IntPtr.Zero) CloseHandle(handle); }
}
'@
}

function Get-Target {
    $disk = Get-Disk -Number $DiskNumber -ErrorAction Stop
    if ([int]$disk.Number -ne $DiskNumber -or [string]$disk.BusType -ne 'USB' -or [UInt64]$disk.Size -ne $ExpectedSize -or
        [string]$disk.UniqueId -ne $ExpectedUniqueId -or $disk.IsBoot -or $disk.IsSystem -or $disk.IsReadOnly -or
        [string]$disk.OperationalStatus -ne 'Online' -or [string]$disk.FriendlyName.Trim() -ne 'PHILIPS') {
        throw 'Måldisken matcher ikke den eksplicitte USB-identitet eller er ikke skrivbar.'
    }
    $system = @(Get-Disk | Where-Object { $_.IsSystem -or $_.IsBoot } | Select-Object -ExpandProperty Number)
    if ($system -contains $DiskNumber) { throw 'Måldisken er system- eller bootdisk.' }
    $letter = $ExpectedDriveLetter.ToUpperInvariant()
    $partitions = @(Get-Partition -DiskNumber $DiskNumber -ErrorAction Stop)
    if ($partitions.Count -ne 1) { throw 'Måldisken skal have præcis én partition.' }
    $matching = @($partitions | Where-Object { [string]$_.DriveLetter -eq $letter })
    if ($matching.Count -ne 1) { throw "Måldisken har ikke præcis én partition med drevbogstav $letter." }
    if ($matching[0].IsBoot -or $matching[0].IsSystem) { throw 'Målpartitionen er system- eller bootpartition.' }
    $volume = Get-Volume -DriveLetter $letter -ErrorAction Stop
    if ([string]$volume.FileSystemLabel -ne $ExpectedLabel) { throw 'Målvolumenets label matcher ikke.' }
    [pscustomobject]@{ Disk = $disk; Partition = $matching[0]; Volume = $volume; Letter = $letter }
}

function Get-SourceHash {
    param([string]$Path, [UInt64]$Length)
    $sha = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        if ([UInt64]$stream.Length -ne $Length) { throw 'ISO-størrelsen ændrede sig under kontrol.' }
        $hash = $sha.ComputeHash($stream)
        return ([BitConverter]::ToString($hash) -replace '-', '').ToLowerInvariant()
    } finally { $stream.Dispose(); $sha.Dispose() }
}

function Copy-VerifiedRaw {
    param([string]$Source, [string]$DevicePath, [UInt64]$Length, [string]$ExpectedHash, [System.Collections.IDictionary]$State)
    $sourceStream = $null
    $device = $null
    $buffer = New-Object byte[] $ChunkSize
    try {
        $sourceStream = [IO.File]::Open($Source, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        if ([UInt64]$sourceStream.Length -ne $Length) { throw 'ISO-læsningen sluttede før den forventede længde.' }
        $sourceSha = [Security.Cryptography.SHA256]::Create()
        try {
            $sourceHash = ([BitConverter]::ToString($sourceSha.ComputeHash($sourceStream)) -replace '-', '').ToLowerInvariant()
        } finally { if ($sourceSha -is [IDisposable]) { $sourceSha.Dispose() } }
        if ($sourceHash -ne $ExpectedHash) { throw 'ISOens SHA-256 matcher ikke før skrivning.' }
        $sourceStream.Position = 0
        $device = [IO.FileStream]::new($DevicePath, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite, $ChunkSize, [IO.FileOptions]::WriteThrough)
        $written = [UInt64]0
        while ($written -lt $Length) {
            $remainingToWrite = [UInt64]($Length - $written)
            $want = if ($remainingToWrite -lt [UInt64]$ChunkSize) { [int]$remainingToWrite } else { $ChunkSize }
            $read = $sourceStream.Read($buffer, 0, $want)
            if ($read -ne $want) { throw 'ISO-læsningen sluttede før den forventede længde.' }
            $device.Write($buffer, 0, $read)
            $written += [UInt64]$read
            $State.bytesWritten = $written
            Write-Report $State
        }
        $device.Flush($true)
        $device.Position = 0
        $sha = [Security.Cryptography.SHA256]::Create()
        try {
            $remaining = $Length
            while ($remaining -gt 0) {
                $want = if ($remaining -lt [UInt64]$ChunkSize) { [int]$remaining } else { $ChunkSize }
                $read = $device.Read($buffer, 0, $want)
                if ($read -ne $want) { throw 'Rådisken kunne ikke læses tilbage i fuld ISO-længde.' }
                [void]$sha.TransformBlock($buffer, 0, $read, $buffer, 0)
                $remaining -= [UInt64]$read
            }
            [void]$sha.TransformFinalBlock([byte[]]::new(0), 0, 0)
            $actual = ([BitConverter]::ToString($sha.Hash) -replace '-', '').ToLowerInvariant()
        } finally { if ($sha -is [IDisposable]) { $sha.Dispose() } }
        $State.readbackSha256 = $actual
        if ($actual -ne $ExpectedHash) { throw 'Readback SHA-256 matcher ikke ISOens SHA-256.' }
    } finally {
        if ($device -is [IDisposable]) { $device.Dispose() }
        if ($sourceStream -is [IDisposable]) { $sourceStream.Dispose() }
    }
}

$state = [ordered]@{ schemaVersion = 1; state = 'starting'; checkOnly = [bool]$CheckOnly; diskNumber = $DiskNumber; expectedSize = $ExpectedSize; expectedDriveLetter = $ExpectedDriveLetter.ToUpperInvariant(); reportPath = [IO.Path]::GetFullPath($ReportPath); bytesWritten = 0 }
$sourceLock = $null
try {
    $iso = [IO.Path]::GetFullPath($IsoPath)
    if (-not (Test-Path -LiteralPath $iso -PathType Leaf)) { Fail $state 'ISO-filen findes ikke.' }
    $isoInfo = Get-Item -LiteralPath $iso -Force
    if ([UInt64]$isoInfo.Length -le 0 -or [UInt64]$isoInfo.Length -gt $ExpectedSize) { Fail $state 'ISO-størrelsen er ugyldig i forhold til måldisken.' }
    $state.isoBytes = [UInt64]$isoInfo.Length
    # Establish the source read lock before hashing and keep it through the
    # operation so the verified ISO cannot be replaced mid-write.
    $sourceLock = [IO.File]::Open($iso, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $state.sourceSha256 = Get-SourceHash $iso $isoInfo.Length
    if ($state.sourceSha256 -ne $ExpectedSha256.ToLowerInvariant()) { Fail $state 'ISOens SHA-256 matcher ikke den forventede kontrolsum.' }
    if ([StringComparer]::OrdinalIgnoreCase.Equals([IO.Path]::GetFullPath($state.reportPath), $iso)) { Fail $state 'Rapportstien må ikke være den samme som ISO-kilden.' }
    if ([IO.Path]::GetPathRoot($iso).TrimEnd('\').ToUpperInvariant() -eq "$($ExpectedDriveLetter.ToUpperInvariant()):") { Fail $state 'ISO-kilden ligger på måldrevets volumen.' }
    $target = Get-Target
    $state.uniqueId = [string]$target.Disk.UniqueId
    $state.diskSize = [UInt64]$target.Disk.Size
    $state.volumeLabel = [string]$target.Volume.FileSystemLabel
    $reportRoot = [IO.Path]::GetPathRoot($state.reportPath).TrimEnd('\').ToUpperInvariant()
    if ($reportRoot -eq "$($target.Letter):") { Fail $state 'Rapportstien ligger på måldrevets volumen.' }
    $state.state = 'checked'
    Write-Report $state
    if ($CheckOnly) { return }
    Assert-Administrator
    # Revalidate every identity field immediately before any dismount/write.
    $target = Get-Target
    $volumeHandle = [IntPtr]::Zero
    try {
        Add-RawIoType
        $volumeHandle = [ElevhubRawIo]::OpenVolume("\\.\$($target.Letter):")
        [ElevhubRawIo]::LockAndDismount($volumeHandle)
        $state.state = 'writing'; Write-Report $state
        Copy-VerifiedRaw $iso "\\.\PhysicalDrive$DiskNumber" $isoInfo.Length $ExpectedSha256.ToLowerInvariant() $state
    } finally {
        if ($volumeHandle -ne [IntPtr]::Zero -and ('ElevhubRawIo' -as [type])) { [ElevhubRawIo]::CloseVolume($volumeHandle) }
    }
    $state.state = 'success'; Write-Report $state
} catch {
    if ($state.state -ne 'error') { $state.state = 'error'; $state.error = $_.Exception.Message; try { Write-Report $state } catch {} }
    throw
} finally {
    if ($sourceLock -is [IDisposable]) { $sourceLock.Dispose() }
}
