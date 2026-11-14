[CmdletBinding()]
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path,
    [string]$RuntimePython,
    [string]$OutputRoot = (Join-Path $PSScriptRoot 'out'),
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($RuntimePython)) { throw 'Angiv -RuntimePython til den signerede runtime\\python.exe.' }
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
$stage = Join-Path $OutputRoot 'ElevhubServer'
if ($CheckOnly) {
    if (-not (Test-Path -LiteralPath $RuntimePython -PathType Leaf)) { throw 'Runtime-python mangler.' }
    foreach ($path in @('app','web','linux-elevhub\server\elevhub')) { if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot $path))) { throw "Mangler $path." } }
    Write-Output 'Build check OK; ingen filer blev skrevet.'
    exit 0
}
if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stage 'runtime'),(Join-Path $stage 'server') | Out-Null
Copy-Item -Path (Join-Path $RuntimePython '..\*') -Destination (Join-Path $stage 'runtime') -Recurse
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'app') -Destination $stage -Recurse
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'web') -Destination $stage -Recurse
Copy-Item -Path (Join-Path $ProjectRoot 'linux-elevhub\server\elevhub\*') -Destination (Join-Path $stage 'hub') -Recurse
Copy-Item -Path (Join-Path $PSScriptRoot '*') -Destination (Join-Path $stage 'server') -Recurse -Exclude 'out'
Write-Output "Staged: $stage"
