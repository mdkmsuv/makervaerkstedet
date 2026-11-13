<#
.SYNOPSIS
Hjælpefunktioner til konsollen på en Hyper-V-VM: skærmbillede, tastetryk og
musklik gennem Hyper-V WMI. Filen skal indlæses med dot-source.

.DESCRIPTION
Bruges til at styre en interaktiv ISO-installation på en NY, tom test-VM, hvor
der ikke findes nogen netværksforbindelse eller gæstetjenester at styre den
med. Filen starter ingen VM, ændrer ingen VM-indstillinger og sender kun
input til den VM, som kalderen har peget på.

Kalderen skal pege på VM og skærmbilledmappe, før en funktion bruges:

    . .\HyperVConsole.ps1
    Set-VmConsoleTarget -VmName '<test-VM>' -ShotDir '<mappe til skærmbilleder>'
    Save-VmScreen 'trin-01.png'
    Send-VmKey 13            # Enter
    Send-VmText 'ja'

Funktioner: Set-VmConsoleTarget, Get-VmSystem, Save-VmScreen, Send-VmKey,
Send-VmChord, Send-VmText, Send-VmClick.

Tastekoder er Hyper-V's egne (Msvm_Keyboard), for eksempel 13 for Enter.
Skærmbilleder gemmes i mappen som PNG; mappen oprettes, hvis den mangler.
#>

$script:Ns = 'root\virtualization\v2'
if (-not (Get-Variable -Name VmName -Scope Script -ErrorAction SilentlyContinue)) { $script:VmName = $null }
if (-not (Get-Variable -Name ShotDir -Scope Script -ErrorAction SilentlyContinue)) { $script:ShotDir = $null }
Add-Type -AssemblyName System.Drawing

function Set-VmConsoleTarget {
    param(
        [Parameter(Mandatory)][string]$VmName,
        [Parameter(Mandatory)][string]$ShotDir
    )
    $script:VmName = $VmName
    $script:ShotDir = $ShotDir
}

function Assert-VmConsoleTarget {
    if ([string]::IsNullOrWhiteSpace($script:VmName)) {
        throw 'VM-navnet er ikke sat. Kald Set-VmConsoleTarget -VmName <navn> -ShotDir <mappe> først.'
    }
}

function Get-VmSystem {
    Assert-VmConsoleTarget
    $name = $script:VmName.Replace("'", "''")
    $system = Get-CimInstance -Namespace $script:Ns -ClassName Msvm_ComputerSystem -Filter "ElementName='$name'"
    if (-not $system) { throw "Fandt ikke VM'en '$($script:VmName)'." }
    $system
}

function Save-VmScreen([string]$Name, [int]$Width = 1024, [int]$Height = 768) {
    if ([string]::IsNullOrWhiteSpace($script:ShotDir)) {
        throw 'Skærmbilledmappen er ikke sat. Kald Set-VmConsoleTarget -VmName <navn> -ShotDir <mappe> først.'
    }
    New-Item -ItemType Directory -Path $script:ShotDir -Force | Out-Null
    $system = Get-VmSystem
    $settings = Get-CimAssociatedInstance -InputObject $system -ResultClassName Msvm_VirtualSystemSettingData -Association Msvm_SettingsDefineState
    $service = Get-CimInstance -Namespace $script:Ns -ClassName Msvm_VirtualSystemManagementService
    $result = Invoke-CimMethod -InputObject $service -MethodName GetVirtualSystemThumbnailImage -Arguments @{ TargetSystem = $settings; WidthPixels = [uint16]$Width; HeightPixels = [uint16]$Height }
    if ($result.ReturnValue -ne 0 -or -not $result.ImageData) { throw "Thumbnail failed: $($result.ReturnValue)" }
    $bitmap = New-Object System.Drawing.Bitmap($Width, $Height, [System.Drawing.Imaging.PixelFormat]::Format16bppRgb565)
    $rect = New-Object System.Drawing.Rectangle(0, 0, $Width, $Height)
    $data = $bitmap.LockBits($rect, [System.Drawing.Imaging.ImageLockMode]::WriteOnly, $bitmap.PixelFormat)
    [byte[]]$bytes = $result.ImageData
    $count = [Math]::Min($bytes.Length, [Math]::Abs($data.Stride) * $Height)
    [System.Runtime.InteropServices.Marshal]::Copy($bytes, 0, $data.Scan0, $count)
    $bitmap.UnlockBits($data)
    $path = Join-Path $script:ShotDir $Name
    $bitmap.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    $bitmap.Dispose()
    $path
}

function Get-VmKeyboard {
    Get-CimAssociatedInstance -InputObject (Get-VmSystem) -ResultClassName Msvm_Keyboard
}

function Send-VmKey([int[]]$Codes, [int]$DelayMs = 150) {
    $keyboard = Get-VmKeyboard
    foreach ($code in $Codes) {
        Invoke-CimMethod -InputObject $keyboard -MethodName TypeKey -Arguments @{ keyCode = [uint32]$code } | Out-Null
        Start-Sleep -Milliseconds $DelayMs
    }
}

function Send-VmChord([int[]]$Codes) {
    $keyboard = Get-VmKeyboard
    foreach ($code in $Codes) { Invoke-CimMethod -InputObject $keyboard -MethodName PressKey -Arguments @{ keyCode = [uint32]$code } | Out-Null }
    Start-Sleep -Milliseconds 80
    [array]::Reverse($Codes)
    foreach ($code in $Codes) { Invoke-CimMethod -InputObject $keyboard -MethodName ReleaseKey -Arguments @{ keyCode = [uint32]$code } | Out-Null }
}

function Send-VmText([string]$Text) {
    Invoke-CimMethod -InputObject (Get-VmKeyboard) -MethodName TypeText -Arguments @{ asciiText = $Text } | Out-Null
}

function Get-VmMouse { Get-CimAssociatedInstance -InputObject (Get-VmSystem) -ResultClassName Msvm_SyntheticMouse }

function Send-VmClick([int]$X, [int]$Y) {
    $mouse = Get-VmMouse
    Invoke-CimMethod -InputObject $mouse -MethodName SetAbsolutePosition -Arguments @{ horizontalPosition = [int32]$X; verticalPosition = [int32]$Y } | Out-Null
    Start-Sleep -Milliseconds 200
    Invoke-CimMethod -InputObject $mouse -MethodName ClickButton -Arguments @{ buttonIndex = [int32]1 } | Out-Null
}
