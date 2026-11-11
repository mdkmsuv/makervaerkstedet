import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest

from app.generation_lock import GenerationLock
from tests import test_school_admin as school_admin_fixture


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'deployment' / 'school-setup' / 'Manage-School.ps1'


class ManageSchoolBindingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != 'nt' or shutil.which('powershell.exe') is None:
            raise unittest.SkipTest('Manage-School-testen kræver Windows PowerShell.')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.program = self.root / 'Program Files'
        self.installed = self.program / 'ElevhubServer'
        (self.installed / 'runtime').mkdir(parents=True)
        (self.installed / 'deployment' / 'school-setup').mkdir(parents=True)
        (self.installed / 'runtime' / 'python.exe').write_bytes(b'fixture')
        (self.installed / 'deployment' / 'school-setup' / 'enrollment.py').write_text('fixture', encoding='utf-8')
        self.data = self.root / 'DataRoot'
        self.data.mkdir()
        self.marker = self.data / 'installation-owner.json'
        self.write_marker()

    def tearDown(self):
        self.temp.cleanup()

    def write_marker(self, **changes):
        value = {'schemaVersion': 1, 'product': 'ElevhubServer', 'state': 'installed',
                 'dataRoot': str(self.data), 'installRoot': str(self.installed)}
        value.update(changes)
        self.marker.write_text(json.dumps(value), encoding='utf-8')

    def invoke(self, caller=None, validate_only=True, input_text=None, inputs=None):
        env = os.environ.copy()
        env['ProgramFiles'] = str(self.program)
        caller = caller or (self.root / 'wrong-caller')
        prefix = "$env:ProgramFiles='" + str(self.program).replace("'", "''") + "';"
        if inputs is not None:
            encoded = ','.join("'" + str(item).replace("'", "''") + "'" for item in inputs)
            prefix += "$global:__ManageInputs=@(" + encoded + "); function global:Read-Host { $v=$global:__ManageInputs[0]; if($global:__ManageInputs.Count -gt 1){$global:__ManageInputs=$global:__ManageInputs[1..($global:__ManageInputs.Count-1)]} else {$global:__ManageInputs=@()}; return $v };"
        command = (prefix + " & '" +
                   str(SCRIPT).replace("'", "''") + "' -PackageRoot '" + str(caller).replace("'", "''") +
                   "' -DataRoot '" + str(self.data).replace("'", "''") + "'" +
                   (" -ValidateOnly" if validate_only else ""))
        return subprocess.run(
            ['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', command],
            env=env, input=input_text, capture_output=True, text=True, timeout=20)

    def enable_generation_fixture(self, wrong_identity=False):
        helper = self.installed / 'server' / 'generation-startup.ps1'
        helper.parent.mkdir(parents=True, exist_ok=True)
        data = str(self.data).replace("'", "''")
        install = str(self.installed).replace("'", "''")
        selected = self.root / 'SelectedProgram'
        selected.mkdir(exist_ok=True)
        reported_data = str(self.root / 'wrong-data') if wrong_identity else data
        reported_program = str(self.root / 'wrong-program') if wrong_identity else selected
        reported_runtime = str(self.root / 'wrong-runtime') if wrong_identity else install
        reported_data = reported_data.replace("'", "''")
        reported_program = str(reported_program).replace("'", "''")
        reported_runtime = reported_runtime.replace("'", "''")
        helper.write_text(
            "function Get-InstallationStartupMode { param($Owner,[string]$InstallRoot) 'generation-v1' }\n"
            "function Get-GenerationInstallationId { param($Owner) [string]$Owner.ownerId }\n"
            "function Get-GenerationCommand { param([string]$InstallRoot,[string]$InstallationId,[string]$Entry,[string[]]$Arguments)\n"
            "$payload = if ($Entry -eq 'school-admin' -and $Arguments[0] -eq 'codes') { @(@{label='Printværksted';code='ABC123'},@{label='Laserværksted';code=$null}) } elseif ($Entry -eq 'school-admin' -and $Arguments[0] -eq 'urls') { @{teacher='http://192.168.1.20:8780';design='http://127.0.0.1:8767';laser='http://127.0.0.1:8790'} } else { @{runtime_root='" + reported_runtime + "';program_root='" + reported_program + "';data_root='" + reported_data + "';installation_id=$InstallationId;revision=7} }\n"
            "$json = $payload | ConvertTo-Json -Compress\n"
            "$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))\n"
            "[pscustomobject]@{Execute='powershell.exe';ArgumentList=@('-NoLogo','-NoProfile','-Command',\"[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$b64'))\");WorkingDirectory='" + install + "'}\n}\n",
            encoding='utf-8')
        self.write_marker(startupProtocol='generation-v1', ownerId='a' * 32)

    def tree_bytes(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()}

    def test_valid_owner_ignores_wrong_caller_and_does_not_enter_menu(self):
        before = self.tree_bytes()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            'dataRoot': str(self.data), 'installRoot': str(self.installed)})
        self.assertFalse((self.root / 'wrong-caller').exists())
        self.assertEqual(self.tree_bytes(), before)

    def test_missing_owner_fails_without_writes(self):
        self.marker.unlink()
        before = self.tree_bytes()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.tree_bytes(), before)

    def test_mismatched_owner_fails_without_writes(self):
        cases = ({'state': 'installing'}, {'product': 'Other'}, {'schemaVersion': 2},
                 {'dataRoot': str(self.root / 'other')}, {'installRoot': str(self.root / 'other')})
        for changes in cases:
            with self.subTest(changes=changes):
                self.write_marker(**changes)
                before = self.tree_bytes()
                result = self.invoke()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.tree_bytes(), before)

    def test_missing_installed_runtime_fails_without_creating_files(self):
        for relative in ('runtime/python.exe', 'deployment/school-setup/enrollment.py'):
            with self.subTest(relative=relative):
                path = self.installed / relative
                path.unlink()
                before = self.tree_bytes()
                result = self.invoke()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.tree_bytes(), before)
                if relative.endswith('python.exe'):
                    path.write_bytes(b'fixture')
                else:
                    path.write_text('fixture', encoding='utf-8')

    def test_reparse_runtime_ancestor_is_rejected_without_writes(self):
        real_runtime = self.installed / 'runtime-real'
        runtime = self.installed / 'runtime'
        runtime.rename(real_runtime)
        try:
            command = ("New-Item -ItemType Junction -Path '" + str(runtime).replace("'", "''") +
                       "' -Target '" + str(real_runtime).replace("'", "''") + "' | Out-Null")
            created = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                                      '-ExecutionPolicy', 'Bypass', '-Command', command],
                                     capture_output=True, text=True, timeout=20)
            if created.returncode != 0:
                self.skipTest('Junctions kræver en Windows-token med denne rettighed.')
            before = self.tree_bytes()
            result = self.invoke()
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.tree_bytes(), before)
        finally:
            if runtime.exists() or runtime.is_symlink():
                os.rmdir(runtime)
            real_runtime.rename(runtime)

    def test_generation_validate_only_uses_active_school_admin_probe(self):
        self.enable_generation_fixture()
        before = self.tree_bytes()
        result = self.invoke(caller=self.root / 'untrusted-package')
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['dataRoot'], str(self.data))
        self.assertEqual(value['installRoot'], str(self.installed))
        self.assertEqual(value['programRoot'], str(self.root / 'SelectedProgram'))
        self.assertEqual(value['protocol'], 'generation-v1')
        self.assertEqual(value['revision'], 7)
        self.assertEqual(self.tree_bytes(), before)

    def test_generation_menu_uses_school_admin_for_codes_and_urls(self):
        source = SCRIPT.read_text(encoding='utf-8')
        self.assertIn("Invoke-SchoolAdmin 'codes'", source)
        self.assertIn("Invoke-SchoolAdmin 'urls'", source)
        self.assertIn("Open-LocalUrl $urls.teacher -Teacher", source)

    def test_generation_inspect_identity_mismatch_is_rejected(self):
        self.enable_generation_fixture(wrong_identity=True)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('matcher ikke skoleadministrationen', result.stderr)

    def test_legacy_rejects_partial_control_even_with_different_case(self):
        (self.installed / 'CONTROL').write_text('partial initialization', encoding='ascii')
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)

    def test_validate_only_uses_real_embedded_active_generation(self):
        fixture = school_admin_fixture.SchoolAdminActiveTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.data.mkdir(parents=True, exist_ok=True)
        fixed = fixture.root / 'Program Files' / 'ElevhubServer'
        shutil.copytree(fixture.stable, fixed)
        shutil.rmtree(fixed / 'control')
        (fixed / 'server').mkdir()
        shutil.copy2(ROOT / 'windows-elevhub/server/generation-startup.ps1',
                     fixed / 'server/generation-startup.ps1')
        control = fixed / 'control'
        control.mkdir()
        lock_path = control / 'generation.lock'
        lock_path.write_bytes(b'1')
        selected_record = dict(fixture.record, runtime_root=str(fixed), revision=1)
        raw = json.dumps(selected_record, sort_keys=True).encode('utf-8')
        with GenerationLock(lock_path, exclusive=True):
            pending = control / 'active-generation.tmp'
            pending.write_bytes(raw)
            os.replace(pending, control / 'active-generation.json')
        anchor = fixture.root / 'anchor-data'
        anchor.mkdir()
        owner = {
            'schemaVersion': 1, 'product': 'ElevhubServer', 'state': 'installed',
            'dataRoot': str(anchor), 'installRoot': str(fixed),
            'ownerId': 'a' * 32, 'startupProtocol': 'generation-v1',
        }
        (anchor / 'installation-owner.json').write_text(json.dumps(owner), encoding='utf-8')
        before = {p.relative_to(fixture.root): p.read_bytes() for p in fixture.root.rglob('*') if p.is_file()}
        command = ("$env:ProgramFiles='" + str(fixture.root / 'Program Files').replace("'", "''") +
                   "'; & '" + str(SCRIPT).replace("'", "''") + "' -PackageRoot '" +
                   str(fixture.root / 'untrusted-package').replace("'", "''") + "' -DataRoot '" +
                   str(anchor).replace("'", "''") + "' -ValidateOnly")
        result = subprocess.run(
            ['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', command],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, 'ProgramFiles': str(fixture.root / 'Program Files')})
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(Path(value['installRoot']).resolve(), fixed.resolve())
        self.assertEqual(Path(value['programRoot']).resolve(), fixture.package.resolve())
        self.assertEqual(Path(value['dataRoot']).resolve(), fixture.data.resolve())
        self.assertEqual(value['protocol'], 'generation-v1')
        self.assertEqual(value['revision'], 1)
        self.assertEqual({p.relative_to(fixture.root): p.read_bytes() for p in fixture.root.rglob('*') if p.is_file()}, before)

    def test_generation_enrollment_accepts_plain_text_and_success_without_stdout(self):
        probe = r'''
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:MANAGE_SOURCE,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Parse error'}
$fn=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-GenerationEntry'},$true)
Invoke-Expression $fn.Extent.Text
$PackageRoot=$env:TEMP; $installationId='a'*32
function Get-GenerationCommand {
  param($InstallRoot,$InstallationId,$Entry,$Arguments)
  $code=if($Arguments[0] -eq 'ticket'){ "Write-Output 'Test enrollment succeeded'; exit 0" } else { 'exit 0' }
  $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code))
  [pscustomobject]@{Execute=(Join-Path $PSHOME 'powershell.exe');ArgumentList=@('-NoProfile','-EncodedCommand',$encoded);WorkingDirectory=$env:TEMP}
}
$plain=Invoke-GenerationEntry enrollment @('ticket')
$empty=Invoke-GenerationEntry enrollment @('connection')
$jsonRejected=$false
try { Invoke-GenerationEntry school-admin @('inspect') -Json } catch { $jsonRejected=$true }
@{plain=$plain;empty=$empty;jsonRejected=$jsonRejected}|ConvertTo-Json -Compress
'''
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', probe],
                                capture_output=True, text=True, timeout=20,
                                env={**os.environ, 'MANAGE_SOURCE': str(SCRIPT)})
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['plain'], 'Test enrollment succeeded')
        self.assertFalse(value['empty'])
        self.assertTrue(value['jsonRejected'])

    def test_panel_urls_accept_loopback_and_teacher_server_without_opening_browser(self):
        probe = r'''
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:MANAGE_SOURCE,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Parse error'}
$fn=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Open-LocalUrl'},$true)
Invoke-Expression $fn.Extent.Text
$script:opened=@()
function Start-Process { param($Url) $script:opened += $Url }
Open-LocalUrl 'http://127.0.0.1:8767'
Open-LocalUrl 'https://192.0.2.10:8780/kontrolpanel' -Teacher
$rejected=$false
try { Open-LocalUrl 'http://192.0.2.20:8790' } catch { $rejected=$true }
@{opened=$script:opened;rejected=$rejected}|ConvertTo-Json -Compress
'''
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', probe],
                                capture_output=True, text=True, timeout=20,
                                env={**os.environ, 'MANAGE_SOURCE': str(SCRIPT)})
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['opened'], ['http://127.0.0.1:8767/', 'https://192.0.2.10:8780/kontrolpanel'])
        self.assertTrue(value['rejected'])


if __name__ == '__main__':
    unittest.main()
