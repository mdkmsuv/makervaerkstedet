[CmdletBinding()]
param(
  [int]$Port = 8812,
  [switch]$Open
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '.')).Path
$index = Join-Path $root 'index.html'
if (-not (Test-Path -LiteralPath $index)) { throw "Kredsløbsværkstedet mangler: $index" }
$probe = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($probe) { throw "Port $Port er allerede i brug. Luk den eksisterende preview eller vælg -Port." }
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw 'Python blev ikke fundet. Installer Python 3 eller brug serverens Python.' }
$process = Start-Process -FilePath $python.Source -WorkingDirectory $root -WindowStyle Hidden -PassThru `
  -ArgumentList @('-m','http.server', [string]$Port, '--bind','127.0.0.1')
Start-Sleep -Milliseconds 350
try {
  $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 3
  if ($response.StatusCode -ne 200) { throw "Preview svarede med HTTP $($response.StatusCode)." }
} catch {
  Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
  throw
}
$url = "http://127.0.0.1:$Port/"
Write-Output "Kredsløbsværkstedet kører lokalt: $url"
Write-Output "Proces-ID: $($process.Id)"
if ($Open) { Start-Process $url | Out-Null }
