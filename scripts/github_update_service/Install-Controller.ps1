# Install the independent GitHub update controller. Kreditering: Klostermarksskolen.
[CmdletBinding()]
param(
 [Parameter(Mandatory=$true)][string]$SourceRoot,
 [Parameter(Mandatory=$true)][string]$ControllerRoot,
 [Parameter(Mandatory=$true)][string]$Python,
 [Parameter(Mandatory=$true)][string]$Profile,
 [switch]$Apply
)
$ErrorActionPreference='Stop'
$SourceRoot=[IO.Path]::GetFullPath($SourceRoot)
$ControllerRoot=[IO.Path]::GetFullPath($ControllerRoot)
$Python=[IO.Path]::GetFullPath($Python)
$Profile=[IO.Path]::GetFullPath($Profile)
foreach($path in @($SourceRoot,$ControllerRoot,$Python,$Profile)) {
 for($item=$path; $item; $item=Split-Path $item -Parent) {
  if(Test-Path -LiteralPath $item) {
   if((Get-Item -LiteralPath $item -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Controller inputs must not pass through links or reparse points.' }
  }
 }
}
if(Test-Path -LiteralPath $ControllerRoot) { throw 'Choose a new controller directory; existing controllers are not overwritten.' }
if(-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'An independently installed Python runtime is required.' }
function Assert-SystemRuntime([string]$Executable) {
 $programFiles=[IO.Path]::GetFullPath($env:ProgramFiles).TrimEnd('\')+'\'
 if(-not $Executable.StartsWith($programFiles,[StringComparison]::OrdinalIgnoreCase)) {
  throw 'The SYSTEM controller requires an administrator-managed Python runtime under Program Files. User-installed Python is not supported.'
 }
 $trustedInstaller=(New-Object Security.Principal.NTAccount('NT SERVICE','TrustedInstaller')).Translate([Security.Principal.SecurityIdentifier]).Value
 $allowed=@('S-1-5-18','S-1-5-32-544',$trustedInstaller)
 $runtime=Split-Path $Executable -Parent
 $items=@(Get-Item -LiteralPath $runtime)+@(Get-ChildItem -LiteralPath $runtime -Recurse -Force)
 $writeMask=[Security.AccessControl.FileSystemRights]::Write -bor [Security.AccessControl.FileSystemRights]::Delete -bor [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor [Security.AccessControl.FileSystemRights]::ChangePermissions -bor [Security.AccessControl.FileSystemRights]::TakeOwnership
 foreach($item in $items) {
  if($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Runtime links and reparse points are not accepted.' }
  foreach($rule in (Get-Acl -LiteralPath $item.FullName).Access) {
   if($rule.AccessControlType -eq 'Allow' -and -not ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and ($rule.FileSystemRights -band $writeMask)) {
    $sid=$rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
    if($allowed -notcontains $sid) { throw 'The Python runtime is writable outside SYSTEM/Administrators/TrustedInstaller.' }
   }
  }
 }
 for($parent=Split-Path $runtime -Parent; $parent; $parent=Split-Path $parent -Parent) {
  foreach($rule in (Get-Acl -LiteralPath $parent).Access) {
   if($rule.AccessControlType -eq 'Allow' -and -not ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles)) {
    $sid=$rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
    if($allowed -notcontains $sid) { throw 'A Python runtime ancestor can be replaced by an untrusted account.' }
   }
  }
 }
}
Assert-SystemRuntime $Python
foreach($root in @('C:\Program Files\ElevhubServer\','C:\Program Files\Printvaerksted\')) {
 if($Python.StartsWith($root,[StringComparison]::OrdinalIgnoreCase) -or $ControllerRoot.StartsWith($root,[StringComparison]::OrdinalIgnoreCase)) {
  throw 'The controller and Python runtime must be outside the installations they update.'
 }
}
$configuration=Get-Content -LiteralPath $Profile -Raw | ConvertFrom-Json
if($configuration.schema -ne 1 -or -not $configuration.repository -or -not $configuration.data_root -or -not $configuration.staging_root) {
 throw 'The profile requires schema 1, repository, data_root, staging_root and locally reviewed profiles.'
}
foreach($path in @([string]$configuration.data_root,[string]$configuration.staging_root)) {
 if(-not [IO.Path]::IsPathRooted($path) -or [IO.Path]::GetFullPath($path) -ne $path) { throw 'Data and staging paths must be normalized absolute paths.' }
 for($item=$path; $item; $item=Split-Path $item -Parent) {
  if(Test-Path -LiteralPath $item) {
   if((Get-Item -LiteralPath $item -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Data/staging paths must not pass through links.' }
  }
 }
}
if(-not (Test-Path -LiteralPath $configuration.data_root -PathType Container)) { throw 'The existing hub data directory must exist; this is not a clean installation.' }
$profileValidation="import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from runtime import validate_profile,protected;v=json.load(open(sys.argv[2],encoding='utf-8-sig'));[validate_profile(p) for p in v['profiles']];[(protected(p['ssh_key']),protected(Path(p['ssh_key']).parent),protected(Path(p['known_hosts']).parent),protected(p['known_hosts']) if Path(p['known_hosts']).exists() else None) for p in v['profiles'] if p['role']!='windows_server']"
& $Python -I -B -c $profileValidation (Join-Path $SourceRoot 'scripts/github_update_service') $Profile
if($LASTEXITCODE -ne 0) { throw 'Local target profile validation failed before installation.' }
$files=@(
 'scripts/github_update_service/__init__.py',
 'scripts/github_update_service/common.py',
 'scripts/github_update_service/runtime.py',
 'scripts/github_update_service/linux.py',
 'scripts/github_update_service/server.py',
 'scripts/school_server_update/update.py',
 'scripts/school_server_update/Invoke-SchoolUpdate.ps1',
 'scripts/school_server_update/Repair-SchoolRuntimeAccess.ps1',
 'scripts/queued_laser_update/queue_update.py',
 'scripts/queued_laser_update/remote_runner.py',
 'scripts/github_update_service/templates/linux_install.py',
 'scripts/github_update_service/templates/linux_remote.py',
 'linux-elevhub/server/elevhub/update_admin.py',
 'linux-elevhub/server/elevhub/device_updates.py',
 'linux-elevhub/server/elevhub/release_feed.py',
 'linux-elevhub/server/elevhub/release_import.py',
 'CREDITS.md'
)
$hashes=[ordered]@{}
foreach($file in $files) {
 $path=Join-Path $SourceRoot $file
 if(-not(Test-Path -LiteralPath $path -PathType Leaf)) { throw "Controller source missing: $file" }
 $hashes[$file]=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
}
if(-not $Apply) {
 [ordered]@{status='reviewed_plan';controller=$ControllerRoot;python=$Python;files=$hashes;scheduled_task='Makervaerkstedet-GitHubUpdates'} | ConvertTo-Json -Depth 5
 return
}
$principal=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator privileges are required for the independent SYSTEM controller.' }
if(Get-ScheduledTask -TaskName 'Makervaerkstedet-GitHubUpdates' -ErrorAction SilentlyContinue) { throw 'The controller task already exists. Preserve its active jobs and profile.' }
New-Item -ItemType Directory -Path $ControllerRoot | Out-Null
& icacls.exe $ControllerRoot /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if($LASTEXITCODE -ne 0) { throw 'Cannot protect controller directory.' }
$code=Join-Path $ControllerRoot 'code'
foreach($file in $files) {
 $destination=Join-Path $code $file
 New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
 Copy-Item -LiteralPath (Join-Path $SourceRoot $file) -Destination $destination
 if((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $hashes[$file]) { throw 'Controller source changed during preparation.' }
}
$configuration | Add-Member -Force NoteProperty code_root $code
$configuration | Add-Member -Force NoteProperty work_root (Join-Path $ControllerRoot 'workers')
$configuration | Add-Member -Force NoteProperty store_root (Join-Path $ControllerRoot 'store')
foreach($dir in @($configuration.work_root,$configuration.store_root)) { New-Item -ItemType Directory -Path $dir | Out-Null }
# Keep the shared directory stable while allowing LocalService to create
# SQLite journals and download subdirectories. Its immutable catalog files get
# protected read-only DACLs atomically from runtime.write_public_config().
$dataRoot=[IO.Path]::GetFullPath([string]$configuration.data_root)
$updates=Join-Path $dataRoot 'updates'
New-Item -ItemType Directory -Path $updates -Force | Out-Null
$localService=New-Object Security.Principal.SecurityIdentifier('S-1-5-19')
$dataAcl=Get-Acl -LiteralPath $dataRoot
$deny=New-Object Security.AccessControl.FileSystemAccessRule($localService,([Security.AccessControl.FileSystemRights]::Delete -bor [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles),'Deny')
$dataAcl.AddAccessRule($deny)
Set-Acl -LiteralPath $dataRoot -AclObject $dataAcl
$updatesAcl=New-Object Security.AccessControl.DirectorySecurity
$updatesAcl.SetAccessRuleProtection($true,$false)
foreach($sid in @('S-1-5-18','S-1-5-32-544')) {
 $identity=New-Object Security.Principal.SecurityIdentifier($sid)
 $updatesAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($identity,'FullControl','ContainerInherit,ObjectInherit','None','Allow')))
}
$updatesAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($localService,([Security.AccessControl.FileSystemRights]::ReadAndExecute -bor [Security.AccessControl.FileSystemRights]::Write),'Allow')))
$updatesAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($localService,'Modify','ContainerInherit,ObjectInherit','InheritOnly','Allow')))
Set-Acl -LiteralPath $updates -AclObject $updatesAcl
foreach($name in @('catalog.json','targets.json')) {
 $metadata=Join-Path $updates $name
 if(Test-Path -LiteralPath $metadata) {
  & icacls.exe $metadata /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' '*S-1-5-19:R' | Out-Null
  if($LASTEXITCODE -ne 0) { throw 'Cannot protect update catalog metadata.' }
 }
}
$configPath=Join-Path $ControllerRoot 'config.json'
[IO.File]::WriteAllText($configPath,($configuration | ConvertTo-Json -Depth 15),(New-Object Text.UTF8Encoding($false)))
[IO.File]::WriteAllText((Join-Path $ControllerRoot 'source-hashes.json'),($hashes | ConvertTo-Json -Depth 4),(New-Object Text.UTF8Encoding($false)))
$script=Join-Path $code 'scripts\github_update_service\runtime.py'
# Validate without touching the network or executing an update.
$validation="import sys;sys.path.insert(0,sys.argv[1]);from runtime import Controller;Controller(sys.argv[2])"
& $Python -I -B -c $validation (Split-Path $script -Parent) $configPath
if($LASTEXITCODE -ne 0) { throw 'Controller profile validation failed; no task was registered.' }
$arguments='-I -B "'+$script+'" --config "'+$configPath+'"'
$action=New-ScheduledTaskAction -Execute $Python -Argument $arguments -WorkingDirectory $ControllerRoot
$trigger=@((New-ScheduledTaskTrigger -AtStartup),(New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Minutes 5)))
$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'Makervaerkstedet-GitHubUpdates' -Action $action -Trigger $trigger -Settings $settings -User 'SYSTEM' -RunLevel Highest | Out-Null
Start-ScheduledTask -TaskName 'Makervaerkstedet-GitHubUpdates'
Write-Output 'The independent controller is installed. It imports verified selected downloads; installation still requires the teacher queue.'
