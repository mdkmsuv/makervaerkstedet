[CmdletBinding()]
param(
    [string]$BuildPath = 'D:\Elevhub-VM\scratch\scratch-gui-v15.1.1-build'
)

$ErrorActionPreference = 'Stop'
$required = @(
    'index.html',
    'guistandalone.js',
    'static',
    'chunks',
    'static\favicon.ico',
    'static\library-assets\809d9b47347a6af2860e7a3a35bce057.svg'
)
foreach ($relative in $required) {
    if (-not (Test-Path (Join-Path $BuildPath $relative))) {
        throw "Missing Scratch build asset: $relative"
    }
}

$port = 8799
try {
    $server = Start-Process -FilePath 'python' -ArgumentList @(
        '-m', 'http.server', "$port", '--bind', '127.0.0.1', '--directory', $BuildPath
    ) -WindowStyle Hidden -PassThru
    Start-Sleep -Milliseconds 500
    foreach ($path in @('index.html', 'guistandalone.js', 'static/library-assets/809d9b47347a6af2860e7a3a35bce057.svg', 'static/favicon.ico')) {
        $response = Invoke-WebRequest "http://127.0.0.1:$port/$path" -Method Head -UseBasicParsing
        if ($response.StatusCode -ne 200) { throw "HTTP smoke-test failed: $path" }
    }
    Write-Output "Scratch static smoke-test passed on loopback:$port"
}
finally {
    if ($server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
}
