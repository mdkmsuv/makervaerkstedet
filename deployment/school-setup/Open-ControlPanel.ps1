[CmdletBinding()]
param(
    [switch]$ResolveOnly,
    [ValidateRange(1,65535)][int]$Port,
    [string]$Address = '127.0.0.1'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-PanelAddress([string]$Value) {
    $ip = $null
    if (-not [Net.IPAddress]::TryParse($Value, [ref]$ip) -or $ip.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or
        $ip.GetAddressBytes()[0] -eq 0 -or $ip.GetAddressBytes()[0] -ge 224) { throw 'Kontrolpanelets adresse skal være en konkret IPv4-adresse.' }
    return $ip.ToString()
}
if ($PSBoundParameters.ContainsKey('Port')) {
    $url = 'https://' + (Assert-PanelAddress $Address) + ':' + $Port + '/kontrolpanel'
    if ($ResolveOnly) { Write-Output $url } else { Start-Process $url }
    return
}

$dataRoot = [IO.Path]::GetFullPath((Join-Path $env:ProgramData 'ElevhubServer'))
$installRoot = [IO.Path]::GetFullPath((Join-Path $env:ProgramFiles 'ElevhubServer'))
$ownerPath = Join-Path $dataRoot 'installation-owner.json'
$cursor = $ownerPath
while ($cursor) {
    if (Test-Path -LiteralPath $cursor) {
        if (((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Installationsstien indeholder et reparse point.' }
    }
    $cursor = Split-Path -Parent $cursor
}
$owner = Get-Content -LiteralPath $ownerPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($owner.schemaVersion -ne 1 -or $owner.product -ne 'ElevhubServer' -or $owner.state -ne 'installed' -or
    [IO.Path]::GetFullPath([string]$owner.installRoot) -ine $installRoot -or [IO.Path]::GetFullPath([string]$owner.dataRoot) -ine $dataRoot) { throw 'Kontrolpanelets installationsejerskab er ugyldigt.' }
$Address = Assert-PanelAddress ([string]$owner.teacherAddress)
if ($Address -ne '127.0.0.1' -and (-not ($owner.PSObject.Properties.Name -contains 'roleIp') -or $Address -ne [string]$owner.roleIp)) { throw 'Kontrolpanelets adresse tilhører ikke denne server.' }
$port = [int]$owner.teacherPort
if ($port -lt 1 -or $port -gt 65535) { throw 'Kontrolpanelets serverport er ugyldig.' }
$url = 'https://' + $Address + ':' + $port + '/kontrolpanel'
if ($ResolveOnly) { Write-Output $url } else { Start-Process $url }
