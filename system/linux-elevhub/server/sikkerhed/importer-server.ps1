# Kopiér kun serverens runtimefiler fra fx Debian WSL til en ny privat Windows-mappe.
# Opretter ikke tjenester og importerer ikke CA'en i Windows' certifikatlager.
param(
    [Parameter(Mandatory = $true)][string]$Kilde,
    [Parameter(Mandatory = $true)][string]$Maal
)
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne 'Win32NT') {
    throw 'Dette script kræver Windows med NTFS-rettigheder.'
}
$maalSti = [IO.Path]::GetFullPath($Maal)
if (Test-Path -LiteralPath $maalSti) {
    throw 'Målmappen findes allerede. Vælg en ny mappe; eksisterende nøgler overskrives ikke.'
}
$filer = @('ca.crt', 'server.crt', 'server.key', 'enheder.json')
foreach ($fil in $filer) {
    if (-not (Test-Path -LiteralPath (Join-Path $Kilde $fil) -PathType Leaf)) {
        throw "Kildemappen mangler $fil. Intet er kopieret."
    }
}
# Luk arv, før første fil kopieres. Kun den aktuelle Windows-konto og SYSTEM.
New-Item -ItemType Directory -Path $maalSti -ErrorAction Stop | Out-Null
$brugerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$systemSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$acl = [Security.AccessControl.DirectorySecurity]::new()
$acl.SetOwner($brugerSid)
$acl.SetAccessRuleProtection($true, $false)
foreach ($sid in @($brugerSid, $systemSid)) {
    $regel = [Security.AccessControl.FileSystemAccessRule]::new(
        $sid, [Security.AccessControl.FileSystemRights]::FullControl,
        [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit',
        [Security.AccessControl.PropagationFlags]::None,
        [Security.AccessControl.AccessControlType]::Allow)
    $acl.AddAccessRule($regel)
}
Set-Acl -LiteralPath $maalSti -AclObject $acl
$kontrol = Get-Acl -LiteralPath $maalSti
$tilladteSid = @($brugerSid.Value, $systemSid.Value)
if (-not $kontrol.AreAccessRulesProtected) { throw 'Adgangsarv kunne ikke deaktiveres. Intet er kopieret.' }
foreach ($regel in $kontrol.Access) {
    $regelSid = $regel.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
    if ($regelSid -notin $tilladteSid) { throw 'Målmappen har uventede adgangsregler. Intet er kopieret.' }
}
foreach ($fil in $filer) {
    Copy-Item -LiteralPath (Join-Path $Kilde $fil) -Destination (Join-Path $maalSti $fil) -ErrorAction Stop
}
Write-Output "Serverens fire runtimefiler er importeret til $maalSti. Kun denne Windows-konto og SYSTEM har fået adgang."
Write-Output 'CA-udstedernøglen og elevcomputernes tokens er ikke kopieret. Ingen tjenester er startet.'
