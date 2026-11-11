$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\school-uninstall-guards.ps1')
$folder = [IO.Path]::GetTempPath()
$path = Join-Path $folder ('elevhub-shortcut-test-' + [Guid]::NewGuid().ToString('N') + '.lnk')
try {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = 'powershell.exe'
    $shortcut.WorkingDirectory = $folder
    $shortcut.Arguments = '-NoProfile'
    $shortcut.Save()
    if (-not (Get-OwnedShortcut $path 'powershell.exe' $folder '-NoProfile')) { throw 'Ejet genvej blev ikke genkendt.' }
    $shortcut.Arguments = '-Changed'
    $shortcut.Save()
    $rejected = $false
    try { $null = Get-OwnedShortcut $path 'powershell.exe' $folder '-NoProfile' } catch { $rejected = $true }
    if (-not $rejected) { throw 'Ændret genvej blev fejlagtigt godkendt.' }
    $shortcut.Arguments = '-Port 18780'
    $shortcut.Save()
    if (-not (Get-OwnedShortcut $path 'powershell.exe' $folder '-Port 18780')) { throw 'Ejet genvej til valgt lærerport blev ikke genkendt.' }
    $rejected = $false
    try { $null = Get-OwnedShortcut $path 'powershell.exe' $folder '-Port 8780' } catch { $rejected = $true }
    if (-not $rejected) { throw 'Genvej til forkert lærerport blev fejlagtigt godkendt.' }
    $ownedArgs = '-Port 18780 -Address 192.0.2.10'
    $legacyArgs = '-Port 18780'
    foreach ($arguments in @($ownedArgs,$legacyArgs)) {
        $shortcut.Arguments = $arguments; $shortcut.Save()
        if (-not (Get-OwnedShortcut $path 'powershell.exe' $folder $ownedArgs -CompatibleArguments @($legacyArgs))) { throw 'Kendt kontrolpanelgenvej blev afvist.' }
    }
    foreach ($arguments in @('-Port 8780 -Address 192.0.2.10','-Port 18780 -Address 192.0.2.99','-Port 8780','-Port 18780 -Address 192.0.2.10 -Changed','')) {
        $shortcut.Arguments = $arguments; $shortcut.Save()
        $rejected = $false
        try { $null = Get-OwnedShortcut $path 'powershell.exe' $folder $ownedArgs -CompatibleArguments @($legacyArgs) } catch { $rejected = $true }
        if (-not $rejected) { throw 'Ændrede kontrolpanelargumenter blev godkendt.' }
    }
    $shortcut.Arguments = $legacyArgs; $shortcut.TargetPath = 'cmd.exe'; $shortcut.Save()
    $rejected = $false
    try { $null = Get-OwnedShortcut $path 'powershell.exe' $folder $ownedArgs -CompatibleArguments @($legacyArgs) } catch { $rejected = $true }
    if (-not $rejected) { throw 'Ændret genvejsprogram blev godkendt via gamle argumenter.' }
    $shortcut.TargetPath = 'powershell.exe'; $shortcut.WorkingDirectory = $env:SystemRoot; $shortcut.Save()
    $rejected = $false
    try { $null = Get-OwnedShortcut $path 'powershell.exe' $folder $ownedArgs -CompatibleArguments @($legacyArgs) } catch { $rejected = $true }
    if (-not $rejected) { throw 'Ændret arbejdsmappe blev godkendt via gamle argumenter.' }
    Write-Output 'Skolens genvejs-ejerskabstest bestod.'
} finally {
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
}
