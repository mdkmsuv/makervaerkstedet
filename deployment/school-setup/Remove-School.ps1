[CmdletBinding(SupportsShouldProcess, ConfirmImpact='High')]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    $parameters = @{}
    foreach ($entry in $PSBoundParameters.GetEnumerator()) { $parameters[$entry.Key] = [bool]$entry.Value }
    $payload = @{script=$PSCommandPath;parameters=$parameters} | ConvertTo-Json -Compress
    $blob = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($payload))
    $command = '$p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(''' + $blob + ''')) | ConvertFrom-Json; $a=@{}; foreach($x in $p.parameters.PSObject.Properties){$a[$x.Name]=$x.Value}; & $p.script @a'
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $elevated = Start-Process powershell.exe -Verb RunAs -ArgumentList @('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand',$encoded) -Wait -PassThru
    exit $elevated.ExitCode
}
$installRoot = [IO.Path]::GetFullPath((Join-Path $env:ProgramFiles 'ElevhubServer'))
$dataRoot = [IO.Path]::GetFullPath((Join-Path $env:ProgramData 'ElevhubServer'))
$remover = Join-Path $installRoot 'server\fjern.ps1'
if (-not (Test-Path -LiteralPath $remover -PathType Leaf)) { throw 'Den installerede, ejerskabskontrollerede fjerner mangler. Intet er fjernet.' }
$cursor = $remover
while ($cursor) {
    if (Test-Path -LiteralPath $cursor) {
        if (((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Fjernelse afviser reparse point i installationsstien.' }
    }
    $cursor = Split-Path -Parent $cursor
}
$arguments = @{InstallRoot=$installRoot;DataRoot=$dataRoot}
foreach ($entry in $PSBoundParameters.GetEnumerator()) { $arguments[$entry.Key] = $entry.Value }
# The shared remover owns the full preflight and the final ShouldProcess prompt.
# It retains pupil files, configuration, keys and all school data.
& $remover @arguments
