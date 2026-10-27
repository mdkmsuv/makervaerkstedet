# Read-only postflight checks for the setup wizard.
# Dot-sourcing this file defines functions only; it never elevates, starts,
# stops, writes, or changes an installed component.
Set-StrictMode -Version Latest

function Add-SetupPostflightCheck {
    param([System.Collections.Generic.List[object]]$Checks, [string]$Name,
          [bool]$Ok, [string]$Message)
    $null = $Checks.Add([ordered]@{ name=$Name; ok=$Ok; message=$Message })
}

function Get-SetupPostflightPlanValue {
    param([System.Collections.IDictionary]$Plan, [string]$Name)
    if ($Plan.Contains('values') -and $Plan.values -is [System.Collections.IDictionary] -and $Plan.values.Contains($Name)) { return $Plan.values[$Name] }
    if ($Plan.Contains('parameters') -and $Plan.parameters -is [System.Collections.IDictionary] -and $Plan.parameters.Contains($Name)) { return $Plan.parameters[$Name] }
    return $null
}

function Assert-SetupPostflightPlainPath([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    while ($null -ne $item) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Omledt installationssti: $Path" }
        if ($item -is [IO.FileInfo]) { $item = $item.Directory } else { $item = $item.Parent }
    }
}

function Test-SetupPostflightConnection($Actual, $Expected) {
    $fields = @('schemaVersion','kind','school_name','server_url','caCertificatePem','station_id','machine')
    if (@($Actual.PSObject.Properties).Count -ne 7 -or @($Expected.PSObject.Properties).Count -ne 7) { return $false }
    foreach ($field in $fields) {
        if (-not $Actual.PSObject.Properties[$field] -or -not $Expected.PSObject.Properties[$field] -or $Actual.$field -cne $Expected.$field) { return $false }
    }
    return $true
}

function Read-SetupPostflightJson([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Mangler filen: $Path" }
    Assert-SetupPostflightPlainPath $Path
    Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
}

function Test-SetupPostflightPath([string]$Actual, [string]$Expected) {
    return [IO.Path]::GetFullPath([string]$Actual) -ieq [IO.Path]::GetFullPath([string]$Expected)
}

function Get-SetupPostflightServerPlan {
    param([System.Collections.IDictionary]$Plan)
    $p = $Plan.parameters
    $roleIp = [string]$p['RoleIp']; $role = [string]$p['Role']
    $teacherAddress = if ($role -eq 'vm' -and $p.Contains('RemoteLaserStation') -and [bool]$p['RemoteLaserStation']) { $roleIp } else { '127.0.0.1' }
    [ordered]@{
        role=$role; roleIp=$roleIp; studentSubnet=[string]$p['StudentSubnet']; schoolName=[string]$p['SchoolName']
        teacherAddress=$teacherAddress; printPort=([int]$(if ($p.Contains('PrintBackendPort')) {$p['PrintBackendPort']} else {8765}))
        teacherPort=([int]$(if ($p.Contains('TeacherPort')) {$p['TeacherPort']} else {8780}))
        installRoot=[IO.Path]::GetFullPath((Join-Path $env:ProgramFiles 'ElevhubServer'))
        dataRoot=[IO.Path]::GetFullPath((Join-Path $env:ProgramData 'ElevhubServer'))
    }
}

function Get-SetupPostflightListener {
    param([string]$Address, [int]$Port)
    @(Get-NetTCPConnection -State Listen -LocalAddress $Address -LocalPort $Port -ErrorAction Stop)
}

function Test-SetupPostflightOwnedListener {
    param([string]$Address, [int]$Port, [string]$Python)
    Assert-SetupPostflightPlainPath $Python
    $listeners = @(Get-SetupPostflightListener $Address $Port)
    if ($listeners.Count -lt 1) { throw "Ingen lyttende forbindelse på $Address`:$Port." }
    foreach ($listener in $listeners) {
        $pidValue = [int]$listener.OwningProcess
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$pidValue" -ErrorAction Stop
        if ($process -and (Test-SetupPostflightPath ([string]$process.ExecutablePath) $Python)) { return $true }
    }
    throw "Port $Address`:$Port ejes ikke af den installerede runtime."
}

function Test-SetupPostflightHttps {
    param([string]$Address, [int]$Port)
    $request = [Net.HttpWebRequest]::Create(('https://{0}:{1}/kontrolpanel' -f $Address,$Port))
    $request.Proxy = $null; $request.AllowAutoRedirect = $false; $request.Timeout = 3000; $request.ReadWriteTimeout = 3000
    $response = $null
    try {
        $response = [Net.HttpWebResponse]$request.GetResponse()
        if ([int]$response.StatusCode -ne 200) { throw "Kontrolpanelet svarede HTTP $([int]$response.StatusCode)." }
    } finally { if ($response) { $response.Dispose() } }
}

function Invoke-SetupStationInfo {
    param([string]$Python, [string]$InstallPath)
    Assert-SetupPostflightPlainPath $Python
    Push-Location -LiteralPath (Split-Path (Split-Path $Python -Parent) -Parent)
    try {
        $text = & $Python -B -m app.laser_station_install info --install-root $InstallPath 2>&1
        if ($LASTEXITCODE -ne 0) { throw 'Den installerede stations runtime kunne ikke validere sin offentlige forbindelse.' }
    } finally { Pop-Location }
    (($text -join "`n") | ConvertFrom-Json -ErrorAction Stop)
}

function Test-SetupWizardPostflight {
    param([Parameter(Mandatory=$true)][System.Collections.IDictionary]$Plan)
    $checks = [Collections.Generic.List[object]]::new(); $next = [Collections.Generic.List[string]]::new()
    $entry = [string]$Plan['entry']; $isStation = $entry -eq 'Setup-LaserStation.ps1'
    try {
        if (-not $Plan.Contains('valid') -or $Plan['valid'] -ne $true -or $entry -notin @('Setup-School.ps1','Setup-LaserStation.ps1')) { throw 'Ugyldig installationsplan.' }
        if ($isStation) {
            $p = $Plan.parameters; $installPath = [string]$(if ($p.Contains('InstallPath')) {$p['InstallPath']} else {Join-Path $env:LOCALAPPDATA 'MakervaerkstedetLaserstation'})
            $active = Read-SetupPostflightJson (Join-Path $installPath 'active.json')
            $validActive = $active.format -eq 1 -and [string]$active.installation_id -cmatch '^[a-f0-9]{32}$' -and $active.kind -eq 'makervaerkstedet-laserstation' -and [string]$active.version_directory -match '^versions/[a-f0-9]{32}$'
            Add-SetupPostflightCheck $checks 'station-active' $validActive $(if($validActive){'Aktiv stationsversion er fundet.'}else{'active.json matcher ikke en gyldig stationsinstallation.'})
            if (-not $validActive) { $next.Add('Kør stationens installation eller ResumeInstall igen med samme pakke.'); throw 'station-active' }
            $version = Join-Path $installPath $active.version_directory; $connection = Read-SetupPostflightJson (Join-Path $version 'connection.json')
            $file = [string]$(if ($p.Contains('ConnectionFile')) {$p['ConnectionFile']} else {$Plan.values['connectionFile']})
            $selected = Read-SetupPostflightJson $file
            Assert-SetupPostflightPlainPath (Join-Path $version 'school-ca.pem')
            $linked = (Test-SetupPostflightConnection $connection $selected) -and ([IO.File]::ReadAllText((Join-Path $version 'school-ca.pem')) -ceq [string]$selected.caCertificatePem)
            Add-SetupPostflightCheck $checks 'station-connection' $linked $(if($linked){'Den installerede station peger på den valgte skoleforbindelse.'}else{'Den installerede stationsforbindelse matcher ikke den valgte skoleforbindelse.'})
            if (-not $linked) { $next.Add('Vælg den samme offentlige laserforbindelsesfil og kør Update/ResumeInstall.'); throw 'station-connection' }
            $python = Join-Path (Join-Path $version 'program') 'runtime\python.exe'; $info = Invoke-SetupStationInfo $python $installPath
            if (-not (Test-SetupPostflightConnection $info.connection $selected)) { throw 'Stationsruntime returnerede en anden skoleforbindelse.' }
            Add-SetupPostflightCheck $checks 'station-runtime-info' $true 'Den installerede stationsruntime kunne validere sin forbindelse.'
            $next.Add('Stationen er installeret; start den separat, når læreren ønsker det.')
        } else {
            $s = Get-SetupPostflightServerPlan $Plan; $owner = Read-SetupPostflightJson (Join-Path $s.dataRoot 'installation-owner.json'); $profile = Read-SetupPostflightJson (Join-Path $s.dataRoot 'school.json')
            $ownerOk = $owner.schemaVersion -eq 1 -and $owner.product -ceq 'ElevhubServer' -and (Test-SetupPostflightPath $owner.installRoot $s.installRoot) -and (Test-SetupPostflightPath $owner.dataRoot $s.dataRoot) -and $owner.state -eq 'installed' -and $owner.roleIp -eq $s.roleIp -and $owner.studentSubnet -eq $s.studentSubnet -and $owner.teacherAddress -eq $s.teacherAddress -and [int]$owner.teacherPort -eq $s.teacherPort
            Add-SetupPostflightCheck $checks 'server-owner' $ownerOk $(if($ownerOk){'Installationsejerskabet er installeret og matcher planen.'}else{'Installationsmarkøren er ikke installeret eller matcher ikke planen.'})
            $profileOk = $profile.schemaVersion -eq 1 -and $profile.school_name -ceq $s.schoolName -and $profile.server -eq $s.roleIp -and $profile.student_subnet -eq $s.studentSubnet -and $profile.role -eq $s.role
            Add-SetupPostflightCheck $checks 'school-profile' $profileOk $(if($profileOk){'Skoleprofilen matcher navn, rolle og netværk.'}else{'Skoleprofilen matcher ikke de valgte oplysninger.'})
            if (-not $ownerOk -or -not $profileOk) { throw 'Installationsmetadata matcher ikke planen.' }
            $ca = Join-Path $s.dataRoot 'security\ca.crt'; $caOk = Test-Path -LiteralPath $ca -PathType Leaf; if ($caOk) { Assert-SetupPostflightPlainPath $ca }
            Add-SetupPostflightCheck $checks 'ca-public-material' $caOk $(if($caOk){'Den offentlige CA-fil findes.'}else{'Den offentlige CA-fil mangler.'})
            foreach($taskName in @('ElevhubServer','ElevhubSchoolEnrollment')) { $task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop; $ok = [string]$task.State -eq 'Running'; Add-SetupPostflightCheck $checks ('task-' + $taskName) $ok $(if($ok){"$taskName kører."}else{"$taskName kører ikke endnu."}); if(-not $ok){$next.Add("Vent på $taskName eller kør efterkontrollen igen.")} }
            $python = Join-Path $s.installRoot 'runtime\python.exe'
            $null = Test-SetupPostflightOwnedListener $s.roleIp 8774 $python; Add-SetupPostflightCheck $checks 'enrollment-listener' $true 'Elevtilmelding lytter på port 8774 med den installerede runtime.'
            $null = Test-SetupPostflightOwnedListener $s.teacherAddress $s.teacherPort $python; Add-SetupPostflightCheck $checks 'teacher-listener' $true 'Lærerens HTTPS-listener ejes af den installerede runtime.'
            $null = Test-SetupPostflightHttps $s.teacherAddress $s.teacherPort; Add-SetupPostflightCheck $checks 'teacher-controlpanel' $true 'Kontrolpanelet svarer med HTTP 200 på den valgte adresse.'
        }
    } catch { if ($checks.Count -eq 0 -or @($checks | Where-Object {$_.ok -eq $false}).Count -eq 0) { Add-SetupPostflightCheck $checks 'postflight-exception' $false $_.Exception.Message }; if ($next.Count -eq 0) {$next.Add('Åbn loggen og kør efterkontrollen igen efter rettelsen.')} }
    $failed = @($checks | Where-Object { -not $_.ok }); $ok = $failed.Count -eq 0
    [ordered]@{ok=$ok;checks=@($checks);message=if($ok){'Efterkontrollen bestod.'}else{'Efterkontrollen kunne ikke bekræfte, at opsætningen er klar.'};nextSteps=@($next)}
}
