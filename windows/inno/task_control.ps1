[CmdletBinding()]
param([Parameter(Mandatory=$true)][ValidateSet('Inspect','Stop','Restore','ConfigureSimple','RestoreSimple','Hold')][string]$Action,
      [Parameter(Mandatory=$true)][string]$Journal, [string]$PackageRoot)
$ErrorActionPreference='Stop'
$expected=[IO.Path]::GetFullPath((Join-Path $env:ProgramFiles 'ElevhubServer'))
$journalRoot=[IO.Path]::GetFullPath((Join-Path $env:ProgramData 'ElevhubServer\installer-updates'))
$Journal=[IO.Path]::GetFullPath($Journal)
if(-not $Journal.StartsWith($journalRoot+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Journal outside installer root'}
if($Action -in @('ConfigureSimple','RestoreSimple')){
 $configPath=Join-Path $env:ProgramData 'ElevhubServer\konfiguration.json'
 $config=Get-Content -LiteralPath $configPath -Raw -Encoding UTF8|ConvertFrom-Json
 $acl=Get-Acl -LiteralPath $configPath
 $saved=Join-Path $Journal 'simple-mode-before.json'
 if($Action -eq 'ConfigureSimple'){
  if(Test-Path -LiteralPath $saved){throw 'Simple-mode transaction already exists'}
  $present=$config.PSObject.Properties.Name -contains 'simple_updates'
  @{present=$present;value=$(if($present){$config.simple_updates}else{$null})}|ConvertTo-Json -Depth 32|Set-Content -LiteralPath $saved -Encoding UTF8
  $template=Get-Content -LiteralPath (Join-Path $PackageRoot 'hub\konfiguration.windows.json') -Raw -Encoding UTF8|ConvertFrom-Json
  if($null -eq $template.simple_updates -or $template.simple_updates.repository -ne 'mdkmsuv/makervaerkstedet'){throw 'Complete simple mode required'}
  . (Join-Path $PackageRoot 'server\public-apt-cache-acl.ps1')
  Set-PublicAptCacheAcl (Join-Path $env:ProgramData 'ElevhubServer')
  if($present){
   # Existing local catalog/bindings are identity/configuration; retain them.
   $template.simple_updates.device_bindings=$config.simple_updates.device_bindings
   $template.simple_updates.catalog=$config.simple_updates.catalog
  }
  $config|Add-Member -NotePropertyName simple_updates -NotePropertyValue $template.simple_updates -Force
 }else{
  if(-not(Test-Path -LiteralPath $saved)){exit 0}
  $before=Get-Content -LiteralPath $saved -Raw -Encoding UTF8|ConvertFrom-Json
  if($before.present){$config|Add-Member -NotePropertyName simple_updates -NotePropertyValue $before.value -Force}
  else{$config.PSObject.Properties.Remove('simple_updates')}
 }
 $temporary=$configPath+'.installer-'+[Guid]::NewGuid().ToString('N')
 $bytes=[Text.UTF8Encoding]::new($false).GetBytes(($config|ConvertTo-Json -Depth 64))
 $file=[IO.File]::Open($temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try{$file.Write($bytes,0,$bytes.Length);$file.Flush($true)}finally{$file.Dispose()}
 Set-Acl -LiteralPath $temporary -AclObject $acl
 Move-Item -LiteralPath $temporary -Destination $configPath -Force
 Set-Acl -LiteralPath $configPath -AclObject $acl
 exit 0
}

if($Action -eq 'Restore' -and -not(Test-Path -LiteralPath (Join-Path $Journal 'task-state.json'))){exit 0}

function Resolve-TaskPrincipalSid([string]$Identity) {
 try {
  if($Identity -match '^S-\d-') { return ([Security.Principal.SecurityIdentifier]::new($Identity)).Value }
  return ([Security.Principal.NTAccount]::new($Identity)).Translate([Security.Principal.SecurityIdentifier]).Value
 } catch { throw 'Task identity could not be resolved to a Windows SID' }
}
$owner=Get-Content -LiteralPath (Join-Path $env:ProgramData 'ElevhubServer\installation-owner.json') -Raw -Encoding UTF8|ConvertFrom-Json
$config=Get-Content -LiteralPath (Join-Path $env:ProgramData 'ElevhubServer\konfiguration.json') -Raw -Encoding UTF8|ConvertFrom-Json
$task=Get-ScheduledTask -TaskName 'ElevhubServer' -TaskPath '\'
if(@($task.Actions).Count -ne 1 -or [IO.Path]::GetFullPath([string]$task.Actions[0].Execute) -ine (Join-Path $expected 'runtime\python.exe')){throw 'Server task does not own expected interpreter; generation/custom layout requires explicit migration'}
if((Resolve-TaskPrincipalSid $task.Principal.UserId) -ne 'S-1-5-19'){throw 'Unexpected server task identity'}
$expectedArguments='-B "{0}" --package-root "{1}" --data-root "{2}" --role-ip "{3}" --teacher-port "{4}" --teacher-address "{5}"' -f (Join-Path $expected 'server\bootstrap.py'),$expected,(Join-Path $env:ProgramData 'ElevhubServer'),$config.elev_adresse,$config.laerer_port,$config.laerer_adresse
if($task.Description -cne "ElevhubServer owner=$($owner.ownerId)" -or [IO.Path]::GetFullPath([string]$task.Actions[0].WorkingDirectory) -ine $expected -or [string]$task.Actions[0].Arguments -cne $expectedArguments){throw 'Task ownership/action changed; stop refused'}
$owned=@($task)
$enrollment=Get-ScheduledTask -TaskName 'ElevhubSchoolEnrollment' -TaskPath '\' -ErrorAction SilentlyContinue
if($enrollment){
 $argsExpected='-B "{0}" serve --data-root "{1}"' -f (Join-Path $expected 'deployment\school-setup\enrollment.py'),(Join-Path $env:ProgramData 'ElevhubServer')
 $argvMatches=([string]$enrollment.Actions[0].Arguments -ceq $argsExpected)
 if(@($enrollment.Actions).Count -ne 1 -or (Resolve-TaskPrincipalSid $enrollment.Principal.UserId) -ne 'S-1-5-18' -or
    $enrollment.Description -cne 'Elevhub skole-enrollment' -or [IO.Path]::GetFullPath([string]$enrollment.Actions[0].Execute) -ine (Join-Path $expected 'runtime\python.exe') -or
    [IO.Path]::GetFullPath([string]$enrollment.Actions[0].WorkingDirectory) -ine $expected -or -not $argvMatches){throw 'Enrollment task ownership/action changed; stop refused'}
 $owned+=,$enrollment
}

if($owner.adoptedPrinterLights){
 $lights=Get-ScheduledTask -TaskName 'Printvaerksted-Kameralys' -TaskPath '\' -ErrorAction Stop
 $argsLights='-B -m app.light_service --data "'+(Join-Path $env:ProgramData 'ElevhubServer\printvaerksted')+'"'
 if(@($lights.Actions).Count -ne 1 -or (Resolve-TaskPrincipalSid $lights.Principal.UserId) -ne 'S-1-5-18' -or
    $lights.Description -cne ('Makervaerkstedet printer lights owner='+$owner.ownerId) -or [IO.Path]::GetFullPath($lights.Actions[0].Execute) -ine (Join-Path $expected 'runtime\python.exe') -or
    [IO.Path]::GetFullPath($lights.Actions[0].WorkingDirectory) -ine $expected -or $lights.Actions[0].Arguments -cne $argsLights){throw 'Owned printer light task changed'}
 $owned+=,$lights
}
if($Action -eq 'Hold'){foreach($item in $owned){Disable-ScheduledTask -TaskName $item.TaskName -TaskPath '\'|Out-Null;Stop-ScheduledTask -TaskName $item.TaskName -TaskPath '\'};exit 0}
# Windows omits Settings/Enabled from the exported XML while a task is enabled
# and writes <Enabled>false</Enabled> once it is disabled. Compare the task
# definition without that one element, and without insignificant whitespace,
# so that our own Disable does not look like a foreign change.
function Get-TaskConfiguration([string]$Xml){
 $document=[xml]$Xml
 foreach($node in @($document.SelectNodes("/*[local-name()='Task']/*[local-name()='Settings']/*[local-name()='Enabled']"))){[void]$node.ParentNode.RemoveChild($node)}
 return $document.OuterXml
}
$rows=@(foreach($item in $owned){
 $xml=Export-ScheduledTask -TaskName $item.TaskName -TaskPath '\'
 @{name=$item.TaskName;xml=$xml;configuration=(Get-TaskConfiguration $xml);enabled=[bool]$item.Settings.Enabled;running=($item.State -eq 'Running')}
})
if($Action -eq 'Inspect'){Write-Output 'owned-flat-server-and-enrollment-tasks';exit 0}
$record=Join-Path $Journal 'task-state.json'
if($Action -eq 'Stop'){
 if(-not(Test-Path -LiteralPath $record)){
  @{schema=2;tasks=$rows}|ConvertTo-Json -Depth 8|Set-Content -LiteralPath $record -Encoding UTF8
 }else{
  $before=Get-Content -LiteralPath $record -Raw|ConvertFrom-Json
  if($before.schema -ne 2 -or @($before.tasks).Count -ne $rows.Count){throw 'Task set changed since snapshot'}
  foreach($row in $rows){$old=@($before.tasks|Where-Object {$_.name -ceq $row.name});if($old.Count -ne 1 -or $old[0].configuration -cne $row.configuration){throw 'Task changed since snapshot'}}
 }
 foreach($row in $rows){Disable-ScheduledTask -TaskName $row.name -TaskPath '\'|Out-Null;Stop-ScheduledTask -TaskName $row.name -TaskPath '\'}
 $deadline=[DateTime]::UtcNow.AddSeconds(25)
 do{
  $remaining=@(Get-CimInstance Win32_Process|Where-Object{$exe=[string]$_.ExecutablePath;$exe.StartsWith((Join-Path $expected 'runtime\'),[StringComparison]::OrdinalIgnoreCase)})
  if($remaining.Count -eq 0){exit 0}
  Start-Sleep -Milliseconds 250
 }while([DateTime]::UtcNow -lt $deadline)
 throw 'Owned runtime processes remain; no process is killed by name'
}
$before=Get-Content -LiteralPath $record -Raw|ConvertFrom-Json
if($before.schema -ne 2 -or @($before.tasks).Count -ne $rows.Count){throw 'Task set changed; restoration refused'}
foreach($row in $rows){
 $old=@($before.tasks|Where-Object {$_.name -ceq $row.name})
 if($old.Count -ne 1 -or $old[0].configuration -cne $row.configuration){throw 'Task changed; restoration refused'}
}
foreach($row in $before.tasks){
 if($row.enabled){Enable-ScheduledTask -TaskName $row.name -TaskPath '\'|Out-Null}
 if($row.running){Start-ScheduledTask -TaskName $row.name -TaskPath '\'}
}
