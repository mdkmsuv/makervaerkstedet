[CmdletBinding()]
param(
    [string]$ScratchRoot = 'D:\Elevhub-VM\scratch',
    [string]$Version = 'v15.1.1',
    [string]$Commit = '99bcc17e0580588f181f8a87577a2f676537a487'
)

$ErrorActionPreference = 'Stop'
$repoUrl = 'https://github.com/scratchfoundation/scratch-editor.git'
$sourceDir = Join-Path $ScratchRoot "scratch-editor-$Version"
$outputDir = Join-Path $ScratchRoot "scratch-gui-$Version-build"
$metadataPath = Join-Path $PSScriptRoot 'installation.json'

function Invoke-Checked {
    param(
        [Parameter(Mandatory)] [string]$FilePath,
        [Parameter(Mandatory)] [string[]]$ArgumentList,
        [Parameter(Mandatory)] [string]$Description
    )
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE"
    }
}

if (Test-Path $sourceDir) {
    throw "Source folder already exists; refusing to overwrite: $sourceDir"
}
if (Test-Path $outputDir) {
    throw "Build output folder already exists; refusing to overwrite: $outputDir"
}
New-Item -ItemType Directory -Force -Path $ScratchRoot | Out-Null

Invoke-Checked -FilePath 'git' -ArgumentList @('clone', '--depth', '1', '--branch', $Version, $repoUrl, $sourceDir) -Description 'git clone'
$actualCommit = (& git -C $sourceDir rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "git rev-parse failed with exit code $LASTEXITCODE" }
if ($actualCommit -ne $Commit) {
    throw "Pinned commit mismatch. Expected $Commit, got $actualCommit"
}

$dependencyPatch = Join-Path $PSScriptRoot 'dependency.patch'
Invoke-Checked -FilePath 'git' -ArgumentList @('-C', $sourceDir, 'apply', '--check', $dependencyPatch) -Description 'Validate pinned hull security patch'
Invoke-Checked -FilePath 'git' -ArgumentList @('-C', $sourceDir, 'apply', $dependencyPatch) -Description 'Apply pinned hull security patch'
Invoke-Checked -FilePath 'npm' -ArgumentList @('ci', '--ignore-scripts', '--prefix', $sourceDir) -Description 'npm ci'
$guiDir = Join-Path $sourceDir 'packages\scratch-gui'

# The browser editor's upstream library URLs point to Scratch's CDN. This small
# source patch makes the pinned build use the asset pack generated below, while
# preserving the upstream source and commit in the external checkout.
$libraryUrlPath = Join-Path $guiDir 'src\lib\legacy-library-asset-url.ts'
$libraryUrl = Get-Content -Raw $libraryUrlPath
if ($libraryUrl -notmatch 'https://cdn\.assets\.scratch\.mit\.edu') {
    throw 'Unexpected legacy-library-asset-url.ts; refusing to apply integration patch'
}
$libraryUrl = $libraryUrl.Replace(
    "const LEGACY_LIBRARY_ASSET_HOST = 'https://cdn.assets.scratch.mit.edu';",
    "const LEGACY_LIBRARY_ASSET_HOST = 'static/library-assets';"
)
$libraryUrl = $libraryUrl.Replace(
    'return `${LEGACY_LIBRARY_ASSET_HOST}/internalapi/asset/${assetId}.${dataFormat}/get/`;',
    'return `${LEGACY_LIBRARY_ASSET_HOST}/${assetId}.${dataFormat}`;'
)
$libraryUrl = $libraryUrl.Replace(
    'return `${host}/internalapi/asset/${assetId}.${dataFormat}/get/`;',
    'return `${host}/static/library-assets/${assetId}.${dataFormat}`;'
)
if ($libraryUrl -match 'cdn\.assets\.scratch\.mit\.edu|internalapi/asset') {
    throw 'Could not fully apply local library URL integration patch'
}
Set-Content -Path $libraryUrlPath -Value $libraryUrl -NoNewline

$storagePath = Join-Path $guiDir 'src\lib\legacy-storage.ts'
$storage = Get-Content -Raw $storagePath
$oldAssetGet = 'return `${this.assetHost}/internalapi/asset/${asset.assetId}.${asset.dataFormat}/get/`;'
if ($storage -notlike "*$oldAssetGet*") {
    throw 'Unexpected legacy-storage.ts; refusing to apply integration patch'
}
$replacement = @'
// The local ElevHub build ships the standard library beside the editor.
        // Project assets loaded from .sb3 files remain in the in-memory store.
        return new URL(
            `static/library-assets/${asset.assetId}.${asset.dataFormat}`,
            document.baseURI
        ).toString();
'@
$storage = $storage.Replace($oldAssetGet, $replacement.TrimEnd("`r", "`n"))
Set-Content -Path $storagePath -Value $storage -NoNewline

$standalonePlaygroundPath = Join-Path $guiDir 'src\playground\render-gui-standalone.jsx'
$standalonePlayground = Get-Content -Raw $standalonePlaygroundPath
$standalonePattern = '(?s)gui\.render\(\{\s*canEditTitle: true,\s*backpackVisible: true,\s*showComingSoon: true,\s*backpackHost,\s*canSave: false,\s*onClickLogo\s*\}\);'
$standaloneReplacement = "// Local school build: no backpack/community/cloud controls.`n        gui.render({`n            canEditTitle: true,`n            canSave: false`n        });"
if (([regex]::Matches($standalonePlayground, $standalonePattern)).Count -ne 1) {
    throw 'Unexpected render-gui-standalone.jsx; refusing to apply local UI patch'
}
$standalonePlayground = [regex]::Replace($standalonePlayground, $standalonePattern, [System.Text.RegularExpressions.MatchEvaluator]{ param($match) $standaloneReplacement }, 1)
$localProjectAdapter = Get-Content -Raw (Join-Path $PSScriptRoot 'local-project-adapter.js')
$adapterAnchor = '    const gui = createStandaloneRoot(state, appTarget, {'
if (([regex]::Matches($standalonePlayground, [regex]::Escape($adapterAnchor))).Count -ne 1) {
    throw 'Unexpected EditorState setup; refusing to patch local project support'
}
# Replace an earlier generated adapter block when rebuilding an existing
# checkout. This keeps the reproducible build aligned with the checked-in
# adapter instead of silently retaining an older generated implementation.
$standalonePlayground = [regex]::Replace(
    $standalonePlayground,
    '(?s)\n\s*// Inserted into render-gui-standalone\.jsx after EditorState is created\..*?\n\s*const gui = createStandaloneRoot\(state, appTarget, \{',
    "`n$localProjectAdapter`n$adapterAnchor",
    1
)
Set-Content -Path $standalonePlaygroundPath -Value $standalonePlayground -NoNewline

$menuBarPath = Join-Path $guiDir 'src\components\menu-bar\menu-bar.jsx'
$menuBar = Get-Content -Raw $menuBarPath
$oldDebugClass = 'className={classNames(styles.menuBarItem, styles.noOffset, styles.hoverable)}'
$newDebugClass = "className={classNames(styles.menuBarItem, styles.noOffset, styles.hoverable, 'debug-button')}"
if (([regex]::Matches($menuBar, [regex]::Escape($oldDebugClass))).Count -ne 1) {
    throw 'Unexpected menu-bar.jsx; refusing to apply local debug-button patch'
}
$menuBar = $menuBar.Replace($oldDebugClass, $newDebugClass)
Set-Content -Path $menuBarPath -Value $menuBar -NoNewline

$workspaceBuilds = @(
    '@scratch/task-herder',
    '@scratch/scratch-storage',
    '@scratch/scratch-svg-renderer',
    '@scratch/scratch-paint',
    '@scratch/scratch-render',
    '@scratch/scratch-vm',
    '@scratch/scratch-media-lib-scripts'
)
foreach ($workspace in $workspaceBuilds) {
    Invoke-Checked -FilePath 'npm' -ArgumentList @('run', 'build', '--workspace', $workspace, '--prefix', $sourceDir) -Description "workspace build $workspace"
}

Push-Location $guiDir
try {
    Invoke-Checked -FilePath 'node' -ArgumentList @('scripts/prepare.mjs') -Description 'Scratch GUI prepare'
    Invoke-Checked -FilePath 'npx' -ArgumentList @('cross-env', 'NODE_ENV=production', 'BUILD_TYPE=dist', 'webpack', '--stats=errors-warnings') -Description 'Scratch GUI production build'
    Invoke-Checked -FilePath 'npx' -ArgumentList @('cross-env', 'NODE_ENV=production', 'BUILD_TYPE=dist-standalone', 'webpack', '--stats=errors-warnings') -Description 'Scratch GUI standalone build'
    Invoke-Checked -FilePath 'node' -ArgumentList @((Join-Path $PSScriptRoot 'build-playground.cjs'), $guiDir) -Description 'Scratch GUI standalone playground build'
}
finally {
    Pop-Location
}

$playgroundDir = Join-Path $guiDir 'build'
if (-not (Test-Path (Join-Path $playgroundDir 'guistandalone.js'))) {
    throw "Standalone playground bundle missing: $playgroundDir"
}
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
Copy-Item (Join-Path $playgroundDir 'guistandalone.js*') $outputDir -Force
Copy-Item (Join-Path $playgroundDir 'chunks') $outputDir -Recurse -Force
Copy-Item (Join-Path $playgroundDir 'static') $outputDir -Recurse -Force
Copy-Item (Join-Path $sourceDir 'LICENSE') $outputDir -Force
Copy-Item (Join-Path $sourceDir 'TRADEMARK') $outputDir -Force
Copy-Item (Join-Path $guiDir 'LICENSE') (Join-Path $outputDir 'LICENSE-scratch-gui') -Force
Copy-Item (Join-Path $guiDir 'TRADEMARK') (Join-Path $outputDir 'TRADEMARK-scratch-gui') -Force
Copy-Item (Join-Path $PSScriptRoot 'CREDITS.txt') $outputDir -Force
Copy-Item (Join-Path $PSScriptRoot 'DEPENDENCY-AUDIT.md') $outputDir -Force

$html = @'
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="google" content="notranslate">
  <style>
    html, body { width: 100%; height: 100%; margin: 0; }
    body { display: flex; flex-direction: column; }
    #source-credit { flex: 0 0 30px; display: flex; align-items: center; justify-content: flex-end; gap: 12px; padding: 0 14px; box-sizing: border-box; font: 12px system-ui, sans-serif; background: #f4f4f4; color: #444; }
    #source-credit a { color: #2456a6; text-decoration: underline; }
    .debug-button { display: none !important; }
    body > div:not(#source-credit) { flex: 1 1 auto; min-height: 0 !important; height: auto !important; }
  </style>
  <title>Scratch editor</title>
</head>
<body>
  <div id="source-credit"><span>Lokal Scratch-editor</span><a href="source.zip" rel="license">Kildekode og licens</a></div>
  <script defer src="guistandalone.js"></script>
</body>
</html>
'@
Set-Content -Path (Join-Path $outputDir 'index.html') -Value $html -Encoding UTF8
Set-Content -Path (Join-Path $outputDir 'standalone.html') -Value $html -Encoding UTF8

# Add the standard costumes, backdrops and sounds to the same static root.
$assetScript = Join-Path $PSScriptRoot 'download-library-assets.ps1'
Invoke-Checked -FilePath 'pwsh' -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $assetScript, '-SourceDir', $guiDir, '-BuildPath', $outputDir) -Description 'Scratch standard library asset pack'
$assetManifest = Get-Content -Raw (Join-Path $outputDir 'library-assets-manifest.json') | ConvertFrom-Json

# AGPL corresponding source: include the exact patched git-tracked checkout,
# lockfile and this integration material, but never node_modules/.git/builds.
$sourceZip = Join-Path $outputDir 'source.zip'
$sourceStage = Join-Path $ScratchRoot "scratch-source-$Version-stage"
if (Test-Path $sourceZip) { throw "Source archive already exists: $sourceZip" }
if (Test-Path $sourceStage) { throw "Source archive staging folder already exists: $sourceStage" }
New-Item -ItemType Directory -Force -Path (Join-Path $sourceStage 'upstream') | Out-Null
$trackedFiles = @(& git -C $sourceDir ls-files)
if ($LASTEXITCODE -ne 0) { throw "git ls-files failed with exit code $LASTEXITCODE" }
foreach ($relativePath in $trackedFiles) {
    $from = Join-Path $sourceDir $relativePath
    $to = Join-Path $sourceStage (Join-Path 'upstream' $relativePath)
    New-Item -ItemType Directory -Force -Path (Split-Path $to -Parent) | Out-Null
    Copy-Item -LiteralPath $from -Destination $to -Force
}
$integrationDir = Join-Path $sourceStage 'integration'
New-Item -ItemType Directory -Force -Path $integrationDir | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'build-scratch.ps1') -Destination (Join-Path $integrationDir 'build-scratch.ps1') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'local-project-adapter.js') -Destination (Join-Path $integrationDir 'local-project-adapter.js') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'build-playground.cjs') -Destination (Join-Path $integrationDir 'build-playground.cjs') -Force
Copy-Item -LiteralPath $dependencyPatch -Destination (Join-Path $integrationDir 'dependency.patch') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'download-library-assets.ps1') -Destination (Join-Path $integrationDir 'download-library-assets.ps1') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.md') -Destination (Join-Path $integrationDir 'README.md') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'CREDITS.txt') -Destination (Join-Path $integrationDir 'CREDITS.txt') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'DEPENDENCY-AUDIT.md') -Destination (Join-Path $integrationDir 'DEPENDENCY-AUDIT.md') -Force
& git -C $sourceDir diff --binary -- packages/scratch-gui/src/lib/legacy-library-asset-url.ts packages/scratch-gui/src/lib/legacy-storage.ts packages/scratch-gui/src/playground/render-gui-standalone.jsx packages/scratch-gui/src/components/menu-bar/menu-bar.jsx packages/scratch-gui/test/unit/util/project-fetcher-hoc.test.jsx | Out-File -FilePath (Join-Path $integrationDir 'local-library-assets.patch') -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw "git diff failed with exit code $LASTEXITCODE" }
$sourceNotes = @"
# Scratch $Version source bundle

Upstream commit: $actualCommit

This archive contains every git-tracked upstream file at the pinned commit,
including local integration edits and the pinned hull security fix. It excludes
.git, node_modules and generated build output. The integration scripts and
patch are under integration/. The root package-lock.json is the npm lock used
by the build. See upstream/LICENSE and upstream/packages/scratch-gui/LICENSE.
"@
Set-Content -LiteralPath (Join-Path $sourceStage 'SOURCE-BUILD-NOTES.md') -Value $sourceNotes -Encoding UTF8
Compress-Archive -Path (Join-Path $sourceStage '*') -DestinationPath $sourceZip -CompressionLevel Optimal
$sourceZipSize = (Get-Item -LiteralPath $sourceZip).Length
if ($sourceZipSize -gt 128MB) {
    throw "AGPL source.zip is $sourceZipSize bytes, above Elevhub's 128 MiB source archive limit"
}

$nodeVersion = (& node --version).Trim()
if ($LASTEXITCODE -ne 0) { throw "node --version failed with exit code $LASTEXITCODE" }
$npmVersion = (& npm --version).Trim()
if ($LASTEXITCODE -ne 0) { throw "npm --version failed with exit code $LASTEXITCODE" }
$metadata = [ordered]@{
    installed = (Get-Date).ToUniversalTime().ToString('o')
    upstream = $repoUrl
    tag = $Version
    commit = $actualCommit
    node = $nodeVersion
    npm = $npmVersion
    sourcePath = $sourceDir
    outputPath = $outputDir
    entrypoint = 'index.html'
    standaloneEntrypoint = 'standalone.html'
    bundle = 'guistandalone.js'
    sourceArchive = 'source.zip'
    sourceArchiveSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourceZip).Hash.ToLowerInvariant()
    sourceArchiveBytes = $sourceZipSize
    localLibraryAssetDir = 'static/library-assets'
    localLibraryAssetCount = [int]$assetManifest.count
    libraryManifestSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $outputDir 'library-assets-manifest.json')).Hash.ToLowerInvariant()
    integrationPatch = 'legacy-library-asset-url.ts uses local library paths and legacy-storage.ts resolves them against document.baseURI'
    licenseFiles = @('LICENSE', 'LICENSE-scratch-gui', 'TRADEMARK', 'TRADEMARK-scratch-gui')
}
$metadata | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 $metadataPath
Write-Output "Scratch editor built at $outputDir"
Write-Output "Metadata written to $metadataPath"
