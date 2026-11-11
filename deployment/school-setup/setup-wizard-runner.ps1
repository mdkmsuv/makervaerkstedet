# Process bridge for the graphical guide. Dot-sourcing never starts installation.
Set-StrictMode -Version Latest

function ConvertTo-SetupHashtable($Value) {
    $table = @{}
    if ($Value -is [Collections.IDictionary]) { foreach ($key in $Value.Keys) { $table[[string]$key] = $Value[$key] } }
    elseif ($Value -is [pscustomobject]) { foreach ($property in $Value.PSObject.Properties) { $table[$property.Name] = $property.Value } }
    else { throw 'Installationsvalgene kunne ikke læses.' }
    return $table
}

function Assert-SetupWizardDirectory([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    $cursor = $full
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            if (((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Installationsstien må ikke gå gennem genveje til andre mapper.' }
        }
        $cursor = Split-Path -Parent $cursor
    }
    return $full
}

function Set-SetupWizardStatus([string]$Directory, [Collections.IDictionary]$Value) {
    $path = Join-Path $Directory 'status.json'; $temporary = Join-Path $Directory 'status.new'
    $Value['updatedAt'] = [DateTime]::UtcNow.ToString('o')
    [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $path -Force
}

function Get-SetupWizardEncodedCall([string]$Script, [Collections.IDictionary]$Parameters) {
    # JSON is data inside Base64, never interpolated PowerShell or a shell argument.
    $payload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((@{script=$Script;parameters=$Parameters} | ConvertTo-Json -Depth 10 -Compress)))
    $command = '$ErrorActionPreference="Stop"; try {$p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(''' + $payload + '''))|ConvertFrom-Json; $a=@{};foreach($x in $p.parameters.PSObject.Properties){$a[$x.Name]=$x.Value}; & $p.script @a; if(-not $?){exit 1}; exit 0} catch {Write-Output $_;exit 1}'
    [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
}

function Test-SetupWizardPackage([string]$PackageRoot, [string]$Entry) {
    $root = Assert-SetupWizardDirectory $PackageRoot
    foreach ($relative in @('runtime\python.exe','elevhub-package.json','makerspace-release.json','school-package.json',('deployment\school-setup\' + $Entry),'deployment\school-setup\Invoke-SetupWizard.ps1')) {
        if (-not (Test-Path -LiteralPath (Join-Path $root $relative) -PathType Leaf)) { throw 'Brug en komplet, udpakket installationspakke med den nye guide. En kildekodemappe eller den gamle pakke er ikke nok.' }
    }
}

function Get-SetupWizardHelpCommand([string]$Script, [Collections.IDictionary]$Parameters) {
    # Display/copy only. The same data-only encoding as the worker preserves
    # quotes and Unicode across native powershell.exe argument parsing.
    foreach ($key in $Parameters.Keys) {
        if ([string]$key -notmatch '^[A-Za-z][A-Za-z0-9]*$') { throw 'Ugyldigt navn i installationshjælpen.' }
    }
    'powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand ' + (Get-SetupWizardEncodedCall $Script $Parameters)
}

function Test-SetupWizardVerifiedStatus([Collections.IDictionary]$Status) {
    $code = 0
    if ([string]$Status['state'] -ne 'succeeded' -or [string]$Status['phase'] -ne 'checked' -or
        -not $Status.Contains('exitCode') -or $null -eq $Status['exitCode'] -or -not [int]::TryParse([string]$Status['exitCode'],[ref]$code) -or $code -ne 0 -or
        -not $Status.Contains('checks') -or @($Status['checks']).Count -eq 0) { return $false }
    foreach ($check in @($Status['checks'])) {
        try { $c = ConvertTo-SetupHashtable $check } catch { return $false }
        if (-not $c.ContainsKey('ok') -or $c['ok'] -isnot [bool] -or -not $c['ok']) { return $false }
    }
    return $true
}

function Get-SetupWizardGuidance([Collections.IDictionary]$Plan, [Collections.IDictionary]$Status) {
    $station = $Plan -and $Plan['entry'] -eq 'Setup-LaserStation.ps1'
    $phase = [string]$Status['phase']; $state = [string]$Status['state']
    $title = 'Sådan kommer du videre'
    $text = 'Vent på installationsresultatet i dette vindue. Her vises også de næste trin.'
    $technical = ''
    if ($state -eq 'running') {
        switch ($phase) {
            'starting' {
                $title = 'Før installationen starter'
                $text = if ($station) { 'Laserstationen installeres for den Windows-bruger, du er logget ind som. Serveren beholder skolens kø. Vent her på resultatet.' }
                    else { 'Windows beder nu om administratorgodkendelse. Godkend for at fortsætte. Hvis du mangler rettigheder, skal skolens IT hjælpe. Guiden fortsætter bagefter i dette vindue.' }
            }
            'installing' {
                $title = 'Det sker lige nu'
                $text = if ($station) { 'Programmet kontrollerer pakken, installerer laserstationen og opretter genveje til stationen og lærerens fælles panel. Der skal ikke indtastes mere nu. Hold computeren tændt og guiden åben.' }
                    else { 'Programmet kontrollerer pakken, installerer hovedsystemet og opretter skolens forbindelse, baggrundsprogrammer og genveje. Der skal ikke indtastes mere nu. Hold computeren tændt og guiden åben.' }
            }
            'checking' {
                $title = 'Vi kontrollerer resultatet'
                $text = if ($station) { 'Programfilerne er på plads. Nu kontrolleres den valgte skoleforbindelse og den installerede station. Vent på resultatet; du skal ikke starte en ny installation.' }
                    else { 'Programfilerne er på plads. Nu kontrolleres skolens opsætning, lærerpanel og elevtilmelding. Kopiering af filer alene er ikke nok til at melde systemet klar.' }
            }
        }
    } elseif (Test-SetupWizardVerifiedStatus $Status) {
        $title = 'Tag værkstedet i brug'
        if ($station) {
            $text = "1. Åbn skrivebordsgenvejen Makerværkstedet, og log ind med koden oprettet på serveren.`n2. Åbn genvejen Laserstation. Dens særskilte kode findes på serveren: Manage-School.cmd → punkt 5.`n3. Send en prøveaflevering, og kontrollér, at den samme opgave ses i serverens kø og på laserstationen."
        } else {
            $folder = Join-Path $env:PUBLIC 'Documents\Elevvaerkstedet'
            $text = "1. Åbn skrivebordsgenvejen Makerværkstedet på denne server, og opret en lærerkode på mindst otte tegn.`n2. Åbn Stifinder, klik i adresselinjen, og indsæt denne mappe: $folder"
            if ($Plan -and $Plan.Contains('values') -and $Plan.values['deployment'] -eq 'separate-computers') {
                $text += "`n3. Kopiér den nye laser-station-connection.json til lasercomputeren. Åbn samme pakkes START.cmd dér, og vælg Server og separat lasercomputer → Lasercomputeren."
            } else {
                $text += "`n3. Åbn genvejen Elevhub Laserstation, når læreren vil arbejde med laserkøen. Stationens særskilte kode findes i Manage-School.cmd → punkt 5."
            }
            $text += "`n4. Hvis der skal tilføjes Linux-elevcomputere: kopiér school-connection.json til USB. Åbn Manage-School.cmd → punkt 1, og opret et computernavn og en engangskode. Vælg skolefilen på elevcomputeren, og indtast det samme navn og koden."
        }
        $text += "`nPrøv skolens netværk og fysiske udstyr med en kontrolleret prøveopgave, før eleverne tager værkstedet i brug."
    } elseif ($state -eq 'failed') {
        if ($phase -eq 'not-started') {
            $title = 'Installationen er ikke startet'
            $text = 'Luk guiden og åbn START.cmd igen, når du kan godkende. Hvis du ikke har administratorrettigheder, skal skolens IT være med. Du behøver ikke genoptage et installationsforsøg, der aldrig startede.'
            if ($station) { $text = 'Luk guiden, kontrollér at hele pakken er udpakket, og åbn START.cmd igen som den Windows-bruger, der skal bruge stationen. Kopiér loggen til IT, hvis programmet stadig ikke starter.' }
        } elseif ($phase -eq 'checked') {
            $title = 'Programmet er installeret; en kontrol kræver hjælp'
            $text = 'Start ikke en ny ren installation. Kopiér log og hjælpetekst her i vinduet til skolens IT. Bevar den udpakkede pakke og logmappen. Kontrollerne nedenfor viser, hvad der ikke kunne bekræftes.'
            if (-not $station) { $text += ' På serveren kan IT åbne Manage-School.cmd og vælge punkt 3 for opsætningskontrol.' }
        } else {
            $title = 'Bevar forsøget, så det kan fortsættes'
            $text = 'Kopiér loggen og hjælpeteksten til skolens IT. Bevar den udpakkede pakke og eventuelle installerede data. Et afbrudt forsøg skal genoptages med samme pakke og valg; start ikke en ny ren installation oveni. Hjælpen nedenfor indeholder de konkrete oplysninger.'
        }
        $technical = 'Oplysninger til skolens IT' + "`r`n" + 'Status: ' + [string]$Status['message']
        if ($Status.Contains('detail')) { $technical += "`r`n" + [string]$Status['detail'] }
        if ($Status.Contains('log')) { $technical += "`r`n" + [string]$Status['log'] }
        if ($Status.Contains('checks')) {
            foreach ($check in @($Status['checks'])) {
                try { $c = ConvertTo-SetupHashtable $check } catch { continue }
                if (-not $c['ok']) { $technical += "`r`nKontrol: " + [string]$c['name'] + ' — ' + [string]$c['message'] }
            }
        }
        if ($Plan -and $Plan.Contains('parameters') -and $phase -in @('installation-failed','interrupted','error')) {
            $p = ConvertTo-SetupHashtable $Plan.parameters
            $technical += "`r`n`r`nOprindelige installationsvalg (ingen adgangskoder):"
            foreach ($key in $p.Keys | Sort-Object) { $technical += "`r`n" + [string]$key + ': ' + [string]$p[$key] }
            $technical += "`r`nKommandoen nedenfor pakker de samme valg, så mellemrum, citationstegn og danske bogstaver bevares."
            if ($station) {
                $p = @{PackageRoot=$p['PackageRoot'];Action='ResumeInstall';NoStart=$true}
                $scriptPath = Join-Path ([string]$p['PackageRoot']) 'deployment\school-setup\Setup-LaserStation.ps1'
                $technical += "`r`n`r`nHvis den rene stationsinstallation blev afbrudt: Åbn Windows PowerShell som den samme Windows-bruger. Genoptag med den oprindelige pakke og journal:`r`n" + (Get-SetupWizardHelpCommand $scriptPath $p)
                $p['Action']='Update'; $p['FinishSetup']=$true
                $technical += "`r`n`r`nKun hvis fejlen udtrykkeligt siger, at programmet er installeret, men certifikat/genveje mangler: færdiggør i stedet brugeropsætningen med:`r`n" + (Get-SetupWizardHelpCommand $scriptPath $p)
            } else {
                $p['Resume']=$true
                $scriptPath = Join-Path ([string]$p['PackageRoot']) 'deployment\school-setup\Setup-School.ps1'
                $technical += "`r`n`r`nHvis serverinstallationen blev afbrudt: Åbn Windows PowerShell, og genoptag med den oprindelige pakke og de samme valg nedenfor. Windows kan bede om administratorgodkendelse:`r`n" + (Get-SetupWizardHelpCommand $scriptPath $p)
            }
            $technical += "`r`nKør kun den handling, der passer til loggens fejl. En genoptagelse er ikke en versionsopdatering."
        }
    }
    @{guidanceTitle=$title;guidanceText=$text;technicalHelp=$technical}
}

function Start-SetupWizardRun([Collections.IDictionary]$Plan) {
    if (-not $Plan.valid) { throw 'Ret de markerede oplysninger før installation.' }
    Test-SetupWizardPackage ([string]$Plan.values.packageRoot) ([string]$Plan.entry)
    $parent = Assert-SetupWizardDirectory (Join-Path $env:LOCALAPPDATA 'Makervaerkstedet\SetupLogs')
    $directory = Join-Path $parent ([Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $request = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($Plan.values | ConvertTo-Json -Depth 6 -Compress)))
    $worker = Join-Path ([string]$Plan.values.packageRoot) 'deployment\school-setup\Invoke-SetupWizard.ps1'
    $encoded = Get-SetupWizardEncodedCall $worker @{RequestBase64=$request;RunDirectory=$directory}
    Set-SetupWizardStatus $directory @{state='running';phase='starting';message='Venter på, at installationsprogrammet starter.';exitCode=$null}
    try {
        $start = @{FilePath=(Join-Path $PSHOME 'powershell.exe');ArgumentList=@('-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-EncodedCommand',$encoded);WorkingDirectory=[string]$Plan.values.packageRoot;WindowStyle='Hidden';PassThru=$true;ErrorAction='Stop'}
        if ($Plan.entry -eq 'Setup-School.ps1') { $start['Verb']='RunAs' }
        $process = Start-Process @start
        return @{directory=$directory;process=$process;plan=$Plan}
    } catch {
        Set-SetupWizardStatus $directory @{state='failed';phase='not-started';message='Installationen blev ikke startet. Windows-godkendelsen kan være afvist eller lukket. Luk guiden, og åbn den igen, når du er klar.';exitCode=$null;detail=$_.Exception.Message}
        return @{directory=$directory;process=$null;plan=$Plan}
    }
}

function Get-SetupWizardRunStatus([Collections.IDictionary]$Handle) {
    $status = Get-Content -LiteralPath (Join-Path $Handle.directory 'status.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($status.state -eq 'running' -and $null -ne $Handle.process) {
        $Handle.process.Refresh()
        if ($Handle.process.HasExited) {
            # Re-read after observing exit: final status may have been written between reads.
            $status = Get-Content -LiteralPath (Join-Path $Handle.directory 'status.json') -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($status.state -eq 'running') {
                $status = [pscustomobject]@{state='failed';phase='interrupted';message='Installationsprogrammet sluttede uden en bekræftet efterkontrol. Se loggen; brug genoptagelse ved et afbrudt forsøg.';exitCode=$Handle.process.ExitCode}
            }
        }
    }
    $result = ConvertTo-SetupHashtable $status
    if ($result.state -eq 'succeeded' -and -not (Test-SetupWizardVerifiedStatus $result)) {
        $result.state = 'failed'; $result.phase = 'status-invalid'
        $result.message = 'Slutstatus mangler en bestået efterkontrol. Gem loggen og få installationen kontrolleret, før systemet bruges.'
    }
    $log = Join-Path $Handle.directory 'installation.log'
    $result.log = 'Logmappe: ' + $Handle.directory
    if (Test-Path -LiteralPath $log -PathType Leaf) { $result.log += "`n" + ((Get-Content -LiteralPath $log -Tail 70 -Encoding UTF8) -join "`n") }
    if ($result.ContainsKey('nextSteps')) { $result.nextSteps = $result.nextSteps -join "`n" }
    $plan = if ($Handle.Contains('plan')) { $Handle['plan'] } else { $null }
    $help = Get-SetupWizardGuidance $plan $result
    foreach ($key in $help.Keys) { $result[$key] = $help[$key] }
    # Main guidance already supplies the complete success path. Keep additional
    # postflight notes available, but avoid displaying the old duplicate list.
    if ($plan -and $result.state -eq 'succeeded') { $result.nextSteps = '' }
    return $result
}

function Invoke-SetupWizardWorker([string]$RequestBase64, [string]$RunDirectory) {
    $directory = Assert-SetupWizardDirectory $RunDirectory
    if (-not (Test-Path -LiteralPath $directory -PathType Container) -or (Split-Path -Leaf $directory) -notmatch '^[a-f0-9]{32}$') { throw 'Installationsloggens mappe er ugyldig.' }
    $logPath = Join-Path $directory 'installation.log'
    $redacting = $false
    try {
        if ($RequestBase64.Length -gt 65536) { throw 'Installationsvalgene er for store.' }
        $values = ConvertTo-SetupHashtable ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($RequestBase64)) | ConvertFrom-Json)
        $plan = Test-SetupWizardInput $values
        if (-not $plan.valid) { throw (($plan.errors | ForEach-Object {$_.message}) -join "`n") }
        $package = Assert-SetupWizardDirectory ([string]$plan.values.packageRoot)
        Test-SetupWizardPackage $package $plan.entry
        $entry = Join-Path $package ('deployment\school-setup\' + $plan.entry)
        $audit = @{startedAt=[DateTime]::UtcNow.ToString('o');entry=$plan.entry;entrySha256=(Get-FileHash -LiteralPath $entry -Algorithm SHA256).Hash;deployment=$values.deployment;component=$values.component;packageRoot=$package}
        [IO.File]::WriteAllText((Join-Path $directory 'operation.json'),($audit|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
        Set-SetupWizardStatus $directory @{state='running';phase='installing';message='Kontrollerer pakken og installerer de valgte dele. Vent, mens programmet arbejder.';exitCode=$null}
        $encoded = Get-SetupWizardEncodedCall $entry $plan.parameters
        $oldPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference='Continue'
            & (Join-Path $PSHOME 'powershell.exe') -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand $encoded 2>&1 | ForEach-Object {
                $line = [string]$_
                if ($line -match '-----BEGIN .*PRIVATE KEY-----') { $redacting=$true }
                if (-not $redacting) {
                    $line = $line -replace '(?i)(password|secret|token|teacher_code)\s*[:=]\s*\S+','$1=[skjult]'
                    [IO.File]::AppendAllText($logPath,$line + "`r`n",[Text.UTF8Encoding]::new($false))
                }
                if ($line -match '-----END .*PRIVATE KEY-----') { $redacting=$false }
            }
            $code = $LASTEXITCODE
        } finally { $ErrorActionPreference=$oldPreference }
        if ($code -ne 0) {
            Set-SetupWizardStatus $directory @{state='failed';phase='installation-failed';message='Installationen blev ikke fuldført. Bevar eventuelle installerede data. Her i guiden vises log og hjælp til det næste skridt.';exitCode=$code}
            return
        }
        Set-SetupWizardStatus $directory @{state='running';phase='checking';message='Programfilerne er installeret. Kontrollerer nu opsætningen, før vi melder klar.';exitCode=0}
        $deadline = [DateTime]::UtcNow.AddSeconds(15)
        do {
            $postflight = Test-SetupWizardPostflight (ConvertTo-SetupHashtable $plan)
            if ($postflight.ok -or [DateTime]::UtcNow -ge $deadline) { break }
            Start-Sleep -Milliseconds 500
        } while ($true)
        [IO.File]::WriteAllText((Join-Path $directory 'checks.json'),($postflight|ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
        $next = @($postflight.nextSteps)
        if ($postflight.ok -and $plan.entry -eq 'Setup-School.ps1') {
            $next += '1. Åbn skrivebordsgenvejen Makerværkstedet på serveren, og vælg lærerens kode.'
            $next += ('2. Offentlige forbindelsesfiler findes i ' + (Join-Path $env:PUBLIC 'Documents\Elevvaerkstedet') + '.')
            if ($values.deployment -eq 'separate-computers') { $next += '3. Flyt den nye laser-station-connection.json til lasercomputeren, og start samme guide dér.' }
            $next += 'Brug school-connection.json til Linux-elevcomputerne. Prøv skolens netværk og fysiske udstyr, før værkstedet tages i brug.'
        }
        Set-SetupWizardStatus $directory @{state=$(if($postflight.ok){'succeeded'}else{'failed'});phase='checked';message=$(if($postflight.ok){'Installation og lokal efterkontrol er gennemført.'}else{'Programmet er installeret, men efterkontrollen kunne ikke bekræfte, at det er klar. Se kontrolresultatet i logmappen.'});exitCode=0;checks=$postflight.checks;nextSteps=$next}
    } catch {
        [IO.File]::AppendAllText($logPath,('Fejl: ' + $_.Exception.Message + "`r`n"),[Text.UTF8Encoding]::new($false))
        Set-SetupWizardStatus $directory @{state='failed';phase='error';message=('Installationen kunne ikke gennemføres: ' + $_.Exception.Message);exitCode=$null}
    }
}
