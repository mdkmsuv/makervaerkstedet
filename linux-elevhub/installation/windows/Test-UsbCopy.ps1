#requires -Version 5.1
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ChunkSize = 4MB
$writer = Join-Path $PSScriptRoot 'Write-ElevhubIso.ps1'
$root = Join-Path ([IO.Path]::GetTempPath()) ('elevhub-usb-copy-test-' + [Guid]::NewGuid().ToString('N'))

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Get-Sha256([string]$Path) {
    $hash = Get-FileHash -LiteralPath $Path -Algorithm SHA256
    return $hash.Hash.ToLowerInvariant()
}

function Get-FunctionText([object]$Ast, [string]$Name) {
    $function = $Ast.Find({ param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $Name
    }, $true)
    if ($null -eq $function) { throw "Funktionen $Name mangler i writer-scriptet." }
    return $function.Extent.Text
}

function Write-Report([System.Collections.IDictionary]$State) { }

try {
    New-Item -ItemType Directory -Path $root | Out-Null
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($writer, [ref]$tokens, [ref]$errors)
    Assert-True ($errors.Count -eq 0) 'Writer-scriptet kunne ikke parses.'

    # Extract only the helper and stream core. The writer entrypoint is never run.
    Invoke-Expression (Get-FunctionText $ast 'Add-RawIoType')
    Add-RawIoType
    Invoke-Expression (Get-FunctionText $ast 'Get-SourceHash')
    Invoke-Expression (Get-FunctionText $ast 'Copy-VerifiedRaw')

    # Execute both actual chunk-size expressions above the Int32 boundary.
    # Small stream fixtures alone cannot catch PowerShell's Math.Min overload.
    $chunkAssignments = @($ast.FindAll({ param($node)
        $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
        $node.Left -is [System.Management.Automation.Language.VariableExpressionAst] -and
        $node.Left.VariablePath.UserPath -eq 'want'
    }, $true))
    Assert-True ($chunkAssignments.Count -eq 2) 'Begge chunk-beregninger skal kontrolleres.'
    foreach ($case in @([UInt64]2863300608, [UInt64]4294967297, [UInt64]12345)) {
        $Length = $remainingToWrite = $remaining = $case
        $written = [UInt64]0
        $expectedChunk = if ($case -lt [UInt64]$ChunkSize) { [int]$case } else { $ChunkSize }
        foreach ($assignment in $chunkAssignments) {
            $actualChunk = & ([scriptblock]::Create($assignment.Extent.Text + '; $want'))
            Assert-True ($actualChunk -eq $expectedChunk) 'Chunk-størrelse fejler over Int32-grænsen.'
        }
    }
    Write-Output 'PASS: write/read chunk sizes above 2GB and 4GB'

    $length = [UInt64](4MB + 12345)
    $source = Join-Path $root 'source.iso'
    $target = Join-Path $root 'target.bin'
    $bytes = New-Object byte[] ([int]$length)
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { if ($rng -is [IDisposable]) { $rng.Dispose() } }
    [IO.File]::WriteAllBytes($source, $bytes)
    $targetStream = [IO.File]::Create($target)
    try { $targetStream.SetLength([Int64]$length) } finally { if ($targetStream -is [IDisposable]) { $targetStream.Dispose() } }

    $expected = Get-Sha256 $source
    $state = @{ state = 'writing'; bytesWritten = 0 }
    Copy-VerifiedRaw $source $target $length $expected $state
    Assert-True ((Get-Sha256 $target) -eq $expected) 'Roundtrip-hash matcher ikke.'
    Assert-True ([UInt64]$state.bytesWritten -eq $length) 'Bytefremdriften matcher ikke testfilens længde.'
    Assert-True ([string]$state.readbackSha256 -eq $expected) 'Readback-hashen blev ikke gemt korrekt.'
    Write-Output 'PASS: regular-file roundtrip with non-aligned tail'

    # A wrong expected hash is rejected by the preflight comparison before the
    # copy core is called, leaving the target byte-for-byte unchanged.
    $before = Get-Sha256 $target
    $wrongState = @{ state = 'writing'; bytesWritten = 0 }
    $failed = $false
    try { Copy-VerifiedRaw $source $target $length ('0' * 64) $wrongState }
    catch { $failed = $true }
    Assert-True $failed 'Forkert kontrolsum blev accepteret.'
    Assert-True ((Get-Sha256 $target) -eq $before) 'Forkert kontrolsum ændrede målfilen.'
    Write-Output 'PASS: incorrect expected hash rejected before copy'

    # Source truncation is surfaced by the copy core and cannot be reported as
    # a successful write.
    $shortState = @{ state = 'writing'; bytesWritten = 0 }
    $shortBefore = Get-Sha256 $target
    $failed = $false
    try { Copy-VerifiedRaw $source $target ($length + 1) $expected $shortState }
    catch { $failed = $true }
    Assert-True $failed 'Trunkeret kilde blev ikke afvist.'
    Assert-True ((Get-Sha256 $target) -eq $shortBefore) 'Trunkeret kilde ændrede målfilen.'
    Write-Output 'PASS: truncated source rejected'
    Write-Output 'PowerShell version: ' + $PSVersionTable.PSVersion.ToString()
} finally {
    if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force }
}
