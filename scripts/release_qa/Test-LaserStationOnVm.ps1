<#
.SYNOPSIS
Installerer en bygget laserstation som standardbruger på en kørende test-VM,
der allerede har en testserver, og kontrollerer stationen og dens forbindelse.

.DESCRIPTION
Testen kører på en VM, der allerede kører og har en syntetisk testserver
(ElevhubServer med school.json, en eksporteret stationsforbindelse og
sikkerhedsfiler). Scriptet gør dette:

  1. Kontrollerer byggekvitteringen (role laser_station, ikke en testbygning),
     payloadens manifest og payload.zip mod -PayloadZipSha256.
  2. Kontrollerer, at serveren i VM'en er den forventede testopsætning
     (-SchoolName, -ServerIp) og at standardbrugeren er aktiv og ikke
     administrator.
  3. Kopierer payload.zip og dette script ind i en ny, unik mappe i VM'en og
     pakker payload ud.
  4. Kører stationsinstallationen (lifecycle.py) som standardbrugeren gennem
     en planlagt opgave med Limited-token og kodeord-logon. Administratorlegitimationen
     bruges kun til VM-styringen.
  5. Kontrollerer installerede filer, rolleadskillelse, aktiv version,
     navigation, stationens sundhedssvar på port 8790, samt verificeret TLS
     mod testserveren (en anonym 401 viser forbindelse og lærer-gate, ikke et
     lærerlogin).
  6. Kontrollerer, at serverens profil-, tillids- og ejerfiler er uændrede.

Scriptet gendanner, starter eller slukker ikke VM'en, ændrer ikke serveren,
logger ikke ind som lærer, sender ingen opgaver og kalder hverken laser-
overdragelse eller "færdig". Det tester ikke en fysisk laser. Stationsin-
stallationen installerer dog en station for standardbrugeren, stoler på dens
offentlige CA og starter dens normale brugerflade i test-VM'en.

Standardbrugeren skal have retten "Log on as a batch job"; se
Grant-StationBatchRight.ps1.

Legitimationsfilerne har to linjer: et navn og et DPAPI-beskyttet kodeord
(ConvertFrom-SecureString for den Windows-bruger, der kører scriptet). De er
privat testmateriale og må ikke være en del af kilden.

Scriptet kopierer sig selv ind i VM'en og kører dér med -GuestWorker
-GuestConfig. Disse to parametre er kun til den interne brug.

.PARAMETER BuildRoot
Byggemappen for laser_station med exe\build-provenance.json,
payload\installer-payload.json og payload.zip.

.PARAMETER CredentialFile
Legitimationsfil til VM'ens lokale administrator.

.PARAMETER StationCredentialFile
Legitimationsfil til standardbrugeren (to linjer; navnet kan have
computer-præfiks).

.PARAMETER ResultFile
Resultatfil (JSON). Scriptet nægter at overskrive en eksisterende fil.

.PARAMETER VMName
Navnet på den allerede kørende test-VM.

.PARAMETER StationUser
Standardbrugerens kontonavn i VM'en (uden computer-præfiks).

.PARAMETER PayloadZipSha256
Forventet SHA-256 (64 hex-tegn) af payload.zip.

.PARAMETER SchoolName
Skolenavn, som testserverens profil skal have. Standard: Testskolen.

.PARAMETER ServerIp
Testserverens adresse. Standard er en dokumentationsadresse.

.PARAMETER ReuseGuestWork
Genbrug en kendt, tidligere mappe i VM'en (C:\mkv-test\laser-station-<32 hex>)
efter en afbrudt kørsel. Zip-hashen kontrolleres igen.

.PARAMETER TimeoutSeconds
Længste tid til installation og kontrol (120-1800).
#>
[CmdletBinding()]
param(
    [string]$BuildRoot,
    [string]$CredentialFile,
    [string]$ResultFile,
    [string]$StationCredentialFile,
    [string]$VMName,
    [string]$StationUser,
    [string]$PayloadZipSha256,
    [string]$SchoolName = 'Testskolen',
    [string]$ServerIp = '198.51.100.10',
    [string]$ReuseGuestWork = '',
    [ValidateRange(120,1800)][int]$TimeoutSeconds = 900,
    [switch]$GuestWorker,
    [string]$GuestConfig
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Report([string]$Path, [object]$Value) {
    [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
}

function Invoke-GuestWorker {
    $cfg = Get-Content -LiteralPath $GuestConfig -Raw | ConvertFrom-Json
    $result = [ordered]@{schema=1;passed=$false;physicalLaserTested=$false;teacherAuthenticated=$false;queueActionsPerformed=$false;checks=@()}
    try {
        $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        if ($admin) { throw 'Station lifecycle requires an unelevated token; Limited task was not limited.' }
        $result.unelevated = $true
        $root = Join-Path $env:LOCALAPPDATA 'MakervaerkstedetLaserstation'
        if (Test-Path -LiteralPath $root) { throw 'Fresh test requires no existing station root; existing installation was preserved.' }
        if (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'Laserstation')) { throw 'Legacy station exists; it was preserved.' }
        if (Get-NetTCPConnection -State Listen -LocalPort 8790 -ErrorAction SilentlyContinue) { throw '8790 already in use; no process was stopped.' }
        $profile = Join-Path $cfg.work 'profile.json'
        Write-Report $profile @{connection_file=(Join-Path $cfg.work 'laser-station-connection.json')}
        $python = Join-Path $cfg.work 'payload\package\runtime\python.exe'
        $lifecycle = Join-Path $cfg.work 'payload\lifecycle.py'
        $arguments = '-B "{0}" --payload "{1}" --payload-sha256 {2} --action auto --profile "{3}" --apply' -f $lifecycle,(Join-Path $cfg.work 'payload'),$cfg.hash,$profile
        $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $cfg.work -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $cfg.work 'lifecycle.out.txt') -RedirectStandardError (Join-Path $cfg.work 'lifecycle.err.txt')
        $null = $process.Handle
        if (-not $process.WaitForExit(($cfg.timeout - 75) * 1000)) {
            # Only this installer process is stopped. No server/station-wide kill.
            Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
            throw 'Station installer timed out; retain guest staging and inspect any partial installation before retry.'
        }
        if ($process.ExitCode -ne 0) { $tail = (Get-Content -LiteralPath (Join-Path $cfg.work 'lifecycle.err.txt') -Tail 12 -ErrorAction SilentlyContinue) -join ' | '; throw ('Station lifecycle failed: exit ' + $process.ExitCode + '; stderr: ' + $tail) }
        $result.checks += 'exact laser_station payload installed through lifecycle.py under Limited token'
        $active = Get-Content -LiteralPath (Join-Path $root 'active.json') -Raw | ConvertFrom-Json
        if ($active.kind -ne 'makervaerkstedet-laserstation' -or $active.version_directory -notmatch '^versions/[a-f0-9]{32}$') { throw 'Invalid active station selection.' }
        $version = Join-Path $root $active.version_directory
        $program = Join-Path $version 'program'
        $installedPython = Join-Path $program 'runtime\python.exe'
        Push-Location -LiteralPath $program
        try {
            $infoText = & $installedPython -B -m app.laser_station_install info --install-root $root
            if ($LASTEXITCODE) { throw 'Installed station inventory/hash verification failed.' }
            $info = ($infoText -join "`n") | ConvertFrom-Json
        } finally { Pop-Location }
        if ($info.config.role -ne 'Station' -or $info.connection.server_url -ne $cfg.server -or $info.connection.station_id -ne $cfg.station -or $info.connection.machine -ne $cfg.machine) { throw 'Station identity differs from the synthetic server export.' }
        if ($info.config.port -ne 8790) { throw 'Unexpected local station port.' }
        $release = Get-Content -LiteralPath (Join-Path $program 'runtime-release.json') -Raw | ConvertFrom-Json
        if ($release.role -ne 'laser_station' -or $release.release_id -ne $cfg.version) { throw 'Installed runtime descriptor does not match the exact role/version.' }
        foreach ($forbidden in @('app\server.py','hub\elevhub.py','deployment\school-setup\Setup-School.ps1')) {
            if (Test-Path -LiteralPath (Join-Path $program $forbidden)) { throw ('Server role leaked into station installation: ' + $forbidden) }
        }
        foreach ($required in @('connection.json','laser-installation.json','school-ca.pem','station-program.json')) {
            if (-not (Test-Path -LiteralPath (Join-Path $version $required) -PathType Leaf)) { throw ('Missing station file: ' + $required) }
        }
        $navigation = Get-Content -LiteralPath $info.config.navigation_config -Raw | ConvertFrom-Json
        if ($navigation.print_url -ne ($cfg.server + '/makerspace') -or $navigation.admin_url -ne ($cfg.server + '/kontrolpanel')) { throw 'Station navigation does not reference the synthetic server.' }
        $deadline = (Get-Date).AddSeconds(45)
        $health = $null
        do {
            try { $health = Invoke-RestMethod 'http://127.0.0.1:8790/api/runtime/health' -TimeoutSec 2 } catch { Start-Sleep -Milliseconds 500 }
        } while (-not $health -and (Get-Date) -lt $deadline)
        if (-not $health -or $health.release_id -ne $cfg.version -or $health.service -ne 'laser_station' -or -not $health.runtime_instance) { throw 'Installed station runtime health is not ready or does not match role/version.' }
        $running = Get-Process -Id $health.pid
        if ([IO.Path]::GetFullPath($running.Path) -ne [IO.Path]::GetFullPath($installedPython)) { throw 'Health PID is not the selected installed Python.' }
        $state = Invoke-RestMethod 'http://127.0.0.1:8790/api/station/state' -TimeoutSec 3
        if ($state.server -ne $cfg.server -or $state.stationId -ne $cfg.station -or $state.bed.model -ne $cfg.machine) { throw 'Running station binding differs from its installed configuration.' }
        $result.checks += 'installed files, role separation, active selection, navigation and same-process runtime health verified'
        # Real verified TLS against the existing test server. An anonymous 401
        # proves connectivity and authentication gating, NOT a teacher login.
        $probe = @'
import json,ssl,sys
from urllib.request import build_opener,ProxyHandler,HTTPSHandler,Request,HTTPRedirectHandler
from urllib.error import HTTPError
class NoRedirect(HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs): return None
opener=build_opener(ProxyHandler({}),HTTPSHandler(context=ssl.create_default_context(cafile=sys.argv[2])),NoRedirect())
try:
 with opener.open(Request(sys.argv[1]),timeout=10) as response: code=response.status
except HTTPError as error:
 code=error.code;error.close()
assert code==401, 'Expected teacher authentication gate, got '+str(code)
print(json.dumps({'tlsVerified':True,'httpStatus':code,'teacherLoginRequired':True}))
'@
        $probeFile = Join-Path $cfg.work 'probe-tls.py'
        [IO.File]::WriteAllText($probeFile,$probe,[Text.UTF8Encoding]::new($false))
        $tlsText = & $installedPython -B $probeFile ($cfg.server + '/api/laser/stations/' + $cfg.station + '/configuration') $info.config.ca_file
        if ($LASTEXITCODE) { throw 'Synthetic server TLS/authentication-gate probe failed.' }
        $result.tls = ($tlsText -join "`n") | ConvertFrom-Json
        $result.checks += 'verified TLS and anonymous teacher gate against the existing same-VM synthetic server'
        $result.station = @{root=$root;version=$active.release;stationId=$state.stationId;machine=$state.bed.model;pid=$health.pid;runtimeInstance=$health.runtime_instance}
        $result.passed = $true
    } catch { $result.error = $_.Exception.Message }
    Write-Report (Join-Path $cfg.work $cfg.result) $result
    if (-not $result.passed) { exit 1 }
    exit 0
}

if ($GuestWorker) { Invoke-GuestWorker; exit }

# ---- Værten: kontrollér parametre, før noget røres ----------------------
$missing = @()
foreach ($required in 'BuildRoot','CredentialFile','ResultFile','StationCredentialFile','VMName','StationUser','PayloadZipSha256') {
    if ([string]::IsNullOrWhiteSpace((Get-Variable -Name $required -ValueOnly))) { $missing += '-' + $required }
}
if ($missing) { throw ('Manglende påkrævede parametre: ' + ($missing -join ', ') + '. Se Get-Help for dette script.') }
if ($PayloadZipSha256 -notmatch '^[0-9a-fA-F]{64}$') { throw 'PayloadZipSha256 skal være 64 hex-tegn.' }
$zipHash = $PayloadZipSha256.ToLowerInvariant()
if (Test-Path -LiteralPath $ResultFile) { throw 'Resultatfilen findes allerede; vælg en ny fil, så tidligere dokumentation bevares.' }
if (-not (Test-Path -LiteralPath $BuildRoot -PathType Container)) { throw 'BuildRoot findes ikke.' }
foreach ($file in $CredentialFile,$StationCredentialFile) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw ('Legitimationsfilen findes ikke: ' + $file) }
}
$stationLeaf = ($StationUser -split '\\')[-1]

$session = $null; $taskName = $null; $guestWork = $null
$report = [ordered]@{schema=1;vm=$VMName;passed=$false;preparedDriver=(Split-Path -Leaf $PSCommandPath);physicalLaserTested=$false;teacherAuthenticated=$false;queueActionsPerformed=$false}
try {
    if ((Get-VM -Name $VMName).State -ne 'Running') { throw 'The named test VM must already be running and released by its owner.' }
    $receipt = Get-Content -LiteralPath (Join-Path $BuildRoot 'exe\build-provenance.json') -Raw | ConvertFrom-Json
    if ($receipt.role -ne 'laser_station' -or $receipt.test_artifact -ne $false) { throw 'Expected a real laser_station build receipt.' }
    $manifest = Join-Path $BuildRoot 'payload\installer-payload.json'
    if ((Get-FileHash -LiteralPath $manifest -Algorithm SHA256).Hash.ToLowerInvariant() -ne $receipt.payload_manifest_sha256) { throw 'Payload manifest does not match the built EXE receipt.' }
    $zip = Join-Path $BuildRoot 'payload.zip'
    if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $zipHash) { throw 'Payload ZIP differs from the expected station build (PayloadZipSha256).' }
    $lines = @(Get-Content -LiteralPath $CredentialFile)
    if ($lines.Count -ne 2) { throw 'Expected a two-line DPAPI credential file.' }
    $credential = [Management.Automation.PSCredential]::new($lines[0],(ConvertTo-SecureString $lines[1]))
    $lines = $null
    $stationLines=@(Get-Content -LiteralPath $StationCredentialFile)
    if($stationLines.Count -ne 2 -or ($stationLines[0] -split '\\')[-1] -ne $stationLeaf){throw 'Expected a two-line credential file for the named standard user.'}
    $stationCredential=[Management.Automation.PSCredential]::new($stationLines[0],(ConvertTo-SecureString $stationLines[1]))
    $stationLines=$null
    $session = New-PSSession -VMName $VMName -Credential $credential
    $guestWork = if($ReuseGuestWork){$ReuseGuestWork}else{'C:\mkv-test\laser-station-' + [guid]::NewGuid().ToString('N')}
    if($guestWork -notmatch '^C:\\mkv-test\\laser-station-[a-f0-9]{32}$'){throw 'Unexpected guest staging path.'}
    $identity = Invoke-Command -Session $session -ArgumentList $guestWork,$stationCredential.UserName,([bool]$ReuseGuestWork),$SchoolName,$ServerIp,$stationLeaf -ScriptBlock {
        param($work,$user,$reuse,$schoolName,$serverIp,$accountName)
        $ErrorActionPreference='Stop'
        $school=Get-Content "$env:ProgramData\ElevhubServer\school.json" -Raw | ConvertFrom-Json
        if ($school.school_name -ne $schoolName -or $school.server -ne $serverIp -or $school.role -ne 'vm' -or $school.remote_laser_station -ne $true) { throw 'Existing server is not the expected synthetic remote-station VM setup.' }
        $public=Join-Path $env:Public 'Documents\Elevvaerkstedet'
        $connection=Get-ChildItem -LiteralPath $public -Filter 'laser-station-connection*.json' | Sort-Object LastWriteTime | Select-Object -Last 1
        if (-not $connection) { throw 'Synthetic server export is missing; do not fabricate a school connection.' }
        $value=Get-Content -LiteralPath $connection.FullName -Raw | ConvertFrom-Json
        if ($value.kind -ne 'makervaerkstedet-laserstation-connection' -or $value.school_name -ne $schoolName -or $value.server_url -ne ('https://' + $serverIp + ':8780') -or $value.station_id -ne $school.laser_station_id -or $value.machine -ne $school.laser_machine) { throw 'Connection export does not match the known test server.' }
        $stationUser=Get-LocalUser -Name $accountName
        $admins=@(Get-LocalGroupMember -SID 'S-1-5-32-544' | ForEach-Object {$_.SID.Value})
        if(-not $stationUser.Enabled -or $admins -contains $stationUser.SID.Value){throw 'Station QA account must be enabled and outside Administrators.'}
        if($reuse){if(-not (Test-Path -LiteralPath $work -PathType Container)){throw 'Known guest staging is missing.'}}
        else{New-Item -ItemType Directory -Path $work | Out-Null}
        & icacls.exe $work /grant ($user + ':(OI)(CI)M') | Out-Null
        if ($LASTEXITCODE) { throw 'Could not grant the existing QA user access to its own test staging.' }
        Copy-Item -LiteralPath $connection.FullName -Destination (Join-Path $work 'laser-station-connection.json')
        $files=@("$env:ProgramData\ElevhubServer\school.json","$env:ProgramData\ElevhubServer\installation-owner.json") + @(Get-ChildItem "$env:ProgramData\ElevhubServer\security" -File | Select-Object -ExpandProperty FullName)
        $hashes=@{};foreach($file in $files){if(Test-Path -LiteralPath $file -PathType Leaf){$hashes[$file]=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash}}
        @{user=$user;server=$value.server_url;station=$value.station_id;machine=$value.machine;before=$hashes}
    }
    if(-not $ReuseGuestWork){Copy-Item -LiteralPath $zip -Destination (Join-Path $guestWork 'payload.zip') -ToSession $session}
    $report.transfer = Invoke-Command -Session $session -ArgumentList $guestWork,$zipHash,$receipt.payload_manifest_sha256,([bool]$ReuseGuestWork) -ScriptBlock {
        param($work,$expectedZip,$expectedManifest,$reuse)
        $ErrorActionPreference='Stop'
        $archive=Join-Path $work 'payload.zip'
        $actualZip=(Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
        if($actualZip -ne $expectedZip){throw 'Guest payload ZIP hash mismatch; nothing extracted.'}
        if(-not $reuse){
            if(Test-Path -LiteralPath (Join-Path $work 'payload')){throw 'Payload extraction target already exists; staging preserved.'}
            Push-Location -LiteralPath $work
            try {
                & tar.exe -xf $archive
                if($LASTEXITCODE){throw 'Guest payload extraction failed; inspect retained staging.'}
            } finally { Pop-Location }
        }
        $actualManifest=(Get-FileHash -LiteralPath (Join-Path $work 'payload\installer-payload.json') -Algorithm SHA256).Hash.ToLowerInvariant()
        if($actualManifest -ne $expectedManifest){throw 'Extracted payload manifest differs from the EXE receipt.'}
        @{zipSha256=$actualZip;payloadManifestSha256=$actualManifest;singleArchiveTransfer=$true;existingVerifiedStagingReused=$reuse}
    }
    # The guest worker is this same script, run inside the VM as driver.ps1.
    Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $guestWork 'driver.ps1') -ToSession $session
    $cfg=@{work=$guestWork;result=('guest-result-standarduser-'+[guid]::NewGuid().ToString('N')+'.json');hash=$receipt.payload_manifest_sha256;version=$receipt.version;timeout=$TimeoutSeconds;server=$identity.server;station=$identity.station;machine=$identity.machine}
    $taskName='MKV-Laser-QA-' + [guid]::NewGuid().ToString('N')
    Invoke-Command -Session $session -ArgumentList $cfg,$taskName,$identity.user,$stationCredential -ScriptBlock {
        param($config,$name,$user,$taskCredential)
        $ErrorActionPreference='Stop'
        [IO.File]::WriteAllText((Join-Path $config.work 'driver-config.json'),($config|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
        $principal=New-ScheduledTaskPrincipal -UserId $user -LogonType Password -RunLevel Limited
        $arguments='-NoLogo -NoProfile -ExecutionPolicy Bypass -File "{0}\driver.ps1" -GuestWorker -GuestConfig "{0}\driver-config.json"' -f $config.work
        $action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $config.work
        $settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Seconds $config.timeout)
        $task=New-ScheduledTask -Action $action -Principal $principal -Settings $settings
        Register-ScheduledTask -TaskName $name -InputObject $task -User $user -Password $taskCredential.GetNetworkCredential().Password | Out-Null
        Start-ScheduledTask -TaskName $name
    }
    $deadline=(Get-Date).AddSeconds($TimeoutSeconds)
    $completed=$null
    do {
        Start-Sleep -Seconds 2
        $completed=Invoke-Command -Session $session -ArgumentList $guestWork,$cfg.result -ScriptBlock {param($work,$result) if(Test-Path -LiteralPath (Join-Path $work $result)){Get-Content -LiteralPath (Join-Path $work $result) -Raw}}
    } while (-not $completed -and (Get-Date) -lt $deadline)
    if (-not $completed) { throw 'Limited standard-user task timed out; inspect guest staging before retry.' }
    $report.guest=$completed | ConvertFrom-Json
    if (-not $report.guest.passed) { throw ('Station test failed: ' + $report.guest.error) }
    $after=Invoke-Command -Session $session -ArgumentList $identity.before -ScriptBlock {param($before) $after=@{};foreach($file in $before.Keys){$after[$file]=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash};$after}
    foreach($file in $identity.before.Keys){if($identity.before[$file] -ne $after[$file]){throw 'Existing synthetic server profile/trust/owner file changed.'}}
    $report.serverProfileAndTrustPreserved=$true
    $report.preservedFileCount=$identity.before.Count
    $report.build=@{role=$receipt.role;version=$receipt.version;payloadManifestSha256=$receipt.payload_manifest_sha256}
    $report.passed=$true
} catch { $report.error=$_.Exception.Message }
finally {
    $report.guestStaging=$guestWork
    if($session -and $taskName){
        Invoke-Command -Session $session -ArgumentList $taskName -ScriptBlock {param($name) $task=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue;if($task){if($task.State -eq 'Running'){Stop-ScheduledTask -TaskName $name};Unregister-ScheduledTask -TaskName $name -Confirm:$false}} -ErrorAction SilentlyContinue
    }
    if($session){Remove-PSSession $session}
    Write-Report $ResultFile $report
}
if(-not $report.passed){Write-Error 'Laser station VM test failed; see private result and guest staging.';exit 1}
Write-Output 'PASS: installed station/runtime/TLS gate and existing server profile preservation. Teacher workflow and physical laser remain untested.'
