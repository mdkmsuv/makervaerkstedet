param([string]$Source,[string]$FixtureRoot,[string]$Mode,[string]$Scenario)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput([IO.File]::ReadAllText($Source),[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors|Out-String)}
# Load production functions only. The script's real main is never invoked.
foreach($function in @($ast.EndBlock.Statements|Where-Object {$_ -is [Management.Automation.Language.FunctionDefinitionAst]})){Invoke-Expression $function.Extent.Text}
if($Scenario -like 'acl-*' -and $Scenario -ne 'acl-reordered-task'){
    $global:restartAcl=[Security.AccessControl.DirectorySecurity]::new()
    $global:restartAcl.SetSecurityDescriptorSddlForm('O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FRFX;;;LS)')
    if($Scenario -ne 'acl-read'){
        $right=@{'acl-write'='Write';'acl-delete'='Delete';'acl-dacl'='ChangePermissions';'acl-owner'='TakeOwnership';'acl-child-delete'='DeleteSubdirectoriesAndFiles'}[$Scenario]
        $global:restartAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-19'),[Security.AccessControl.FileSystemRights]$right,'Allow'))
    }
    function Get-Acl {param([string]$LiteralPath);return $global:restartAcl}
    $failure=$null
    try {Assert-RestartProtected $FixtureRoot}catch{$failure=$_.Exception.Message}
    @{result=@{allowed=($null -eq $failure)};error=$failure;events=@();shutdown_args=@()}|ConvertTo-Json -Compress
    exit 0
}
$global:restartEvents=[Collections.Generic.List[string]]::new()
$global:restartShutdownArgs=@()
$global:restartPaths=[pscustomobject]@{Root=(Join-Path $FixtureRoot 'program');Data=(Join-Path $FixtureRoot 'data');Helper=(Join-Path $FixtureRoot 'program\server\server-restart.ps1');Store=(Join-Path $FixtureRoot 'restart');Anchor=(Join-Path $FixtureRoot 'restart\owner.json');PowerShell='C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe';Shutdown=(Join-Path $FixtureRoot 'fake-shutdown.ps1');Task='Makervaerkstedet-GenstartServer'}
$global:restartOwnerId='11111111-2222-3333-4444-555555555555'
foreach($dir in @($restartPaths.Root,$restartPaths.Data,(Split-Path $restartPaths.Helper -Parent),$restartPaths.Store)){New-Item -ItemType Directory -Path $dir -Force|Out-Null}
Copy-Item -LiteralPath $Source -Destination $restartPaths.Helper
[IO.File]::WriteAllText($restartPaths.Shutdown,'$global:restartEvents.Add("shutdown"); $global:restartShutdownArgs=@($args); $global:LASTEXITCODE=0')
[IO.File]::WriteAllText($restartPaths.Anchor,(@{schema=1;owner_id=$restartOwnerId}|ConvertTo-Json))
$owner=@{schemaVersion=1;product='ElevhubServer';state='installed';taskName='ElevhubServer';ownerId=$restartOwnerId;installRoot=$restartPaths.Root;dataRoot=$restartPaths.Data}
if($Scenario -eq 'owner-changed'){$owner.ownerId='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'}
if($Scenario -eq 'removed'){$owner.state='removed'}
if($Scenario -in @('generation','generation-owner-changed','generation-transition','maintenance','tls-pending','tls-complete')){
    $owner.startupProtocol='generation-v1';$owner.generationLayout=$true
    $control=Join-Path $restartPaths.Root 'control';New-Item -ItemType Directory -Path $control|Out-Null
    [IO.File]::WriteAllText((Join-Path $control 'generation.lock'),'1')
    $selectedData=Join-Path $FixtureRoot 'selected-data';New-Item -ItemType Directory -Path $selectedData|Out-Null
    $selectedOwner=$owner.Clone();$selectedOwner.dataRoot=$selectedData
    if($Scenario -eq 'generation-owner-changed'){$selectedOwner.ownerId='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'}
    [IO.File]::WriteAllText((Join-Path $selectedData 'installation-owner.json'),($selectedOwner|ConvertTo-Json))
    $selection=@{format=1;installation_id='11111111222233334444555555555555';revision=1;runtime_root=$restartPaths.Root;program_root=$restartPaths.Root;data_root=$selectedData;manifest_sha256=('a'*64)}
    [IO.File]::WriteAllText((Join-Path $control 'active-generation.json'),($selection|ConvertTo-Json))
    if($Scenario -eq 'generation-transition'){[IO.File]::WriteAllText((Join-Path $control 'selection-transition.json'),'{}')}
    if($Scenario -eq 'maintenance'){[IO.File]::WriteAllText((Join-Path $control 'school-maintenance-fixture.json'),'{"phase":"stopped"}')}
    if($Scenario -like 'tls-*'){
        $phase=if($Scenario -eq 'tls-complete'){'complete'}else{'renewing'}
        [IO.File]::WriteAllText((Join-Path $control 'school-tls-renewal.json'),(@{phase=$phase}|ConvertTo-Json))
    }
}
[IO.File]::WriteAllText((Join-Path $restartPaths.Data 'installation-owner.json'),($owner|ConvertTo-Json))
function Get-RestartPaths {return $global:restartPaths}
function Assert-RestartProtected([string]$Path) {Assert-RestartPlain $Path;if(-not (Test-Path -LiteralPath $Path)){throw 'Fixture path missing'}}
function Assert-RestartAdministrator {}
function Assert-RestartSystem {}
function Start-Sleep {$global:restartEvents.Add('delay')}
function New-RestartMutex {
    $mutex=[pscustomobject]@{Busy=($Scenario -eq 'busy')}
    $mutex|Add-Member ScriptMethod WaitOne {param($timeout);return -not $this.Busy}
    $mutex|Add-Member ScriptMethod ReleaseMutex {}
    $mutex|Add-Member ScriptMethod Dispose {}
    return $mutex
}
# No fixture ever writes Windows ACLs or registers/starts/deletes a real task.
function Initialize-RestartStore($Paths,[string]$OwnerId) {
    if((Get-RestartAnchor $Paths) -cne $OwnerId){throw 'Fixture anchor mismatch'}
}
$global:restartTask=[pscustomobject]@{Xml=(Get-RestartTaskXml $restartPaths $restartOwnerId);State=3;Sddl=(Get-RestartSddl)}
$restartTask|Add-Member ScriptMethod GetSecurityDescriptor {param($flags);return $this.Sddl}
$restartTask|Add-Member ScriptMethod SetSecurityDescriptor {param($sddl,$flags);$this.Sddl=$sddl;$global:restartEvents.Add('task-acl')}
$restartTask|Add-Member ScriptMethod Run {param($parameters);$global:restartEvents.Add('request')}
switch($Scenario){
 'foreign-action' {$restartTask.Xml=$restartTask.Xml.Replace('-Mode Execute','-Mode Install')}
 'foreign-owner' {$restartTask.Xml=$restartTask.Xml.Replace($restartOwnerId,'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')}
 'task-trigger' {$restartTask.Xml=$restartTask.Xml.Replace('<Triggers />','<Triggers><BootTrigger /></Triggers>')}
 'task-retry' {$restartTask.Xml=$restartTask.Xml.Replace('</Settings>','<RestartOnFailure><Interval>PT1M</Interval><Count>3</Count></RestartOnFailure></Settings>')}
 'disabled' {$restartTask.Xml=$restartTask.Xml.Replace('<Enabled>true</Enabled>','<Enabled>false</Enabled>')}
 'foreign-acl' {$restartTask.Sddl='O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;LS)'}
 'acl-reordered-task' {$restartTask.Sddl='O:BAG:BAD:P(A;;FRFX;;;LS)(A;;FA;;;BA)(A;;FA;;;SY)'}
}
$global:restartMissing=$Scenario -in @('missing-task','not-installed')
if($Scenario -like 'logon-*'){
    $restartTask.Xml=[regex]::Replace($restartTask.Xml,'<LogonType>[^<]*</LogonType>','')
    $restartTask.Xml=$restartTask.Xml.Replace('<RunLevel>','<LogonType>'+$Scenario.Substring(6)+'</LogonType><RunLevel>')
}
if($Scenario -eq 'not-installed'){
    Remove-Item -LiteralPath $restartPaths.Anchor
    Remove-Item -LiteralPath $restartPaths.Store
}
$global:restartFolder=[pscustomobject]@{}
$restartFolder|Add-Member ScriptMethod GetTask {param($name);if($global:restartMissing){return $null};return $global:restartTask}
$restartFolder|Add-Member ScriptMethod RegisterTask {
    param($name,$xml,$flags,$user,$password,$logon,$sddl)
    # Native Task Scheduler rejects ServiceAccount in XML; the service-account
    # type belongs only to RegisterTask's separate TASK_LOGON_TYPE argument.
    [xml]$definition=$xml
    if($definition.SelectNodes('//*[local-name()="LogonType"]').Count){throw 'SYSTEM task XML must omit LogonType'}
    if($logon -ne 5 -or $user -cne 'S-1-5-18'){throw 'SYSTEM registration must use TASK_LOGON_SERVICE_ACCOUNT'}
    $global:restartEvents.Add('register');$global:restartTask.Xml=$xml;$global:restartTask.Sddl=$sddl;$global:restartMissing=$false;return $global:restartTask
}
$restartFolder|Add-Member ScriptMethod DeleteTask {param($name,$flags);$global:restartEvents.Add('remove')}
function Connect-RestartScheduler {return $global:restartFolder}
$result=$null;$failure=$null
try {$result=Invoke-ServerRestart -Mode $Mode}catch{$failure=$_.Exception.Message}
@{result=$result;error=$failure;events=@($restartEvents);shutdown_args=$restartShutdownArgs}|ConvertTo-Json -Depth 8 -Compress
