<#
.SYNOPSIS
Giver en lokal standardbruger på en test-VM retten "Log on as a batch job"
(SeBatchLogonRight) og fjerner den igen bagefter. Bruges som forberedelse og
oprydning omkring Test-LaserStationOnVm.ps1.

.DESCRIPTION
Test-LaserStationOnVm.ps1 starter stationsinstallationen som en planlagt opgave
under en ikke-administrator. Det kræver batch-logon-retten. Dette script
ændrer kun denne ene ret for denne ene konto på den navngivne test-VM.

Grant
  Kontrollerer, at kontoen findes, er aktiv, ikke er administrator, og at ingen
  "deny batch logon"-regel rammer den. Tilføjer derefter retten, hvis den
  mangler, og skriver en kvittering (-ReportPath) med kontoens rettigheder før
  og efter og med, om retten blev tilføjet af denne kørsel.

Cleanup
  Læser kvitteringen fra Grant og fjerner retten igen, men kun hvis Grant
  tilføjede den. Kontrollerer, at kontoens rettigheder er tilbage på
  udgangspunktet, og udvider kvitteringen med resultatet.

Scriptet starter, stopper eller gendanner ikke VM'en, ændrer ingen
deny-regler, ingen andre politikker og ingen andre konti. Det skal køres med
VM'en kørende. Legitimationsfilen er privat testmateriale uden for kilden.

.PARAMETER Mode
Grant eller Cleanup.

.PARAMETER VMName
Navnet på den kørende test-VM.

.PARAMETER CredentialFile
To linjer: VM'ens lokale administratornavn og et DPAPI-beskyttet kodeord
(ConvertFrom-SecureString for den Windows-bruger, der kører scriptet).

.PARAMETER StationUser
Navnet på den lokale standardbruger i VM'en (uden computer-præfiks).

.PARAMETER ReportPath
Kvitteringsfil (JSON). Grant nægter at overskrive en eksisterende fil.
Cleanup kræver, at den findes.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Grant', 'Cleanup')][string]$Mode,
    [Parameter(Mandatory)][string]$VMName,
    [Parameter(Mandatory)][string]$CredentialFile,
    [Parameter(Mandatory)][string]$StationUser,
    [Parameter(Mandatory)][string]$ReportPath
)
$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'
if ($Mode -eq 'Grant' -and (Test-Path -LiteralPath $ReportPath)) { throw 'Kvitteringsfilen findes allerede; den overskrives ikke.' }
if ($Mode -eq 'Cleanup' -and -not (Test-Path -LiteralPath $ReportPath -PathType Leaf)) { throw 'Kvitteringen fra Grant findes ikke; der er intet at rydde op efter.' }
if (-not (Test-Path -LiteralPath $CredentialFile -PathType Leaf)) { throw 'Legitimationsfilen findes ikke.' }
$lines = @(Get-Content -LiteralPath $CredentialFile)
if ($lines.Count -ne 2) { throw 'Legitimationsfilen skal have præcis to linjer.' }
$StationUser = ($StationUser -split '\\')[-1]
$prior = if ($Mode -eq 'Cleanup') { Get-Content -LiteralPath $ReportPath -Raw | ConvertFrom-Json } else { $null }
$native = @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Principal;
public static class MkvLsa {
 [StructLayout(LayoutKind.Sequential)] struct OA { public uint Length; public IntPtr RootDirectory,ObjectName; public uint Attributes; public IntPtr SecurityDescriptor,SecurityQualityOfService; }
 [StructLayout(LayoutKind.Sequential)] struct US { public ushort Length,MaximumLength; public IntPtr Buffer; }
 [DllImport("advapi32.dll")] static extern uint LsaOpenPolicy(IntPtr name,ref OA attrs,uint access,out IntPtr handle);
 [DllImport("advapi32.dll")] static extern uint LsaClose(IntPtr handle);
 [DllImport("advapi32.dll")] static extern uint LsaFreeMemory(IntPtr value);
 [DllImport("advapi32.dll")] static extern uint LsaNtStatusToWinError(uint status);
 [DllImport("advapi32.dll")] static extern uint LsaEnumerateAccountRights(IntPtr handle,IntPtr sid,out IntPtr rights,out uint count);
 [DllImport("advapi32.dll")] static extern uint LsaEnumerateAccountsWithUserRight(IntPtr handle,ref US right,out IntPtr accounts,out uint count);
 [DllImport("advapi32.dll")] static extern uint LsaAddAccountRights(IntPtr handle,IntPtr sid,ref US right,uint count);
 [DllImport("advapi32.dll")] static extern uint LsaRemoveAccountRights(IntPtr handle,IntPtr sid,[MarshalAs(UnmanagedType.U1)]bool all,ref US right,uint count);
 static void Check(uint status){if(status!=0)throw new Win32Exception((int)LsaNtStatusToWinError(status));}
 static IntPtr Open(){OA attrs=new OA();attrs.Length=(uint)Marshal.SizeOf(typeof(OA));IntPtr h;Check(LsaOpenPolicy(IntPtr.Zero,ref attrs,0x811,out h));return h;}
 static US Text(string text){US value=new US();value.Buffer=Marshal.StringToHGlobalUni(text);value.Length=(ushort)(text.Length*2);value.MaximumLength=(ushort)(value.Length+2);return value;}
 static IntPtr Sid(string sid){SecurityIdentifier value=new SecurityIdentifier(sid);byte[] data=new byte[value.BinaryLength];value.GetBinaryForm(data,0);IntPtr p=Marshal.AllocHGlobal(data.Length);Marshal.Copy(data,0,p,data.Length);return p;}
 public static string[] Rights(string sid){IntPtr h=Open(),s=Sid(sid),buffer=IntPtr.Zero;try{uint n;uint status=LsaEnumerateAccountRights(h,s,out buffer,out n);if(status==0xc0000034)return new string[0];Check(status);List<string> result=new List<string>();int size=Marshal.SizeOf(typeof(US));for(int i=0;i<n;i++){US v=(US)Marshal.PtrToStructure(IntPtr.Add(buffer,i*size),typeof(US));result.Add(Marshal.PtrToStringUni(v.Buffer,v.Length/2));}return result.ToArray();}finally{if(buffer!=IntPtr.Zero)LsaFreeMemory(buffer);Marshal.FreeHGlobal(s);LsaClose(h);}}
 public static string[] Holders(string name){IntPtr h=Open(),buffer=IntPtr.Zero;US right=Text(name);try{uint n;uint status=LsaEnumerateAccountsWithUserRight(h,ref right,out buffer,out n);if(status==0x8000001a)return new string[0];Check(status);List<string> result=new List<string>();for(int i=0;i<n;i++){IntPtr p=Marshal.ReadIntPtr(buffer,i*IntPtr.Size);result.Add(new SecurityIdentifier(p).Value);}return result.ToArray();}finally{if(buffer!=IntPtr.Zero)LsaFreeMemory(buffer);Marshal.FreeHGlobal(right.Buffer);LsaClose(h);}}
 public static void Change(string sid,bool add){IntPtr h=Open(),s=Sid(sid);US right=Text("SeBatchLogonRight");try{Check(add?LsaAddAccountRights(h,s,ref right,1):LsaRemoveAccountRights(h,s,false,ref right,1));}finally{Marshal.FreeHGlobal(s);Marshal.FreeHGlobal(right.Buffer);LsaClose(h);}}
}
'@
$credential = [Management.Automation.PSCredential]::new($lines[0], (ConvertTo-SecureString $lines[1])); $lines = $null
$session = New-PSSession -VMName $VMName -Credential $credential
try {
    $result = Invoke-Command -Session $session -ArgumentList $native, $Mode, $prior, $StationUser, $VMName -ScriptBlock {
        param($source, $mode, $prior, $accountName, $vm)
        $ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'
        Add-Type -TypeDefinition $source
        $user = Get-LocalUser -Name $accountName; $sid = $user.SID.Value
        if (-not $user.Enabled) { throw 'Testkontoen er ikke aktiv.' }
        $admins = @(Get-LocalGroupMember -SID 'S-1-5-32-544' | ForEach-Object { $_.SID.Value })
        if ($admins -contains $sid) { throw 'Testkontoen tilhører Administrators; retten ændres ikke.' }
        $before = @([MkvLsa]::Rights($sid) | Sort-Object)
        $deny = @([MkvLsa]::Holders('SeDenyBatchLogonRight') | Sort-Object)
        $holders = @([MkvLsa]::Holders('SeBatchLogonRight') | Sort-Object)
        if ($mode -eq 'Grant') {
            $tokenSids = @($sid, 'S-1-1-0', 'S-1-2-0', 'S-1-5-3', 'S-1-5-11', 'S-1-5-113')
            foreach ($group in Get-LocalGroup) { if (@(Get-LocalGroupMember -SID $group.SID | Where-Object { $_.SID.Value -eq $sid }).Count) { $tokenSids += $group.SID.Value } }
            if (@($deny | Where-Object { $_ -in $tokenSids }).Count) { throw 'En deny-batch-regel gælder; ingen ret er ændret, og reglen omgås ikke.' }
            $added = $before -notcontains 'SeBatchLogonRight'
            if ($added) { [MkvLsa]::Change($sid, $true) }
            $after = @([MkvLsa]::Rights($sid) | Sort-Object)
            if (($after -notcontains 'SeBatchLogonRight') -or @($after | Where-Object { $_ -ne 'SeBatchLogonRight' -and $_ -notin $before }).Count -or @($before | Where-Object { $_ -notin $after }).Count) { throw 'Uventet ændring af kontoens rettigheder; undersøg før du fortsætter.' }
            @{ schema = 1; vm = $vm; account = $accountName; sid = $sid; right = 'SeBatchLogonRight'; addedByThisTest = $added; targetRightsBefore = $before; targetRightsAfter = $after; holdersBefore = $holders; holdersAfter = @([MkvLsa]::Holders('SeBatchLogonRight') | Sort-Object); denyHoldersBefore = $deny; denyHoldersAfter = @([MkvLsa]::Holders('SeDenyBatchLogonRight') | Sort-Object); denyChanged = $false; otherPoliciesChanged = $false; cleanupPending = $added }
        } else {
            if ($prior.sid -ne $sid -or $prior.right -ne 'SeBatchLogonRight') { throw 'Kvitteringen gælder ikke præcis denne konto og denne ret.' }
            if ($prior.addedByThisTest -and $before -contains 'SeBatchLogonRight') { [MkvLsa]::Change($sid, $false) }
            $after = @([MkvLsa]::Rights($sid) | Sort-Object)
            if (@($after | Where-Object { $_ -notin $prior.targetRightsBefore }).Count -or @($prior.targetRightsBefore | Where-Object { $_ -notin $after }).Count) { throw 'Kontoens rettigheder er ikke tilbage på det noterede udgangspunkt.' }
            @{ targetRightsAfterCleanup = $after; holdersAfterCleanup = @([MkvLsa]::Holders('SeBatchLogonRight') | Sort-Object); denyHoldersAfterCleanup = @([MkvLsa]::Holders('SeDenyBatchLogonRight') | Sort-Object); cleanupPending = $false; cleanupPassed = $true }
        }
    }
    if ($Mode -eq 'Grant') { $final = $result } else { $final = $prior; foreach ($key in $result.Keys) { $final | Add-Member -NotePropertyName $key -NotePropertyValue $result[$key] -Force } }
    [IO.File]::WriteAllText($ReportPath, ($final | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    [pscustomobject]@{ mode = $Mode; account = $StationUser; right = 'SeBatchLogonRight'; report = $ReportPath; cleanupPending = $final.cleanupPending }
} finally { Remove-PSSession $session }
