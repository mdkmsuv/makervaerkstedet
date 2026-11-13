<#
.SYNOPSIS
Venter, til konsollen på en Hyper-V-VM ikke ændrer sig mere (en prompt), eller
til VM'en er slukket, og gemmer så et skærmbillede.

.DESCRIPTION
Hører sammen med HyperVConsole.ps1 (ISO-installation af en ny, tom test-VM).
Scriptet læser kun miniaturebilledet af konsollen. Det starter, stopper og
ændrer ikke VM'en. Hvis VM'en ikke længere kører, skriver det
"VM-STATE=<tilstand> after <sekunder>s" og afslutter uden skærmbillede.

.PARAMETER VMName
Navnet på test-VM'en.

.PARAMETER ScreenshotFolder
Mappen, hvor skærmbilledet gemmes. Oprettes, hvis den mangler.

.PARAMETER Name
Filnavn på skærmbilledet, for eksempel trin-03.png.

.PARAMETER StableSeconds
Så mange sekunder skal skærmen være uændret, før den regnes for en prompt.

.PARAMETER TimeoutSeconds
Længste ventetid i alt.

.PARAMETER Tolerance
Hvor mange billedbytes der må ændre sig mellem to målinger (hver 5. sekund),
før skærmen regnes for ændret.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$VMName,
    [Parameter(Mandatory)][string]$ScreenshotFolder,
    [Parameter(Mandatory)][string]$Name,
    [int]$StableSeconds = 25,
    [int]$TimeoutSeconds = 1500,
    [int]$Tolerance = 400
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'HyperVConsole.ps1')
Set-VmConsoleTarget -VmName $VMName -ShotDir $ScreenshotFolder

function Get-Raw {
    $system = Get-VmSystem
    $settings = Get-CimAssociatedInstance -InputObject $system -ResultClassName Msvm_VirtualSystemSettingData -Association Msvm_SettingsDefineState
    $service = Get-CimInstance -Namespace $script:Ns -ClassName Msvm_VirtualSystemManagementService
    [byte[]](Invoke-CimMethod -InputObject $service -MethodName GetVirtualSystemThumbnailImage -Arguments @{ TargetSystem = $settings; WidthPixels = [uint16]320; HeightPixels = [uint16]240 }).ImageData
}

$start = Get-Date; $previous = $null; $stableSince = Get-Date
while (((Get-Date) - $start).TotalSeconds -lt $TimeoutSeconds) {
    if ((Get-VM -Name $VMName).State -ne 'Running') { "VM-STATE=$((Get-VM -Name $VMName).State) after $([int]((Get-Date) - $start).TotalSeconds)s"; exit 0 }
    $raw = Get-Raw
    $diff = 0
    if ($previous -and $raw -and $previous.Length -eq $raw.Length) {
        for ($i = 0; $i -lt $raw.Length; $i += 2) { if ($raw[$i] -ne $previous[$i]) { $diff++ } }
    } else { $diff = 999999 }
    if ($diff -gt $Tolerance) { $stableSince = Get-Date }
    $previous = $raw
    if (((Get-Date) - $stableSince).TotalSeconds -ge $StableSeconds) { break }
    Start-Sleep -Seconds 5
}
"WAITED=$([int]((Get-Date) - $start).TotalSeconds)s"
Save-VmScreen $Name
