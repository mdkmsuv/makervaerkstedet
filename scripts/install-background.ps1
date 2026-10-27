# Preparation runs after package verification and before copying any program file.
function Lock-PrintFolder([string]$Folder) {
    $null = New-Item -ItemType Directory -Path $Folder -Force
    $stream = [IO.File]::Open((Join-Path $Folder 'instance.lock'), 'OpenOrCreate', 'ReadWrite', 'ReadWrite')
    try { $stream.Lock(0,1) } catch { $stream.Dispose(); throw 'Printvaerksted arbejder stadig. Vent paa afsluttet klargoering, og koer installationen igen.' }
    $script:installLocks += $stream
}
function Stop-InstalledQueue([string]$Path) {
    $executable = Join-Path $Path 'runtime\python.exe'
    if (Test-Path -LiteralPath $executable) {
        Push-Location $Path
        try {
            # Upgrades may still contain an older desktop module without the
            # privileged flag.  Use the new flag only when that source knows it.
            $desktopSource = Join-Path $Path 'app\desktop.py'
            $stopArguments = @('--stop', '--no-window')
            $supportsPrivilegedStop = (Test-Path -LiteralPath $desktopSource -PathType Leaf) -and
                (Select-String -LiteralPath $desktopSource -Pattern '--privileged-stop' -Quiet)
            if ($supportsPrivilegedStop) {
                $stopArguments += '--privileged-stop'
            }
            & $executable -m app.desktop @stopArguments
            if ($LASTEXITCODE -ne 0) { throw 'En anden Printvaerksted-koe bruger adressen, eller programmet lukker stadig. Stop den foerst.' }
        } finally { Pop-Location }
    }
}
function Prepare-PrintInstallation {
    $script:legacySource = $null
    $oldConfig = Join-Path $installPath 'installation.json'
    if (Test-Path -LiteralPath $oldConfig) {
        $previous = Get-Content -LiteralPath $oldConfig -Raw | ConvertFrom-Json
        if ($previous.data_root -ne $dataPath -or ($previous.task_name -and $previous.task_name -ne $TaskName)) { throw 'Brug samme datamappe og opgavenavn ved opdatering.' }
        $script:legacySource = $previous.legacy_source
    }
    $existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existingTask) {
        if ($existingTask.Actions.Execute -ne (Join-Path $installPath 'runtime\python.exe')) { throw 'Opgavenavnet bruges af en anden installation.' }
        $null = Disable-ScheduledTask -TaskName $TaskName
    }
    $recoveryTask = Get-ScheduledTask -TaskName ($TaskName + '-Recovery') -ErrorAction SilentlyContinue
    if ($recoveryTask) {
        if ($recoveryTask.Actions.Execute -ne (Join-Path $installPath 'runtime\python.exe')) { throw 'Genopretningsopgaven bruges af en anden installation.' }
        $null = Disable-ScheduledTask -TaskName ($TaskName + '-Recovery')
    }
    $stopTask = Get-ScheduledTask -TaskName ($TaskName + '-Stop') -ErrorAction SilentlyContinue
    if ($stopTask) {
        if ($stopTask.Actions.Execute -ne (Join-Path $installPath 'runtime\python.exe')) { throw 'Stopopgaven bruges af en anden installation.' }
        $null = Disable-ScheduledTask -TaskName ($TaskName + '-Stop')
        if ($Autostart -ne 'Boot') {
            $null = Unregister-ScheduledTask -TaskName ($TaskName + '-Stop') -Confirm:$false
        }
    }
    Stop-InstalledQueue $installPath
    $migrationData = $null
    if ($MigrateFrom -and (Test-Path -LiteralPath (Join-Path $MigrateFrom 'printvaerksted-package.json')) -and -not (Test-Path -LiteralPath (Join-Path $dataPath 'school\queue.sqlite3'))) {
        Stop-InstalledQueue $MigrateFrom
        $migrationData = Join-Path $MigrateFrom 'data'
        Lock-PrintFolder (Join-Path $migrationData 'school')
        Lock-PrintFolder (Join-Path $migrationData 'demo')
    }
    foreach ($mode in @('school','demo')) {
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            try { Lock-PrintFolder (Join-Path $dataPath "$mode\supervisor"); break } catch { if ($attempt -eq 39) { throw }; Start-Sleep -Milliseconds 250 }
        }
        Lock-PrintFolder (Join-Path $dataPath $mode)
    }
    if ($migrationData) {
        Push-Location $packagePath
        try {
            & $packagePython -m app.installation migrate-data --source $migrationData --destination $dataPath
            if ($LASTEXITCODE -ne 0) { throw 'Den gamle koe kunne ikke flyttes. Originalen er bevaret.' }
            $script:legacySource = $MigrateFrom
        } finally { Pop-Location }
    }
    $null = New-Item -ItemType Directory -Path $installPath -Force
    if ($machine) {
        # The background account can read code and modify data, with no admin powers.
        & icacls.exe $installPath /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' '*S-1-5-19:(OI)(CI)RX' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Programfilerne kunne ikke beskyttes.' }
        # Remove an explicit Users ACE as well as inherited access. Recurse so
        # upgrades also repair existing files below the data root.
        & icacls.exe $dataPath /remove:g '*S-1-5-32-545' '*S-1-5-11' '*S-1-1-0' /T | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Datamappens gamle brugerrettigheder kunne ikke fjernes.' }
        & icacls.exe $dataPath /inheritance:r /T /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-19:(OI)(CI)M' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Datamappens rettigheder kunne ikke oprettes.' }
    }
    $config = @{ai_enabled=$false; data_root=$dataPath; port=$Port; autostart=$Autostart; legacy_source=$script:legacySource; task_name=$(if ($Autostart -eq 'None') { $null } else { $TaskName })}
    $config | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $installPath 'installation.json') -Encoding UTF8
}
