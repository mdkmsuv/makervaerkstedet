[CmdletBinding()]
param(
    [string]$Computer,
    [string]$OutputFile,
    [string]$InstallRoot = "$env:ProgramFiles\ElevhubServer",
    [string]$DataRoot = "$env:ProgramData\ElevhubServer"
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Kør tilmeldingen som administrator.' }
$InstallRoot=[IO.Path]::GetFullPath($InstallRoot); $DataRoot=[IO.Path]::GetFullPath($DataRoot)
if ($InstallRoot -ne [IO.Path]::GetFullPath("$env:ProgramFiles\ElevhubServer") -or $DataRoot -ne [IO.Path]::GetFullPath("$env:ProgramData\ElevhubServer")) { throw 'Tilmelding bruger kun Elevhubs faste installation.' }
$markerPath=Join-Path $DataRoot 'installation-owner.json'
if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) { throw 'ElevhubServer er ikke installeret.' }
$marker=Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
if ($marker.product -ne 'ElevhubServer' -or $marker.state -ne 'installed' -or $marker.installRoot -ne $InstallRoot) { throw 'Serverens ejerskabsmarkør er ikke aktiv.' }
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'generation-startup.ps1')
if ((Get-InstallationStartupMode -Owner $marker -InstallRoot $InstallRoot) -eq 'generation-v1') {
    throw 'Denne ældre USB-tilmelding understøtter ikke generationsvalg. Brug Elevhub skoleadmin til skolens tilmelding.'
}

if ([string]::IsNullOrWhiteSpace($Computer)) { $Computer=(Read-Host 'Navn til elevcomputeren (små bogstaver, tal og bindestreg)').Trim().ToLowerInvariant() }
if ($Computer -notmatch '^[a-z0-9][a-z0-9-]{0,31}$') { throw 'Navnet skal være 1-32 små bogstaver, tal eller bindestreger.' }
if ([string]::IsNullOrWhiteSpace($OutputFile)) {
    Add-Type -AssemblyName System.Windows.Forms
    $dialog=[Windows.Forms.SaveFileDialog]::new()
    $dialog.Title='Gem Elevhub-tilmeldingsfilen på administratorens USB-nøgle'
    $dialog.Filter='Elevhub tilmelding (*.json)|*.json'
    $dialog.FileName="$Computer-elevhub.json"
    if ($dialog.ShowDialog() -ne [Windows.Forms.DialogResult]::OK) { Write-Output 'Tilmeldingen blev annulleret.'; exit 0 }
    $OutputFile=$dialog.FileName
}
$OutputFile=[IO.Path]::GetFullPath($OutputFile)
if (Test-Path -LiteralPath $OutputFile) { throw 'Filen findes allerede og overskrives ikke.' }
$parent=Split-Path -Parent $OutputFile
if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw 'Mappen til tilmeldingsfilen findes ikke.' }
$cursor=Get-Item -LiteralPath $parent -Force
while ($null -ne $cursor) { if (($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Tilmeldingsmålet indeholder et reparse point: $($cursor.FullName)" }; $cursor=$cursor.Parent }

$python=Join-Path $InstallRoot 'runtime\python.exe'; $provision=Join-Path $InstallRoot 'server\provision.py'
$registry=Join-Path $DataRoot 'security\enheder.json'; $ca=Join-Path $DataRoot 'security\ca.crt'
foreach ($path in @($python,$provision,$registry,$ca)) { if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Serverfil mangler: $path" } }
$runtimeConfig=Join-Path $DataRoot 'konfiguration.json'
if (-not (Test-Path -LiteralPath $runtimeConfig -PathType Leaf)) { throw 'Serverens runtime-konfiguration mangler.' }
$config=Get-Content -LiteralPath $runtimeConfig -Raw | ConvertFrom-Json
if ([string]::IsNullOrWhiteSpace([string]$config.elev_adresse) -or [int]$config.hub_port -lt 1) { throw 'Serverens runtime-konfiguration mangler en gyldig adresse.' }
$serverUrl="https://$($config.elev_adresse):$([int]$config.hub_port)"
& $python -B $provision enroll --registry $registry --name $Computer --server $serverUrl --ca-certificate $ca --output $OutputFile --write | Out-Null
if ($LASTEXITCODE -ne 0) { if (Test-Path -LiteralPath $OutputFile -PathType Leaf) { Remove-Item -LiteralPath $OutputFile -Force }; throw 'Tilmeldingsfilen kunne ikke oprettes.' }
if (-not (Test-Path -LiteralPath $OutputFile -PathType Leaf)) { throw 'Tilmeldingsfilen kunne ikke oprettes.' }
$result=Get-Content -LiteralPath $OutputFile -Raw | ConvertFrom-Json
if ($result.schemaVersion -ne 1 -or $result.server -ne $serverUrl -or $result.computer -ne $Computer -or [string]::IsNullOrWhiteSpace($result.token) -or [string]::IsNullOrWhiteSpace($result.ui_token)) { Remove-Item -LiteralPath $OutputFile -Force; throw 'Den oprettede tilmeldingsfil var ugyldig og er slettet.' }
Write-Output "Tilmeldingsfilen er gemt i $OutputFile. Den indeholder adgangsnøgler og skal opbevares hos administratoren."
