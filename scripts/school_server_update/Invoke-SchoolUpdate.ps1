# Existing school server update. Credits: Klostermarksskolen.
[CmdletBinding()]
param(
 [Parameter(Mandatory=$true)][string]$Candidate,
 [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{64}$')][string]$Sha256,
 [Parameter(Mandatory=$true)][string]$Journal,
 [switch]$Apply
)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$runner=Join-Path $PSScriptRoot 'update.py'
$Candidate=[IO.Path]::GetFullPath($Candidate)
$Journal=[IO.Path]::GetFullPath($Journal)
$python=Join-Path $Candidate 'hub\runtime\python.exe'
function Invoke-CheckedPython([string[]]$Arguments) {
 $previousPreference=$ErrorActionPreference
 try {
  # Windows PowerShell treats each native stderr line as an ErrorRecord.
  # Continue draining both streams before testing exit status so the original
  # traceback cannot be replaced by its first NativeCommandError line.
  $ErrorActionPreference='Continue'
  $output=@(& $python -B $runner @Arguments 2>&1)
  $nativeExit=$LASTEXITCODE
 } finally { $ErrorActionPreference=$previousPreference }
 foreach($line in $output) { Write-Output ([string]$line) }
 if ($nativeExit -ne 0) { throw ("School update Python failed ($nativeExit).`n"+(($output | ForEach-Object {[string]$_}) -join "`n")) }
}
Invoke-CheckedPython -Arguments @('verify','--candidate',$Candidate,'--sha256',$Sha256)
if (-not $Apply) { Write-Output 'Verified candidate only. Add -Apply for the authorized existing installation update.'; return }
# The native Python runtime is longPathAware, but Windows also requires the
# machine LongPathsEnabled DWORD. Check it before task snapshots or service stops.
Invoke-CheckedPython -Arguments @('long-path-preflight')
if (Test-Path -LiteralPath $Journal) { throw 'Journal already exists; inspect the previous operation. This command is not a resume/restore entry.' }
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$principal=New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run from an elevated administrator session.' }
$lease=New-Object Threading.Mutex($false,'Global\Makervaerkstedet-SchoolFlatUpdate')
$leased=$false
try {
 try { $leased=$lease.WaitOne(0) } catch [Threading.AbandonedMutexException] { $leased=$true }
 if(-not $leased) { throw 'Another school update owns the transaction. No task state was changed.' }
$roots=@('C:\Program Files\ElevhubServer\runtime\','C:\Program Files\Printvaerksted\runtime\')
function Get-OwnedTasks {
 @(Get-ScheduledTask | Where-Object {
  $owned=$false
  foreach($action in $_.Actions) {
   foreach($root in $roots) { if ([string]$action.Execute -and $action.Execute.StartsWith($root,[StringComparison]::OrdinalIgnoreCase)) { $owned=$true } }
   if ($action.Execute -eq 'C:\Program Files\PrintvaerkstedWeb\caddy.exe') { $owned=$true }
  }
  $owned
 })
}
$tasks=Get-OwnedTasks
$names=@($tasks | ForEach-Object {$_.TaskPath+$_.TaskName} | Sort-Object)
foreach($required in @('\ElevhubServer','\Printvaerksted','\PrintvaerkstedWeb','\Printvaerksted-Kameralys','\Printvaerksted-Recovery')) { if ($names -notcontains $required) { throw "Required existing task missing: $required" } }
Invoke-CheckedPython -Arguments @('idle')
$configPath='C:\ProgramData\ElevhubServer\konfiguration.json'
$configAcl=Get-Acl -LiteralPath $configPath
$parent=Split-Path $Journal -Parent
New-Item -ItemType Directory -Path $parent -Force | Out-Null
$taskBackup=$Journal+'.tasks'
New-Item -ItemType Directory -Path $taskBackup | Out-Null
# Snapshot includes task XML, ownership/action and initial enabled/running state.
$state=@($tasks | ForEach-Object {
 $xml=Export-ScheduledTask -TaskName $_.TaskName -TaskPath $_.TaskPath
 $filename=([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($_.TaskPath+$_.TaskName))).Replace('/','_')+'.xml'
 [IO.File]::WriteAllText((Join-Path $taskBackup $filename),$xml,(New-Object Text.UTF8Encoding($false)))
 [ordered]@{name=$_.TaskName;path=$_.TaskPath;enabled=[bool]$_.Settings.Enabled;running=($_.State -eq 'Running');xml_sha256=(Get-FileHash (Join-Path $taskBackup $filename) -Algorithm SHA256).Hash.ToLower();xml=$filename}
})
[IO.File]::WriteAllText((Join-Path $taskBackup 'states.json'),(ConvertTo-Json -InputObject $state -Depth 5),(New-Object Text.UTF8Encoding($false)))
# Preserve identities, queues and key material in administrator-only rollback data.
& icacls.exe $taskBackup /inheritance:r /grant:r "$($identity.Name):(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot protect task snapshot ACL.' }
function Restore-TaskStates {
 foreach($item in $state) {
  if ($item.enabled) { Enable-ScheduledTask -TaskName $item.name -TaskPath $item.path | Out-Null }
  else { Disable-ScheduledTask -TaskName $item.name -TaskPath $item.path | Out-Null }
 }
 foreach($item in $state) { if($item.running -and $item.enabled) { Start-ScheduledTask -TaskName $item.name -TaskPath $item.path } }
}
$applied=$false
try {
 $now=@(Get-OwnedTasks | ForEach-Object {$_.TaskPath+$_.TaskName} | Sort-Object)
 if (Compare-Object $names $now) { throw 'Task ownership changed during preflight.' }
 foreach($item in $state) {
  $currentXml=Export-ScheduledTask -TaskName $item.name -TaskPath $item.path
  $savedXml=[IO.File]::ReadAllText((Join-Path $taskBackup $item.xml))
  if($currentXml -ne $savedXml) { throw 'Task definition changed during preflight.' }
 }
 foreach($item in $state) { Disable-ScheduledTask -TaskName $item.name -TaskPath $item.path | Out-Null }
 foreach($item in $state) { Stop-ScheduledTask -TaskName $item.name -TaskPath $item.path }
 $deadline=[DateTime]::UtcNow.AddSeconds(25)
 do {
  $remaining=@(Get-CimInstance Win32_Process | Where-Object { $exe=[string]$_.ExecutablePath; @($roots | Where-Object {$exe.StartsWith($_,[StringComparison]::OrdinalIgnoreCase)}).Count -gt 0 })
  if ($remaining.Count -eq 0) { break }
  Start-Sleep -Milliseconds 300
 } while ([DateTime]::UtcNow -lt $deadline)
 if ($remaining.Count -gt 0) { throw 'Runtime processes remain after Task Scheduler stop. No process is killed by name.' }
 # Journal inherits restricted ACL from protected staging parent; assert it now.
 & icacls.exe $parent /inheritance:r /grant:r "$($identity.Name):(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
 if ($LASTEXITCODE -ne 0) { throw 'Cannot protect update snapshot ACL.' }
 Invoke-CheckedPython -Arguments @('apply','--candidate',$Candidate,'--sha256',$Sha256,'--journal',$Journal)
 $applied=$true
 # The legacy data root intentionally gives LocalService directory RX only.
 # Locks created by SYSTEM during the update need a narrow file-level grant;
 # matching protocol metadata must exist before the LocalService supervisor.
 & (Join-Path $PSScriptRoot 'Repair-SchoolRuntimeAccess.ps1') -Receipt ($Journal+'.runtime-access.json') -Apply
 Set-Acl -LiteralPath $configPath -AclObject $configAcl
 # Both backends run as LocalService. Internal bridge keys must be readable
 # there, while interactive standard users cannot read their bearer tokens.
 foreach($key in @('C:\ProgramData\Printvaerksted\data\school\makerspace-bridge.key','C:\ProgramData\ElevhubServer\designvaerksted\makerspace-laser.key')) {
  $keyAcl=New-Object Security.AccessControl.FileSecurity
  $keyAcl.SetAccessRuleProtection($true,$false)
  foreach($entry in @(@('S-1-5-18','FullControl'),@('S-1-5-19','Read'),@('S-1-5-32-544','FullControl'))) {
   $sid=New-Object Security.Principal.SecurityIdentifier($entry[0])
   $rule=New-Object Security.AccessControl.FileSystemAccessRule($sid,$entry[1],'Allow')
   $keyAcl.AddAccessRule($rule)
  }
  if(-not (Test-Path -LiteralPath $key)) {
   $bytes=New-Object byte[] 32
   $rng=[Security.Cryptography.RandomNumberGenerator]::Create()
   try {$rng.GetBytes($bytes)} finally {$rng.Dispose()}
   $token='mkbridge_'+[Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
   $secureTemp=Join-Path $taskBackup ('bridge-'+[Guid]::NewGuid().ToString('N')+'.key')
   [IO.File]::WriteAllText($secureTemp,$token,(New-Object Text.ASCIIEncoding))
   Set-Acl -LiteralPath $secureTemp -AclObject $keyAcl
   [IO.File]::Move($secureTemp,$key)
  }
  Set-Acl -LiteralPath $key -AclObject $keyAcl
 }
 Restore-TaskStates
 $deadline=[DateTime]::UtcNow.AddSeconds(35)
 $healthy=$false
 $installedConfig=Get-Content -LiteralPath 'C:\ProgramData\ElevhubServer\konfiguration.json' -Raw | ConvertFrom-Json
 do {
  try {
   foreach($port in @(8765,8766,8767,8773,8780)) {
    $address=if($port -eq 8773) { [string]$installedConfig.elev_adresse } else { '127.0.0.1' }
    $client=New-Object Net.Sockets.TcpClient
    try { $connect=$client.ConnectAsync($address,$port); if(-not $connect.Wait(800) -or -not $client.Connected) { throw "Port $port unavailable" } }
    finally { $client.Dispose() }
   }
   $healthy=$true;break
  } catch { Start-Sleep -Milliseconds 500 }
 } while([DateTime]::UtcNow -lt $deadline)
 if(-not $healthy) { throw 'Updated program failed health check. Snapshot and current data are retained; investigate before restoring any database.' }
 $final=@(Get-OwnedTasks | ForEach-Object {[ordered]@{name=$_.TaskName;path=$_.TaskPath;enabled=$_.Settings.Enabled;state=[string]$_.State}})
 [IO.File]::WriteAllText((Join-Path $taskBackup 'after.json'),(ConvertTo-Json -InputObject $final -Depth 5),(New-Object Text.UTF8Encoding($false)))
 Invoke-CheckedPython -Arguments @('finish','--candidate',$Candidate,'--sha256',$Sha256,'--journal',$Journal)
 Write-Output 'Updated existing installation; all original rows verified. Print queue remains PAUSED for teacher review. Laser has no automatic machine start.'
} catch {
 Set-Acl -LiteralPath $configPath -AclObject $configAcl
 if(-not $applied) {
  $journalFile=Join-Path $Journal 'journal.json'
  $mayRestart=$true
  if(Test-Path -LiteralPath $journalFile) {
   $journalState=(Get-Content -LiteralPath $journalFile -Raw | ConvertFrom-Json).state
   if($journalState -eq 'prepared') { $mayRestart=$false }
  }
  if($mayRestart) { Restore-TaskStates }
  else { Write-Warning 'Rollback did not finish. Original tasks remain disabled while the preserved error is investigated.' }
 }
 else { Write-Warning 'Program was applied. Automatic database rollback after service start is refused to protect newly received submissions. Inspect the preserved journal.' }
 throw
}
} finally {
 if($leased) { $lease.ReleaseMutex() }
 $lease.Dispose()
}

# The helper acquires the same update mutex, so run it only after releasing our
# completed transaction. Failure leaves the healthy update and its data intact.
try {
 $restartSetup='C:\Program Files\ElevhubServer\server\configure-restart.ps1'
 if(Test-Path -LiteralPath $restartSetup -PathType Leaf){& $restartSetup -Mode Enable}
} catch {
 Write-Warning ('Serveren er opdateret, men genstartsvalget kræver eftersyn. Brug skoleadministrationens menu 13. '+$_.Exception.Message)
}

