"""Execute real installer AST sections, never either installer entry point.

All paths belong to TemporaryDirectory. ACL and installer process calls are
recording fakes; no task, firewall, trust store or hardware API is executed.
"""
import os
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
INSTALL = ROOT / "windows-elevhub/server/install.ps1"
SETUP = ROOT / "deployment/school-setup/Setup-School.ps1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")
PROVISION = INSTALL.parent / 'provision.py'
spec = importlib.util.spec_from_file_location('installer_provision_fixture', PROVISION)
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


HARNESS = r'''
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
# Load built-in script cmdlets before isolating ProgramFiles in this process.
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Management\Microsoft.PowerShell.Management.psd1') -ErrorAction Stop
function Parse([string]$Path) {
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($Path,[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw ($errors | Out-String) }
    return $ast
}
function Section($Statements,[string]$Start,[string]$End) {
    $first = -1; $last = -1
    for ($i=0; $i -lt $Statements.Count; $i++) {
        if ($first -lt 0 -and $Statements[$i].Extent.Text.StartsWith($Start)) { $first=$i; continue }
        if ($first -ge 0 -and $Statements[$i].Extent.Text.StartsWith($End)) { $last=$i; break }
    }
    if ($first -lt 0 -or $last -lt 0) { throw "Missing AST section: $Start -> $End" }
    return ($Statements[$first..($last-1)] | ForEach-Object { $_.Extent.Text }) -join "`n"
}
function FunctionText($Ast,[string]$Name) {
    $nodes=@($Ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $Name},$true))
    if ($nodes.Count -ne 1) { throw "Missing unique function $Name" }
    return $nodes[0].Extent.Text
}
function Assert([bool]$Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
$installAst=Parse $env:INSTALL_SCRIPT; $setupAst=Parse $env:SETUP_SCRIPT
$layoutAst=Parse (Join-Path (Split-Path -Parent $env:SETUP_SCRIPT) 'school-layout.ps1')
. (Join-Path (Split-Path -Parent $env:INSTALL_SCRIPT) 'school-uninstall-guards.ps1')
foreach ($name in @('Full','Get-InstalledRoleIp','Get-InstalledStudentSubnet','Assert-NotReparse','Assert-NoReparseTree','Write-JsonAtomic','Assert-OwnedProgramTree','Assert-InstallSpace','Get-TaskPrincipalSid','Assert-OwnedScheduledTask')) { Invoke-Expression (FunctionText $installAst $name) }
Invoke-Expression (FunctionText $setupAst 'Assert-SchoolDataPath')
Invoke-Expression (FunctionText $layoutAst 'Assert-SchoolLaserProfile')
$env:ProgramFiles=Join-Path $env:FIXTURE_ROOT 'program-files'
$env:ProgramData=Join-Path $env:FIXTURE_ROOT 'program-data'
$PackageRoot=Join-Path $env:FIXTURE_ROOT 'package'; $InstallRoot=Join-Path $env:ProgramFiles 'ElevhubServer'; $DataRoot=Join-Path $env:ProgramData 'ElevhubServer'
$ExpectedInstall=$InstallRoot; $ExpectedData=$DataRoot
New-Item -ItemType Directory -Path $PackageRoot,(Join-Path $PackageRoot 'hub'),$env:ProgramFiles,$DataRoot | Out-Null
New-Item -ItemType Directory -Path (Join-Path $PackageRoot 'server') | Out-Null
Copy-Item -LiteralPath $env:PROVISION_SCRIPT -Destination (Join-Path $PackageRoot 'server\provision.py')
$env:FIXTURE_PROVISION_MODULE=(Join-Path $PackageRoot 'server\provision.py')
[IO.File]::WriteAllText((Join-Path $PackageRoot 'server\fixture-provision.py'), @'
import importlib.util
import os
import sys

source = os.environ['FIXTURE_PROVISION_MODULE']
spec = importlib.util.spec_from_file_location('fixture_provision', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
# The fixture owns its temporary ACL boundary.  Production provision.py is
# unchanged and still refuses private writes from an unelevated process.
module._restrict_private_path = lambda path: None
arguments = sys.argv[1:]
if arguments and ('provision.py' in arguments[0].lower()
                  or os.path.normcase(os.path.abspath(arguments[0])) == os.path.normcase(os.path.abspath(source))):
    arguments = arguments[1:]
sys.argv = [source, *arguments]
raise SystemExit(module.main())
'@, [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $PackageRoot 'integrity.json'),'fixture manifest',[Text.UTF8Encoding]::new($false))
Copy-Item -LiteralPath $env:BASE_CONFIG -Destination (Join-Path $PackageRoot 'hub\konfiguration.windows.json')
[IO.File]::WriteAllText((Join-Path $PackageRoot 'program.txt'),'owned package')
$fixtureFiles=[ordered]@{}
Get-ChildItem -LiteralPath $PackageRoot -File -Recurse | Where-Object Name -ne 'integrity.json' | ForEach-Object { $fixtureFiles[$_.FullName.Substring($PackageRoot.Length+1).Replace('\','/')]=(Get-FileHash -LiteralPath $_.FullName).Hash.ToLowerInvariant() }
Write-JsonAtomic (Join-Path $PackageRoot 'integrity.json') ([ordered]@{algorithm='sha256';files=$fixtureFiles})
$RoleIp='192.0.2.2'; $RoleNetwork='192.0.2.0/24'; $StudentSubnet=$RoleNetwork; $TeacherAddress='127.0.0.1'
$TeacherPort=8780; $PrintBackendPort=8765; $DesignBackendPort=8766; $TeacherBackendPort=8767
$TaskName='ElevhubServer'; $FirewallPorts=@(8770,8771,8772,8773)
$Resume=[Management.Automation.SwitchParameter]::new($true); $GenerationLayout=$false; $UseExistingPrintBackend=$false; $releaseManifestSha256=$null; $RecoveryBackup=''
[IO.File]::WriteAllText((Join-Path $DataRoot 'instance.lock'),'1')
$Security=Join-Path $DataRoot 'security'; New-Item -ItemType Directory -Path $Security | Out-Null
foreach ($name in @('ca.crt','server.crt','server.key','enheder.json')) { [IO.File]::WriteAllText((Join-Path $Security $name),"retained-$name") }
$queue=Join-Path $DataRoot 'printvaerksted'; New-Item -ItemType Directory -Path $queue | Out-Null
[IO.File]::WriteAllText((Join-Path $queue 'queue.json'),'retained queue')
[IO.File]::WriteAllText((Join-Path $queue 'makerspace-bridge.key'),'retained bridge key')
$adminSsh=Join-Path $DataRoot 'admin-ssh'; New-Item -ItemType Directory -Path $adminSsh | Out-Null
[IO.File]::WriteAllText((Join-Path $adminSsh 'id_ed25519'),'retained administration key')
$programRoot=$PackageRoot; $packageManifest=[pscustomobject]@{localAiIncluded=$false}
$aclCalls=[Collections.Generic.List[object]]::new()
function Invoke-Icacls([string]$Path,[string[]]$Arguments) { $aclCalls.Add([pscustomobject]@{path=$Path;args=$Arguments}) }
function Get-ScheduledTask { return $null }
function Get-NetFirewallRule { return $null }
function Assert-PortsFree { }
$provisionCalls=[Collections.Generic.List[object]]::new(); $useRealProvision=$false
function FixturePython {
    $provisionCalls.Add(@($args))
    if ($script:useRealProvision -or $args -contains 'check-initial-install-tree') {
        $invokeArgs=@($args | Where-Object { ([string]$_) -ne '-B' -and ([string]$_) -notmatch '(?i)provision\.py$' })
        & $env:FIXTURE_PYTHON (Join-Path $PackageRoot 'server\fixture-provision.py') @invokeArgs
    } else { $global:LASTEXITCODE=0 }
}
$pythonSource='FixturePython'
function NewOwner {
    return [ordered]@{ schemaVersion=1;product='ElevhubServer';ownerId='11111111-1111-4111-8111-111111111111';state='rolledBack';createdUtc='original';installRoot=$InstallRoot;dataRoot=$DataRoot;taskName=$TaskName;roleIp=$RoleIp;roleNetwork=$RoleNetwork;studentSubnet=$StudentSubnet;teacherAddress=$TeacherAddress;teacherPort=$TeacherPort;printBackendPort=$PrintBackendPort;designBackendPort=$DesignBackendPort;teacherBackendPort=$TeacherBackendPort;generationLayout=$false;externalBackends=@();firewallNames=@($FirewallPorts | ForEach-Object { "ElevhubServer-$_" });caThumbprint=$null;packageManifestSha256=(Get-FileHash -LiteralPath (Join-Path $PackageRoot 'integrity.json')).Hash.ToLowerInvariant() }
}
$ownerMarker=Join-Path $DataRoot 'installation-owner.json'; $originalOwner=NewOwner; $marker=$originalOwner; Write-JsonAtomic $ownerMarker $originalOwner
$RemoteLaserStation=$false; $Machine='fbb2'
$preflight=Section $installAst.EndBlock.Statements 'if (Test-Path -LiteralPath $InstallRoot)' '$ownerId ='
$prepareMarker=Section $installAst.EndBlock.Statements '$ownerId =' 'try {'
$tryAst=@($installAst.EndBlock.Statements | Where-Object { $_ -is [Management.Automation.Language.TryStatementAst] })[-1]
$prepareRoot=Section $tryAst.Body.Statements 'if (-not $Resume)' '$securityPath ='
$provisionSecurity=Section $tryAst.Body.Statements '$securityPath =' '$security = $securityPath'
$prepareSecurity=Section $tryAst.Body.Statements '$security = $securityPath' 'New-Item -ItemType Directory -Path $InstallRoot'
$configure=Section $tryAst.Body.Statements '$runtimeConfig = Join-Path' 'Write-JsonAtomic (Join-Path $InstallRoot'
$schoolDispatch=Section $setupAst.EndBlock.Statements '$ownerMarker = Join-Path' '$owner = Get-Content'
function Fingerprints {
    return (@(Get-ChildItem -LiteralPath $DataRoot -File -Recurse | Sort-Object FullName | ForEach-Object { $_.FullName + '=' + (Get-FileHash -LiteralPath $_.FullName).Hash }) -join "`n")
}
'''


@unittest.skipUnless(POWERSHELL, "PowerShell is required for behavioral installer fixtures")
class InstallerResumeBehaviorTest(unittest.TestCase):
    def run_fixture(self, scenario, material=None):
        with tempfile.TemporaryDirectory(prefix="makerspace-resume-") as fixture:
            env = {**os.environ, "FIXTURE_ROOT": fixture, "INSTALL_SCRIPT": str(INSTALL),
                   "SETUP_SCRIPT": str(SETUP), "BASE_CONFIG": str(INSTALL.parent / "hub/konfiguration.windows.json"),
                   "PROVISION_SCRIPT": str(PROVISION), "FIXTURE_PYTHON": sys.executable,
                   "TLS_MATERIAL": json.dumps(material or {})}
            result = subprocess.run(
                [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", HARNESS + scenario + "\n'PASS'"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40, env=env,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_replay_retains_owner_tls_queue_and_exact_configuration(self):
        self.run_fixture(r'''
# Create the actual fresh-install configuration block, then customize it.
$Resume=[Management.Automation.SwitchParameter]::new($false); Invoke-Expression $configure
$runtimeConfig=Join-Path $DataRoot 'konfiguration.json'
$config=Get-Content -LiteralPath $runtimeConfig -Raw -Encoding UTF8 | ConvertFrom-Json
$config.apps[0].navn='Værksted med æ, ø og å'
$config | Add-Member -NotePropertyName school_extension -NotePropertyValue 'Bevar lærervalg'
Write-JsonAtomic $runtimeConfig $config
$configHash=(Get-FileHash -LiteralPath $runtimeConfig).Hash
$keyHash=(Get-FileHash -LiteralPath (Join-Path $Security 'server.key')).Hash
$queueHash=(Get-FileHash -LiteralPath (Join-Path $queue 'queue.json')).Hash
$registryHash=(Get-FileHash -LiteralPath (Join-Path $Security 'enheder.json')).Hash
$bridgeHash=(Get-FileHash -LiteralPath (Join-Path $queue 'makerspace-bridge.key')).Hash
$adminHash=(Get-FileHash -LiteralPath (Join-Path $adminSsh 'id_ed25519')).Hash
$Resume=[Management.Automation.SwitchParameter]::new($true)
Invoke-Expression $preflight; Invoke-Expression $prepareMarker
$securityPath=$Security
Invoke-Expression $prepareRoot; Invoke-Expression $prepareSecurity; Invoke-Expression $configure
$saved=Get-Content -LiteralPath $ownerMarker -Raw -Encoding UTF8 | ConvertFrom-Json
Assert ($saved.ownerId -eq '11111111-1111-4111-8111-111111111111' -and $saved.createdUtc -eq 'original' -and $saved.state -eq 'installing') 'Replay replaced installation identity.'
Assert ((Get-FileHash -LiteralPath $runtimeConfig).Hash -eq $configHash) 'Replay rewrote configuration.'
Assert ((Get-FileHash -LiteralPath (Join-Path $Security 'server.key')).Hash -eq $keyHash) 'Replay rewrote TLS key.'
Assert ((Get-FileHash -LiteralPath (Join-Path $queue 'queue.json')).Hash -eq $queueHash) 'Replay rewrote queue.'
Assert ((Get-FileHash -LiteralPath (Join-Path $Security 'enheder.json')).Hash -eq $registryHash) 'Replay rewrote registry.'
Assert ((Get-FileHash -LiteralPath (Join-Path $queue 'makerspace-bridge.key')).Hash -eq $bridgeHash) 'Replay rewrote bridge key.'
Assert ((Get-FileHash -LiteralPath (Join-Path $adminSsh 'id_ed25519')).Hash -eq $adminHash) 'Replay rewrote administration key.'
$lockCalls=@($aclCalls | Where-Object path -eq (Join-Path $Security 'enheder.lock'))
Assert ($lockCalls.Count -eq 1 -and $lockCalls[0].args -contains '*S-1-5-19:M') 'Registry lock lacks LocalService modify contract.'
Assert (Test-Path -LiteralPath (Join-Path $Security 'enheder.lock') -PathType Leaf) 'Registry lock was not created.'
''')

    def test_resume_rejects_marker_mismatches_before_mutation(self):
        self.run_fixture(r'''
foreach ($bad in @(
    @{roleIp='172.17.1.3'}, @{roleNetwork='172.18.0.0/24'}, @{studentSubnet='172.18.0.0/24'},
    @{teacherAddress='192.0.2.2'}, @{taskName='other'}, @{ownerId=''},
    @{printBackendPort=9991}, @{designBackendPort=9992}, @{teacherBackendPort=9993}, @{teacherPort=9994},
    @{generationLayout=$true}, @{externalBackends=@('print')}, @{externalBackends=@('unknown')}, @{packageManifestSha256='wrong'},
    @{installRoot=(Join-Path $env:FIXTURE_ROOT 'elsewhere')}, @{dataRoot=(Join-Path $env:FIXTURE_ROOT 'elsewhere')}, @{state='installed'}
)) {
    $candidate=NewOwner; foreach ($name in $bad.Keys) { $candidate[$name]=$bad[$name] }; Write-JsonAtomic $ownerMarker $candidate
    $before=Fingerprints; $rejected=$false
    try { Invoke-Expression $preflight } catch { $rejected=$true }
    Assert $rejected ('Accepted mismatched marker: '+($bad|ConvertTo-Json -Compress))
    Assert ((Fingerprints) -ceq $before -and $aclCalls.Count -eq 0) 'Rejected preflight changed data or ACLs.'
}
Write-JsonAtomic $ownerMarker (NewOwner)
Remove-Item -LiteralPath (Join-Path $Security 'server.key')
$before=Fingerprints; $rejected=$false
try { Invoke-Expression $preflight } catch { $rejected=$true }
Assert ($rejected -and (Fingerprints) -ceq $before) 'Incomplete TLS was not safely rejected.'
''')

    def test_actual_rollback_after_tls_task_and_readiness_can_replay(self):
        self.run_fixture(r'''
Invoke-Expression (FunctionText $installAst 'Remove-OwnedResources')
function Get-InstallationStartupMode { return 'legacy' }
function Get-CimInstance { return @() }
$events=[Collections.Generic.List[string]]::new()
function Get-ScheduledTask { if ($script:taskPresent) { return $script:ownedTask } }
function Stop-ScheduledTask { $events.Add('stop-task') }
function Unregister-ScheduledTask { $script:taskPresent=$false; $events.Add('remove-task') }
function Get-NetFirewallRule { param($Name) if ($script:rulePresent -and $Name -eq 'ElevhubServer-8770') { return $script:ownedRule } }
function Get-NetFirewallPortFilter { return [pscustomobject]@{Protocol='TCP';LocalPort=8770} }
function Get-NetFirewallAddressFilter { return [pscustomobject]@{LocalAddress=@($RoleIp);RemoteAddress=@($StudentSubnet)} }
function Get-NetFirewallApplicationFilter { return [pscustomobject]@{Program=(Join-Path $InstallRoot 'runtime\python.exe')} }
function Remove-NetFirewallRule { $script:rulePresent=$false; $events.Add('remove-rule') }
foreach ($stage in @('after-tls','after-task','readiness')) {
    New-Item -ItemType Directory -Path $InstallRoot | Out-Null
    [IO.File]::WriteAllText((Join-Path $InstallRoot 'program.txt'),'owned package')
    $candidate=NewOwner; $candidate.state='installing'
    Write-JsonAtomic $ownerMarker $candidate
    $script:taskPresent=$stage -ne 'after-tls'; $script:rulePresent=$stage -eq 'readiness'
    $expectedPython=Join-Path $InstallRoot 'runtime\python.exe'; $expectedBootstrap=Join-Path $InstallRoot 'server\bootstrap.py'
    $arguments='-B "{0}" --package-root "{1}" --data-root "{2}" --role-ip "{3}" --teacher-port "{4}" --teacher-address "{5}"' -f $expectedBootstrap,$InstallRoot,$DataRoot,$RoleIp,$TeacherPort,$TeacherAddress
    $script:ownedTask=[pscustomobject]@{Description='ElevhubServer owner=11111111-1111-4111-8111-111111111111';Actions=@([pscustomobject]@{Execute=$expectedPython;WorkingDirectory=$InstallRoot;Arguments=$arguments});Principal=[pscustomobject]@{UserId='S-1-5-19'}}
    $script:ownedRule=[pscustomobject]@{Description='ElevhubServer owner=11111111-1111-4111-8111-111111111111';Direction='Inbound';Action='Allow';Enabled='True';Profile='Any'}
    $keyHash=(Get-FileHash -LiteralPath (Join-Path $Security 'server.key')).Hash
    $queueHash=(Get-FileHash -LiteralPath (Join-Path $queue 'queue.json')).Hash
    Remove-OwnedResources $ownerMarker
    $rolledBack=Get-Content -LiteralPath $ownerMarker -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert ($rolledBack.state -eq 'rolledBack' -and $rolledBack.ownerId -eq '11111111-1111-4111-8111-111111111111') ('Rollback failed: '+$stage)
    Assert (-not (Test-Path -LiteralPath $InstallRoot) -and -not $script:taskPresent -and -not $script:rulePresent) 'Rollback left owned resources.'
    Assert ((Get-FileHash -LiteralPath (Join-Path $Security 'server.key')).Hash -eq $keyHash) 'Rollback changed TLS key.'
    Assert ((Get-FileHash -LiteralPath (Join-Path $queue 'queue.json')).Hash -eq $queueHash) 'Rollback changed queue.'
    Invoke-Expression $preflight; Invoke-Expression $prepareMarker
    Assert ($marker.ownerId -eq '11111111-1111-4111-8111-111111111111' -and $marker.state -eq 'installing') ('Replay failed: '+$stage)
}
Assert ($events -contains 'stop-task' -and $events -contains 'remove-task' -and $events -contains 'remove-rule') 'Fixture did not exercise resource rollback.'
''')

    def test_changed_configuration_rejected_before_mutation(self):
        self.run_fixture(r'''
$Resume=[Management.Automation.SwitchParameter]::new($false); Invoke-Expression $configure
$config=Get-Content -LiteralPath $runtimeConfig -Raw -Encoding UTF8 | ConvertFrom-Json
$config.laerer_port=9999; Write-JsonAtomic $runtimeConfig $config
$Resume=[Management.Automation.SwitchParameter]::new($true)
$before=Fingerprints; $rejected=$false
try { Invoke-Expression $preflight } catch { $rejected=$true }
Assert ($rejected -and (Fingerprints) -ceq $before -and $aclCalls.Count -eq 1) 'Changed runtime identity was not safely rejected.'
''')

    def test_hard_crash_installing_and_rollback_failed_recover_owned_partial_programs(self):
        self.run_fixture(r'''
Invoke-Expression (FunctionText $installAst 'Remove-OwnedResources')
function Get-InstallationStartupMode { return 'legacy' }
function Get-CimInstance { return @() }
$events=[Collections.Generic.List[string]]::new()
function Get-ScheduledTask { if ($script:taskPresent) { return $script:ownedTask } }
function Stop-ScheduledTask { $events.Add('stop') }
function Unregister-ScheduledTask { $script:taskPresent=$false; $events.Add('unregister') }
foreach ($state in @('installing','rollbackFailed')) {
    foreach ($taskExists in @($false,$true)) {
        New-Item -ItemType Directory -Path $InstallRoot | Out-Null
        [IO.File]::WriteAllText((Join-Path $InstallRoot 'program.txt'),'owned pack')
        $candidate=NewOwner; $candidate.state=$state; Write-JsonAtomic $ownerMarker $candidate
        $script:taskPresent=$taskExists
        $arguments='-B "{0}" --package-root "{1}" --data-root "{2}" --role-ip "{3}" --teacher-port "{4}" --teacher-address "{5}"' -f (Join-Path $InstallRoot 'server\bootstrap.py'),$InstallRoot,$DataRoot,$RoleIp,$TeacherPort,$TeacherAddress
        $script:ownedTask=[pscustomobject]@{Description=('ElevhubServer owner='+$candidate.ownerId);Actions=@([pscustomobject]@{Execute=(Join-Path $InstallRoot 'runtime\python.exe');WorkingDirectory=$InstallRoot;Arguments=$arguments});Principal=[pscustomobject]@{UserId='S-1-5-19'}}
        $keyHash=(Get-FileHash -LiteralPath (Join-Path $Security 'server.key')).Hash
        $queueHash=(Get-FileHash -LiteralPath (Join-Path $queue 'queue.json')).Hash
        Invoke-Expression $preflight
        $saved=Get-Content -LiteralPath $ownerMarker -Raw -Encoding UTF8 | ConvertFrom-Json
        Assert ($saved.state -eq 'rolledBack' -and -not (Test-Path $InstallRoot) -and -not $script:taskPresent) 'Crash recovery left owned resources.'
        Assert ((Get-FileHash -LiteralPath (Join-Path $Security 'server.key')).Hash -eq $keyHash) 'Crash recovery changed TLS key.'
        Assert ((Get-FileHash -LiteralPath (Join-Path $queue 'queue.json')).Hash -eq $queueHash) 'Crash recovery changed queue.'
        Invoke-Expression $prepareMarker
        Assert ($marker.ownerId -eq $candidate.ownerId -and $marker.state -eq 'installing') 'Crash recovery replaced owner.'
    }
}
Assert ($events.Count -eq 4) 'Fixture did not stop and remove both crashed owned tasks.'
''')

    def test_crash_recovery_rejects_foreign_task_and_unknown_program_data(self):
        self.run_fixture(r'''
Invoke-Expression (FunctionText $installAst 'Remove-OwnedResources')
function Get-InstallationStartupMode { return 'legacy' }
function Get-CimInstance { return @() }
$events=0
function Stop-ScheduledTask { $script:events++ }
function Unregister-ScheduledTask { $script:events++ }
function Remove-NetFirewallRule { $script:events++ }
New-Item -ItemType Directory -Path $InstallRoot | Out-Null
[IO.File]::WriteAllText((Join-Path $InstallRoot 'program.txt'),'owned package')
$candidate=NewOwner; $candidate.state='installing'; Write-JsonAtomic $ownerMarker $candidate
$before=Fingerprints
[IO.File]::WriteAllText((Join-Path $InstallRoot 'student-private.key'),'unowned key')
$rejected=$false
try { Invoke-Expression $preflight } catch { $rejected=$true }
Assert ($rejected -and $events -eq 0 -and (Fingerprints) -ceq $before) 'Unknown program data changed resources or data.'
Assert ([IO.File]::ReadAllText((Join-Path $InstallRoot 'student-private.key')) -eq 'unowned key') 'Unknown key was deleted.'
Remove-Item -LiteralPath (Join-Path $InstallRoot 'student-private.key')
function Get-ScheduledTask { return [pscustomobject]@{Description='foreign task';Actions=@();Principal=[pscustomobject]@{UserId='S-1-5-19'}} }
$rejected=$false
try { Invoke-Expression $preflight } catch { $rejected=$true }
Assert ($rejected -and $events -eq 0 -and (Test-Path $InstallRoot)) 'Foreign task was stopped or programs removed.'
Assert ([IO.File]::ReadAllText((Join-Path $queue 'queue.json')) -eq 'retained queue') 'Foreign task rejection changed queue.'
''')

    def test_before_tls_and_staged_tls_rollbacks_resume_exact_identities(self):
        material = provision._tls_material('192.0.2.2')
        self.run_fixture(r'''
$useRealProvision=$true
Invoke-Expression (FunctionText $installAst 'Remove-OwnedResources')
function Get-InstallationStartupMode { return 'legacy' }
function Get-CimInstance { return @() }
$material=$env:TLS_MATERIAL | ConvertFrom-Json
foreach ($count in @(0,1,2,3,4)) {
    Remove-Item -LiteralPath $Security -Recurse
    $candidate=NewOwner; $candidate.state='installing'; Write-JsonAtomic $ownerMarker $candidate
    if ($count -gt 0) {
        New-Item -ItemType Directory -Path $Security | Out-Null
        $hashes=[ordered]@{}; $files=[ordered]@{}
        foreach ($name in @('ca.crt','server.crt','server.key','enheder.json')) {
            $content=[string]$material.$name; $files[$name]=$content
            $hashes[$name]=[BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($content))).Replace('-','').ToLowerInvariant()
        }
        Write-JsonAtomic (Join-Path $Security 'tls-initialization.json') ([ordered]@{schemaVersion=1;ownerId='11111111-1111-4111-8111-111111111111';serverIp=$RoleIp;state='staged';material=$files;sha256=$hashes})
        $names=@('ca.crt','server.crt','server.key','enheder.json')
        for ($i=0;$i -lt $count;$i++) { [IO.File]::WriteAllText((Join-Path $Security $names[$i]),$files[$names[$i]],[Text.UTF8Encoding]::new($false)) }
    }
    $queueHash=(Get-FileHash -LiteralPath (Join-Path $queue 'queue.json')).Hash
    Remove-OwnedResources $ownerMarker
    $before=Fingerprints; Invoke-Expression $preflight
    Assert ((Fingerprints) -ceq $before) 'Read-only TLS preflight changed data.'
    Invoke-Expression $prepareMarker; Invoke-Expression $prepareRoot; Invoke-Expression $provisionSecurity
    foreach ($name in @('ca.crt','server.crt','server.key','enheder.json')) {
        Assert (Test-Path -LiteralPath (Join-Path $Security $name) -PathType Leaf) 'Resume did not finish TLS.'
        if ($count -gt 0) { Assert ([IO.File]::ReadAllText((Join-Path $Security $name)) -ceq $files[$name]) 'Resume replaced an already-issued identity.' }
    }
    Assert ((Get-FileHash -LiteralPath (Join-Path $queue 'queue.json')).Hash -eq $queueHash) 'TLS resume changed queue.'
    $receipt=Get-Content -LiteralPath (Join-Path $Security 'tls-initialization.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert ($receipt.state -eq 'complete' -and $receipt.PSObject.Properties.Name -notcontains 'material') 'Complete TLS retained temporary secret bundle.'
    $completedHash=(Get-FileHash -LiteralPath (Join-Path $Security 'server.key')).Hash
    # A retry after enrollment retains the changed registry as well as all keys.
    [IO.File]::WriteAllText((Join-Path $Security 'enheder.json'),' {"enheder":{"elev-1":{"token_sha256":"preserved"}}} ',[Text.UTF8Encoding]::new($false))
    $registryHash=(Get-FileHash -LiteralPath (Join-Path $Security 'enheder.json')).Hash
    Remove-OwnedResources $ownerMarker; Invoke-Expression $preflight; Invoke-Expression $prepareMarker
    Invoke-Expression $provisionSecurity
    Assert ((Get-FileHash -LiteralPath (Join-Path $Security 'server.key')).Hash -eq $completedHash) 'Complete retry replaced TLS key.'
    Assert ((Get-FileHash -LiteralPath (Join-Path $Security 'enheder.json')).Hash -eq $registryHash) 'Complete retry removed enrollment.'
}
Assert (@($provisionCalls | Where-Object { $_ -contains '--check' }).Count -eq 10) 'Fixture did not execute actual read-only TLS dispatch.'
''', material)

    def test_foreign_staged_tls_and_missing_issued_tls_rejected_without_mutation(self):
        material = provision._tls_material('192.0.2.2')
        self.run_fixture(r'''
$useRealProvision=$true
Remove-Item -LiteralPath $Security -Recurse; New-Item -ItemType Directory -Path $Security | Out-Null
$material=$env:TLS_MATERIAL | ConvertFrom-Json
$files=[ordered]@{}; $hashes=[ordered]@{}
foreach ($name in @('ca.crt','server.crt','server.key','enheder.json')) {
    $files[$name]=[string]$material.$name
    $hashes[$name]=[BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($files[$name]))).Replace('-','').ToLowerInvariant()
}
foreach ($bad in @(@{ownerId='foreign'},@{serverIp='172.17.1.3'},@{state='unknown'})) {
    $journal=[ordered]@{schemaVersion=1;ownerId='11111111-1111-4111-8111-111111111111';serverIp=$RoleIp;state='staged';material=$files;sha256=$hashes}
    foreach ($name in $bad.Keys) { $journal[$name]=$bad[$name] }
    Write-JsonAtomic (Join-Path $Security 'tls-initialization.json') $journal
    $before=Fingerprints; $rejected=$false
    try { Invoke-Expression $preflight } catch { $rejected=$true }
    Assert ($rejected -and (Fingerprints) -ceq $before -and $aclCalls.Count -eq 0) 'Foreign TLS journal changed data.'
}
Remove-Item -LiteralPath $Security -Recurse
$candidate=NewOwner; $candidate.caThumbprint='issued'; Write-JsonAtomic $ownerMarker $candidate
$before=Fingerprints; $rejected=$false
try { Invoke-Expression $preflight } catch { $rejected=$true }
Assert ($rejected -and (Fingerprints) -ceq $before) 'Missing issued identity was regenerated.'
New-Item -ItemType Directory -Path $Security | Out-Null
foreach ($name in @('ca.crt','server.crt','server.key','enheder.json')) { [IO.File]::WriteAllText((Join-Path $Security $name),$files[$name],[Text.UTF8Encoding]::new($false)) }
$before=Fingerprints; $rejected=$false
try { Invoke-Expression $preflight } catch { $rejected=$true }
Assert ($rejected -and (Fingerprints) -ceq $before -and $aclCalls.Count -eq 0) 'Changed already-issued CA was accepted.'
''', material)

    def test_fresh_config_and_marker_use_original_install_path(self):
        self.run_fixture(r'''
$Resume=[Management.Automation.SwitchParameter]::new($false)
Remove-Item -LiteralPath $DataRoot -Recurse
Invoke-Expression $preflight; Invoke-Expression $prepareMarker; Invoke-Expression $prepareRoot
$security=Join-Path $DataRoot 'security'; Invoke-Expression $configure
$saved=Get-Content -LiteralPath $markerPath -Raw -Encoding UTF8 | ConvertFrom-Json
$config=Get-Content -LiteralPath $runtimeConfig -Raw -Encoding UTF8 | ConvertFrom-Json
Assert ($saved.ownerId -ne '11111111-1111-4111-8111-111111111111' -and $saved.state -eq 'installing') 'Fresh installation reused identity.'
Assert ($config.backend_ports.print -eq 8765 -and $config.makerspace_printers.backend -eq '127.0.0.1:8765') 'Fresh configuration changed backend wiring.'
Assert ($config.apps[4].navn -eq 'Datatræneren') 'BOM-less JSON lost Danish text.'
Assert ($config.tls.noegle -eq (Join-Path $security 'server.key')) 'Fresh configuration changed TLS wiring.'
''')

    def test_school_dispatch_runs_rollback_resume_but_skips_installed(self):
        self.run_fixture(r'''
$SchoolName='Skolen med æ, ø og å'; $Theme='green'; $Role='vm'
$school=[ordered]@{schemaVersion=1;school_name=$SchoolName;theme=$Theme;role=$Role;server=$RoleIp;student_subnet=$StudentSubnet;admin_public_key='retained public key'}
Write-JsonAtomic (Join-Path $DataRoot 'school.json') $school
$serverInstall=Join-Path $PackageRoot 'server\install.ps1'
$calls=[Collections.Generic.List[object]]::new()
function powershell.exe {
    $calls.Add(@($args))
    New-Item -ItemType Directory -Path $InstallRoot | Out-Null
    $completed=NewOwner; $completed.state='installed'; Write-JsonAtomic $ownerMarker $completed
    $global:LASTEXITCODE=0
}
$before=(Get-FileHash -LiteralPath (Join-Path $DataRoot 'school.json')).Hash
Invoke-Expression $schoolDispatch
Assert ($calls.Count -eq 1 -and $calls[0] -contains '-Resume') 'RolledBack Resume failed to dispatch installer Resume.'
Assert ($existingProfile.school_name -ceq $SchoolName) 'School Resume lost Danish text.'
Assert ((Get-FileHash -LiteralPath (Join-Path $DataRoot 'school.json')).Hash -eq $before) 'School Resume rewrote profile.'
Invoke-Expression $schoolDispatch
Assert ($calls.Count -eq 1) 'Installed Resume reinstalled server.'
# Fresh setup dispatches the original installation command, without Resume.
Remove-Item -LiteralPath $InstallRoot -Recurse; Remove-Item -LiteralPath $ownerMarker
$Resume=[Management.Automation.SwitchParameter]::new($false)
Invoke-Expression $schoolDispatch
Assert ($calls.Count -eq 2 -and $calls[1] -notcontains '-Resume') 'Fresh setup did not use original install dispatch.'
''')

    def test_school_mismatches_and_unsafe_paths_rejected_before_dispatch(self):
        self.run_fixture(r'''
$SchoolName='Skolen med æ, ø og å'; $Theme='green'; $Role='vm'; $serverInstall='unused'
$calls=0
function powershell.exe { $script:calls++; throw 'Unexpected installer invocation' }
foreach ($state in @('installed','rolledBack')) {
    if ($state -eq 'installed') { New-Item -ItemType Directory -Path $InstallRoot | Out-Null } else { Remove-Item -LiteralPath $InstallRoot -Recurse }
    foreach ($bad in @(@{roleIp='172.17.1.3'}, @{studentSubnet='172.18.0.0/24'}, @{ownerId=''}, @{generationLayout=$true}, @{packageManifestSha256='wrong'}, @{teacherPort=9999}, @{printBackendPort=9999}, @{externalBackends=@('print')})) {
        $candidate=NewOwner; $candidate.state=$state
        foreach ($name in $bad.Keys) { $candidate[$name]=$bad[$name] }; Write-JsonAtomic $ownerMarker $candidate
        $before=Fingerprints; $rejected=$false
        try { Invoke-Expression $schoolDispatch } catch { $rejected=$true }
        Assert ($rejected -and $calls -eq 0 -and (Fingerprints) -ceq $before) 'School mismatch mutated data or invoked installer.'
    }
}
Write-JsonAtomic $ownerMarker (NewOwner)
Write-JsonAtomic (Join-Path $DataRoot 'school.json') ([ordered]@{schemaVersion=1;school_name=$SchoolName;theme=$Theme;role='makerspace';server=$RoleIp;student_subnet=$StudentSubnet})
$before=Fingerprints; $rejected=$false
try { Invoke-Expression $schoolDispatch } catch { $rejected=$true }
Assert ($rejected -and $calls -eq 0 -and (Fingerprints) -ceq $before) 'Changed school role was accepted.'
Assert-SchoolDataPath $DataRoot
$rejected=$false
try { Assert-SchoolDataPath (Join-Path $env:FIXTURE_ROOT 'wrong-data') } catch { $rejected=$true }
Assert $rejected 'Unsafe school data path accepted.'
$guard=@($installAst.EndBlock.Statements | Where-Object { $_.Extent.Text.StartsWith('if ($InstallRoot -ne $ExpectedInstall') })
Assert ($guard.Count -eq 1) 'Installer fixed-path guard missing.'
$InstallRoot=Join-Path $env:FIXTURE_ROOT 'wrong-install'; $rejected=$false
try { Invoke-Expression $guard[0].Extent.Text } catch { $rejected=$true }
Assert $rejected 'Unsafe installer path accepted.'
# A junction points within the fixture; reject it without touching its target.
$target=Join-Path $env:FIXTURE_ROOT 'junction-target'; New-Item -ItemType Directory -Path $target | Out-Null
$junction=Join-Path $DataRoot 'linked-security'; New-Item -ItemType Junction -Path $junction -Target $target | Out-Null
$before=Fingerprints; $rejected=$false
try { Assert-SchoolDataPath $DataRoot } catch { $rejected=$true }
Assert ($rejected -and (Fingerprints) -ceq $before) 'School reparse tree accepted or changed.'
$rejected=$false
try { Assert-NoReparseTree $DataRoot } catch { $rejected=$true }
Assert $rejected 'Installer reparse tree accepted.'
''')


if __name__ == '__main__':
    unittest.main()
