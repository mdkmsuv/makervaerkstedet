[CmdletBinding()]
param([Parameter(Mandatory)][ValidateSet('enroll','remove')][string]$Action)
$ErrorActionPreference='Stop'
$root=Join-Path $env:ProgramFiles 'ElevhubServer'
$entry=if ($Action -eq 'enroll') { Join-Path $root 'server\launchers\enroll.ps1' } else { Join-Path $root 'server\fjern.ps1' }
if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) { throw 'Installér Elevhub-serveren først.' }
$principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { & $entry; exit $LASTEXITCODE }
$path64=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($entry))
$command="& ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$path64'))); exit `$LASTEXITCODE"
$encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
# Enrollment intentionally opens an interactive name prompt and save-file dialog.
$window=if ($Action -eq 'enroll') { 'Normal' } else { 'Hidden' }
$process=Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -Verb RunAs -WindowStyle $window -Wait -PassThru -ArgumentList @('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand',$encoded)
if ($process.ExitCode -ne 0) { Write-Error 'Handlingen blev ikke gennemført. Kør serverens administrationsværktøj fra en administrator-session for detaljer.' }
exit $process.ExitCode
