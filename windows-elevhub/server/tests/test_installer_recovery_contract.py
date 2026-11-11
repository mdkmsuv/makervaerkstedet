"""Execute the recovery plan branch from the real installer AST without UAC."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

PS = shutil.which('powershell.exe') or shutil.which('pwsh')
ROOT = Path(__file__).resolve().parents[3]


@unittest.skipUnless(PS, 'requires PowerShell parser')
class InstallerRecoveryContractTests(unittest.TestCase):
    def test_fixed_app_copy_loop_handles_old_and_new_manifests(self):
        body = r'''
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $env:SOURCE_ROOT 'windows-elevhub/server/install.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Parse failure'}
$node=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Copy-FixedAdministrativeAppFiles'},$true))
if($node.Count -ne 1){throw 'Missing copy helper'}
Invoke-Expression $node[0].Extent.Text
$root=Join-Path $env:TEMP ('fixed-app-copy-'+[guid]::NewGuid().ToString('N')); $program=Join-Path $root 'program'; $target=Join-Path $root 'install'; New-Item -ItemType Directory -Path (Join-Path $program 'app'),(Join-Path $target 'app') | Out-Null
$core=@('__init__.py','backup_databases.py','backup_guard.py','data_contract.py','database_migrations.py','generation_activation.py','generation_host.py','generation_lock.py','generation_materialization.py','generation_relocation.py','generation_rollback.py','generation_runtime.py','generation_selection.py','generation_transaction.py','installation_backup.py','installation_update.py','instance.py','release_compatibility.py','school_maintenance.py','school_maintenance_host.py')
foreach($name in $core){[IO.File]::WriteAllText((Join-Path $program "app\$name"),'core')}
$old=[pscustomobject]@{files=([pscustomobject]@{ 'app/core.py'='x' })}; Copy-FixedAdministrativeAppFiles $program $target $old
if(@(Get-ChildItem (Join-Path $target 'app') -File).Count -ne $core.Count){throw 'Old manifest did not copy exactly core helpers'}
foreach($name in @('school_recovery.py','school_tls.py')){[IO.File]::WriteAllText((Join-Path $program "app\$name"),'optional')}
$new=[pscustomobject]@{files=([pscustomobject]@{ 'app/school_recovery.py'='x'; 'app/school_tls.py'='y' })}; Copy-FixedAdministrativeAppFiles $program $target $new
if(-not (Test-Path (Join-Path $target 'app\school_recovery.py')) -or -not (Test-Path (Join-Path $target 'app\school_tls.py'))){throw 'Declared optional helpers were not copied'}
Remove-Item -LiteralPath (Join-Path $program 'app\school_tls.py')
$declaredMissing=$false; try {Copy-FixedAdministrativeAppFiles $program $target $new}catch{if($_.Exception.Message -notmatch 'Pakken mangler den faste administrative app-fil: app/school_tls\.py'){throw}; $declaredMissing=$true}; if(-not $declaredMissing){throw 'Declared missing helper was accepted'}
Remove-Item -LiteralPath (Join-Path $program 'app\backup_guard.py'); $coreMissing=$false; try {Copy-FixedAdministrativeAppFiles $program $target $old}catch{if($_.Exception.Message -notmatch 'Pakken mangler den faste administrative app-fil: app/backup_guard\.py'){throw}; $coreMissing=$true}; if(-not $coreMissing){throw 'Missing core helper was accepted'}
if(-not (Test-Path -LiteralPath $root -PathType Container)){throw 'Fixture root disappeared unexpectedly'}
$resolvedRoot=[IO.Path]::GetFullPath($root).TrimEnd([IO.Path]::DirectorySeparatorChar,[IO.Path]::AltDirectorySeparatorChar)
$resolvedTemp=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar,[IO.Path]::AltDirectorySeparatorChar)
$rootLeaf=[IO.Path]::GetFileName($resolvedRoot)
if(-not $resolvedRoot.StartsWith($resolvedTemp + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Fixture cleanup target escaped TEMP'}
if($rootLeaf -notmatch '^fixed-app-copy-[0-9a-f]{32}$'){throw 'Fixture cleanup target has unexpected name'}
Remove-Item -LiteralPath $resolvedRoot -Recurse -Force
@{oldManifest=$true;newManifest=$true;declaredMissingRejected=$declaredMissing;coreMissingRejected=$coreMissing}|ConvertTo-Json -Compress
'''
        result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', body],
            env={**os.environ, 'SOURCE_ROOT': str(ROOT)}, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), {'oldManifest': True, 'newManifest': True, 'declaredMissingRejected': True, 'coreMissingRejected': True})

    def test_recovery_backup_old_package_fails_preflight_without_write(self):
        body = r'''
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $env:SOURCE_ROOT 'windows-elevhub/server/install.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Parse failure'}
$node=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.IfStatementAst] -and $n.Extent.Text.StartsWith('if ($RecoveryBackup) {') -and $n.Extent.Text -match 'school_recovery plan'},$true))[0]
$RecoveryBackup='X:\old-backup'; $PackageRoot='X:\old-package'; $InstallRoot='X:\runtime'; $DataRoot='X:\data'; $GenerationLayout=$true; $UseExistingPrintBackend=$false
$RoleIp='192.0.2.1'; $StudentSubnet='192.0.2.1/32'; $TeacherAddress='127.0.0.1'; $TeacherPort=18780; $PrintBackendPort=18765; $DesignBackendPort=18766; $TeacherBackendPort=18767; $pythonSource='FakePython'
$writes=0
function FakePython { $global:LASTEXITCODE=1; 'old package has no recovery helper' }
function New-Item { $script:writes++ ; throw 'Unexpected write' }
$failed=$false; try { Invoke-Expression $node.Extent.Text } catch { if($_.Exception.Message -notmatch 'Backupens værtsgendannelse bestod ikke den skrivefri kontrol'){throw}; $failed=$true }
if(-not $failed -or $writes -ne 0){throw 'Old recovery package was not rejected read-only'}
@{rejected=$true;writes=$writes}|ConvertTo-Json -Compress
'''
        result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', body],
            env={**os.environ, 'SOURCE_ROOT': str(ROOT)}, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), {'rejected': True, 'writes': 0})

    def test_school_plan_rejects_profile_and_port_changes_before_prepare(self):
        body = r'''
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $env:SOURCE_ROOT 'deployment/school-setup/Setup-School.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Parse failure'}
$layoutTokens=$null; $layoutErrors=$null; $layoutAst=[Management.Automation.Language.Parser]::ParseFile((Join-Path $env:SOURCE_ROOT 'deployment/school-setup/school-layout.ps1'),[ref]$layoutTokens,[ref]$layoutErrors)
if($layoutErrors.Count){throw 'Layout parse failure'}
$laserNode=@($layoutAst.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Assert-SchoolLaserProfile'},$true))
if($laserNode.Count -ne 1){throw 'Missing school laser profile helper'}
Invoke-Expression $laserNode[0].Extent.Text
$nodes=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.IfStatementAst] -and $n.Extent.Text.StartsWith('if ($RecoveryBackup) {') -and $n.Extent.Text -match 'school_recovery plan'},$true))
if($nodes.Count -ne 1){throw 'Expected unique school recovery preflight'}
$RecoveryBackup='X:\offline backup'; $PackageRoot='X:\original package'; $DataRoot='X:\data'; $GenerationLayout=$true
$env:ProgramFiles=Join-Path $env:TEMP 'recovery-program-files-not-created'; $python='FakePython'
$SchoolName='Eksempelskolen'; $Role='vm'; $Theme='green'; $RoleIp='192.0.2.10'; $StudentSubnet='192.0.2.0/24'
$RemoteLaserStation=$false; $Machine='fbb2'
$teacherAddress='127.0.0.1'
$TeacherPort=8780; $PrintBackendPort=8765; $DesignBackendPort=8766; $TeacherBackendPort=8767
$profile=@{school_name=$SchoolName;role=$Role;theme=$Theme;server=$RoleIp;student_subnet=$StudentSubnet}
$school=@{teacherAddress='127.0.0.1';teacherPort=$TeacherPort;printBackendPort=$PrintBackendPort;designBackendPort=$DesignBackendPort;teacherBackendPort=$TeacherBackendPort}
function FakePython { if($args -contains 'prepare'){throw 'Unexpected prepare'}; $global:LASTEXITCODE=0; @{school=$school;school_profile=$profile}|ConvertTo-Json -Depth 5 -Compress }
function New-Item {throw 'Unexpected write'}
Invoke-Expression $nodes[0].Extent.Text
$rejected=0
foreach($key in @('school_name','role','theme','server','student_subnet')){
 $saved=$profile[$key]; $profile[$key]='changed'; $failed=$false
 try { Invoke-Expression $nodes[0].Extent.Text } catch { if($_.Exception.Message -notmatch 'oprindelige skoleprofil'){throw}; $failed=$true }
 $profile[$key]=$saved; if(-not $failed){throw "Accepted changed $key"}; $rejected++
}
foreach($key in @('teacherPort','printBackendPort','designBackendPort','teacherBackendPort')){
 $saved=$school[$key]; $school[$key]=9001; $failed=$false
 try { Invoke-Expression $nodes[0].Extent.Text } catch { if($_.Exception.Message -notmatch 'oprindelige serverporte'){throw}; $failed=$true }
 $school[$key]=$saved; if(-not $failed){throw "Accepted changed $key"}; $rejected++
}
@{rejected=$rejected}|ConvertTo-Json -Compress
'''
        result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', body],
            env={**os.environ, 'SOURCE_ROOT': str(ROOT)}, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), {'rejected': 9})

    def test_recovery_plan_rejects_changed_lan_or_ports_before_any_write(self):
        body = r'''
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $env:SOURCE_ROOT 'windows-elevhub/server/install.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Parse failure'}
$nodes=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.IfStatementAst] -and $n.Extent.Text.StartsWith('if ($RecoveryBackup) {') -and $n.Extent.Text -match 'school_recovery plan'},$true))
if($nodes.Count -ne 1){throw 'Expected unique recovery preflight'}
$RecoveryBackup='X:\offline backup'; $PackageRoot='X:\original package'; $InstallRoot='X:\runtime'; $DataRoot='X:\data'
$GenerationLayout=$true; $UseExistingPrintBackend=$false; $pythonSource='FakePython'
$RoleIp='192.0.2.10'; $StudentSubnet='192.0.2.0/24'; $TeacherAddress='127.0.0.1'
$TeacherPort=8780; $PrintBackendPort=8765; $DesignBackendPort=8766; $TeacherBackendPort=8767
$school=@{roleIp=$RoleIp;studentSubnet=$StudentSubnet;teacherAddress=$TeacherAddress;teacherPort=$TeacherPort;printBackendPort=$PrintBackendPort;designBackendPort=$DesignBackendPort;teacherBackendPort=$TeacherBackendPort}
$script:called=@()
function FakePython { $script:called=@($args); $global:LASTEXITCODE=0; @{school=$school}|ConvertTo-Json -Depth 5 -Compress }
function New-Item {throw 'Unexpected write'}
function Start-ScheduledTask {throw 'Unexpected task'}
function Invoke-Icacls {throw 'Unexpected ACL'}
Invoke-Expression $nodes[0].Extent.Text
$accepted=$script:called -contains 'plan' -and $script:called -contains $RecoveryBackup -and $script:called -contains $PackageRoot
$rejected=@()
foreach($key in @('roleIp','studentSubnet','teacherAddress','teacherPort','printBackendPort','designBackendPort','teacherBackendPort')){
 $saved=$school[$key]; $school[$key]='changed'; $failed=$false
 try { Invoke-Expression $nodes[0].Extent.Text } catch { if($_.Exception.Message -notmatch 'uændrede LAN'){throw}; $failed=$true }
 $school[$key]=$saved
 if(-not $failed){throw "Accepted changed $key"}; $rejected+=$key
}
@{accepted=$accepted;rejected=$rejected.Count}|ConvertTo-Json -Compress
'''
        result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', body],
            env={**os.environ, 'SOURCE_ROOT': str(ROOT)}, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), {'accepted': True, 'rejected': 7})


if __name__ == '__main__':
    unittest.main()
