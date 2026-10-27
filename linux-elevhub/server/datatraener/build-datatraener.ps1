[CmdletBinding()]
param(
    [string]$SourceDir = 'D:\Elevhub-VM\datatraener\cctd-ml-machine-release-dr',
    [string]$OutputDir = 'D:\Elevhub-VM\datatraener\cctd-ml-machine-release-dr-build',
    [string]$Commit = 'd6fe8045d262553be2e5f67457b440949e048f2f'
)

$ErrorActionPreference = 'Stop'

function Invoke-Checked {
    param([Parameter(Mandatory)][string]$FilePath,
          [Parameter(Mandatory)][string[]]$ArgumentList,
          [Parameter(Mandatory)][string]$Description)
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) { throw "$Description failed with exit code $LASTEXITCODE" }
}

if (-not (Test-Path -LiteralPath $SourceDir -PathType Container)) {
    throw "Pinned source folder does not exist: $SourceDir"
}
$actualCommit = (& git -C $SourceDir rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'git rev-parse failed' }
if ($actualCommit -ne $Commit) {
    throw "Pinned commit mismatch. Expected $Commit, got $actualCommit"
}

$main = Get-Content -Raw -LiteralPath (Join-Path $SourceDir 'src\main.ts')
$app = Get-Content -Raw -LiteralPath (Join-Path $SourceDir 'src\App.svelte')
$css = Get-Content -Raw -LiteralPath (Join-Path $SourceDir 'public\global.css')
$html = Get-Content -Raw -LiteralPath (Join-Path $SourceDir 'index.html')
if ($main -match 'appInsights|ApplicationInsights') {
    throw 'Offline patch missing: main.ts still imports analytics.'
}
if ($app -match 'CookieBanner|ApplicationInsights|analytics') {
    throw 'Offline patch missing: App.svelte still renders the analytics consent banner.'
}
if ($css -match 'fonts\.googleapis|gstatic') {
    throw 'Offline patch missing: global.css still fetches a remote font.'
}
if ($html -match 'unpkg\.com|d3js\.org|googleapis|ApplicationInsights') {
    throw 'Offline patch missing: index.html still references a remote library.'
}
$adapterSource = Join-Path $PSScriptRoot 'project-adapter.ts'
if (-not (Test-Path -LiteralPath $adapterSource -PathType Leaf)) {
    throw "Missing Elevhub project adapter source: $adapterSource"
}
$adapterTarget = Join-Path $SourceDir 'src\project-adapter.ts'
Copy-Item -LiteralPath $adapterSource -Destination $adapterTarget -Force
if ($main -notmatch 'project-adapter') {
    $main = "import './project-adapter';`n" + $main
    Set-Content -LiteralPath (Join-Path $SourceDir 'src\main.ts') -Value $main -Encoding UTF8
}
if (Test-Path -LiteralPath $OutputDir) {
    throw "Build output already exists; refusing to overwrite: $OutputDir"
}

Invoke-Checked -FilePath 'npm' -ArgumentList @('ci', '--ignore-scripts', '--prefix', $SourceDir) -Description 'npm ci'
Invoke-Checked -FilePath 'npm' -ArgumentList @('run', 'build', '--prefix', $SourceDir) -Description 'Vite build'

$dist = Join-Path $SourceDir 'dist'
if (-not (Test-Path -LiteralPath (Join-Path $dist 'index.html') -PathType Leaf)) {
    throw "Vite output is missing index.html: $dist"
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
Copy-Item -Path (Join-Path $dist '*') -Destination $OutputDir -Recurse -Force
Copy-Item -LiteralPath (Join-Path $SourceDir 'LICENSE') -Destination (Join-Path $OutputDir 'LICENSE.txt') -Force
foreach ($required in @('index.html', 'assets', 'ultrabit\fonts\ultrabit.ttf')) {
    if (-not (Test-Path -LiteralPath (Join-Path $OutputDir $required))) {
        throw "Datatræner build asset is missing: $required"
    }
}

$forbidden = Get-ChildItem -LiteralPath $OutputDir -Recurse -File |
    Where-Object { $_.Extension -in @('.js', '.css', '.html') } |
    Select-String -Pattern 'application\s+insights|InstrumentationKey=|Forbrug og analyse|Performance\s*&\s*analytics|fonts\.googleapis|gstatic|azure\.com/|d3js\.org|unpkg\.com'
if ($forbidden) {
    throw 'Built Datatræner still contains a forbidden external analytics/CDN reference.'
}

[ordered]@{
    installed = (Get-Date).ToUniversalTime().ToString('o')
    upstream = 'https://github.com/microbit-foundation/cctd-ml-machine'
    branch = 'release-dr'
    commit = $actualCommit
    sourcePath = $SourceDir
    outputPath = $OutputDir
    entrypoint = 'index.html'
    licenseFile = 'LICENSE.txt'
    bundled = @('ml5', 'chart.js', 'smoothie', 'ultrabit font')
    dependencyOverrides = @(
        'js-cookie 3.0.8',
        'postcss 8.5.28',
        'axios 1.20.0',
        'node-fetch 2.7.0',
        'form-data 4.0.6'
    )
    audit = 'npm audit --omit=dev; 12 high advisories remain, primarily the pinned ml5 0.12.2 tfjs-vis/Vega browser bundle; review before production.'
    offlinePatch = @('remove Application Insights import', 'remove analytics consent banner', 'retain local reconnect flag only', 'remove remote Google font', 'remove CDN script tags', 'inject Elevhub Datatræner project adapter')
    license = 'MIT (c) Center for Computational Thinking and Design, Aarhus University (2023)'
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'installation.json') -Encoding UTF8

Write-Output "Datatræner built at $OutputDir"
