[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Manifest,
    [string]$OutputDirectory
)
# Reassemble downloaded release parts on stock Windows. No elevation needed.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$manifestPath = (Resolve-Path -LiteralPath $Manifest).ProviderPath
$sourceDirectory = Split-Path -Parent $manifestPath
if (-not $OutputDirectory) { $OutputDirectory = $sourceDirectory }
$destinationDirectory = (Resolve-Path -LiteralPath $OutputDirectory).ProviderPath
$specification = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
function Test-Leaf([string]$Name) {
    return ($Name -match '^[A-Za-z0-9][A-Za-z0-9._-]*$' -and $Name -notmatch '\.\.' -and $Name.Length -le 200)
}
if ($specification.format -cne 'elevhub-release-parts-v1' -or -not (Test-Leaf $specification.file) -or
    $specification.sha256 -cnotmatch '^[a-f0-9]{64}$' -or $specification.bytes -le 0 -or @($specification.parts).Count -lt 1) {
    throw 'Invalid release manifest.'
}
$target = Join-Path $destinationDirectory $specification.file
if (Test-Path -LiteralPath $target) { throw 'Destination already exists; keep it or select another output folder.' }
$seen = @{}; [long]$total = 0
foreach ($part in $specification.parts) {
    if (-not (Test-Leaf $part.name) -or $seen.ContainsKey($part.name) -or $part.sha256 -cnotmatch '^[a-f0-9]{64}$' -or $part.bytes -le 0) {
        throw 'Invalid or duplicate release part.'
    }
    $seen[$part.name] = $true
    $path = Join-Path $sourceDirectory $part.name
    $item = Get-Item -LiteralPath $path
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -ne $part.bytes) {
        throw ('Missing, redirected or incomplete release part: ' + $part.name)
    }
    $total += $item.Length
}
if ($total -ne $specification.bytes) { throw 'Release size does not match the manifest.' }
$partial = Join-Path $destinationDirectory ('.elevhub-join-' + [guid]::NewGuid().ToString('N') + '.partial')
$output = $null; $inputStream = $null; $wholeHash = $null; $partHash = $null
try {
    $output = [IO.File]::Open($partial, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    $wholeHash = [Security.Cryptography.SHA256]::Create()
    $buffer = New-Object byte[] (1024 * 1024)
    foreach ($part in $specification.parts) {
        $inputStream = [IO.File]::OpenRead((Join-Path $sourceDirectory $part.name))
        $partHash = [Security.Cryptography.SHA256]::Create()
        while (($count = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $output.Write($buffer, 0, $count)
            $null = $partHash.TransformBlock($buffer, 0, $count, $buffer, 0)
            $null = $wholeHash.TransformBlock($buffer, 0, $count, $buffer, 0)
        }
        $inputStream.Dispose(); $inputStream = $null
        $null = $partHash.TransformFinalBlock([byte[]]@(), 0, 0)
        $actual = [BitConverter]::ToString($partHash.Hash).Replace('-', '').ToLowerInvariant()
        $partHash.Dispose(); $partHash = $null
        if ($actual -cne $part.sha256) { throw ('Damaged release part: ' + $part.name) }
    }
    $null = $wholeHash.TransformFinalBlock([byte[]]@(), 0, 0)
    $actual = [BitConverter]::ToString($wholeHash.Hash).Replace('-', '').ToLowerInvariant()
    if ($actual -cne $specification.sha256 -or $output.Length -ne $specification.bytes) { throw 'Assembled release checksum mismatch.' }
    $output.Dispose(); $output = $null
    # File.Move does not overwrite an existing destination on Windows PowerShell 5.1.
    [IO.File]::Move($partial, $target)
    Write-Output ('Verified release: ' + $target)
} finally {
    if ($inputStream) { $inputStream.Dispose() }
    if ($partHash) { $partHash.Dispose() }
    if ($wholeHash) { $wholeHash.Dispose() }
    if ($output) { $output.Dispose() }
    # This exact generated leaf is the only temporary file owned by this run.
    if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force }
}
