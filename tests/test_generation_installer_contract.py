"""Execute bounded installer branches; never install, elevate or touch services."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PS = shutil.which('powershell.exe') or shutil.which('pwsh')


@unittest.skipUnless(PS, 'Windows PowerShell is required')
class GenerationInstallerContractTests(unittest.TestCase):
    def run_ps(self, body, **env):
        result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', body],
                                capture_output=True, text=True, timeout=20,
                                env={**os.environ, 'SOURCE_ROOT': str(ROOT), **env})
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_installer_failure_preserves_data_after_successful_or_failed_rollback(self):
        with tempfile.TemporaryDirectory(prefix='generation-install-rollback-') as folder:
            data = Path(folder) / 'data'
            data.mkdir()
            (data / 'queue.sqlite3').write_bytes(b'accepted student job sentinel')
            (data / 'private.key').write_bytes(b'test-only key sentinel')
            before = {p.name: p.read_bytes() for p in data.iterdir()}
            for fail_rollback in ('false', 'true'):
                value = self.run_ps(r'''
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $env:SOURCE_ROOT 'windows-elevhub/server/install.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){ throw 'Parse failure' }
$catches=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.CatchClauseAst] -and $n.Extent.Text -match 'Installationsdata er bevaret' },$true))
if($catches.Count -ne 1){throw 'Expected one final rollback handler'}
$createdDataRoot=$true; $DataRoot=$env:DATA_ROOT; $ExpectedData=$DataRoot
$InstallRoot=Join-Path $env:DATA_ROOT 'not-created'; $markerPath=Join-Path $DataRoot 'installation-owner.json'
$script:rollbackCalls=0
function Remove-OwnedResources { param($p) $script:rollbackCalls++; if($env:FAIL_ROLLBACK -eq 'true'){throw 'rollback sentinel'} }
function Remove-Item { throw 'Unexpected data deletion' }
$caught=''
$body=$catches[0].Body.Extent.Text
try { try { throw 'readiness sentinel' } catch { Invoke-Expression $body.Substring(1,$body.Length-2) } } catch { $caught=$_.Exception.Message }
@{caught=$caught; rollbacks=$script:rollbackCalls} | ConvertTo-Json -Compress
''', DATA_ROOT=str(data), FAIL_ROLLBACK=fail_rollback)
                self.assertEqual(value, {'caught': 'readiness sentinel', 'rollbacks': 1})
                self.assertEqual(before, {p.name: p.read_bytes() for p in data.iterdir()})

    def test_usb_enrollment_rejects_generation_before_side_effects(self):
        for relative in ('windows-elevhub/server/launchers/enroll.ps1',):
            value = self.run_ps(r'''
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $env:SOURCE_ROOT 'windows-elevhub/server/generation-startup.ps1')
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $env:SOURCE_ROOT $env:GUARD_SOURCE),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Parse failure'}
$nodes=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.IfStatementAst] -and $n.Extent.Text -match 'Get-InstallationStartupMode' },$true))
if($nodes.Count -ne 1){throw 'Expected one guard'}
$InstallRoot=Join-Path $env:TEMP 'unused-install-contract'
$marker=[pscustomobject]@{ownerId=('a'*32); startupProtocol='generation-v1'}
$rejected=$false
try { Invoke-Expression $nodes[0].Extent.Text } catch { $rejected=$true }
@{rejected=$rejected} | ConvertTo-Json -Compress
''', GUARD_SOURCE=relative)
            self.assertTrue(value['rejected'])

    def test_generation_server_task_and_rollback_use_identical_arguments(self):
        value = self.run_ps(r'''
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $env:SOURCE_ROOT 'windows-elevhub/server/generation-startup.ps1')
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $env:SOURCE_ROOT 'windows-elevhub/server/install.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Parse failure'}
$commands=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst] -and $n.GetCommandName() -eq 'Get-GenerationCommand' -and $n.Extent.Text -notmatch "'--check'" },$true))
if($commands.Count -ne 2){throw 'Expected task and rollback command'}
$InstallRoot=Join-Path $env:TEMP 'Install root with spaces'
$RoleIp=$markerRoleIp='192.0.2.10'; $TeacherAddress=$markerTeacherAddress='127.0.0.1'; $TeacherPort=8780
$installationId='a'*32; $marker=[pscustomobject]@{ownerId=$installationId;teacherPort=$TeacherPort}
$values=@($commands | ForEach-Object { Invoke-Expression $_.Extent.Text })
@{task=$values[0].Arguments; rollback=$values[1].Arguments; executable=$values[0].Execute} | ConvertTo-Json -Compress
''')
        self.assertEqual(value['task'], value['rollback'])
        self.assertIn('"--active"', value['task'])
        self.assertNotIn('--data-root', value['task'])
