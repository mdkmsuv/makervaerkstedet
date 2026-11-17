# Fixed, demand-only Windows server update bridge. Credits: Klostermarksskolen.
[CmdletBinding()]
param([ValidateSet('Status','Check','Request','Execute','Install','Remove')][string]$Mode='Status',
      [string]$CommandId='', [string]$ManifestSha256='')
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$env:PSModulePath=Join-Path $PSHOME 'Modules'
$script:UpdateSource=$PSCommandPath

function Get-UpdatePaths {
    $root=Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'ElevhubServer'
    $store=Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'MakervaerkstedetServerUpdate'
    [pscustomobject]@{Root=$root;Data=(Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'ElevhubServer');Store=$store;Anchor=(Join-Path $store 'owner.json');Helper=(Join-Path $store 'worker.ps1');Inbox=(Join-Path $store 'inbox');Request=(Join-Path $store 'inbox\request.json');State=(Join-Path $store 'state.json');Receipts=(Join-Path $store 'receipts');Downloads=(Join-Path $store 'downloads');Python=(Join-Path $root 'runtime\python.exe');Worker=(Join-Path $root 'server\server_update_worker.py');PowerShell=(Join-Path ([Environment]::GetFolderPath('Windows')) 'System32\WindowsPowerShell\v1.0\powershell.exe');Task='Makervaerkstedet-ServerUpdate'}
}

function Assert-UpdatePlain([string]$Path) {
    if(-not [IO.Path]::IsPathRooted($Path) -or $Path.StartsWith('\\')){throw 'En lokal, absolut installationssti kraeves.'}
    $p=[IO.Path]::GetFullPath($Path)
    while($p){
        if(Test-Path -LiteralPath $p){if((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Installationsstien indeholder et link.'}}
        $next=Split-Path $p -Parent;if($next -eq $p){break};$p=$next
    }
}


function Get-UpdateSid([string]$Identity) {
    if($Identity -match '^S-1-'){return [Security.Principal.SecurityIdentifier]::new($Identity).Value}
    return [Security.Principal.NTAccount]::new($Identity).Translate([Security.Principal.SecurityIdentifier]).Value
}


function Assert-UpdateProtected([string]$Path) {
    Assert-UpdatePlain $Path
    $acl=Get-Acl -LiteralPath $Path
    $trusted=@('S-1-5-18','S-1-5-32-544','S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    if($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted){throw 'Installationsfilens ejer er ikke betroet.'}
    # Modify also contains read/execute bits: only intersect actual mutation bits.
    $write=[Security.AccessControl.FileSystemRights]'Write,Delete,ChangePermissions,TakeOwnership,DeleteSubdirectoriesAndFiles'
    foreach($ace in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])){
        if($ace.AccessControlType -eq 'Allow' -and -not ($ace.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and ($ace.FileSystemRights -band $write) -and $ace.IdentityReference.Value -notin $trusted){throw 'Installationsfilen kan aendres af en ubeføjet konto.'}
    }
}


function Read-UpdateJson([string]$Path) {
    Assert-UpdatePlain $Path
    if((Get-Item -LiteralPath $Path -Force).Length -gt 65536){throw 'Installationsmetadata er for store.'}
    Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}


function Assert-UpdateOwner($Owner,[string]$Root,[string]$Data,[string]$ExpectedId) {
    $id=[Guid]::Empty
    if($Owner.schemaVersion -ne 1 -or $Owner.product -cne 'ElevhubServer' -or $Owner.state -cne 'installed' -or $Owner.taskName -cne 'ElevhubServer' -or [string]$Owner.installRoot -ine $Root -or [string]$Owner.dataRoot -ine $Data -or -not [Guid]::TryParseExact([string]$Owner.ownerId,'D',[ref]$id) -or $id -eq [Guid]::Empty -or ($ExpectedId -and [string]$Owner.ownerId -cne $ExpectedId)){throw 'Installationsejeren er aendret eller installationen er ikke aktiv.'}
}


function Get-UpdateSddl {return 'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FRFX;;;LS)'}

function Get-UpdateArguments($Paths) {return '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$Paths.Helper+'" -Mode Execute'}

function Get-UpdateDescription([string]$OwnerId) {return 'Makervaerkstedet server update; owner='+$OwnerId}


function Get-UpdateTaskXml($Paths,[string]$OwnerId) {
    $description=[Security.SecurityElement]::Escape((Get-UpdateDescription $OwnerId))
    $command=[Security.SecurityElement]::Escape($Paths.PowerShell)
    $arguments=[Security.SecurityElement]::Escape((Get-UpdateArguments $Paths))
    $directory=[Security.SecurityElement]::Escape($Paths.Store)
    return @"
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
<RegistrationInfo><Description>$description</Description></RegistrationInfo><Triggers />
<Principals><Principal id="System"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><AllowHardTerminate>false</AllowHardTerminate><StartWhenAvailable>false</StartWhenAvailable><AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled><ExecutionTimeLimit>PT2H</ExecutionTimeLimit></Settings>
<Actions Context="System"><Exec><Command>$command</Command><Arguments>$arguments</Arguments><WorkingDirectory>$directory</WorkingDirectory></Exec></Actions></Task>
"@
}


function Connect-UpdateScheduler {
    $service=New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    return $service.GetFolder('\')
}


function Find-UpdateTask($Folder,$Paths) {
    try {return $Folder.GetTask($Paths.Task)}
    catch {if(($_.Exception.HResult -band 0xFFFF) -eq 2 -or ($_.Exception.InnerException -and ($_.Exception.InnerException.HResult -band 0xFFFF) -eq 2)){return $null};throw}
}


function Assert-UpdateTask($Task,$Paths,[string]$OwnerId) {
    if($null -eq $Task){throw 'Serveropdatering er ikke installeret.'}
    [xml]$xml=$Task.Xml
    $ns=[Xml.XmlNamespaceManager]::new($xml.NameTable);$ns.AddNamespace('t','http://schemas.microsoft.com/windows/2004/02/mit/task')
    $expected=@{
        'RegistrationInfo/Description'=(Get-UpdateDescription $OwnerId);
        'Principals/Principal/UserId'='S-1-5-18';'Principals/Principal/RunLevel'='HighestAvailable';
        'Actions/Exec/Command'=$Paths.PowerShell;'Actions/Exec/Arguments'=(Get-UpdateArguments $Paths);'Actions/Exec/WorkingDirectory'=$Paths.Store;
        'Settings/MultipleInstancesPolicy'='IgnoreNew';'Settings/Enabled'='true';'Settings/AllowStartOnDemand'='true';'Settings/ExecutionTimeLimit'='PT2H'
    }
    foreach($key in $expected.Keys){
        $nodes=$xml.SelectNodes('/t:Task/t:'+($key -replace '/','/t:'),$ns)
        # Native Windows export omits only these default-true values.
        # Explicit false/empty values and duplicate elements still fail closed.
        if($nodes.Count -eq 0 -and $key -in @('Settings/Enabled','Settings/AllowStartOnDemand')){continue}
        if($nodes.Count -ne 1){throw ('Opdateringsopgavens definition er aendret: '+$key)}
        $actual=[string]$nodes[0].InnerText
        if($key -eq 'Principals/Principal/UserId'){$actual=Get-UpdateSid $actual}
        if($actual -cne $expected[$key]){throw ('Opdateringsopgavens definition er aendret: '+$key)}
    }
    foreach($path in @('/t:Task/t:Triggers/*','/t:Task/t:Settings/t:RestartOnFailure')){if($xml.SelectNodes($path,$ns).Count){throw 'Opdateringsopgaven maa kun koere efter en udtrykkelig bestilling.'}}
    # ServiceAccount is a COM enum value (5), not a valid XML LogonType.
    # SYSTEM tasks omit the XML element; an explicit logon mode is a change.
    if($xml.SelectNodes('/t:Task/t:Principals/t:Principal/t:LogonType',$ns).Count){throw 'SYSTEM-opgavens logontype er aendret.'}
    if($xml.SelectNodes('/t:Task/t:Actions/*',$ns).Count -ne 1 -or $xml.SelectNodes('/t:Task/t:Principals/*',$ns).Count -ne 1){throw 'Opdateringsopgaven har fremmede handlinger.'}
    $security=[Security.AccessControl.RawSecurityDescriptor]::new([string]$Task.GetSecurityDescriptor(7))
    $expectedSecurity=[Security.AccessControl.RawSecurityDescriptor]::new((Get-UpdateSddl))
    # Task Scheduler may reorder equivalent allow ACEs. Compare their exact
    # rights/flags/SIDs, while still rejecting extra, inherited or deny ACEs.
    if($security.Owner.Value -cne $expectedSecurity.Owner.Value -or $security.Group.Value -cne $expectedSecurity.Group.Value -or -not ($security.ControlFlags -band [Security.AccessControl.ControlFlags]::DiscretionaryAclProtected) -or $security.DiscretionaryAcl.Count -ne 3){throw 'Opdateringsopgavens adgangsrettigheder er aendret.'}
    $actualAces=@($security.DiscretionaryAcl|ForEach-Object {$bytes=[byte[]]::new($_.BinaryLength);$_.GetBinaryForm($bytes,0);[Convert]::ToBase64String($bytes)}|Sort-Object)
    $expectedAces=@($expectedSecurity.DiscretionaryAcl|ForEach-Object {$bytes=[byte[]]::new($_.BinaryLength);$_.GetBinaryForm($bytes,0);[Convert]::ToBase64String($bytes)}|Sort-Object)
    if(($actualAces -join ';') -cne ($expectedAces -join ';')){throw 'Opdateringsopgavens adgangsrettigheder er aendret.'}
}


function Assert-UpdateAdministrator {
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    if(-not [Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Handlingen kraever administratorrettigheder.'}
}

function Assert-UpdateSystem {if([Security.Principal.WindowsIdentity]::GetCurrent().User.Value -cne 'S-1-5-18'){throw 'Kun den installerede SYSTEM-opgave maa udfoere opdateringen.'}}

function Get-UpdateAnchor($Paths) {
    Assert-UpdateProtected $Paths.Store
    Assert-UpdateProtected $Paths.Anchor
    $anchor=Read-UpdateJson $Paths.Anchor
    $id=[Guid]::Empty
    if($anchor.schema -ne 1 -or @($anchor.PSObject.Properties).Count -ne 2 -or -not [Guid]::TryParseExact([string]$anchor.owner_id,'D',[ref]$id) -or $id -eq [Guid]::Empty){throw 'Ugyldig opdateringsejer.'}
    return [string]$anchor.owner_id
}

function Get-UpdateInstallation($Paths,[string]$ExpectedId) {
    $path=Join-Path $Paths.Data 'installation-owner.json'
    Assert-UpdateProtected $path
    $owner=Read-UpdateJson $path
    Assert-UpdateOwner $owner $Paths.Root $Paths.Data $ExpectedId
    return [string]$owner.ownerId
}

function Assert-UpdateCode($Paths) {
    foreach($path in @((Split-Path $Paths.Root -Parent),$Paths.Root,(Split-Path $Paths.Python -Parent),$Paths.Python,(Split-Path $Paths.Worker -Parent),$Paths.Worker)){Assert-UpdateProtected $path}
}

function Get-UpdateMutexName {return 'Global\Makervaerkstedet-ServerUpdateBridge'}

function Get-UpdateMutexRights {return [Security.AccessControl.MutexRights]'Modify,Synchronize'}

function Get-UpdateMutexSecurity {
    $security=[Security.AccessControl.MutexSecurity]::new()
    $security.SetAccessRuleProtection($true,$false)
    foreach($sid in @('S-1-5-18','S-1-5-32-544')){
        $security.AddAccessRule([Security.AccessControl.MutexAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),[Security.AccessControl.MutexRights]::FullControl,'Allow'))
    }
    $security.AddAccessRule([Security.AccessControl.MutexAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-19'),(Get-UpdateMutexRights),'Allow'))
    return $security
}

function New-UpdateMutex {
    $name=Get-UpdateMutexName
    # Do not use the creation constructor to open an existing object.
    # LocalService only needs WaitOne/ReleaseMutex, even with a SYSTEM handle.
    try {return [Threading.Mutex]::OpenExisting($name,(Get-UpdateMutexRights))}
    catch [Threading.WaitHandleCannotBeOpenedException] {
        $created=$false
        try {return [Threading.Mutex]::new($false,$name,[ref]$created,(Get-UpdateMutexSecurity))}
        catch [UnauthorizedAccessException] {
            # Another identity may create the object between open and create.
            # Reopen with minimal rights; never repair an existing object's ACL.
            return [Threading.Mutex]::OpenExisting($name,(Get-UpdateMutexRights))
        }
    }
}

function Set-UpdateDirectoryAcl([string]$Path,[switch]$Inbox) {
    $acl=[Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true,$false);$acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    foreach($sid in @('S-1-5-18','S-1-5-32-544')){$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','ContainerInherit,ObjectInherit','None','Allow'))}
    $rights=if($Inbox){'Modify'}else{'ReadAndExecute'}
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-19'),$rights,'ContainerInherit,ObjectInherit','None','Allow'))
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Assert-UpdateInbox($Paths) {
    Assert-UpdatePlain $Paths.Inbox
    $acl=Get-Acl -LiteralPath $Paths.Inbox
    if($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18','S-1-5-32-544') -or -not $acl.AreAccessRulesProtected){throw 'Indbakken har en fremmed ejer.'}
    $expected=[Security.AccessControl.DirectorySecurity]::new()
    $expected.SetSecurityDescriptorSddlForm('O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1301bf;;;LS)')
    $actualRules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])|ForEach-Object {"$($_.IdentityReference.Value):$([int]$_.FileSystemRights):$($_.AccessControlType):$([int]$_.InheritanceFlags):$([int]$_.PropagationFlags):$($_.IsInherited)"}|Sort-Object)
    $expectedRules=@($expected.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])|ForEach-Object {"$($_.IdentityReference.Value):$([int]$_.FileSystemRights):$($_.AccessControlType):$([int]$_.InheritanceFlags):$([int]$_.PropagationFlags):$($_.IsInherited)"}|Sort-Object)
    if(($actualRules -join ';') -cne ($expectedRules -join ';')){throw 'Indbakkens adgangsrettigheder er aendret.'}
}

function Write-UpdateJsonAtomic([string]$Path,$Value) {
    Assert-UpdatePlain $Path
    $temporary=Join-Path (Split-Path $Path -Parent) ([Guid]::NewGuid().ToString('N')+'.pending')
    $stream=[IO.File]::Open($temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try {$bytes=[Text.UTF8Encoding]::new($false).GetBytes(($Value|ConvertTo-Json -Depth 12 -Compress));$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
    try {if(Test-Path -LiteralPath $Path){[IO.File]::Replace($temporary,$Path,[NullString]::Value)}else{[IO.File]::Move($temporary,$Path)}}finally{if(Test-Path -LiteralPath $temporary){Remove-Item -LiteralPath $temporary}}
}

function Initialize-UpdateStore($Paths,[string]$OwnerId) {
    Assert-UpdatePlain $Paths.Store
    if(Test-Path -LiteralPath $Paths.Store){if((Get-UpdateAnchor $Paths) -cne $OwnerId){throw 'En anden installation ejer opdateringsmappen.'}}
    else {
        New-Item -ItemType Directory -Path $Paths.Store|Out-Null
        Set-UpdateDirectoryAcl $Paths.Store
        Write-UpdateJsonAtomic $Paths.Anchor @{schema=1;owner_id=$OwnerId}
    }
    foreach($directory in @($Paths.Inbox,$Paths.Receipts,$Paths.Downloads)){
        Assert-UpdatePlain $directory
        if(-not (Test-Path -LiteralPath $directory)){New-Item -ItemType Directory -Path $directory|Out-Null}
        if($directory -eq $Paths.Inbox){Set-UpdateDirectoryAcl $directory -Inbox}else{Assert-UpdateProtected $directory}
    }
    Assert-UpdateProtected $script:UpdateSource
    $temporary=Join-Path $Paths.Store ([Guid]::NewGuid().ToString('N')+'.pending')
    try {
        [IO.File]::Copy($script:UpdateSource,$temporary,$false)
        if(Test-Path -LiteralPath $Paths.Helper){Assert-UpdateProtected $Paths.Helper;[IO.File]::Replace($temporary,$Paths.Helper,[NullString]::Value)}else{[IO.File]::Move($temporary,$Paths.Helper)}
    }finally{if(Test-Path -LiteralPath $temporary){Remove-Item -LiteralPath $temporary}}
    Assert-UpdateProtected $Paths.Helper
    Assert-UpdateInbox $Paths
}

function Assert-UpdateRequest($Request) {
    $id=[Guid]::Empty
    $fields=@($Request.PSObject.Properties.Name|Sort-Object) -join ','
    if($fields -cne 'action,command_id,manifest_sha256,schema' -or $Request.schema -isnot [int] -or $Request.schema -ne 1 -or $Request.action -cnotin @('check','install') -or -not [Guid]::TryParseExact([string]$Request.command_id,'D',[ref]$id) -or $id -eq [Guid]::Empty -or [string]$Request.command_id -cne $id.ToString('D')){throw 'Ugyldig opdateringsbestilling.'}
    if(($Request.action -ceq 'check' -and $null -ne $Request.manifest_sha256) -or ($Request.action -ceq 'install' -and [string]$Request.manifest_sha256 -cnotmatch '^[a-f0-9]{64}$')){throw 'Ugyldig udgivelsesbinding.'}
}

function Test-UpdateSameRequest($A,$B) {
    return ($A.schema -eq $B.schema -and $A.action -ceq $B.action -and $A.command_id -ceq $B.command_id -and $A.manifest_sha256 -ceq $B.manifest_sha256)
}

function Get-UpdateReceipt($Paths,[string]$Id) {
    $path=Join-Path $Paths.Receipts ($Id+'.json')
    Assert-UpdatePlain $path
    if(-not (Test-Path -LiteralPath $path)){return $null}
    Assert-UpdateProtected $path
    $receipt=Read-UpdateJson $path;Assert-UpdateRequest $receipt
    if($receipt.command_id -cne $Id){throw 'Bestillingskvitteringen er aendret.'}
    return $receipt
}

function Invoke-UpdatePython($Paths,[string]$Action,[int]$ExitCode=0) {
    Assert-UpdateCode $Paths
    $arguments=@('-I','-B',$Paths.Worker,'--action',$Action,'--store',$Paths.Store)
    if($Action -eq 'finish'){$arguments+=@('--exit-code',[string]$ExitCode)}
    if($Action -eq 'fail'){$arguments+=@('--code','worker_failed')}
    $output=@(& $Paths.Python @arguments 2>$null)
    if($LASTEXITCODE -ne 0){throw 'Opdateringsarbejderen kunne ikke afslutte handlingen.'}
    $json=$output -join "`n"
    if($json.Length -gt 65536 -or -not $json){throw 'Opdateringsarbejderens svar er ugyldigt.'}
    return $json|ConvertFrom-Json
}

function Assert-UpdatePlan($Paths,$Plan,$Request) {
    if(@($Plan.PSObject.Properties).Count -ne 4 -or $Plan.command_id -cne $Request.command_id -or [string]$Plan.sha256 -cnotmatch '^[a-f0-9]{64}$' -or [string]$Plan.version -cnotmatch '^[A-Za-z0-9][A-Za-z0-9.+~_-]{0,127}$'){throw 'Ugyldig installationsplan.'}
    $path=[string]$Plan.exe
    Assert-UpdatePlain $path
    $absolute=[IO.Path]::GetFullPath($path)
    $bound=[IO.Path]::GetFullPath($Paths.Downloads).TrimEnd('\')+'\'
    if($absolute -cne $path -or -not $absolute.StartsWith($bound,[StringComparison]::OrdinalIgnoreCase) -or [IO.Path]::GetExtension($path) -ine '.exe' -or -not (Test-Path -LiteralPath $path -PathType Leaf)){throw 'Installationsfilen ligger uden for den beskyttede downloadmappe.'}
    $parent=Split-Path $path -Parent
    while($parent -and $parent.StartsWith($bound,[StringComparison]::OrdinalIgnoreCase)){Assert-UpdateProtected $parent;$parent=Split-Path $parent -Parent}
    Assert-UpdateProtected $Paths.Downloads;Assert-UpdateProtected $path
    if((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Plan.sha256){throw 'Installationsfilen har aendret indhold.'}
}

function Invoke-UpdateInstaller($Paths,$Plan) {
    $log=Join-Path $Paths.Store ('installer-'+$Plan.command_id+'.log')
    Assert-UpdatePlain $log
    if(Test-Path -LiteralPath $log){Assert-UpdateProtected $log}
    $arguments='/SP- /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /UPDATEID='+$Plan.command_id+' /LOG="'+$log+'"'
    $process=Start-Process -FilePath $Plan.exe -ArgumentList $arguments -WorkingDirectory $Paths.Store -WindowStyle Hidden -Wait -PassThru
    return [int]$process.ExitCode
}

function Get-UpdateStatus($Paths,$Task) {
    $pending=[int]$Task.State -in @(2,4)
    $state=@{state='idle'}
    if(Test-Path -LiteralPath $Paths.State){Assert-UpdateProtected $Paths.State;$state=Read-UpdateJson $Paths.State}
    $fresh=$false
    if(Test-Path -LiteralPath $Paths.Request){Assert-UpdatePlain $Paths.Request;$fresh=((Get-Date).ToUniversalTime()-(Get-Item -LiteralPath $Paths.Request).LastWriteTimeUtc).TotalSeconds -lt 5}
    if(-not $pending -and -not $fresh -and $state.state -cin @('checking','downloading','verifying','installing')){
        $age=if(Test-Path -LiteralPath $Paths.State){((Get-Date).ToUniversalTime()-(Get-Item -LiteralPath $Paths.State).LastWriteTimeUtc).TotalSeconds}else{0}
        if($age -ge 5){$state.state='interrupted'}
    }
    return @{schema=1;available=$true;pending=($pending -or $fresh);state=$state}
}

function Invoke-ServerUpdate {
    param([string]$Mode,[string]$CommandId='', [string]$ManifestSha256='')
    $paths=Get-UpdatePaths
    if($Mode -in @('Install','Remove')){Assert-UpdateAdministrator}
    if($Mode -eq 'Execute'){Assert-UpdateSystem}
    $lease=$null;$leased=$false
    try {
        if($Mode -ne 'Status'){
            $lease=New-UpdateMutex
            try {$leased=$lease.WaitOne(0)}catch [Threading.AbandonedMutexException]{$leased=$true}
            if(-not $leased){throw 'En anden serveropdateringshandling er i gang.'}
        }
        if($Mode -eq 'Remove' -and -not (Test-Path -LiteralPath $paths.Store)){
            Assert-UpdatePlain $paths.Store
            $absentFolder=Connect-UpdateScheduler
            if($null -eq (Find-UpdateTask $absentFolder $paths)){return @{schema=1;removed=$false}}
            throw 'Opdateringsopgaven findes uden installationsejer.'
        }
        $ownerId=Get-UpdateInstallation $paths $(if($Mode -eq 'Install'){$null}else{Get-UpdateAnchor $paths})
        $folder=Connect-UpdateScheduler;$task=Find-UpdateTask $folder $paths
        if($Mode -eq 'Install'){
            Assert-UpdateCode $paths
            if($null -ne $task){Assert-UpdateTask $task $paths $ownerId}
            Initialize-UpdateStore $paths $ownerId
            if($null -eq $task){
                $task=$folder.RegisterTask($paths.Task,(Get-UpdateTaskXml $paths $ownerId),2,'S-1-5-18',$null,5,(Get-UpdateSddl))
                try {$task.SetSecurityDescriptor((Get-UpdateSddl),16);Assert-UpdateTask $task $paths $ownerId}catch{$folder.DeleteTask($paths.Task,0);throw}
            }
            return @{schema=1;installed=$true}
        }
        Assert-UpdateProtected $paths.Helper;Assert-UpdateProtected $paths.Receipts;Assert-UpdateInbox $paths
        if($Mode -eq 'Remove' -and $null -eq $task){return @{schema=1;removed=$false;store_preserved=$true}}
        Assert-UpdateTask $task $paths $ownerId
        if($Mode -eq 'Status'){return Get-UpdateStatus $paths $task}
        if($Mode -eq 'Remove'){
            if([int]$task.State -in @(2,4)){throw 'Afvent den igangvaerende opdatering.'}
            $folder.DeleteTask($paths.Task,0)
            # Evidence/downloads remain owner-protected; never recursively delete.
            return @{schema=1;removed=$true;store_preserved=$true}
        }
        if($Mode -in @('Check','Request')){
            $request=[pscustomobject]@{schema=1;action=$(if($Mode -eq 'Check'){'check'}else{'install'});command_id=$CommandId;manifest_sha256=$(if($Mode -eq 'Check'){$null}else{$ManifestSha256})}
            Assert-UpdateRequest $request
            $receipt=Get-UpdateReceipt $paths $request.command_id
            if($null -ne $receipt){if(-not (Test-UpdateSameRequest $request $receipt)){throw 'Bestillings-ID er allerede brugt.'};return @{schema=1;accepted=$true;already_pending=$true;command_id=$CommandId;receipt=$receipt}}
            if(Test-Path -LiteralPath $paths.Request){
                $prior=Read-UpdateJson $paths.Request;Assert-UpdateRequest $prior
                if(Test-UpdateSameRequest $prior $request){if([int]$task.State -notin @(2,4)){$null=$task.Run($null)};return @{schema=1;accepted=$true;already_pending=$true;command_id=$CommandId}}
                if($null -eq (Get-UpdateReceipt $paths $prior.command_id)){throw 'En anden bestilling afventer behandling.'}
            }
            if([int]$task.State -in @(2,4)){throw 'En anden bestilling behandles.'}
            Write-UpdateJsonAtomic $paths.Request $request
            $null=$task.Run($null)
            return @{schema=1;accepted=$true;already_pending=$false;command_id=$CommandId}
        }
        if($Mode -eq 'Execute'){
            $request=Read-UpdateJson $paths.Request;Assert-UpdateRequest $request
            # The installer may refresh this same owner's demand-only task.
            # Do not hold the enqueue lease across that independent process.
            $lease.ReleaseMutex();$leased=$false
            $lease.Dispose();$lease=$null
            $priorLocation=Get-Location
            try {
                Set-Location -LiteralPath $paths.Store
                if($request.action -ceq 'check'){$result=Invoke-UpdatePython $paths 'check';return @{schema=1;accepted=$true}}
                $plan=Invoke-UpdatePython $paths 'prepare'
                Assert-UpdatePlan $paths $plan $request
                $exitCode=Invoke-UpdateInstaller $paths $plan
                $result=Invoke-UpdatePython $paths 'finish' $exitCode
                return @{schema=1;accepted=$true}
            }catch{
                try {$result=Invoke-UpdatePython $paths 'fail'}catch{}
                throw 'Serveropdateringen kunne ikke afsluttes. Se opdateringsstatus.'
            }finally{
                Set-Location -LiteralPath $priorLocation.Path
                if(Test-Path -LiteralPath $paths.Request){
                    try {$current=Read-UpdateJson $paths.Request;Assert-UpdateRequest $current;if(Test-UpdateSameRequest $current $request){Remove-Item -LiteralPath $paths.Request}}catch{}
                }
            }
        }
        throw 'Ukendt opdateringshandling.'
    }finally{if($lease){if($leased){$lease.ReleaseMutex()};$lease.Dispose()}}
}

try {Invoke-ServerUpdate -Mode $Mode -CommandId $CommandId -ManifestSha256 $ManifestSha256|ConvertTo-Json -Depth 12 -Compress}
catch {@{schema=1;available=$false;accepted=$false;error=$_.Exception.Message}|ConvertTo-Json -Compress;exit 1}
