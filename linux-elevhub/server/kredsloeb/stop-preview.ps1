[CmdletBinding()]
param([int]$Port = 8812)
$ErrorActionPreference = 'Stop'
$listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $listeners) { Write-Output "Ingen Kredsløb-preview lytter på port $Port."; exit 0 }
$pids = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($pidValue in $pids) {
  $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
  if ($process -and $process.Path -match 'python(\.exe)?$') { Stop-Process -Id $pidValue -Force; Write-Output "Stoppede Python-preview (PID $pidValue)." }
  else { throw "Port $Port bruges af en anden proces (PID $pidValue); den blev ikke stoppet." }
}
