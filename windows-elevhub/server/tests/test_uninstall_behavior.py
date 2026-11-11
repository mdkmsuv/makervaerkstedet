"""Execute the remover with isolated roots and recording Windows resource fakes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

import test_initial_install_tree as tree_fixture

PS = shutil.which('powershell.exe') or shutil.which('pwsh')
SERVER = Path(__file__).resolve().parents[1]

HARNESS = r'''
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1')
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Management\Microsoft.PowerShell.Management.psd1')
$tokens=$null; $errors=$null
# The school pack writes PowerShell files with a UTF-8 BOM. Read source UTF-8
# explicitly here so Windows PowerShell's ANSI fallback cannot rename the
# Danish shortcut in this source-only harness.
$source=[IO.File]::ReadAllText((Join-Path $env:SERVER 'fjern.ps1'),[Text.Encoding]::UTF8)
$ast=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Parse failure'}
. (Join-Path $env:SERVER 'school-uninstall-guards.ps1')
. (Join-Path $env:SERVER 'generation-startup.ps1')
foreach($function in @($ast.EndBlock.Statements | Where-Object {$_ -is [Management.Automation.Language.FunctionDefinitionAst]})) {
  # Use the test runner's existing Python. Everything else in the exact
  # production ownership-check branch runs against the isolated package.
  $text=$function.Extent.Text.Replace("`$python=Join-Path `$InstallRoot 'runtime\python.exe'",'$python=$env:FIXTURE_PYTHON')
  Invoke-Expression $text
}
function Assert-Administrator { }
$env:ProgramFiles=Join-Path $env:FIXTURE_ROOT 'program-files'
$env:ProgramData=Join-Path $env:FIXTURE_ROOT 'program-data'
$env:Public=Join-Path $env:FIXTURE_ROOT 'public'; New-Item -ItemType Directory -Path $env:Public | Out-Null
$InstallRoot=Join-Path $env:ProgramFiles 'ElevhubServer'; $DataRoot=Join-Path $env:ProgramData 'ElevhubServer'
$fixtureTeacherAddress=if($env:MODE -like '*-lan*'){'192.0.2.10'}else{'127.0.0.1'}
$shortcutPath=Join-Path $env:Public 'Desktop\Makerværkstedet.lnk'
if($env:MODE -like 'school-shortcut-*') {
  New-Item -ItemType Directory -Path (Split-Path -Parent $shortcutPath) | Out-Null
  $shell=New-Object -ComObject WScript.Shell
  $shortcut=$shell.CreateShortcut($shortcutPath)
  $shortcut.TargetPath=Join-Path $InstallRoot 'deployment\school-setup\Open-ControlPanel.cmd'
  $shortcut.WorkingDirectory=$InstallRoot
  $shortcut.Arguments='-Port 8780 -Address ' + $fixtureTeacherAddress
  if($env:MODE -like '*-old*'){$shortcut.Arguments='-Port 8780'}
  if($env:MODE -like '*-wrong-address'){$shortcut.Arguments='-Port 8780 -Address 192.0.2.99'}
  if($env:MODE -like '*-wrong-port'){$shortcut.Arguments='-Port 18780 -Address ' + $fixtureTeacherAddress}
  if($env:MODE -like '*-wrong-target'){$shortcut.TargetPath='cmd.exe'}
  if($env:MODE -like '*-wrong-directory'){$shortcut.WorkingDirectory=$env:FIXTURE_ROOT}
  $shortcut.Save()
}
$PSScriptRoot=$env:SERVER
$fixtureCmdlet=[pscustomobject]@{}
$fixtureCmdlet | Add-Member -MemberType ScriptMethod -Name ShouldProcess -Value {param($target,$action) return $env:MODE -ne 'whatif'}
$script:events=[Collections.Generic.List[string]]::new(); $script:taskExists=$true
function Get-ScheduledTask {
  param($TaskName)
  if($TaskName -eq 'ElevhubServer' -and $script:taskExists){
    $command=Get-GenerationCommand -InstallRoot $InstallRoot -InstallationId '11111111111141118111111111111111' -Entry server -Arguments @('--role-ip','192.0.2.10','--teacher-port','8780','--teacher-address',$fixtureTeacherAddress)
    return [pscustomobject]@{State='Ready';Description='ElevhubServer owner=11111111-1111-4111-8111-111111111111';Actions=@([pscustomobject]@{Execute=$command.Execute;Arguments=$command.Arguments;WorkingDirectory=$InstallRoot});Principal=[pscustomobject]@{UserId='S-1-5-19'}}
  }
}
function Stop-ScheduledTask {$script:events.Add('stop-task')}
function Unregister-ScheduledTask {$script:taskExists=$false; $script:events.Add('unregister-task')}
function Get-NetFirewallRule {
  param($Name)
  if($Name -notlike 'ElevhubServer-*'){return $null}
  $description=if($env:MODE -eq 'foreign-firewall'){'foreign'}else{'ElevhubServer owner=11111111-1111-4111-8111-111111111111'}
  [pscustomobject]@{Name=$Name;Description=$description;Direction='Inbound';Action='Allow';Enabled='True';Profile='Any'}
}
function Get-NetFirewallPortFilter {param([Parameter(ValueFromPipeline)]$rule) process {[pscustomobject]@{Protocol='TCP';LocalPort=($rule.Name -replace '^ElevhubServer-','')}}}
function Get-NetFirewallAddressFilter {
  $remote=if($env:MODE -eq 'school-host-form'){'192.0.2.10'}else{'192.0.2.0/24'}
  [pscustomobject]@{LocalAddress=@('192.0.2.10');RemoteAddress=@($remote)}
}
function Get-NetFirewallApplicationFilter {[pscustomobject]@{Program=(Join-Path $InstallRoot 'runtime\python.exe')}}
function Remove-NetFirewallRule {$script:events.Add('remove-firewall')}
$script:stationExists=$env:MODE -eq 'generation-laser'
function Get-CimInstance {
  if($script:stationExists){
    $hash=(Get-FileHash -LiteralPath (Join-Path $InstallRoot 'control\active-generation.json')).Hash.ToLowerInvariant()
    $command=Get-GenerationCommand -InstallRoot $InstallRoot -InstallationId '11111111111141118111111111111111' -Entry 'laser-station' -ExpectedSelection $hash -Arguments @()
    return [pscustomobject]@{ExecutablePath=$command.Execute;CommandLine=('"{0}" {1}' -f $command.Execute,$command.Arguments);ProcessId=42}
  }
  return @()
}
function Stop-Process {param($Id) if($Id -ne 42){throw 'Unexpected process'}; $script:stationExists=$false; $script:events.Add('stop-station')}
$main=(@($ast.EndBlock.Statements | Where-Object {$_ -isnot [Management.Automation.Language.FunctionDefinitionAst] -and $_.Extent.Text -notmatch '^\. '}) | ForEach-Object {$_.Extent.Text}) -join "`n"
$main=$main.Replace('$PSCmdlet.ShouldProcess','$fixtureCmdlet.ShouldProcess')
$errorText=''
try {Invoke-Expression $main | Out-Null} catch {$errorText=$_.Exception.Message}
$saved=Get-Content -LiteralPath (Join-Path $DataRoot 'installation-owner.json') -Raw | ConvertFrom-Json
@{error=$errorText;events=@($script:events);program=(Test-Path $InstallRoot);shortcut=(Test-Path -LiteralPath $shortcutPath);state=$saved.state;anchorState=(Get-Content -LiteralPath (Join-Path $env:ProgramData 'ElevhubServer\installation-owner.json') -Raw | ConvertFrom-Json).state;key=[string](Get-Content -LiteralPath (Join-Path $DataRoot 'private.key') -Raw)} | ConvertTo-Json -Compress
'''


@unittest.skipUnless(PS and os.name == 'nt', 'Windows PowerShell is required')
class UninstallBehaviorTest(unittest.TestCase):
    def run_fixture(self, mode):
        fixture = tree_fixture.InitialInstallTreeTest()
        fixture.setUp()
        try:
            fixture.runtime = fixture.root / 'program-files/ElevhubServer'
            fixture.data = fixture.root / 'program-data/ElevhubServer'
            fixture.runtime.mkdir(parents=True)
            fixture.data.mkdir(parents=True)
            student_subnet='192.0.2.10/32' if mode == 'school-host-form' else '192.0.2.0/24'
            teacher_address = '192.0.2.10' if '-lan' in mode else '127.0.0.1'
            firewall_ports = list(range(8770, 8774)) + ([8780] if teacher_address != '127.0.0.1' else [])
            fixture.owner.update(installRoot=str(fixture.runtime), dataRoot=str(fixture.data),
                                 state='installed', roleIp='192.0.2.10', roleNetwork='192.0.2.0/24',
                                 studentSubnet=student_subnet, teacherAddress=teacher_address, teacherPort=8780,
                                 firewallNames=['ElevhubServer-' + str(p) for p in firewall_ports], caThumbprint=None)
            # The production verifier runs through the isolated copied helper.
            (fixture.package / 'server/provision.py').write_bytes((SERVER / 'provision.py').read_bytes())
            manifest = json.loads((fixture.package / 'integrity.json').read_text())
            import hashlib
            manifest['files']['server/provision.py'] = hashlib.sha256((SERVER / 'provision.py').read_bytes()).hexdigest()
            (fixture.package / 'integrity.json').write_text(json.dumps(manifest))
            fixture.owner['packageManifestSha256'] = hashlib.sha256((fixture.package / 'integrity.json').read_bytes()).hexdigest()
            fixture.generation()
            (fixture.data / 'instance.lock').write_bytes(b'1')
            (fixture.data / 'private.key').write_bytes(b'retained')
            if mode in ('later-selection','after-update','after-restore'):
                fixture.selection['revision'] = 2
                (fixture.runtime / 'control/active-generation.json').write_text(json.dumps(fixture.selection))
            if mode in ('after-update','after-restore'):
                next_program=fixture.root / 'selected-program'
                next_data=fixture.root / 'selected-data'
                shutil.copytree(fixture.package,next_program)
                shutil.copytree(fixture.data,next_data)
                selected_owner={**fixture.owner,'dataRoot':str(next_data)}
                (next_data / 'installation-owner.json').write_text(json.dumps(selected_owner))
                (next_data / 'private.key').write_bytes(b'selected retained key')
                (next_program / 'makerspace-release.json').write_text(json.dumps({'release':mode}))
                fixture.selection.update(program_root=str(next_program),data_root=str(next_data),
                                         manifest_sha256=hashlib.sha256((next_program / 'makerspace-release.json').read_bytes()).hexdigest())
                (fixture.runtime / 'control/active-generation.json').write_text(json.dumps(fixture.selection))
                before={str(p):p.read_bytes() for folder in (fixture.runtime,next_program,fixture.data,next_data)
                        for p in folder.rglob('*') if p.is_file() and p.name != 'installation-owner.json'}
            if mode in ('generation-laser', 'school-host-form') or mode.startswith('school-shortcut-'):
                school_subnet = '192.0.2.10/32' if mode == 'school-host-form' else '192.0.2.0/24'
                role = 'makerspace' if mode == 'generation-laser' else 'vm'
                (fixture.data / 'school.json').write_text(json.dumps({'schemaVersion':1,'role':role,'server':'192.0.2.10','student_subnet':school_subnet,'admin_public_key':'test-only'}))
            if mode == 'unknown-data':
                (fixture.runtime / 'student-private.key').write_bytes(b'do not delete')
            result = subprocess.run([PS, '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', HARNESS],
                                    env={**os.environ, 'FIXTURE_ROOT': str(fixture.root), 'SERVER': str(SERVER),
                                         'FIXTURE_PYTHON': sys.executable, 'MODE': mode},
                                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            if mode in ('after-update','after-restore'):
                after={str(p):p.read_bytes() for folder in (fixture.runtime,next_program,fixture.data,next_data)
                       for p in folder.rglob('*') if p.is_file() and p.name != 'installation-owner.json'}
                self.assertEqual(before,after)
                self.assertEqual((fixture.data / 'private.key').read_bytes(),b'retained')
                self.assertEqual((next_data / 'private.key').read_bytes(),b'selected retained key')
            return json.loads(result.stdout)
        finally:
            fixture.tearDown()

    def test_initial_generation_removes_owned_programs_and_preserves_data(self):
        result = self.run_fixture('remove')
        self.assertEqual(result['error'], '')
        self.assertFalse(result['program'])
        self.assertEqual(result['state'], 'removed')
        self.assertEqual(result['key'], 'retained')
        self.assertEqual(result['events'].count('stop-task'), 1)
        self.assertEqual(result['events'].count('unregister-task'), 1)
        self.assertEqual(result['events'].count('remove-firewall'), 4)

    def test_whatif_foreign_firewall_and_unknown_data_preserve_initial_installation(self):
        for mode in ('whatif', 'foreign-firewall', 'unknown-data'):
            with self.subTest(mode=mode):
                result = self.run_fixture(mode)
                self.assertTrue(result['program'])
                self.assertEqual(result['state'], 'installed')
                self.assertEqual(result['events'], [])
                self.assertEqual(result['key'], 'retained')
                self.assertEqual(bool(result['error']), mode != 'whatif')

    def test_generation_laser_stops_only_exact_pinned_owned_process(self):
        result=self.run_fixture('generation-laser')
        self.assertEqual(result['error'],'')
        self.assertEqual(result['events'].count('stop-station'),1)
        self.assertFalse(result['program'])
        self.assertEqual(result['state'],'removed')

    def test_after_update_and_restore_remove_integration_and_preserve_all_archives(self):
        for mode in ('later-selection','after-update','after-restore'):
            with self.subTest(mode=mode):
                result=self.run_fixture(mode)
                self.assertEqual(result['error'],'')
                self.assertTrue(result['program'])
                self.assertEqual(result['state'],'removed')
                self.assertEqual(result['anchorState'],'removed')
                self.assertEqual(result['events'].count('stop-task'),1)
                self.assertEqual(result['events'].count('unregister-task'),1)
                self.assertEqual(result['events'].count('remove-firewall'),4)

    def test_school_firewall_accepts_windows_host_form_for_32(self):
        result = self.run_fixture('school-host-form')
        self.assertEqual(result['error'], '')
        self.assertEqual(result['state'], 'removed')
        self.assertEqual(result['events'].count('remove-firewall'), 4)

    def test_owned_controlpanel_shortcuts_accept_current_and_legacy_arguments(self):
        for mode in ('school-shortcut-new', 'school-shortcut-new-lan', 'school-shortcut-old', 'school-shortcut-old-lan'):
            with self.subTest(mode=mode):
                result = self.run_fixture(mode)
                self.assertEqual(result['error'], '')
                self.assertFalse(result['shortcut'])
                self.assertFalse(result['program'])
                self.assertEqual(result['state'], 'removed')
                self.assertEqual(result['key'], 'retained')

    def test_changed_controlpanel_shortcut_stops_uninstall_before_any_mutation(self):
        for suffix in ('wrong-address', 'wrong-port', 'wrong-target', 'wrong-directory'):
            with self.subTest(suffix=suffix):
                result = self.run_fixture('school-shortcut-lan-' + suffix)
                self.assertIn('Genvejen', result['error'])
                self.assertEqual(result['events'], [])
                self.assertTrue(result['shortcut'])
                self.assertTrue(result['program'])
                self.assertEqual(result['state'], 'installed')
                self.assertEqual(result['key'], 'retained')


if __name__ == '__main__':
    unittest.main()
