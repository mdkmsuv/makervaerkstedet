param([string]$Source,[string]$FixtureRoot,[string]$Mode,[string]$Scenario,[string]$CommandId,[string]$ManifestSha256)
$ErrorActionPreference='Stop'
$env:PSModulePath=Join-Path $PSHOME 'Modules'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput([IO.File]::ReadAllText($Source),[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors|Out-String)}
foreach($function in @($ast.EndBlock.Statements|Where-Object {$_ -is [Management.Automation.Language.FunctionDefinitionAst]})){Invoke-Expression $function.Extent.Text}
if($Scenario -eq 'kernel-mutex'){
    $failure=$null;$result=$null;$creator=$null;$foreign=$null;$opened=$null;$held=$false
    try {
        if(-not (Get-Command Get-UpdateMutexName -ErrorAction SilentlyContinue)){throw 'Explicit cross-identity mutex contract missing'}
        $global:fixtureMutexName='Local\MKV-UpdateMutex-'+[Guid]::NewGuid().ToString('N')
        function Get-UpdateMutexName {return $global:fixtureMutexName}
        function Probe-MutexChild {
            $code=@'
$m=[Threading.Mutex]::OpenExisting('__NAME__',[Security.AccessControl.MutexRights]'Modify,Synchronize')
try{$acquired=$m.WaitOne(0);if($acquired){$m.ReleaseMutex()};@{acquired=$acquired}|ConvertTo-Json -Compress}finally{$m.Dispose()}
'@
            $code=$code.Replace('__NAME__',$global:fixtureMutexName)
            $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code))
            $raw=& (Join-Path $PSHOME 'powershell.exe') -NoProfile -NonInteractive -EncodedCommand $encoded
            if($LASTEXITCODE -ne 0){throw 'Synthetic child mutex probe failed'}
            return ($raw -join "`n")|ConvertFrom-Json
        }
        $creator=New-UpdateMutex
        $security=$creator.GetAccessControl()
        $rules=@($security.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
        $ls=@($rules|Where-Object {$_.IdentityReference.Value -ceq 'S-1-5-19'})
        # The host test runs as an ordinary developer, not LocalService. Give
        # that synthetic actor precisely LS's minimum rights on this unique
        # test object; inspect the unchanged production DACL separately above.
        $current=[Security.Principal.WindowsIdentity]::GetCurrent().User
        $probeSecurity=$creator.GetAccessControl()
        $probeSecurity.AddAccessRule([Security.AccessControl.MutexAccessRule]::new($current,(Get-UpdateMutexRights),'Allow'))
        $creator.SetAccessControl($probeSecurity)
        $held=$creator.WaitOne(0)
        if(-not $held){throw 'Synthetic creator could not acquire mutex'}
        $busy=Probe-MutexChild
        $creator.ReleaseMutex();$held=$false
        # Keep the creator HANDLE alive: ReleaseMutex alone does not destroy it.
        $released=Probe-MutexChild
        $alive=-not $creator.SafeWaitHandle.IsClosed
        $creator.Dispose();$creator=$null

        # A default/full-control open must fail, but the production minimum
        # must open without modifying the existing object's descriptor.
        $global:fixtureMutexName='Local\MKV-UpdateMutex-'+[Guid]::NewGuid().ToString('N')
        $restricted=Get-UpdateMutexSecurity
        $restricted.AddAccessRule([Security.AccessControl.MutexAccessRule]::new($current,(Get-UpdateMutexRights),'Allow'))
        $restricted.AddAccessRule([Security.AccessControl.MutexAccessRule]::new($current,[Security.AccessControl.MutexRights]::Delete,'Deny'))
        $made=$false;$foreign=[Threading.Mutex]::new($false,$global:fixtureMutexName,[ref]$made,$restricted)
        $before=$foreign.GetAccessControl().GetSecurityDescriptorSddlForm('Access')
        $fullDenied=$false
        try {$extra=[Threading.Mutex]::OpenExisting($global:fixtureMutexName,[Security.AccessControl.MutexRights]::FullControl);$extra.Dispose()}catch [UnauthorizedAccessException]{$fullDenied=$true}
        $opened=New-UpdateMutex
        $minimumWorks=$opened.WaitOne(0)
        if($minimumWorks){$opened.ReleaseMutex()}
        $after=$foreign.GetAccessControl().GetSecurityDescriptorSddlForm('Access')
        $result=@{rule_count=$rules.Count;localservice_rule_count=$ls.Count;localservice_rights=[int]$ls[0].MutexRights;
            protected=$security.AreAccessRulesProtected;busy_while_owned=(-not $busy.acquired);opens_after_release=$released.acquired;
            creator_handle_still_alive=$alive;default_full_open_denied=$fullDenied;minimal_open_works=$minimumWorks;existing_acl_unchanged=($before -ceq $after)}
    }catch{$failure=$_.Exception.Message}
    finally{if($creator){if($held){$creator.ReleaseMutex()};$creator.Dispose()};if($opened){$opened.Dispose()};if($foreign){$foreign.Dispose()}}
    @{result=$result;error=$failure;events=@()}|ConvertTo-Json -Depth 6 -Compress
    exit 0
}
if($Scenario -eq 'replace-request'){
    $path=Join-Path $FixtureRoot 'request.json'
    Write-UpdateJsonAtomic $path @{value=1}
    Write-UpdateJsonAtomic $path @{value=2}
    @{result=@{value=(Read-UpdateJson $path).value;pending=@(Get-ChildItem -LiteralPath $FixtureRoot -Filter '*.pending').Count};error=$null;events=@()}|ConvertTo-Json -Compress
    exit 0
}
if($Scenario -eq 'python-arguments'){
    $fakePython=Join-Path $FixtureRoot 'fake python.ps1'
    [IO.File]::WriteAllText($fakePython,('$global:capturedPythonArgs=@($args);$global:LASTEXITCODE=0;''{"state":"available"}'''))
    function Assert-UpdateCode($Paths) {}
    $paths=[pscustomobject]@{Python=$fakePython;Worker=(Join-Path $FixtureRoot 'worker code.py');Store=(Join-Path $FixtureRoot 'store with spaces')}
    $failure=$null;$calls=@()
    try {
        foreach($action in @('check','prepare','finish','fail')){
            $null=Invoke-UpdatePython $paths $action 42
            $calls+=,@($global:capturedPythonArgs)
        }
    }catch{$failure=$_.Exception.Message}
    @{result=@{calls=$calls;worker=$paths.Worker;store=$paths.Store};error=$failure;events=@()}|ConvertTo-Json -Depth 8 -Compress
    exit 0
}
if($Scenario -eq 'directory-acls'){
    $global:capturedAcl=$null
    function Set-Acl {param($LiteralPath,$AclObject);$global:capturedAcl=$AclObject}
    Set-UpdateDirectoryAcl $FixtureRoot
    $storeRights=@($capturedAcl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])|Where-Object {$_.IdentityReference.Value -eq 'S-1-5-19'})[0].FileSystemRights
    Set-UpdateDirectoryAcl $FixtureRoot -Inbox
    $inboxRights=@($capturedAcl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])|Where-Object {$_.IdentityReference.Value -eq 'S-1-5-19'})[0].FileSystemRights
    function Get-Acl {param($LiteralPath);return $global:capturedAcl}
    $failure=$null
    try {Assert-UpdateInbox ([pscustomobject]@{Inbox=$FixtureRoot})}catch{$failure=$_.Exception.Message}
    @{result=@{store_write=[bool]($storeRights -band [Security.AccessControl.FileSystemRights]::Write);inbox_write=[bool]($inboxRights -band [Security.AccessControl.FileSystemRights]::Write)};error=$failure;events=@()}|ConvertTo-Json -Compress
    exit 0
}
if($Scenario -like 'acl-*'){
    $global:updateAcl=[Security.AccessControl.DirectorySecurity]::new()
    $updateAcl.SetSecurityDescriptorSddlForm('O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FRFX;;;LS)')
    if($Scenario -ne 'acl-read'){
        $right=@{'acl-write'='Write';'acl-delete'='Delete';'acl-dacl'='ChangePermissions';'acl-owner'='TakeOwnership';'acl-child-delete'='DeleteSubdirectoriesAndFiles'}[$Scenario]
        $updateAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-19'),[Security.AccessControl.FileSystemRights]$right,'Allow'))
    }
    function Get-Acl {param([string]$LiteralPath);return $global:updateAcl}
    $failure=$null
    try {Assert-UpdateProtected $FixtureRoot}catch{$failure=$_.Exception.Message}
    @{result=@{allowed=($null -eq $failure)};error=$failure;events=@()}|ConvertTo-Json -Compress
    exit 0
}
$global:updateEvents=[Collections.Generic.List[string]]::new()
$global:updateOwnerId='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
$store=Join-Path $FixtureRoot 'store'
$global:updatePaths=[pscustomobject]@{Root=(Join-Path $FixtureRoot 'program');Data=(Join-Path $FixtureRoot 'data');Store=$store;Helper=(Join-Path $store 'worker.ps1');Anchor=(Join-Path $store 'owner.json');Inbox=(Join-Path $store 'inbox');Request=(Join-Path $store 'inbox\request.json');State=(Join-Path $store 'state.json');Receipts=(Join-Path $store 'receipts');Downloads=(Join-Path $store 'downloads');Python=(Join-Path $FixtureRoot 'program\runtime\python.exe');Worker=(Join-Path $FixtureRoot 'program\server\server_update_worker.py');Task='Makervaerkstedet-ServerUpdate';PowerShell='C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'}
foreach($dir in @($updatePaths.Root,$updatePaths.Data,$store,$updatePaths.Inbox,$updatePaths.Receipts,$updatePaths.Downloads,(Split-Path $updatePaths.Python -Parent),(Split-Path $updatePaths.Worker -Parent))){New-Item -ItemType Directory -Path $dir -Force|Out-Null}
Copy-Item -LiteralPath $Source -Destination $updatePaths.Helper
[IO.File]::WriteAllText($updatePaths.Python,'fixture-not-executable')
[IO.File]::WriteAllText($updatePaths.Worker,'fixture-not-executable')
[IO.File]::WriteAllText($updatePaths.Anchor,(@{schema=1;owner_id=$updateOwnerId}|ConvertTo-Json))
$owner=@{schemaVersion=1;product='ElevhubServer';state='installed';taskName='ElevhubServer';ownerId=$updateOwnerId;installRoot=$updatePaths.Root;dataRoot=$updatePaths.Data}
if($Scenario -eq 'owner-changed'){$owner.ownerId='bbbbbbbb-cccc-dddd-eeee-ffffffffffff'}
if($Scenario -eq 'store-owner-changed'){[IO.File]::WriteAllText($updatePaths.Anchor,(@{schema=1;owner_id='bbbbbbbb-cccc-dddd-eeee-ffffffffffff'}|ConvertTo-Json))}
[IO.File]::WriteAllText((Join-Path $updatePaths.Data 'installation-owner.json'),($owner|ConvertTo-Json))
$global:updateCommand='11111111-2222-3333-4444-555555555555'
$request=@{schema=1;action='install';command_id=$updateCommand;manifest_sha256=('a'*64)}
if($Scenario -eq 'check-request'){$request.action='check';$request.manifest_sha256=$null}
if($Scenario -eq 'request-boolean-schema'){$request.schema=$true}
if($Scenario -eq 'request-extra-field'){$request.Remove('manifest_sha256');$request.extra='fixture'}
if($Mode -eq 'Execute' -or $Scenario -in @('pending','pending-conflict','interrupted','fresh-queue')){
    if($Scenario -eq 'pending-conflict'){$request.command_id='bbbbbbbb-cccc-dddd-eeee-ffffffffffff'}
    [IO.File]::WriteAllText($updatePaths.Request,($request|ConvertTo-Json))
}
if($Scenario -in @('receipt','receipt-conflict')){
    if($Scenario -eq 'receipt-conflict'){$request.manifest_sha256='b'*64}
    [IO.File]::WriteAllText((Join-Path $updatePaths.Receipts ($updateCommand+'.json')),($request|ConvertTo-Json))
}
if($Scenario -in @('interrupted','fresh-queue')){
    [IO.File]::WriteAllText($updatePaths.State,(@{state='installing';command_id=$updateCommand}|ConvertTo-Json))
    [IO.File]::SetLastWriteTimeUtc($updatePaths.State,[DateTime]::UtcNow.AddSeconds(-60))
    if($Scenario -eq 'interrupted'){[IO.File]::SetLastWriteTimeUtc($updatePaths.Request,[DateTime]::UtcNow.AddSeconds(-60))}
}
$global:updateExe=Join-Path $updatePaths.Downloads 'fixture.exe'
[IO.File]::WriteAllText($updateExe,'fixture-not-executable')
$global:updateHash=(Get-FileHash -LiteralPath $updateExe -Algorithm SHA256).Hash.ToLowerInvariant()
$global:updateInstallerFlags=@();$global:updateWaitHidden=$false;$global:updateCwd=$false;$global:updateFinishExit=$null;$global:updateTaskArgsNull=$false
$global:updateMutexDisposed=$false;$global:updateDisposedAtWorker=$false
function Get-UpdatePaths {return $global:updatePaths}
function Assert-UpdateProtected([string]$Path) {Assert-UpdatePlain $Path;if(-not (Test-Path -LiteralPath $Path)){throw 'Fixture protected path missing'}}
function Assert-UpdateInbox($Paths) {Assert-UpdatePlain $Paths.Inbox}
function Assert-UpdateAdministrator {}
function Assert-UpdateSystem {}
function Initialize-UpdateStore($Paths,[string]$OwnerId) {if((Get-UpdateAnchor $Paths) -cne $OwnerId){throw 'Fixture owner mismatch'};$global:updateEvents.Add('initialize')}
function New-UpdateMutex {
    if($Scenario -eq 'denied-mutex'){throw 'Synthetic SYSTEM mutex access denied'}
    $m=[pscustomobject]@{}
    $m|Add-Member ScriptMethod WaitOne {param($timeout);return $true}
    $m|Add-Member ScriptMethod ReleaseMutex {}
    $m|Add-Member ScriptMethod Dispose {$global:updateMutexDisposed=$true}
    return $m
}
# The real Python/EXE executables are never run. Production request, plan,
# ownership, task and checksum checks remain active around these doubles.
function Invoke-UpdatePython($Paths,[string]$Action,[int]$ExitCode=0) {
    $global:updateEvents.Add('python-'+$Action)
    $global:updateCwd=((Get-Location).Path -ceq $Paths.Store)
    if($Action -in @('prepare','check')){
        $global:updateDisposedAtWorker=$global:updateMutexDisposed
        if($Scenario -eq 'mutex-lifetime' -and -not $global:updateDisposedAtWorker){throw 'Creator handle still alive'}
    }
    if($Action -eq 'prepare'){
        if($Scenario -eq 'prepare-failed'){throw 'sensitive stderr fixture must stay private'}
        $plan=[pscustomobject]@{exe=$global:updateExe;sha256=$global:updateHash;command_id=$global:updateCommand;version='2.1.0-20261117.2'}
        switch($Scenario){
            'plan-escape' {$plan.exe=Join-Path $FixtureRoot 'outside.exe';[IO.File]::WriteAllText($plan.exe,'fixture')}
            'plan-relative' {$plan.exe='fixture.exe'}
            'plan-command' {$plan.command_id='bbbbbbbb-cccc-dddd-eeee-ffffffffffff'}
            'plan-extra' {$plan|Add-Member NoteProperty arguments ';whoami'}
            'plan-bad-hash' {$plan.sha256='A'*64}
            'plan-hash-mismatch' {$plan.sha256='b'*64}
        }
        return $plan
    }
    if($Action -eq 'finish'){$global:updateFinishExit=$ExitCode}
    return @{state='succeeded'}
}
function Start-Process {
    param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,[switch]$Wait,[switch]$PassThru)
    if($FilePath -cne $global:updateExe){throw 'Unexpected executable'}
    $global:updateEvents.Add('installer')
    $global:updateInstallerFlags=@($ArgumentList -split ' ',6)
    $global:updateWaitHidden=($Wait -and $PassThru -and $WindowStyle -ceq 'Hidden' -and $WorkingDirectory -ceq $global:updatePaths.Store)
    if($Scenario -eq 'newer-inbox'){
        [IO.File]::WriteAllText($global:updatePaths.Request,(@{schema=1;action='check';command_id='bbbbbbbb-cccc-dddd-eeee-ffffffffffff';manifest_sha256=$null}|ConvertTo-Json))
    }
    return [pscustomobject]@{ExitCode=$(if($Scenario -eq 'installer-failed'){42}else{0})}
}
$global:updateTask=[pscustomobject]@{Xml=(Get-UpdateTaskXml $updatePaths $updateOwnerId);State=$(if($Scenario -eq 'pending'){4}else{3});Sddl=(Get-UpdateSddl)}
$updateTask|Add-Member ScriptMethod GetSecurityDescriptor {param($flags);return $this.Sddl}
$updateTask|Add-Member ScriptMethod SetSecurityDescriptor {param($sddl,$flags);$this.Sddl=$sddl;$global:updateEvents.Add('task-acl')}
$updateTask|Add-Member ScriptMethod Run {param($parameters);$global:updateTaskArgsNull=($null -eq $parameters);$global:updateEvents.Add('task-run')}
switch($Scenario){
    'foreign-action' {$updateTask.Xml=$updateTask.Xml.Replace('-Mode Execute','-Mode Install')}
    'foreign-owner' {$updateTask.Xml=$updateTask.Xml.Replace($updateOwnerId,'bbbbbbbb-cccc-dddd-eeee-ffffffffffff')}
    'foreign-acl' {$updateTask.Sddl='O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;LS)'}
    'task-trigger' {$updateTask.Xml=$updateTask.Xml.Replace('<Triggers />','<Triggers><BootTrigger /></Triggers>')}
    'task-retry' {$updateTask.Xml=$updateTask.Xml.Replace('</Settings>','<RestartOnFailure><Interval>PT1M</Interval><Count>3</Count></RestartOnFailure></Settings>')}
    'extra-action' {$updateTask.Xml=$updateTask.Xml.Replace('</Actions>','<Exec><Command>foreign.exe</Command></Exec></Actions>')}
    'task-logon' {$updateTask.Xml=$updateTask.Xml.Replace('<RunLevel>','<LogonType>S4U</LogonType><RunLevel>')}
}
$global:updateMissing=$Scenario -in @('missing-task','not-installed','missing-task-preserved-store')
if($Scenario -eq 'not-installed'){$updatePaths.Store=Join-Path $FixtureRoot 'never-installed-store';$updatePaths.Anchor=Join-Path $updatePaths.Store 'owner.json'}
$global:updateFolder=[pscustomobject]@{}
$updateFolder|Add-Member ScriptMethod GetTask {param($name);if($global:updateMissing){return $null};return $global:updateTask}
$updateFolder|Add-Member ScriptMethod RegisterTask {
    param($name,$xml,$flags,$user,$password,$logon,$sddl)
    [xml]$definition=$xml
    if($definition.SelectNodes('//*[local-name()="LogonType"]').Count -or $logon -ne 5 -or $user -cne 'S-1-5-18'){throw 'Wrong SYSTEM registration'}
    $global:updateEvents.Add('register');$global:updateTask.Xml=$xml.Replace('<Enabled>true</Enabled>','').Replace('<AllowStartOnDemand>true</AllowStartOnDemand>','');$global:updateTask.Sddl=$sddl;return $global:updateTask
}
$updateFolder|Add-Member ScriptMethod DeleteTask {param($name,$flags);$global:updateEvents.Add('remove')}
function Connect-UpdateScheduler {return $global:updateFolder}
$result=$null;$failure=$null
try {$result=Invoke-ServerUpdate -Mode $Mode -CommandId $CommandId -ManifestSha256 $ManifestSha256}catch{$failure=$_.Exception.Message}
$afterRequest=if(Test-Path -LiteralPath $updatePaths.Request){Read-UpdateJson $updatePaths.Request}else{$null}
@{result=$result;error=$failure;events=@($updateEvents);request=$afterRequest;task_arguments_null=$updateTaskArgsNull;installer_flags=$updateInstallerFlags;installer_wait_hidden=$updateWaitHidden;cwd_is_store=$updateCwd;finish_exit_code=$updateFinishExit;mutex_disposed_before_worker=$updateDisposedAtWorker}|ConvertTo-Json -Depth 12 -Compress
