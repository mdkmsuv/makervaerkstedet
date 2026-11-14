<#
.SYNOPSIS
Opdaterer en eksisterende server på en lokal Hyper-V test-VM med en ny payload
og kontrollerer, at opdateringen bevarer data. Intet uden for VM'en røres.

.DESCRIPTION
Følgesvend til windows/inno/Test-InstallOnVm.ps1. Den tester ren installation
fra et checkpoint; dette script tester opdatering af en server, der allerede
er installeret, uden at gendanne et checkpoint. Tag derfor selv et checkpoint
af VM'en, før scriptet køres, så testen kan gentages.

Scriptet kører samme lifecycle.py som installerens EXE, på præcis den payload
som EXE'en indeholder. Det klikker ikke gennem installerens sider.

  1. Tager et øjebliksbillede af bevaret tilstand: skoleprofil, SSH-nøgle,
     forbindelsesfil og antal rækker i printkøen og designafleveringerne.
  2. Kopierer payloaden til en ny, unik mappe i VM'en og pakker den ud.
  3. Kører opdateringen (--action auto --apply) med tom profil.
  4. Kontrollerer version, planlagte opgaver, lyttende porte på -RoleIp,
     journalens tilstand og at ingen programmapper er efterladt.
  5. Tager tilstanden igen og sammenligner. Bevarede filer skal være
     uændrede, rækkerne skal være ens (på nær køspærringen), og
     køspærringen skal være ophævet.

Serverens profil (school.json) har intet role_ip-felt. Adressen, som portene
kontrolleres på, angives derfor med -RoleIp.

Resultatet skrives som JSON til -ResultFile. Scriptet afslutter med 0, kun
hvis alt blev bestået. Legitimationsfilen er privat testmateriale og må ikke
være en del af kilden.

.PARAMETER VMName
Navnet på den kørende test-VM med en eksisterende server.

.PARAMETER CredentialFile
To linjer: VM'ens lokale administratornavn og et DPAPI-beskyttet kodeord
(ConvertFrom-SecureString for den Windows-bruger, der kører scriptet).

.PARAMETER PayloadZip
Payload-zip fra windows/inno/build.py --payload-output. Indeholder mappen
"payload".

.PARAMETER PayloadManifestSha256
SHA-256 (64 hex-tegn) af payloadens manifest; står som payload_manifest_sha256
i byggekvitteringen.

.PARAMETER Version
Den version, serveren skal have efter opdateringen (release_id).

.PARAMETER ResultFile
Resultatfil (JSON). Scriptet nægter at overskrive en eksisterende fil.

.PARAMETER RoleIp
Serverens adresse i VM'en. Standard er en dokumentationsadresse.

.PARAMETER StepTimeoutSeconds
Længste tid til opdateringen.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$VMName,
    [Parameter(Mandatory)][string]$CredentialFile,
    [Parameter(Mandatory)][string]$PayloadZip,
    [Parameter(Mandatory)][string]$PayloadManifestSha256,
    [Parameter(Mandatory)][string]$Version,
    [Parameter(Mandatory)][string]$ResultFile,
    [string]$RoleIp = '198.51.100.10',
    [int]$StepTimeoutSeconds = 1800
)
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $ResultFile) { throw 'Resultatfilen findes allerede.' }
if ($PayloadManifestSha256 -notmatch '^[0-9a-fA-F]{64}$') { throw 'PayloadManifestSha256 skal være 64 hex-tegn.' }
$hash = $PayloadManifestSha256.ToLowerInvariant()
if (-not (Test-Path -LiteralPath $PayloadZip -PathType Leaf)) { throw 'Payload-zip findes ikke.' }
$zip = (Resolve-Path -LiteralPath $PayloadZip).Path
if (-not (Test-Path -LiteralPath $CredentialFile -PathType Leaf)) { throw 'Legitimationsfilen findes ikke.' }
$lines = @(Get-Content -LiteralPath $CredentialFile)
if ($lines.Count -ne 2) { throw 'Legitimationsfilen skal have præcis to linjer.' }
$credential = [Management.Automation.PSCredential]::new($lines[0], (ConvertTo-SecureString $lines[1]))
$session = $null
for ($attempt = 0; $attempt -lt 30 -and -not $session; $attempt++) {
    try { $session = New-PSSession -VMName $VMName -Credential $credential -ErrorAction Stop } catch { Start-Sleep -Seconds 10 }
}
if (-not $session) { throw 'Ingen forbindelse til test-VM.' }

$snapshot = {
    $data = "$env:ProgramData\ElevhubServer"
    $python = "$env:ProgramFiles\ElevhubServer\runtime\python.exe"
    $kept = [ordered]@{}
    foreach ($relative in 'school.json', 'admin-ssh\id_ed25519', 'admin-ssh\id_ed25519.pub') {
        $path = Join-Path $data $relative
        $kept[$relative] = if (Test-Path -LiteralPath $path) { (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower() } else { 'mangler' }
    }
    $connection = Join-Path $env:Public 'Documents\Elevvaerkstedet\school-connection.json'
    $kept['school-connection.json'] = if (Test-Path -LiteralPath $connection) { (Get-FileHash -LiteralPath $connection -Algorithm SHA256).Hash.ToLower() } else { 'mangler' }
    $code = @'
import json, os, sqlite3
out = {}
root = os.path.join(os.environ['ProgramData'], 'ElevhubServer')
for name, relative in (('print', 'printvaerksted/queue.sqlite3'), ('design', 'designvaerksted/afleveringer.sqlite3')):
    try:
        db = sqlite3.connect('file:' + os.path.join(root, relative).replace('\\', '/') + '?mode=ro', uri=True)
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        out[name] = {t: db.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0] for t in tables}
        db.close()
    except Exception as error:
        out[name] = 'fejl: ' + type(error).__name__
print(json.dumps(out, sort_keys=True))
'@
    New-Item -ItemType Directory -Force C:\mkv-test | Out-Null
    [IO.File]::WriteAllText('C:\mkv-test\rows.py', $code, [Text.UTF8Encoding]::new($false))
    $rows = if (Test-Path $python) { (& $python -B C:\mkv-test\rows.py) -join '' } else { 'ingen runtime' }
    [pscustomobject]@{
        release = (Get-Content "$env:ProgramFiles\ElevhubServer\runtime-release.json" -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json).release_id
        kept = $kept
        rows = $rows
        dataFiles = @(Get-ChildItem $data -Recurse -File -ErrorAction SilentlyContinue).Count
    }
}

try {
    $before = Invoke-Command -Session $session -ScriptBlock $snapshot
    if (-not $before.release -or $before.kept['school.json'] -eq 'mangler') { throw 'Test-VM har ingen eksisterende server (runtime-release.json eller school.json mangler); intet er ændret.' }
    # Unik mappe pr. kørsel, så en tidligere kørsels spor aldrig blandes ind.
    $folder = 'C:\mkv-test\update-' + [guid]::NewGuid().ToString('N')
    Invoke-Command -Session $session -ArgumentList $folder -ScriptBlock {
        param($f)
        if (Test-Path -LiteralPath $f) { throw 'Mappen i test-VM findes allerede.' }
        New-Item -ItemType Directory -Force $f | Out-Null
    }
    Copy-Item -LiteralPath $zip -Destination "$folder\payload.zip" -ToSession $session
    $step = Invoke-Command -Session $session -ArgumentList $folder, $hash, $Version, $RoleIp, $StepTimeoutSeconds -ScriptBlock {
        param($folder, $hash, $version, $RoleIp, $timeout)
        Set-Location $folder
        tar.exe -xf payload.zip
        [IO.File]::WriteAllText("$folder\profile.json", '{}', [Text.UTF8Encoding]::new($false))
        $clock = [Diagnostics.Stopwatch]::StartNew()
        $process = Start-Process -FilePath "$folder\payload\package\runtime\python.exe" -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput "$folder\out.txt" -RedirectStandardError "$folder\err.txt" `
            -ArgumentList '-B', "$folder\payload\lifecycle.py", '--payload', "$folder\payload", '--payload-sha256', $hash, '--action', 'auto', '--profile', "$folder\profile.json", '--apply'
        $null = $process.Handle
        $finished = $process.WaitForExit($timeout * 1000)
        $seconds = [int]$clock.Elapsed.TotalSeconds
        Start-Sleep -Seconds 20
        $release = (Get-Content "$env:ProgramFiles\ElevhubServer\runtime-release.json" -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json).release_id
        $tasks = @{}
        foreach ($task in Get-ScheduledTask | Where-Object TaskName -in 'ElevhubServer', 'ElevhubSchoolEnrollment') { $tasks[$task.TaskName] = [string]$task.State }
        $listening = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object LocalAddress -eq $RoleIp | ForEach-Object LocalPort | Sort-Object -Unique)
        $journal = $null
        $latest = Get-ChildItem "$env:ProgramData\ElevhubServer\installer-updates" -Directory | Sort-Object LastWriteTime | Select-Object -Last 1
        if ($latest) { $journal = (Get-Content (Join-Path $latest.FullName 'state.json') -Raw | ConvertFrom-Json).phase }
        $leftovers = @(Get-ChildItem $env:ProgramFiles -Directory | Where-Object Name -match '^ElevhubServer\.' | ForEach-Object Name)
        $problems = @()
        if (-not $finished) { $problems += "ikke færdig efter $timeout s" }
        elseif ($process.ExitCode -ne 0) { $problems += "exitkode $($process.ExitCode)" }
        if ($release -ne $version) { $problems += "version er '$release', forventede '$version'" }
        foreach ($name in 'ElevhubServer', 'ElevhubSchoolEnrollment') { if ($tasks[$name] -ne 'Running') { $problems += "$name er $($tasks[$name])" } }
        foreach ($port in 8770, 8771, 8772, 8773, 8774, 8780) { if ($listening -notcontains $port) { $problems += "port $port lytter ikke" } }
        if ($journal -ne 'complete_paused') { $problems += "journal er '$journal'" }
        if ($leftovers) { $problems += "efterladte programmapper: $($leftovers -join ', ')" }
        [pscustomobject]@{
            action = 'update'; version = $version; seconds = $seconds
            exitCode = $(if ($finished) { $process.ExitCode } else { $null }); release = $release; tasks = $tasks
            listening = $listening; journal = $journal; problems = $problems
            stderrTail = @(Get-Content "$folder\err.txt" -Tail 12 -ErrorAction SilentlyContinue)
            stdoutTail = @(Get-Content "$folder\out.txt" -Tail 3 -ErrorAction SilentlyContinue)
        }
    }
    $after = Invoke-Command -Session $session -ScriptBlock $snapshot
} finally {
    if ($session) { Remove-PSSession $session }
}
$problems = @($step.problems)
foreach ($name in $before.kept.Keys) { if ($before.kept[$name] -ne $after.kept[$name]) { $problems += "ændret eller mistet: $name" } }
$holdFree = $after.rows -notmatch '"maintenance_hold": [1-9]'
if (-not $holdFree) { $problems += 'køspærring er ikke ophævet' }
$rowsBefore = $before.rows -replace '"maintenance_hold": \d+', ''
$rowsAfter = $after.rows -replace '"maintenance_hold": \d+', ''
if ($rowsBefore -ne $rowsAfter) { $problems += 'queue rows changed during update' }
$result = [ordered]@{
    schema = 1; vm = $VMName; from = $before.release; to = $after.release; passed = ($problems.Count -eq 0)
    problems = $problems
    step = ($step | Select-Object action, version, seconds, exitCode, release, tasks, listening, journal, stderrTail, stdoutTail)
    keptFilesUnchanged = [bool](-not ($before.kept.Keys | Where-Object { $before.kept[$_] -ne $after.kept[$_] }))
    queueRowsBefore = $before.rows; queueRowsAfter = $after.rows; queueRowsEqualExceptHold = ($rowsBefore -eq $rowsAfter)
    dataFilesBefore = $before.dataFiles; dataFilesAfter = $after.dataFiles
}
[IO.File]::WriteAllText($ResultFile, ($result | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
'{0} -> {1}  {2} s  {3}' -f $before.release, $after.release, $step.seconds, $(if ($problems.Count) { 'FEJL: ' + ($problems -join '; ') } else { 'ok' })
'rows equal: ' + ($rowsBefore -eq $rowsAfter)
if ($problems.Count) { exit 1 }
