# Used by the installer and isolated Windows verification.
$ErrorActionPreference = 'Stop'
function Register-PrintBackground([string]$InstallPath, [string]$TaskName, [string]$Mode, [switch]$DefinitionOnly) {
    $pythonPath = Join-Path $InstallPath 'runtime\python.exe'
    $action = New-ScheduledTaskAction -Execute $pythonPath -Argument '-m app.background' -WorkingDirectory $InstallPath
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    if ($Mode -eq 'Boot') {
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $principal = New-ScheduledTaskPrincipal -UserId 'S-1-5-19' -LogonType ServiceAccount
    } else {
        $userId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
        $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive
    }
    $task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Printvaerksted: baggrundskoe og sikker genoptagelse. Intet browservindue.'
    if ($DefinitionOnly) { return $task }
    $null = Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force
    # Windows can classify externally terminated processes as a completed task.
    # A periodic guard therefore also checks ownership; it honors an intentional stop.
    $watchAction = New-ScheduledTaskAction -Execute $pythonPath -Argument '-m app.background --watchdog' -WorkingDirectory $InstallPath
    $watchTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
    $watchTask = New-ScheduledTask -Action $watchAction -Trigger $watchTrigger -Principal $principal -Settings $settings -Description 'Printvaerksted: genstarter efter nedbrud; respekterer Stop Printvaerksted.'
    $null = Register-ScheduledTask -TaskName ($TaskName + '-Recovery') -InputObject $watchTask -Force
    if ($Mode -eq 'Boot') {
        # Manual stop runs with LocalService access to the protected data folder.
        # It calls the authenticated service endpoint and never terminates a process.
        $stopAction = New-ScheduledTaskAction -Execute $pythonPath -Argument '-m app.desktop --privileged-stop --no-window' -WorkingDirectory $InstallPath
        # A failed manual stop must not retry later and stop a newly started queue.
        $stopSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
        $stopTask = New-ScheduledTask -Action $stopAction -Principal $principal -Settings $stopSettings -Description 'Printvaerksted: manuel, ordentlig stop via LocalService; genoprettelse respekteres.'
        $null = Register-ScheduledTask -TaskName ($TaskName + '-Stop') -InputObject $stopTask -Force
        # Desktop users can read/run these fixed tasks, never change their executables.
        $scheduler = New-Object -ComObject 'Schedule.Service'
        $scheduler.Connect()
        foreach ($fixedTaskName in @($TaskName, $TaskName + '-Stop')) {
            $registered = $scheduler.GetFolder('\').GetTask($fixedTaskName)
            $sddl = $registered.GetSecurityDescriptor(4)
            $registered.SetSecurityDescriptor(($sddl + '(A;;GRGX;;;BU)'), 0)
        }
    }
    $actual = Get-ScheduledTask -TaskName $TaskName
    if ($actual.Actions.Execute -ne $pythonPath -or $actual.Settings.MultipleInstances -ne 'IgnoreNew') { throw 'Windows-opstart kunne ikke bekraeftes.' }
}
