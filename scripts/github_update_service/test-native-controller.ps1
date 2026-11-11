# Isolated native Windows server-VM test. Kreditering: Klostermarksskolen.
[CmdletBinding()]
param(
 [Parameter(Mandatory=$true)][string]$SourceRoot,
 [Parameter(Mandatory=$true)][string]$Python,
 [Parameter(Mandatory=$true)][string]$EvidenceRoot,
 [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{12}$')][string]$RunId,
 [ValidateSet('Run','Cleanup')][string]$Mode='Run'
)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$SourceRoot=[IO.Path]::GetFullPath($SourceRoot)
$Python=[IO.Path]::GetFullPath($Python)
$EvidenceRoot=[IO.Path]::GetFullPath($EvidenceRoot)
$fixture=[IO.Path]::GetFullPath((Join-Path $env:ProgramData ('MakervaerkstedetGitHubQA-'+$RunId)))
$controller=Join-Path $fixture 'controller'
$configPath=Join-Path $controller 'config.json'
$ownerPath=Join-Path $fixture 'fixture-owner.json'
$productionTask='Makervaerkstedet-GitHubUpdates'
$taskPrefix='Makervaerkstedet-GitHubQA-'+$RunId+'-'
$userName='MkGHQA'+$RunId.Substring(0,8)
$principal=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'The isolated fixture requires an elevated session on the chosen server VM.' }
if($EvidenceRoot.StartsWith($fixture+'\',[StringComparison]::OrdinalIgnoreCase) -or $EvidenceRoot -eq $fixture) { throw 'Evidence must be outside the removable fixture.' }
function Write-Json([string]$Path,$Value) {
 [IO.File]::WriteAllText($Path,($Value | ConvertTo-Json -Depth 15),(New-Object Text.UTF8Encoding($false)))
}
function Assert-Plain([string]$Path) {
 for($item=$Path; $item; $item=Split-Path $item -Parent) {
  if(Test-Path -LiteralPath $item) {
   if((Get-Item -LiteralPath $item -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Fixture paths must not contain links.' }
  }
 }
}
foreach($path in @($fixture,$SourceRoot,$Python,$EvidenceRoot)) { Assert-Plain $path }
function Set-FixtureAcl([string]$Path,[string[]]$Rules) {
 & icacls.exe $Path /inheritance:r /grant:r @Rules | Out-Null
 if($LASTEXITCODE -ne 0) { throw "Cannot protect fixture path: $Path" }
}
function Assert-OwnedTask($Task) {
 foreach($action in $Task.Actions) {
  if([string]$action.Execute -ne $Python -or -not ([string]$action.Arguments).Contains($fixture)) {
   throw ('Task is not owned by this fixture: '+$Task.TaskName)
  }
 }
}
function Stop-FixtureTask([string]$Name) {
 $task=Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
 if($task) {
  Assert-OwnedTask $task
  Stop-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $Name -Confirm:$false
 }
}
if($Mode -eq 'Cleanup') {
 if(-not(Test-Path -LiteralPath $ownerPath)) { throw 'Fixture ownership receipt is missing.' }
 $owner=Get-Content -LiteralPath $ownerPath -Raw | ConvertFrom-Json
 if($owner.run_id -ne $RunId -or $owner.fixture -ne $fixture -or $owner.python -ne $Python) { throw 'Fixture ownership does not match cleanup inputs.' }
 foreach($name in @($productionTask,($taskPrefix+'system'),($taskPrefix+'localservice'),($taskPrefix+'standard'))) { Stop-FixtureTask $name }
 $local=Get-LocalUser -Name $userName -ErrorAction SilentlyContinue
 if($local) {
  if(-not $owner.user_sid -or $local.SID.Value -ne $owner.user_sid) { throw 'Temporary user ownership does not match.' }
  Disable-LocalUser -SID $local.SID
  $profile=Get-CimInstance Win32_UserProfile | Where-Object SID -EQ $owner.user_sid
  if($profile) {
   if($profile.Loaded) { throw 'The temporary user profile remains loaded; retry cleanup after its task exits.' }
   $expectedProfile=[IO.Path]::GetFullPath((Join-Path (Split-Path $env:PUBLIC -Parent) $userName))
   if([IO.Path]::GetFullPath([string]$profile.LocalPath) -ne $expectedProfile) { throw 'Temporary account profile path differs; review before removing it.' }
   Assert-Plain $expectedProfile
   Remove-CimInstance -InputObject $profile
  }
  Remove-LocalUser -SID $local.SID
 }
 $remaining=@(Get-CimInstance Win32_Process | Where-Object { [string]$_.CommandLine -and ([string]$_.CommandLine).Contains($fixture) -and $_.ProcessId -ne $PID })
 if($remaining.Count) { throw 'Fixture processes remain. Preserve files and retry cleanup after task shutdown.' }
 # Deletion stays within the exact receipt-bound ProgramData fixture.
 $resolved=(Resolve-Path -LiteralPath $fixture).Path
 if($resolved -ne $fixture -or -not $resolved.StartsWith(([IO.Path]::GetFullPath($env:ProgramData).TrimEnd('\')+'\MakervaerkstedetGitHubQA-'),[StringComparison]::OrdinalIgnoreCase)) { throw 'Cleanup path escaped the fixture boundary.' }
 Assert-Plain $resolved
 Remove-Item -LiteralPath $resolved -Recurse -Force
 Write-Json (Join-Path $EvidenceRoot 'cleanup.json') ([ordered]@{run_id=$RunId;fixture_removed=(-not(Test-Path -LiteralPath $fixture));tasks_removed=$true;temporary_user_removed=$true;python_preserved=$Python;source_preserved=$SourceRoot;completed_utc=[DateTime]::UtcNow.ToString('o')})
 Write-Output 'Owned fixture tasks, account and data removed; supplied runtime/source preserved.'
 return
}
if(Test-Path -LiteralPath $fixture) { throw 'Fixture already exists; use its evidence and explicit cleanup mode.' }
if(Get-ScheduledTask -TaskName $productionTask -ErrorAction SilentlyContinue) { throw 'A real controller task already exists. This fixture refuses to replace it.' }
if(Get-LocalUser -Name $userName -ErrorAction SilentlyContinue) { throw 'Temporary fixture user name already exists.' }
if(-not(Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'The independently protected QA Python runtime is missing.' }
New-Item -ItemType Directory -Path $EvidenceRoot -Force | Out-Null
New-Item -ItemType Directory -Path $fixture | Out-Null
Set-FixtureAcl $fixture @('*S-1-5-18:(OI)(CI)F','*S-1-5-32-544:(OI)(CI)F','*S-1-5-19:(OI)(CI)RX','*S-1-5-32-545:(OI)(CI)RX')
$owner=[ordered]@{run_id=$RunId;fixture=$fixture;python=$Python;source=$SourceRoot;user_sid=$null;created_utc=[DateTime]::UtcNow.ToString('o')}
Write-Json $ownerPath $owner
$data=Join-Path $fixture 'data'
New-Item -ItemType Directory -Path $data | Out-Null
Set-FixtureAcl $data @('*S-1-5-18:(OI)(CI)F','*S-1-5-32-544:(OI)(CI)F','*S-1-5-19:(OI)(CI)M')
$profile=Join-Path $fixture 'profile.json'
Write-Json $profile ([ordered]@{schema=1;repository='example/project';data_root=$data;staging_root=(Join-Path $data 'updates\downloads');profiles=@()})
$probe=Join-Path $fixture 'native_probe.py'
Copy-Item -LiteralPath (Join-Path $SourceRoot 'scripts\github_update_service\native_probe.py') -Destination $probe
Write-Json (Join-Path $EvidenceRoot 'fixture-source-hashes.json') ([ordered]@{
 'test-native-controller.ps1'=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
 'native_probe.py'=(Get-FileHash -LiteralPath $probe -Algorithm SHA256).Hash.ToLowerInvariant()
})
$resultRoot=Join-Path $fixture 'results'
New-Item -ItemType Directory -Path $resultRoot | Out-Null
$start=[DateTime]::UtcNow
$successful=$false
Start-Transcript -Path (Join-Path $EvidenceRoot 'native-transcript.txt') -Force | Out-Null
try {
 & (Join-Path $SourceRoot 'scripts\github_update_service\Install-Controller.ps1') -SourceRoot $SourceRoot -ControllerRoot $controller -Python $Python -Profile $profile -Apply
 $task=Get-ScheduledTask -TaskName $productionTask
 Assert-OwnedTask $task
 Export-ScheduledTask -TaskName $productionTask | Set-Content -LiteralPath (Join-Path $EvidenceRoot 'controller-task.xml') -Encoding UTF8
 $random=New-Object byte[] 32
 $generator=[Security.Cryptography.RandomNumberGenerator]::Create()
 try { $generator.GetBytes($random) } finally { $generator.Dispose() }
 $password=[Convert]::ToBase64String($random)+'Aa1!'
 $securePassword=ConvertTo-SecureString $password -AsPlainText -Force
 $qaUser=New-LocalUser -Name $userName -Password $securePassword -AccountNeverExpires -PasswordNeverExpires -UserMayNotChangePassword -Description ('Temporary GitHub controller QA '+$RunId)
 Add-LocalGroupMember -SID 'S-1-5-32-545' -Member $qaUser
 $owner.user_sid=$qaUser.SID.Value
 Write-Json $ownerPath $owner
 foreach($modeName in @('system','localservice','standard')) {
  $results=Join-Path $resultRoot $modeName
  New-Item -ItemType Directory -Path $results | Out-Null
  $sid=if($modeName -eq 'system') {'S-1-5-18'} elseif($modeName -eq 'localservice') {'S-1-5-19'} else {$qaUser.SID.Value}
  Set-FixtureAcl $results @('*S-1-5-18:(OI)(CI)F','*S-1-5-32-544:(OI)(CI)F',("*"+$sid+':(OI)(CI)M'))
  $result=Join-Path $results 'result.json'
  $arguments='-I -B "'+$probe+'" --mode '+$modeName+' --fixture "'+$fixture+'" --result "'+$result+'" --expected-sid '+$sid
  $action=New-ScheduledTaskAction -Execute $Python -Argument $arguments -WorkingDirectory $fixture
  $settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
  $name=$taskPrefix+$modeName
  if($modeName -eq 'standard') {
   Register-ScheduledTask -TaskName $name -Action $action -Settings $settings -User $userName -Password $password -RunLevel Limited | Out-Null
  } else {
   $taskPrincipal=New-ScheduledTaskPrincipal -UserId $sid -LogonType ServiceAccount -RunLevel $(if($modeName -eq 'system'){'Highest'}else{'Limited'})
   Register-ScheduledTask -TaskName $name -Action $action -Settings $settings -Principal $taskPrincipal | Out-Null
  }
  Start-ScheduledTask -TaskName $name
  $deadline=[DateTime]::UtcNow.AddSeconds(90)
  do {
   if(Test-Path -LiteralPath $result) { break }
   Start-Sleep -Milliseconds 300
  } while([DateTime]::UtcNow -lt $deadline)
  Get-ScheduledTaskInfo -TaskName $name | Select-Object LastRunTime,LastTaskResult,NumberOfMissedRuns | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $EvidenceRoot ($modeName+'-task.json')) -Encoding UTF8
  if(-not(Test-Path -LiteralPath $result)) { throw "No result from $modeName token." }
  Copy-Item -LiteralPath $result -Destination (Join-Path $EvidenceRoot ($modeName+'-result.json'))
  $receipt=Get-Content -LiteralPath $result -Raw | ConvertFrom-Json
  if(-not $receipt.passed) { throw "Native $modeName checks failed; inspect preserved evidence." }
 }
 $password=$null;$securePassword=$null
 $aclEvidence=@(foreach($path in @($fixture,$data,(Join-Path $data 'updates'),$controller,$configPath,(Join-Path $data 'updates\catalog.json'),(Join-Path $data 'updates\targets.json'))) {
  [ordered]@{path=$path;sddl=(Get-Acl -LiteralPath $path).Sddl}
 })
 Write-Json (Join-Path $EvidenceRoot 'acl-sddl.json') $aclEvidence
 Copy-Item -LiteralPath (Join-Path $controller 'source-hashes.json') -Destination (Join-Path $EvidenceRoot 'installed-source-hashes.json')
 $successful=$true
} catch {
 Write-Json (Join-Path $EvidenceRoot 'failure.json') ([ordered]@{type=$_.Exception.GetType().FullName;message=$_.Exception.Message;detail=[string]$_;script_stack=$_.ScriptStackTrace})
 throw
} finally {
 $local=Get-LocalUser -Name $userName -ErrorAction SilentlyContinue
 if($local -and $owner.user_sid -eq $local.SID.Value) { Disable-LocalUser -SID $local.SID }
 $task=Get-ScheduledTask -TaskName $productionTask -ErrorAction SilentlyContinue
 if($task) { Assert-OwnedTask $task; Stop-ScheduledTask -TaskName $productionTask -ErrorAction SilentlyContinue; Disable-ScheduledTask -TaskName $productionTask | Out-Null }
 Write-Json (Join-Path $EvidenceRoot 'result.json') ([ordered]@{run_id=$RunId;status=$(if($successful){'passed'}else{'failed'});fixture=$fixture;started_utc=$start.ToString('o');completed_utc=[DateTime]::UtcNow.ToString('o');controller_task_stopped=$true;temporary_user_disabled=[bool]$local;real_installations_touched=$false;ssh_used=$false;cleanup_required=$true})
 Stop-Transcript | Out-Null
}
