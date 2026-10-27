# Narrow access repair for the school's existing flat server. Credits: Klostermarksskolen.
[CmdletBinding()]
param(
 [string]$DataRoot='C:\ProgramData\ElevhubServer',
 [Parameter(Mandatory=$true)][string]$Receipt,
 [switch]$Apply
)
$ErrorActionPreference='Stop'
$DataRoot=[IO.Path]::GetFullPath($DataRoot)
foreach($path in @($DataRoot,(Join-Path $DataRoot 'instance.lock'),(Join-Path $DataRoot 'backup-protocol.json'),(Join-Path $DataRoot 'installation-owner.json'))) {
 if(Test-Path -LiteralPath $path) { if((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse point refused.' } }
}
$lock=Join-Path $DataRoot 'instance.lock'
$protocol=Join-Path $DataRoot 'backup-protocol.json'
$owner=Join-Path $DataRoot 'installation-owner.json'
if(-not (Test-Path -LiteralPath $lock -PathType Leaf)) { throw 'Expected existing instance lock missing.' }
if((Test-Path -LiteralPath $owner) -and (-not (Test-Path -LiteralPath $owner -PathType Leaf))) { throw 'Installation owner marker must be a regular file.' }
$rootBefore=(Get-Acl -LiteralPath $DataRoot).Sddl
$config=Join-Path $DataRoot 'konfiguration.json'
$configHash=if(Test-Path -LiteralPath $config){(Get-FileHash -LiteralPath $config -Algorithm SHA256).Hash}else{$null}
$record=[ordered]@{format='school-runtime-access-repair-v1';reason='LocalService needs the existing supervisor lock, installation owner marker, and read access to the idempotent backup protocol; preserve root/config/security access.';utc=[DateTime]::UtcNow.ToString('o');root_acl_before=$rootBefore;lock_acl_before=(Get-Acl -LiteralPath $lock).Sddl;protocol_existed=(Test-Path -LiteralPath $protocol);owner_existed=(Test-Path -LiteralPath $owner);code_or_database_changed=$false;task_started=$false}
$record.protocol_acl_before=if($record.protocol_existed){(Get-Acl -LiteralPath $protocol).Sddl}else{$null}
$record.owner_acl_before=if($record.owner_existed){(Get-Acl -LiteralPath $owner).Sddl}else{$null}
$record.other_lock_checks=@()
if($DataRoot -eq 'C:\ProgramData\ElevhubServer') {
 foreach($other in @('C:\ProgramData\ElevhubServer\hub\instance.lock','C:\ProgramData\ElevhubServer\designvaerksted\instance.lock','C:\ProgramData\Printvaerksted\data\school\instance.lock','C:\ProgramData\Printvaerksted\data\school\supervisor\instance.lock')) {
  $acl=Get-Acl -LiteralPath $other
  $allow=0;$deny=0
  foreach($rule in $acl.Access) {
   if($rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -eq 'S-1-5-19') {
    if($rule.AccessControlType -eq 'Allow'){$allow=$allow -bor [int]$rule.FileSystemRights}else{$deny=$deny -bor [int]$rule.FileSystemRights}
   }
  }
  if(($allow -band 7) -ne 7 -or ($deny -band 7) -ne 0){throw 'Another service lock lacks LocalService read/write; stop for a separate reviewed repair.'}
  $record.other_lock_checks+=@{path=$other;sddl=$acl.Sddl;local_service_read_write=$true;changed=$false}
 }
}
if($record.protocol_existed) {
 $value=Get-Content -LiteralPath $protocol -Raw | ConvertFrom-Json
 if($value.protocol -ne 1 -or $value.role -ne 'server' -or @($value.psobject.Properties).Count -ne 2) { throw 'Unexpected protocol metadata; investigate rather than overwrite.' }
}
if(-not $Apply) { Write-Output 'Verified narrow repair inputs only; no ACL, data, process or task changed.';return }
if(Test-Path -LiteralPath $Receipt) { throw 'Repair receipt already exists; preserve the previous operation.' }
[IO.File]::WriteAllText(($Receipt+'.before.json'),($record|ConvertTo-Json -Depth 6),(New-Object Text.UTF8Encoding($false)))
$service=New-Object Security.Principal.SecurityIdentifier('S-1-5-19')
$acl=Get-Acl -LiteralPath $lock
$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($service,'Modify','Allow')))
Set-Acl -LiteralPath $lock -AclObject $acl
if(-not $record.protocol_existed) {
 # The root intentionally allows LocalService only directory traversal/read.
 # Create this version's exact protocol under administrator ownership.
 [IO.File]::WriteAllText($protocol,'{"protocol":1,"role":"server"}',(New-Object Text.UTF8Encoding($false)))
}
$acl=Get-Acl -LiteralPath $protocol
$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($service,'Read','Allow')))
Set-Acl -LiteralPath $protocol -AclObject $acl
if($record.owner_existed) {
 $acl=Get-Acl -LiteralPath $owner
 $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($service,'Read','Allow')))
 Set-Acl -LiteralPath $owner -AclObject $acl
}
$record.root_acl_after=(Get-Acl -LiteralPath $DataRoot).Sddl
if($record.root_acl_after -ne $rootBefore) { throw 'Root directory permissions changed unexpectedly.' }
if($configHash -and (Get-FileHash -LiteralPath $config -Algorithm SHA256).Hash -ne $configHash){throw 'Configuration changed unexpectedly.'}
$record.lock_acl_after=(Get-Acl -LiteralPath $lock).Sddl
$record.protocol_acl_after=(Get-Acl -LiteralPath $protocol).Sddl
$record.owner_acl_after=if($record.owner_existed){(Get-Acl -LiteralPath $owner).Sddl}else{$null}
$record.status='repaired'
[IO.File]::WriteAllText($Receipt,($record|ConvertTo-Json -Depth 5),(New-Object Text.UTF8Encoding($false)))
Write-Output 'Repaired LocalService access to supervisor lock, installation owner marker, and matching protocol; root/config/security preserved. No task started.'
