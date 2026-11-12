<#
.SYNOPSIS
Installs a server build on a clean local Hyper-V test VM and then applies
one or more updates on top of it. Nothing outside that VM is touched.

.DESCRIPTION
The test runs the same lifecycle.py that the installer EXE runs, on the exact
payload that the EXE contains (windows/inno/build.py --payload-output). It does
not click through the installer wizard.

  1. Restore the VM to its clean checkpoint and start it.
  2. Give the VM a fixed test address.
  3. Copy and unpack each payload.
  4. Clean installation from the first payload.
  5. One update per further payload, in the order given.
  6. After every step: check version, scheduled tasks, listening ports and,
     for updates, the journal state.

The result is written as JSON to -ResultFile and the script exits 0 only when
every step passed.

.PARAMETER Payloads
Payload zip files in installation order. Each zip contains the folder
"payload" as written by build.py --payload-output, and must have the matching
build receipt next to it as receipt.json (stdout of build.py).

.PARAMETER CredentialFile
Two lines: the VM's local administrator name and a DPAPI-protected password
(ConvertFrom-SecureString output for the current Windows user). The file is
private test material and must not be part of the source.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$VMName,
    [Parameter(Mandatory)][string]$Checkpoint,
    [Parameter(Mandatory)][string]$CredentialFile,
    [Parameter(Mandatory)][string[]]$Payloads,
    [Parameter(Mandatory)][string]$ResultFile,
    [string]$RoleIp = '198.51.100.10',
    [string]$StudentSubnet = '198.51.100.0/24',
    [int]$StepTimeoutSeconds = 1500
)
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $ResultFile) { throw 'Resultatfilen findes allerede.' }
$lines = @(Get-Content -LiteralPath $CredentialFile)
if ($lines.Count -ne 2) { throw 'Legitimationsfilen skal have præcis to linjer.' }
$credential = [Management.Automation.PSCredential]::new($lines[0], (ConvertTo-SecureString $lines[1]))

# powershell.exe -File passes "a.zip,b.zip" as one string; accept both forms.
$Payloads = @($Payloads | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$builds = foreach ($zip in $Payloads) {
    $zip = (Resolve-Path -LiteralPath $zip).Path
    $receipt = Get-Content -LiteralPath (Join-Path (Split-Path $zip) 'receipt.json') -Raw | ConvertFrom-Json
    if ($receipt.role -ne 'server' -or $receipt.test_artifact) { throw "Ikke en serverbygning: $zip" }
    [pscustomobject]@{ zip = $zip; version = $receipt.version; hash = $receipt.payload_manifest_sha256 }
}

if ((Get-VM -Name $VMName).State -ne 'Off') { Stop-VM -Name $VMName -TurnOff -Force }
Restore-VMSnapshot -VMName $VMName -Name $Checkpoint -Confirm:$false
Start-VM -Name $VMName
$session = $null
for ($attempt = 0; $attempt -lt 30 -and -not $session; $attempt++) {
    try { $session = New-PSSession -VMName $VMName -Credential $credential -ErrorAction Stop } catch { Start-Sleep -Seconds 10 }
}
if (-not $session) { throw 'Ingen forbindelse til test-VM.' }

$steps = [Collections.Generic.List[object]]::new()
try {
    Invoke-Command -Session $session -ArgumentList $RoleIp -ScriptBlock {
        param($RoleIp)
        if (Test-Path "$env:ProgramFiles\ElevhubServer") { throw 'Test-VM er ikke ren.' }
        New-Item -ItemType Directory -Force C:\mkv-test | Out-Null
        $adapter = Get-NetAdapter | Where-Object Status -eq 'Up' | Select-Object -First 1
        if (-not (Get-NetIPAddress -IPAddress $RoleIp -ErrorAction SilentlyContinue)) {
            New-NetIPAddress -InterfaceAlias $adapter.Name -IPAddress $RoleIp -PrefixLength 24 | Out-Null
        }
    }
    $index = 0
    foreach ($build in $builds) {
        $index++
        $folder = "C:\mkv-test\step$index"
        Invoke-Command -Session $session -ArgumentList $folder -ScriptBlock { param($f) New-Item -ItemType Directory -Force $f | Out-Null }
        Copy-Item -LiteralPath $build.zip -Destination "$folder\payload.zip" -ToSession $session
        $install = ($index -eq 1)
        $profile = if ($install) {
            @{ school_name = 'Testskolen'; role_ip = $RoleIp; student_subnet = $StudentSubnet; server_mode = 'vm'; theme = 'green'; machine = 'fbb2' } | ConvertTo-Json -Compress
        } else { '{}' }
        $step = Invoke-Command -Session $session -ArgumentList $folder, $build.hash, $profile, $StepTimeoutSeconds, $build.version, $install, $RoleIp -ScriptBlock {
            param($folder, $hash, $profile, $timeout, $version, $install, $RoleIp)
            Set-Location $folder
            tar.exe -xf payload.zip
            [IO.File]::WriteAllText("$folder\profile.json", $profile, [Text.UTF8Encoding]::new($false))
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
            if (-not $install) {
                $latest = Get-ChildItem "$env:ProgramData\ElevhubServer\installer-updates" -Directory | Sort-Object LastWriteTime | Select-Object -Last 1
                if ($latest) { $journal = (Get-Content (Join-Path $latest.FullName 'state.json') -Raw | ConvertFrom-Json).phase }
            }
            # The update's own hold on the print and laser queues must be gone,
            # otherwise the teacher cannot resume the queue afterwards.
            $holdScript = @'
import json, os, sqlite3
out = {}
root = os.path.join(os.environ['ProgramData'], 'ElevhubServer')
for name, relative in (('print', 'printvaerksted/queue.sqlite3'), ('design', 'designvaerksted/afleveringer.sqlite3')):
    try:
        db = sqlite3.connect('file:' + os.path.join(root, relative).replace('\\', '/') + '?mode=ro', uri=True)
        out[name] = db.execute('SELECT COUNT(*) FROM maintenance_hold').fetchone()[0]
        db.close()
    except Exception as error:
        out[name] = 'fejl: ' + type(error).__name__
print(json.dumps(out))
'@
            [IO.File]::WriteAllText("$folder\holds.py", $holdScript, [Text.UTF8Encoding]::new($false))
            $holds = & "$folder\payload\package\runtime\python.exe" -B "$folder\holds.py" | ConvertFrom-Json
            $leftovers = @(Get-ChildItem $env:ProgramFiles -Directory | Where-Object Name -match '^ElevhubServer\.' | ForEach-Object Name)
            $problems = @()
            if (-not $finished) { $problems += "ikke færdig efter $timeout s" }
            elseif ($process.ExitCode -ne 0) { $problems += "exitkode $($process.ExitCode)" }
            if ($release -ne $version) { $problems += "version er '$release', forventede '$version'" }
            foreach ($name in 'ElevhubServer', 'ElevhubSchoolEnrollment') { if ($tasks[$name] -ne 'Running') { $problems += "$name er $($tasks[$name])" } }
            foreach ($port in 8770, 8771, 8772, 8773, 8774, 8780) { if ($listening -notcontains $port) { $problems += "port $port lytter ikke" } }
            if (-not $install -and $journal -ne 'complete_paused') { $problems += "journal er '$journal'" }
            if ($leftovers) { $problems += "efterladte programmapper: $($leftovers -join ', ')" }
            if (-not $install) { foreach ($queue in 'print', 'design') { if ("$($holds.$queue)" -ne '0') { $problems += "køspærring på $queue er ikke ophævet ($($holds.$queue))" } } }
            [pscustomobject]@{
                action = $(if ($install) { 'install' } else { 'update' }); version = $version; seconds = $seconds
                exitCode = $(if ($finished) { $process.ExitCode } else { $null }); release = $release; tasks = $tasks
                listening = $listening; journal = $journal; holds = "print=$($holds.print) design=$($holds.design)"; problems = $problems
                stderrTail = @(Get-Content "$folder\err.txt" -Tail 8 -ErrorAction SilentlyContinue)
                stdoutTail = @(Get-Content "$folder\out.txt" -Tail 2 -ErrorAction SilentlyContinue)
            }
        }
        $steps.Add(($step | Select-Object action, version, seconds, exitCode, release, tasks, listening, journal, holds, problems, stderrTail, stdoutTail))
        if ($step.problems.Count) { break }
    }
} finally {
    if ($session) { Remove-PSSession $session }
}
$passed = ($steps.Count -eq @($builds).Count) -and -not ($steps | Where-Object { $_.problems.Count })
$result = [ordered]@{ schema = 1; vm = $VMName; checkpoint = $Checkpoint; passed = [bool]$passed; steps = $steps }
[IO.File]::WriteAllText($ResultFile, ($result | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
$steps | ForEach-Object { '{0,-8} {1,-20} {2,5} s  {3}' -f $_.action, $_.version, $_.seconds, $(if ($_.problems.Count) { 'FEJL: ' + ($_.problems -join '; ') } else { 'ok' }) }
if (-not $passed) { exit 1 }
