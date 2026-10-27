[CmdletBinding()]
param([string]$BuildPath = 'D:\Elevhub-VM\datatraener\cctd-ml-machine-release-dr-build')

$ErrorActionPreference = 'Stop'
foreach ($relative in @('index.html', 'assets', 'ultrabit\fonts\ultrabit.ttf')) {
    if (-not (Test-Path -LiteralPath (Join-Path $BuildPath $relative))) {
        throw "Missing Datatræner build asset: $relative"
    }
}
$forbidden = Get-ChildItem -LiteralPath $BuildPath -Recurse -File |
    Where-Object { $_.Extension -in @('.js', '.css', '.html') } |
    Select-String -Pattern 'applicationinsights|InstrumentationKey=|fonts\.googleapis|gstatic|azure\.com/|d3js\.org|unpkg\.com'
if ($forbidden) { throw 'Offline smoke-test found a forbidden external analytics/CDN reference.' }

$port = 8798
$server = $null
try {
    $server = Start-Process -FilePath 'python' -ArgumentList @(
        '-m', 'http.server', "$port", '--bind', '127.0.0.1', '--directory', $BuildPath
    ) -WindowStyle Hidden -PassThru
    Start-Sleep -Milliseconds 500
    foreach ($path in @('index.html', 'assets', 'ultrabit/fonts/ultrabit.ttf')) {
        $response = Invoke-WebRequest "http://127.0.0.1:$port/$path" -Method Head -UseBasicParsing
        if ($response.StatusCode -ne 200) { throw "HTTP smoke-test failed: $path" }
    }
    Write-Output "Datatræner static smoke-test passed on loopback:$port"
}
finally {
    if ($server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
}
