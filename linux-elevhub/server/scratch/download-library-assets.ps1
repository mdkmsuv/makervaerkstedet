[CmdletBinding()]
param(
    [string]$SourceDir = 'D:\Elevhub-VM\scratch\scratch-editor-v15.1.1\packages\scratch-gui',
    [string]$BuildPath = 'D:\Elevhub-VM\scratch\scratch-gui-v15.1.1-build',
    [int]$ThrottleLimit = 24
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$assetDir = Join-Path $BuildPath 'static\library-assets'
New-Item -ItemType Directory -Force -Path $assetDir | Out-Null

$ids = [System.Collections.Generic.HashSet[string]]::new()
function Add-AssetIds([object]$value) {
    if ($null -eq $value) { return }
    if ($value -is [System.Collections.IEnumerable] -and $value -isnot [string]) {
        foreach ($item in $value) { Add-AssetIds $item }
        return
    }
    $md5 = $value.PSObject.Properties['md5ext']
    if ($md5) { [void]$ids.Add([string]$md5.Value) }
    foreach ($property in $value.PSObject.Properties) {
        if ($property.Value -is [PSObject]) { Add-AssetIds $property.Value }
    }
}

foreach ($file in @('costumes.json', 'backdrops.json', 'sounds.json', 'sprites.json')) {
    Add-AssetIds (Get-Content (Join-Path $SourceDir "src\lib\libraries\$file") -Raw | ConvertFrom-Json)
}

$assetList = @($ids | Sort-Object)
$baseUrl = 'https://cdn.assets.scratch.mit.edu/internalapi/asset'
$results = $assetList | ForEach-Object -Parallel {
    $md5ext = $_
    $destination = Join-Path $using:assetDir $md5ext
    # Scratch's asset endpoint requires the md5ext (including the data format).
    # The extensionless endpoint returns a generic placeholder image.
    $url = "$using:baseUrl/$md5ext/get/"
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            Invoke-WebRequest -Uri $url -OutFile $destination -UseBasicParsing
            return [pscustomobject]@{asset = $md5ext; status = 'downloaded'}
        } catch {
            if ($attempt -eq 3) { return [pscustomobject]@{asset = $md5ext; status = 'failed'; error = $_.Exception.Message} }
            Start-Sleep -Seconds $attempt
        }
    }
} -ThrottleLimit $ThrottleLimit

$failed = @($results | Where-Object status -eq 'failed')
if ($failed.Count -gt 0) {
    $failed | ConvertTo-Json -Depth 3 | Set-Content -Encoding UTF8 (Join-Path $BuildPath 'library-assets-failed.json')
    throw "Failed to download $($failed.Count) Scratch library assets"
}

$manifest = [ordered]@{
    source = 'https://github.com/scratchfoundation/scratch-editor'
    tag = 'v15.1.1'
    assetService = $baseUrl
    count = $assetList.Count
    assets = $assetList
}
$manifest | ConvertTo-Json -Depth 3 | Set-Content -Encoding UTF8 (Join-Path $BuildPath 'library-assets-manifest.json')
Write-Output "Scratch library asset pack ready: $($assetList.Count) assets in $assetDir"
