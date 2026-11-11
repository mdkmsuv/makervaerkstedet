[CmdletBinding()]
param([Parameter(Mandatory)][string]$ArgumentFile, [switch]$Test)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$decoded = ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($ArgumentFile))
[string[]]$Arguments = $decoded
Remove-Item -LiteralPath $ArgumentFile -ErrorAction Stop
if ($Test) { ConvertTo-Json -InputObject $Arguments -Compress; exit 0 }
Write-Host 'Elevhub administration starter Ansible i WSL Debian.' -ForegroundColor Cyan
& wsl.exe @Arguments
$code=$LASTEXITCODE
$resultColor = if ($code -eq 0) { 'Green' } else { 'Red' }
Write-Host "`nFærdig. Exitkode: $code" -ForegroundColor $resultColor
Read-Host 'Tryk Enter for at lukke terminalen'
exit $code
