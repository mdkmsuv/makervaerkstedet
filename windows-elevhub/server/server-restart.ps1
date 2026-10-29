# Fixed, demand-only Windows restart bridge. Credits: Klostermarksskolen.
[CmdletBinding()]
param([ValidateSet('Status','Request','Execute','Install','Remove','Local')][string]$Mode='Status')
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$env:PSModulePath=Join-Path $PSHOME 'Modules'

function Get-RestartPaths {
    $root=Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'ElevhubServer'
    $windows=[Environment]::GetFolderPath('Windows')
    $store=Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'MakervaerkstedetServerRestart'
    [pscustomobject]@{Root=$root;Data=(Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'ElevhubServer');Helper=(Join-Path $root 'server\server-restart.ps1');Store=$store;Anchor=(Join-Path $store 'owner.json');PowerShell=(Join-Path $windows 'System32\WindowsPowerShell\v1.0\powershell.exe');Shutdown=(Join-Path $windows 'System32\shutdown.exe');Task='Makervaerkstedet-GenstartServer'}
}

function Assert-RestartPlain([string]$Path) {
    if(-not [IO.Path]::IsPathRooted($Path) -or $Path.StartsWith('\\')){throw 'En lokal, absolut installationssti kraeves.'}
    $p=[IO.Path]::GetFullPath($Path)
    while($p){
        if(Test-Path -LiteralPath $p){if((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Installationsstien indeholder et link.'}}
        $next=Split-Path $p -Parent;if($next -eq $p){break};$p=$next
    }
}

function Get-RestartSid([string]$Identity) {
    if($Identity -match '^S-1-'){return [Security.Principal.SecurityIdentifier]::new($Identity).Value}
    return [Security.Principal.NTAccount]::new($Identity).Translate([Security.Principal.SecurityIdentifier]).Value
}

function Assert-RestartProtected([string]$Path) {
    Assert-RestartPlain $Path
    $acl=Get-Acl -LiteralPath $Path
    $trusted=@('S-1-5-18','S-1-5-32-544','S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    if($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted){throw 'Installationsfilens ejer er ikke betroet.'}
    # Modify also contains read/execute bits: only intersect actual mutation bits.
    $write=[Security.AccessControl.FileSystemRights]'Write,Delete,ChangePermissions,TakeOwnership,DeleteSubdirectoriesAndFiles'
    foreach($ace in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])){
        if($ace.AccessControlType -eq 'Allow' -and -not ($ace.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and ($ace.FileSystemRights -band $write) -and $ace.IdentityReference.Value -notin $trusted){throw 'Installationsfilen kan aendres af en ubeføjet konto.'}
    }
}

function Assert-RestartCode($Paths) {
    foreach($p in @((Split-Path $Paths.Root -Parent),$Paths.Root,(Split-Path $Paths.Helper -Parent),$Paths.Helper)) {Assert-RestartProtected $p}
}

function Read-RestartJson([string]$Path) {
    Assert-RestartPlain $Path
    if((Get-Item -LiteralPath $Path -Force).Length -gt 65536){throw 'Installationsmetadata er for store.'}
    Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Get-RestartAnchor($Paths) {
    Assert-RestartProtected $Paths.Store
    Assert-RestartProtected $Paths.Anchor
    $a=Read-RestartJson $Paths.Anchor
    $id=[Guid]::Empty
    if($a.schema -ne 1 -or @($a.PSObject.Properties).Count -ne 2 -or -not [Guid]::TryParseExact([string]$a.owner_id,'D',[ref]$id) -or $id -eq [Guid]::Empty){throw 'Genstartsrettigheden mangler en gyldig installationsejer.'}
    return [string]$a.owner_id
}

function Assert-RestartOwner($Owner,[string]$Root,[string]$Data,[string]$ExpectedId) {
    $id=[Guid]::Empty
    if($Owner.schemaVersion -ne 1 -or $Owner.product -cne 'ElevhubServer' -or $Owner.state -cne 'installed' -or $Owner.taskName -cne 'ElevhubServer' -or [string]$Owner.installRoot -ine $Root -or [string]$Owner.dataRoot -ine $Data -or -not [Guid]::TryParseExact([string]$Owner.ownerId,'D',[ref]$id) -or $id -eq [Guid]::Empty -or ($ExpectedId -and [string]$Owner.ownerId -cne $ExpectedId)){throw 'Installationsejeren er aendret eller installationen er ikke aktiv.'}
}

function Assert-RestartMaintenance($Paths) {
    $control=Join-Path $Paths.Root 'control'
    Assert-RestartPlain $control
    foreach($name in @('selection-transition.json','active-generation.json.pending')){
        if(Test-Path -LiteralPath (Join-Path $control $name)){throw 'Afslut det igangvaerende versionsskift foer genstart.'}
    }
    if(Test-Path -LiteralPath $control){
        foreach($journal in @(Get-ChildItem -LiteralPath $control -Filter 'school-maintenance-*.json' -File)){
            if((Read-RestartJson $journal.FullName).phase -cnotin @('complete','cancelled')){throw 'Afslut vedligeholdelsen foer genstart.'}
        }
        $tls=Join-Path $control 'school-tls-renewal.json'
        if((Test-Path -LiteralPath $tls) -and (Read-RestartJson $tls).phase -cne 'complete'){throw 'Afslut certifikatfornyelsen foer genstart.'}
    }
}

function Open-RestartGenerationLock($Paths) {
    $path=Join-Path $Paths.Root 'control\instance.lock'
    Assert-RestartPlain $path
    $stream=[IO.File]::Open($path,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
    try {
        if($stream.Length -eq 0){$stream.WriteByte(49);$stream.Flush()}
        $stream.Lock(0,1) # Same byte-range lock as app.instance.InstanceLock.
        return $stream
    } catch {$stream.Dispose();throw 'En anden vedligeholdelse ejer installationen.'}
}

function Open-RestartSelectionLock($Paths) {
    $path=Join-Path $Paths.Root 'control\generation.lock'
    Assert-RestartPlain $path
    if(-not ('Makervaerkstedet.RestartSelectionLease' -as [type])){
        Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
namespace Makervaerkstedet {
 public sealed class RestartSelectionLease : IDisposable {
  [StructLayout(LayoutKind.Sequential)]
  private struct Overlapped {
   public IntPtr Internal, InternalHigh;
   public uint Offset, OffsetHigh;
   public IntPtr Event;
  }
  [DllImport("kernel32.dll", SetLastError=true)]
  private static extern bool LockFileEx(SafeFileHandle handle, uint flags,
   uint reserved, uint low, uint high, ref Overlapped overlapped);
  private FileStream file;
  public RestartSelectionLease(string path) {
   // Existing marker only; deny replacement while leased. Shared readers
   // remain compatible with running apps, unlike an exclusive FileStream.Lock.
   file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
   var overlapped = new Overlapped();
   if (!LockFileEx(file.SafeFileHandle, 1, 0, 1, 0, ref overlapped)) {
    var error = Marshal.GetLastWin32Error();
    file.Dispose(); file = null;
    throw new Win32Exception(error);
   }
  }
  public void Dispose() { if(file != null) { file.Dispose(); file = null; } }
 }
}
'@
    }
    return [Makervaerkstedet.RestartSelectionLease]::new($path)
}

function Get-RestartInstallation($Paths,[string]$ExpectedId) {
    $ownerPath=Join-Path $Paths.Data 'installation-owner.json'
    Assert-RestartProtected $ownerPath
    $owner=Read-RestartJson $ownerPath
    Assert-RestartOwner $owner $Paths.Root $Paths.Data $ExpectedId
    if($owner.PSObject.Properties.Name -contains 'startupProtocol'){
        if($owner.startupProtocol -cne 'generation-v1' -or $owner.generationLayout -ne $true){throw 'Ukendt generationsinstallation.'}
        $selected=Read-RestartJson (Join-Path $Paths.Root 'control\active-generation.json')
        $id=([Guid]$owner.ownerId).ToString('N')
        if($selected.format -ne 1 -or $selected.installation_id -cne $id -or $selected.runtime_root -ine $Paths.Root -or $selected.revision -lt 1 -or $selected.manifest_sha256 -cnotmatch '^[a-f0-9]{64}$'){throw 'Det aktive generationsvalg tilhoerer ikke installationen.'}
        Assert-RestartPlain ([string]$selected.program_root)
        Assert-RestartPlain ([string]$selected.data_root)
        $selectedOwnerPath=Join-Path $selected.data_root 'installation-owner.json'
        Assert-RestartProtected $selectedOwnerPath
        $selectedOwner=Read-RestartJson $selectedOwnerPath
        Assert-RestartOwner $selectedOwner $Paths.Root $selected.data_root ([string]$owner.ownerId)
        if($selectedOwner.startupProtocol -cne 'generation-v1' -or $selectedOwner.generationLayout -ne $true){throw 'Den aktive dataejers generationsprotokol er aendret.'}
        $data=[string]$selected.data_root
        while($data){
            if(Test-Path -LiteralPath (Join-Path $data '.restore-hold.json')){throw 'Den aktive datageneration er spaerret til gendannelse.'}
            $parent=Split-Path $data -Parent;if($parent -eq $data){break};$data=$parent
        }
    } elseif(Test-Path -LiteralPath (Join-Path $Paths.Root 'control')) {throw 'Kontrolmappen kraever en gyldig generationsinstallation.'}
    return $owner
}

function Get-RestartSddl {return 'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FRFX;;;LS)'}
function Get-RestartArguments($Paths) {return '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$Paths.Helper+'" -Mode Execute'}
function Get-RestartDescription([string]$OwnerId) {return 'Makervaerkstedet server restart; owner='+$OwnerId}

function Get-RestartTaskXml($Paths,[string]$OwnerId) {
    $description=[Security.SecurityElement]::Escape((Get-RestartDescription $OwnerId))
    $command=[Security.SecurityElement]::Escape($Paths.PowerShell)
    $arguments=[Security.SecurityElement]::Escape((Get-RestartArguments $Paths))
    $directory=[Security.SecurityElement]::Escape((Split-Path $Paths.Helper -Parent))
    return @"
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
<RegistrationInfo><Description>$description</Description></RegistrationInfo><Triggers />
<Principals><Principal id="System"><UserId>S-1-5-18</UserId><LogonType>ServiceAccount</LogonType><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><AllowHardTerminate>false</AllowHardTerminate><StartWhenAvailable>false</StartWhenAvailable><AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled><ExecutionTimeLimit>PT1M</ExecutionTimeLimit></Settings>
<Actions Context="System"><Exec><Command>$command</Command><Arguments>$arguments</Arguments><WorkingDirectory>$directory</WorkingDirectory></Exec></Actions></Task>
"@
}

function Connect-RestartScheduler {
    $service=New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    return $service.GetFolder('\')
}

function Find-RestartTask($Folder,$Paths) {
    try {return $Folder.GetTask($Paths.Task)}
    catch {if(($_.Exception.HResult -band 0xFFFF) -eq 2 -or ($_.Exception.InnerException -and ($_.Exception.InnerException.HResult -band 0xFFFF) -eq 2)){return $null};throw}
}

function Assert-RestartTask($Task,$Paths,[string]$OwnerId) {
    if($null -eq $Task){throw 'Genstart af serveren er ikke installeret.'}
    [xml]$xml=$Task.Xml
    $ns=[Xml.XmlNamespaceManager]::new($xml.NameTable);$ns.AddNamespace('t','http://schemas.microsoft.com/windows/2004/02/mit/task')
    $expected=@{
        'RegistrationInfo/Description'=(Get-RestartDescription $OwnerId);
        'Principals/Principal/UserId'='S-1-5-18';'Principals/Principal/LogonType'='ServiceAccount';'Principals/Principal/RunLevel'='HighestAvailable';
        'Actions/Exec/Command'=$Paths.PowerShell;'Actions/Exec/Arguments'=(Get-RestartArguments $Paths);'Actions/Exec/WorkingDirectory'=(Split-Path $Paths.Helper -Parent);
        'Settings/MultipleInstancesPolicy'='IgnoreNew';'Settings/Enabled'='true';'Settings/AllowStartOnDemand'='true';'Settings/ExecutionTimeLimit'='PT1M'
    }
    foreach($key in $expected.Keys){
        $nodes=$xml.SelectNodes('/t:Task/t:'+($key -replace '/','/t:'),$ns)
        if($nodes.Count -ne 1){throw 'Genstartsopgavens definition er aendret.'}
        $actual=[string]$nodes[0].InnerText
        if($key -eq 'Principals/Principal/UserId'){$actual=Get-RestartSid $actual}
        if($actual -cne $expected[$key]){throw 'Genstartsopgavens definition er aendret.'}
    }
    foreach($path in @('/t:Task/t:Triggers/*','/t:Task/t:Settings/t:RestartOnFailure')){if($xml.SelectNodes($path,$ns).Count){throw 'Genstartsopgaven maa kun koere efter en udtrykkelig bestilling.'}}
    if($xml.SelectNodes('/t:Task/t:Actions/*',$ns).Count -ne 1 -or $xml.SelectNodes('/t:Task/t:Principals/*',$ns).Count -ne 1){throw 'Genstartsopgaven har fremmede handlinger.'}
    $security=[Security.AccessControl.RawSecurityDescriptor]::new([string]$Task.GetSecurityDescriptor(7))
    $expectedSecurity=[Security.AccessControl.RawSecurityDescriptor]::new((Get-RestartSddl))
    # Task Scheduler may reorder equivalent allow ACEs. Compare their exact
    # rights/flags/SIDs, while still rejecting extra, inherited or deny ACEs.
    if($security.Owner.Value -cne $expectedSecurity.Owner.Value -or $security.Group.Value -cne $expectedSecurity.Group.Value -or -not ($security.ControlFlags -band [Security.AccessControl.ControlFlags]::DiscretionaryAclProtected) -or $security.DiscretionaryAcl.Count -ne 3){throw 'Genstartsopgavens adgangsrettigheder er aendret.'}
    $actualAces=@($security.DiscretionaryAcl|ForEach-Object {$bytes=[byte[]]::new($_.BinaryLength);$_.GetBinaryForm($bytes,0);[Convert]::ToBase64String($bytes)}|Sort-Object)
    $expectedAces=@($expectedSecurity.DiscretionaryAcl|ForEach-Object {$bytes=[byte[]]::new($_.BinaryLength);$_.GetBinaryForm($bytes,0);[Convert]::ToBase64String($bytes)}|Sort-Object)
    if(($actualAces -join ';') -cne ($expectedAces -join ';')){throw 'Genstartsopgavens adgangsrettigheder er aendret.'}
}

function Assert-RestartAdministrator {
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    if(-not [Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Handlingen kraever administratorrettigheder.'}
}
function Assert-RestartSystem {if([Security.Principal.WindowsIdentity]::GetCurrent().User.Value -cne 'S-1-5-18'){throw 'Kun den installerede SYSTEM-opgave maa udfoere genstarten.'}}
function New-RestartMutex {return [Threading.Mutex]::new($false,'Global\Makervaerkstedet-SchoolFlatUpdate')}

function Initialize-RestartStore($Paths,[string]$OwnerId) {
    Assert-RestartPlain $Paths.Store
    if(Test-Path -LiteralPath $Paths.Store){
        if((Get-RestartAnchor $Paths) -cne $OwnerId){throw 'En anden installation ejer genstartsrettigheden.'}
        return
    }
    New-Item -ItemType Directory -Path $Paths.Store|Out-Null
    $acl=[Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true,$false);$acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    foreach($sid in @('S-1-5-18','S-1-5-32-544')){$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','ContainerInherit,ObjectInherit','None','Allow'))}
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-19'),'ReadAndExecute','ContainerInherit,ObjectInherit','None','Allow'))
    Set-Acl -LiteralPath $Paths.Store -AclObject $acl
    $pending=Join-Path $Paths.Store 'owner.pending'
    # CreateNew refuses a planted file/link, including during initial directory creation.
    $stream=[IO.File]::Open($pending,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try {$bytes=[Text.UTF8Encoding]::new($false).GetBytes((@{schema=1;owner_id=$OwnerId}|ConvertTo-Json -Compress));$stream.Write($bytes,0,$bytes.Length)}finally{$stream.Dispose()}
    $fileAcl=[Security.AccessControl.FileSecurity]::new()
    $fileAcl.SetAccessRuleProtection($true,$false);$fileAcl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    foreach($sid in @('S-1-5-18','S-1-5-32-544')){$fileAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','Allow'))}
    $fileAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-19'),'Read','Allow'))
    Set-Acl -LiteralPath $pending -AclObject $fileAcl
    Move-Item -LiteralPath $pending -Destination $Paths.Anchor
    Assert-RestartProtected $Paths.Anchor
}

function Invoke-RestartShutdown($Paths) {
    $arguments=@('/r','/t','0','/d','p:4:1','/c','Makervaerkstedet: teacher requested server restart')
    & $Paths.Shutdown @arguments | Out-Null
    if($LASTEXITCODE -ne 0){throw 'Windows afviste genstarten.'}
}

function Invoke-RestartLocal($Paths) {
    Add-Type -AssemblyName System.Windows.Forms
    $answer=[Windows.Forms.MessageBox]::Show('Vil du genstarte hele Windows-serveren nu? Gem dit arbejde. Elever og maskiner mister forbindelsen under genstarten.','Genstart Makervaerkstedets server',[Windows.Forms.MessageBoxButtons]::YesNo,[Windows.Forms.MessageBoxIcon]::Warning,[Windows.Forms.MessageBoxDefaultButton]::Button2)
    if($answer -ne [Windows.Forms.DialogResult]::Yes){return @{accepted=$false;cancelled=$true}}
    try {Assert-RestartAdministrator} catch {
        $arguments='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$Paths.Helper+'" -Mode Request'
        $process=Start-Process -FilePath $Paths.PowerShell -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
        if($process.ExitCode -ne 0){throw 'Genstarten blev ikke bestilt. Kontroller vedligeholdelse og installation.'}
        return @{accepted=$true}
    }
    return Invoke-ServerRestart -Mode Request
}

function Invoke-ServerRestart {
    param([string]$Mode)
    $paths=Get-RestartPaths
    Assert-RestartCode $paths
    if($Mode -eq 'Local'){return Invoke-RestartLocal $paths}
    if($Mode -in @('Install','Remove')){Assert-RestartAdministrator}
    if($Mode -eq 'Execute'){Assert-RestartSystem;Start-Sleep -Seconds 3}
    $lease=New-RestartMutex;$leased=$false;$generationLock=$null;$selectionLock=$null
    try {
        try {$leased=$lease.WaitOne(0)}catch [Threading.AbandonedMutexException]{$leased=$true}
        if(-not $leased){throw 'En serveropdatering er i gang. Proev igen efter opdateringen.'}
        if($Mode -eq 'Remove' -and -not (Test-Path -LiteralPath $paths.Store)){
            Assert-RestartPlain $paths.Store
            $absentFolder=Connect-RestartScheduler
            if($null -eq (Find-RestartTask $absentFolder $paths)){return @{removed=$false}}
            throw 'Genstartsopgaven findes uden ejerfil. Kontroller installationen foer fjernelse.'
        }
        if($Mode -in @('Install','Remove','Execute')){
            $rawOwner=Read-RestartJson (Join-Path $paths.Data 'installation-owner.json')
            if($rawOwner.startupProtocol -ceq 'generation-v1'){
                $generationLock=Open-RestartGenerationLock $paths
                $selectionLock=Open-RestartSelectionLock $paths
            }
            Assert-RestartMaintenance $paths
            $expectedId=if($Mode -eq 'Install'){$null}else{Get-RestartAnchor $paths}
            $owner=Get-RestartInstallation $paths $expectedId
            $ownerId=[string]$owner.ownerId
        } else {$ownerId=Get-RestartAnchor $paths;Assert-RestartMaintenance $paths}
        $folder=Connect-RestartScheduler
        $task=Find-RestartTask $folder $paths
        if($Mode -eq 'Install'){
            if($null -ne $task){Assert-RestartTask $task $paths $ownerId}
            Initialize-RestartStore $paths $ownerId
            if($null -eq $task){
                $task=$folder.RegisterTask($paths.Task,(Get-RestartTaskXml $paths $ownerId),2,'S-1-5-18',$null,5,(Get-RestartSddl))
                try {$task.SetSecurityDescriptor((Get-RestartSddl),16);Assert-RestartTask $task $paths $ownerId}
                catch {$folder.DeleteTask($paths.Task,0);throw}
            }
            return @{installed=$true}
        }
        Assert-RestartTask $task $paths $ownerId
        if($Mode -eq 'Status'){return @{available=$true;pending=([int]$task.State -in @(2,4))}}
        if($Mode -eq 'Request'){
            $pending=[int]$task.State -in @(2,4)
            if(-not $pending){$null=$task.Run($null)}
            return @{accepted=$true;already_pending=$pending}
        }
        if($Mode -eq 'Remove'){
            $children=@(Get-ChildItem -LiteralPath $paths.Store -Force)
            if($children.Count -ne 1 -or $children[0].Name -cne 'owner.json'){throw 'Genstartsmappen indeholder ukendte filer og bevares.'}
            Assert-RestartPlain $paths.Anchor
            $folder.DeleteTask($paths.Task,0)
            Remove-Item -LiteralPath $paths.Anchor
            Remove-Item -LiteralPath $paths.Store
            return @{removed=$true}
        }
        if($Mode -eq 'Execute'){Invoke-RestartShutdown $paths;return @{accepted=$true}}
        throw 'Ukendt genstartshandling.'
    } finally {
        if($selectionLock){$selectionLock.Dispose()}
        if($generationLock){$generationLock.Dispose()}
        if($leased){$lease.ReleaseMutex()};$lease.Dispose()
    }
}

try {Invoke-ServerRestart -Mode $Mode | ConvertTo-Json -Depth 5 -Compress}
catch {@{available=$false;accepted=$false;error=$_.Exception.Message}|ConvertTo-Json -Compress;exit 1}
