[CmdletBinding()]
param(
    [string]$PackageRoot,
    [ValidateSet('single-computer','separate-computers')][string]$Deployment,
    [ValidateSet('server','laser-station')][string]$Component,
    [switch]$PlanOnly,
    [switch]$TextMode
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $PackageRoot) { $PackageRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }
if (-not $TextMode -and -not $PlanOnly -and -not $Deployment -and -not $Component) {
    & (Join-Path $PSScriptRoot 'Open-SetupWizard.ps1') -PackageRoot $PackageRoot
    return
}
if ($PlanOnly -and -not $Deployment) { throw 'PlanOnly kræver et eksplicit installationsvalg.' }
if (-not $Deployment) {
    Write-Host 'Makerværkstedet - ny installation'
    Write-Host '1: Alt på én Windows-computer (server, lærerpanel og laserstation)'
    Write-Host '2: Central server/VM og separat computer ved laseren'
    switch (Read-Host 'Vælg løsning (1/2)') {
        '1' { $Deployment = 'single-computer' }
        '2' { $Deployment = 'separate-computers' }
        default { throw 'Vælg 1 eller 2. Ingen installation er startet.' }
    }
}
if ($Deployment -eq 'single-computer') {
    if ($Component -and $Component -ne 'server') { throw 'Den samlede løsning installeres som server med lokal laserstation.' }
    $Component = 'server'
} elseif (-not $Component) {
    if ($PlanOnly) { throw 'PlanOnly kræver valg af computer i den delte løsning.' }
    Write-Host '1: Dette er den centrale Windows-server eller VM'
    Write-Host '2: Dette er lasercomputeren; den centrale server er allerede installeret'
    switch (Read-Host 'Hvilken computer installerer du nu? (1/2)') {
        '1' { $Component = 'server' }
        '2' { $Component = 'laser-station' }
        default { throw 'Vælg 1 eller 2. Ingen installation er startet.' }
    }
}
$parameters = @{PackageRoot=[IO.Path]::GetFullPath($PackageRoot)}
if ($Component -eq 'laser-station') {
    $entry = 'Setup-LaserStation.ps1'
    $parameters.Action = 'Install'
} else {
    $entry = 'Setup-School.ps1'
    $parameters.Role = if ($Deployment -eq 'single-computer') { 'makerspace' } else { 'vm' }
    if ($Deployment -eq 'separate-computers') { $parameters.RemoteLaserStation = $true }
}
if ($PlanOnly) {
    [ordered]@{deployment=$Deployment;component=$Component;entry=$entry;parameters=$parameters} | ConvertTo-Json -Depth 4 -Compress
    return
}
& (Join-Path $PSScriptRoot $entry) @parameters
