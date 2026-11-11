"""PowerShell AST contract tests for generation-aware school setup."""

import json
from pathlib import Path
import shutil
import subprocess
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'deployment' / 'school-setup' / 'Setup-School.ps1'
POWERSHELL = shutil.which('pwsh') or shutil.which('powershell.exe')


@unittest.skipUnless(POWERSHELL, 'PowerShell kræves til Setup-School AST-testen')
class SchoolGenerationStartupTests(unittest.TestCase):
    @staticmethod
    def _ast():
        probe = r'''
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:SETUP_SCRIPT, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw (($errors | ForEach-Object Message) -join '; ') }
$parameters = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.ParameterAst] }, $true) |
    ForEach-Object { $_.Name.VariablePath.UserPath })
$ifBlocks = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.IfStatementAst] }, $true) |
    ForEach-Object { $_.Extent.Text })
$commands = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true) |
    ForEach-Object { $_.Extent.Text })
[pscustomobject]@{parameters=$parameters; ifBlocks=$ifBlocks; commands=$commands; source=$ast.Extent.Text} |
    ConvertTo-Json -Depth 8 -Compress
'''
        result = subprocess.run([POWERSHELL, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', probe],
                                capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=15, env={**__import__('os').environ, 'SETUP_SCRIPT': str(SCRIPT)})
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_generation_switch_and_owner_mode_are_ast_validated(self):
        ast = self._ast()
        self.assertIn('GenerationLayout', ast['parameters'])
        self.assertIn("startupMode -eq 'generation-v1'", ast['source'])
        self.assertIn("installArgs += '-GenerationLayout'", ast['source'])
        self.assertTrue(any('Get-InstallationStartupMode' in command
                            for command in ast['commands']))
        self.assertTrue(any('Get-GenerationInstallationId' in command
                            for command in ast['commands']))
        self.assertIn('$generationLease = New-GenerationLease', ast['source'])
        self.assertIn('Close-GenerationLease -Lease $generationLease', ast['source'])
        self.assertIn('finally', ast['source'])
        self.assertIn('Assert-GenerationCommandCoherence', ast['source'])
        self.assertIn('ConvertTo-GenerationCommandLine', ast['source'])

    def test_generation_enrollment_and_laser_arguments_are_separate_from_legacy(self):
        ast = self._ast()
        self.assertTrue(any("Get-GenerationCommand -InstallRoot $installedRoot" in command and
                            "-Entry 'enrollment'" in command and "@('serve')" in command
                            for command in ast['commands']))
        self.assertIn('-ExpectedSelection $generationSnapshot.selection_sha256', ast['source'])
        # Persisted shortcuts resolve the current generation at launch; the
        # live lease still pins the immediate URL/process pair.
        self.assertNotIn('-ExpectedSelectionSha256', ast['source'])
        self.assertIn('-ActiveGeneration', ast['source'])
        self.assertIn('-InstallationId', ast['source'])
        # Existing installations retain their direct command forms.
        self.assertTrue(any('serve --data-root' in block for block in ast['ifBlocks']))
        self.assertTrue(any('-ConfigPath' in block and 'startupMode' in block for block in ast['ifBlocks']))
        # Generation enrollment must not reintroduce a data-root override.
        generation_commands = [command for command in ast['commands']
                               if "-Entry 'enrollment'" in command]
        self.assertTrue(generation_commands)
        self.assertTrue(all("@('serve')" in command or "'connection'" in command
                            for command in generation_commands))
        serve_commands = [command for command in generation_commands if "@('serve')" in command]
        connection_commands = [command for command in generation_commands if "'connection'" in command]
        self.assertTrue(serve_commands)
        self.assertTrue(all('-ExpectedSelection' not in command for command in serve_commands))
        self.assertTrue(connection_commands)
        self.assertTrue(any('-ExpectedSelection $generationSnapshot.selection_sha256' in command
                            for command in connection_commands))

    def test_laser_start_parameter_binds_active_switch_without_config_path(self):
        laser_script = SCRIPT.parent / 'Start-LaserStation.ps1'
        probe = r'''
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:LASER_SCRIPT, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw (($errors | ForEach-Object Message) -join '; ') }
$parameter = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.ParameterAst] -and $n.Name.VariablePath.UserPath -eq 'ActiveGeneration' }, $true))
$active = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.IfStatementAst] -and $n.Extent.Text -match '\$ActiveGeneration' }, $true))
$setup = [System.Management.Automation.Language.Parser]::ParseFile($env:SETUP_SCRIPT, [ref]$tokens, [ref]$errors)
$laser = @($setup.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Extent.Text -match '\$laserArgs' }, $true) | ForEach-Object Extent.Text)
[pscustomobject]@{parameter=$parameter[0].Extent.Text; active=@($active | ForEach-Object Extent.Text); setup=$laser; source=$ast.Extent.Text; setupSource=$setup.Extent.Text} | ConvertTo-Json -Compress
'''
        import os
        env = {**os.environ, 'LASER_SCRIPT': str(laser_script), 'SETUP_SCRIPT': str(SCRIPT)}
        result = subprocess.run([POWERSHELL, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', probe],
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        value = json.loads(result.stdout)
        self.assertIn('[switch]$ActiveGeneration', value['parameter'])
        active_blocks = [text for text in (value['active'] if isinstance(value['active'], list) else [value['active']]) if text]
        self.assertIn('Get-ActiveCommand', value['source'])
        self.assertIn('-ActiveGeneration', value['setupSource'])
        self.assertIn('-InstallationId', value['setupSource'])
        self.assertNotIn('-ConfigPath', value['setupSource'].split('$laserArgs')[1].split('}')[0])

    def test_generation_resume_inspects_selected_data_before_any_write(self):
        # Execute the exact generation-inspection IfStatementAst with a
        # mocked generation command.  This avoids UAC, installer, tasks and
        # network while still exercising the production PowerShell branch.
        probe = r'''
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:SETUP_SCRIPT, [ref]$tokens, [ref]$errors)
$node = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.IfStatementAst] -and
    $n.Extent.Text -match '\$generationLease = New-GenerationLease' }, $true))
if ($node.Count -ne 1) { throw "Expected one inspect branch, got $($node.Count)" }
$block = $node[0].Extent.Text
$root = Join-Path $env:TEMP ('school-generation-probe-' + [Guid]::NewGuid().ToString('N'))
$installed = Join-Path $root 'installed'; $program = Join-Path $installed 'program'; $data = Join-Path $root 'data'
New-Item -ItemType Directory -Force -Path $installed, $program, $data | Out-Null
$startupMode = 'generation-v1'; $installedRoot = $installed; $DataRoot = $data; $fixedRoot = $installed
$owner = [pscustomobject]@{ ownerId=('a' * 32) }
$marker = Join-Path $root 'write-marker'
$dataValue = if ($env:INSPECT_MODE -eq 'data') { Join-Path $root 'wrong-data' } else { $data }
$runtimeValue = if ($env:INSPECT_MODE -eq 'runtime') { Join-Path $root 'wrong-runtime' } else { $installed }
$programValue = if ($env:INSPECT_MODE -eq 'program') { Join-Path $root 'wrong-program' } else { $program }
$idValue = if ($env:INSPECT_MODE -eq 'id') { 'b' * 32 } else { 'a' * 32 }
$revisionValue = if ($env:INSPECT_MODE -eq 'revision') { 2 } else { 1 }
$env:INSPECT_JSON = (@{data_root=$dataValue; runtime_root=$runtimeValue; program_root=$programValue; installation_id=$idValue; revision=$revisionValue} | ConvertTo-Json -Compress)
function Get-GenerationInstallationId { return ('a' * 32) }
function New-GenerationLease {
  [pscustomobject]@{ Process=[pscustomobject]@{}; Snapshot=[pscustomobject]@{
    data_root=$dataValue; runtime_root=$runtimeValue; program_root=$programValue;
    installation_id=$idValue; revision=$revisionValue; selection_sha256=('a' * 64) } }
}
function Get-GenerationCommand {
  [pscustomobject]@{ Execute=(Join-Path $PSHOME 'pwsh.exe');
    ArgumentList=@('-NoLogo','-NoProfile','-Command','Write-Output $env:INSPECT_JSON');
    Arguments=''; WorkingDirectory=$PSHOME }
}
$result = try { Invoke-Expression $block; 'OK' } catch { 'ERR:' + $_.Exception.Message }
[pscustomobject]@{result=$result; marker=(Test-Path -LiteralPath $marker)} | ConvertTo-Json -Compress
Remove-Item -LiteralPath $root -Recurse -Force
'''
        def run(value):
            import os
            env = {**os.environ, 'SETUP_SCRIPT': str(SCRIPT), 'INSPECT_MODE': value}
            result = subprocess.run([POWERSHELL, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', probe],
                                  capture_output=True, text=True, encoding='utf-8', errors='replace',
                                  timeout=15, env=env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout.strip())
        for mode in ('data', 'runtime', 'program', 'id', 'revision'):
            with self.subTest(mode=mode):
                mismatch = run(mode)
                self.assertTrue(mismatch['result'].startswith('ERR:'))
                self.assertIn('matcher ikke', mismatch['result'])
                self.assertFalse(mismatch['marker'])
        correct = run('good')
        self.assertEqual(correct['result'], 'OK')
        self.assertFalse(correct['marker'])


if __name__ == '__main__':
    unittest.main()
