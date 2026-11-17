param([string]$Source,[string]$Root,[string]$Scenario)
$ErrorActionPreference='Stop';$env:PSModulePath=Join-Path $PSHOME 'Modules'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$env:ProgramFiles=Join-Path $Root 'program';$env:ProgramData=Join-Path $Root 'data'
$program=Join-Path $env:ProgramFiles 'ElevhubServer';$data=Join-Path $env:ProgramData 'ElevhubServer';$journal=Join-Path $data 'installer-updates\11111111-2222-3333-4444-555555555555'
foreach($p in @($program,$data,$journal)){New-Item -ItemType Directory -Path $p -Force|Out-Null}
$python=Join-Path $program 'runtime\python.exe';$ownerId='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';$ownerPath=Join-Path $data 'installation-owner.json'
$global:events=[Collections.Generic.List[string]]::new();$global:exports=0;$global:tasks=@{}
$config=@{elev_adresse='192.0.2.10';laerer_port=8780;laerer_adresse='127.0.0.1'}
[IO.File]::WriteAllText((Join-Path $data 'konfiguration.json'),($config|ConvertTo-Json))
$school=@{schemaVersion=1;school_name='Synthetic fixture';role='vm';admin_public_key='synthetic-public';server='192.0.2.10'}
[IO.File]::WriteAllText((Join-Path $data 'school.json'),($school|ConvertTo-Json))
$receipt=@{schemaVersion=1;product='ElevhubSchoolSetup';ownerId=$ownerId;state='complete';settings=@{school_name=$school.school_name;role='vm';server=$school.server}}
[IO.File]::WriteAllText((Join-Path $data 'school-setup-state.json'),($receipt|ConvertTo-Json))
function Task($name,$description,$identity,$arguments,$cwd){return [pscustomobject]@{TaskName=$name;TaskPath='\';State='Running';Description=$description;Principal=@{UserId=$identity};Settings=@{Enabled=$true};Actions=@([pscustomobject]@{Execute=$python;Arguments=$arguments;WorkingDirectory=$cwd})}}
$serverArgs='-B "{0}" --package-root "{1}" --data-root "{2}" --role-ip "192.0.2.10" --teacher-port "8780" --teacher-address "127.0.0.1"' -f (Join-Path $program 'server\bootstrap.py'),$program,$data
$enrollArgs='-B "{0}" serve --data-root "{1}"' -f (Join-Path $program 'deployment\school-setup\enrollment.py'),$data
$tasks.ElevhubServer=Task 'ElevhubServer' ('ElevhubServer owner='+$ownerId) 'S-1-5-19' $serverArgs $program
$tasks.ElevhubSchoolEnrollment=Task 'ElevhubSchoolEnrollment' 'Elevhub skole-enrollment' 'S-1-5-18' $enrollArgs $program
$names=@('ElevhubServer-PublicPrintHttp','ElevhubServer-TeacherPrintHttps')
$tasks[$names[0]]=Task $names[0] 'Synthetic HTTP fixture' 'S-1-5-19' '-B fixture.py --synthetic http' (Join-Path $data 'public-print-http')
$tasks[$names[1]]=Task $names[1] 'Synthetic HTTPS fixture' 'S-1-5-18' '-B fixture.py --synthetic https' (Join-Path $data 'teacher-print-https')
function Export-ScheduledTask($TaskName,$TaskPath){
 if($TaskPath -cne '\'){throw 'Wrong task folder'};$global:exports++;$t=$global:tasks[$TaskName]
 $enabled=if($t.Settings.Enabled){''}else{'<Enabled>false</Enabled>'}
 $actions=(@($t.Actions|ForEach-Object {'<Exec><Command>'+[Security.SecurityElement]::Escape($_.Execute)+'</Command><Arguments>'+[Security.SecurityElement]::Escape($_.Arguments)+'</Arguments><WorkingDirectory>'+[Security.SecurityElement]::Escape($_.WorkingDirectory)+'</WorkingDirectory></Exec>'}) -join '')
 return '<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><RegistrationInfo><Description>'+[Security.SecurityElement]::Escape($t.Description)+'</Description></RegistrationInfo><Triggers><BootTrigger><Enabled>true</Enabled></BootTrigger></Triggers><Principals><Principal><UserId>'+$t.Principal.UserId+'</UserId></Principal></Principals><Settings>'+$enabled+'<Hidden>false</Hidden></Settings><Actions>'+$actions+'</Actions></Task>'
}
function FixtureHash($name){
 $xml=[xml](Export-ScheduledTask $name '\');foreach($node in @($xml.SelectNodes("/*[local-name()='Task']/*[local-name()='Settings']/*[local-name()='Enabled']"))){[void]$node.ParentNode.RemoveChild($node)}
 $hash=[Security.Cryptography.SHA256]::Create();try{return [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($xml.OuterXml))).Replace('-','').ToLowerInvariant()}finally{$hash.Dispose()}
}
switch($Scenario){
 'wrong-executable' {$tasks[$names[0]].Actions[0].Execute=Join-Path $program 'runtime\foreign.exe'}
 'wrong-cwd' {$tasks[$names[0]].Actions[0].WorkingDirectory=Join-Path $data 'teacher-print-https'}
 'wrong-principal' {$tasks[$names[0]].Principal.UserId='S-1-5-32-545'}
 'multiple-actions' {$tasks[$names[0]].Actions+=,$tasks.ElevhubServer.Actions[0]}
 'disabled' {$tasks[$names[1]].State='Disabled';$tasks[$names[1]].Settings.Enabled=$false}
}
$bindings=@(foreach($name in $names){[pscustomobject]@{task_name=$name;definition_sha256=(FixtureHash $name)}})
$beforeHashes=@{};foreach($b in $bindings){$beforeHashes[$b.task_name]=$b.definition_sha256}
switch($Scenario){
 'changed-hash' {$bindings[1].definition_sha256='b'*64}
 'upper-hash' {$bindings[1].definition_sha256=$bindings[1].definition_sha256.ToUpperInvariant()}
 'missing-task' {$tasks.Remove($names[1])}
 'unknown' {$bindings[1].task_name='Foreign-Unknown-Task'}
 'duplicate' {$bindings[1]=$bindings[0]}
 'too-many' {$bindings+=,$bindings[0]}
 'extra-field' {$bindings[0]|Add-Member NoteProperty extra 'fixture'}
}
$owner=@{ownerId=$ownerId;adoptedAuxiliaryTasks=$bindings}
if($Scenario -eq 'metadata-object'){$owner.adoptedAuxiliaryTasks=$bindings[0]}
if($Scenario -eq 'metadata-null'){$owner.adoptedAuxiliaryTasks=$null}
if($Scenario -in @('unbound','no-auxiliary')){$owner.Remove('adoptedAuxiliaryTasks')}
if($Scenario -eq 'no-auxiliary'){foreach($name in $names){$tasks.Remove($name)}}
[IO.File]::WriteAllText($ownerPath,($owner|ConvertTo-Json -Depth 8));$global:exports=0
function Get-Acl {param($LiteralPath);$acl=[Security.AccessControl.FileSecurity]::new();$sddl='O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FR;;;LS)';if($Scenario -eq 'unsafe-owner'){$sddl='O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;LS)'};$acl.SetSecurityDescriptorSddlForm($sddl);return $acl}
if($Scenario -eq 'linked-owner'){function Get-Item {param($LiteralPath,[switch]$Force);return [pscustomobject]@{Attributes=[IO.FileAttributes]::ReparsePoint}}}
function Get-ScheduledTask($TaskName,$TaskPath,$ErrorAction){if($TaskPath -cne '\'){throw 'Wrong task folder'};return $global:tasks[$TaskName]}
function Disable-ScheduledTask($TaskName,$TaskPath){$global:events.Add('disable:'+$TaskName);$global:tasks[$TaskName].Settings.Enabled=$false}
function Stop-ScheduledTask($TaskName,$TaskPath){$global:events.Add('stop:'+$TaskName);$global:tasks[$TaskName].State='Ready'}
function Enable-ScheduledTask($TaskName,$TaskPath){$global:events.Add('enable:'+$TaskName);$global:tasks[$TaskName].Settings.Enabled=$true}
function Start-ScheduledTask($TaskName,$TaskPath){$global:events.Add('start:'+$TaskName);$global:tasks[$TaskName].State='Running'}
function Get-CimInstance($ClassName){$id=100;foreach($t in $global:tasks.Values){if($t.State -eq 'Running'){$id++;[pscustomobject]@{ExecutablePath=$t.Actions[0].Execute;CommandLine=('"'+$t.Actions[0].Execute+'" '+$t.Actions[0].Arguments);ProcessId=$id;ParentProcessId=1}}}}
$failure=$null;$same=$false
try{
 & $Source -Action PreflightUpdate -Journal $journal | Out-Null
 & $Source -Action Stop -Journal $journal | Out-Null
 # A failed native update enters the identical existing Restore path.
 if($Scenario -eq 'failed-update'){$failurePhase='synthetic_native_failure'}
 & $Source -Action Restore -Journal $journal | Out-Null
 $same=$true;foreach($name in $beforeHashes.Keys){if($tasks.ContainsKey($name) -and (FixtureHash $name) -cne $beforeHashes[$name]){$same=$false}}
}catch{$failure=$_.Exception.Message}
$count=0;$record=Join-Path $journal 'task-state.json';if(Test-Path -LiteralPath $record){$count=@((Get-Content -LiteralPath $record -Raw|ConvertFrom-Json).tasks).Count}
@{error=$failure;events=@($events);journal_task_count=$count;definitions_unchanged=$same;export_count=$exports}|ConvertTo-Json -Depth 5 -Compress
