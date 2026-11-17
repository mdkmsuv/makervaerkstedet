"""Execute task_control's read-only update branch with mocked host inventory."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'system/windows/inno/task_control.ps1'


class WindowsUpdatePreflightTests(unittest.TestCase):
    def setUp(self):
        if not shutil.which('powershell.exe'):
            self.skipTest('Windows PowerShell is required')
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.program = self.root / 'Program Files'
        self.installed = self.program / 'ElevhubServer'
        self.data = self.root / 'ProgramData' / 'ElevhubServer'
        self.data.mkdir(parents=True)
        self.python = str(self.installed / 'runtime/python.exe')
        self.owner = self.data / 'installation-owner.json'
        self.owner.write_text(json.dumps({'ownerId': 'fixture-owner', 'adoptedPrinterLights': False}), encoding='utf-8')
        self.config = {'elev_adresse': '192.0.2.10', 'laerer_port': 8780, 'laerer_adresse': '127.0.0.1'}
        (self.data / 'konfiguration.json').write_text(json.dumps(self.config), encoding='utf-8')
        self.school = self.data / 'school.json'
        self.profile = {'schemaVersion': 1, 'school_name': 'Synthetic school', 'server': '192.0.2.10',
                        'role': 'makerspace', 'admin_public_key': 'ssh-ed25519 fixture'}
        self.school.write_text(json.dumps(self.profile), encoding='utf-8')
        self.receipt = self.data / 'school-setup-state.json'
        self.receipt_value = {'schemaVersion': 1, 'product': 'ElevhubSchoolSetup', 'ownerId': 'fixture-owner',
                              'state': 'complete', 'settings': {k: self.profile[k] for k in ('school_name', 'role', 'server')}}
        self.receipt.write_text(json.dumps(self.receipt_value), encoding='utf-8')
        self.processes = []
        self.harness = self.root / 'preflight.ps1'
        self.harness.write_text(r'''param($Source,$ProgramRoot,$DataRoot,$Fixture,$Mode)
$ErrorActionPreference='Stop'
$env:ProgramFiles=$ProgramRoot; $env:ProgramData=$DataRoot
$env:Public=Join-Path (Split-Path $DataRoot -Parent) 'Public'
$installed=Join-Path $ProgramRoot 'ElevhubServer'; $data=Join-Path $DataRoot 'ElevhubServer'
$python=Join-Path $installed 'runtime\python.exe'
$serverArgs='-B "{0}" --package-root "{1}" --data-root "{2}" --role-ip "192.0.2.10" --teacher-port "8780" --teacher-address "127.0.0.1"' -f (Join-Path $installed 'server\bootstrap.py'),$installed,$data
$enrollmentArgs='-B "{0}" serve --data-root "{1}"' -f (Join-Path $installed 'deployment\school-setup\enrollment.py'),$data
function Get-ScheduledTask {
 param($TaskName,$TaskPath,$ErrorAction)
 if($TaskName -eq 'ElevhubSchoolEnrollment' -and $Mode -eq 'missing-enrollment'){return}
 $isServer=$TaskName -eq 'ElevhubServer'
 $arguments=if($isServer){$serverArgs}else{$enrollmentArgs}
 if(-not $isServer -and $Mode -eq 'changed-enrollment'){$arguments+=' --foreign'}
 [pscustomobject]@{TaskName=$TaskName;Actions=@([pscustomobject]@{Execute=$python;Arguments=$arguments;WorkingDirectory=$installed});
  Principal=[pscustomobject]@{UserId=$(if($isServer){'S-1-5-19'}else{'S-1-5-18'})};
  Description=$(if($isServer){'ElevhubServer owner=fixture-owner'}else{'Elevhub skole-enrollment'})}
}
function Get-CimInstance {
 $values=@(Get-Content -LiteralPath $Fixture -Raw -Encoding UTF8|ConvertFrom-Json)
 if($Mode -eq 'owned-tree'){
  $values+=@([pscustomobject]@{ExecutablePath=$python;CommandLine=('"'+$python+'" '+$serverArgs);ProcessId=100;ParentProcessId=50},
   [pscustomobject]@{ExecutablePath=$python;CommandLine='server backend';ProcessId=101;ParentProcessId=100},
   [pscustomobject]@{ExecutablePath=$python;CommandLine=('"'+$python+'" '+$enrollmentArgs);ProcessId=200;ParentProcessId=50})
 }
 $values
}
# Any attempt to touch the host or write a task snapshot is a regression.
function Export-ScheduledTask {throw 'SIDE_EFFECT_EXPORT'}
function Disable-ScheduledTask {throw 'SIDE_EFFECT_DISABLE'}
function Stop-ScheduledTask {throw 'SIDE_EFFECT_STOP'}
function Start-ScheduledTask {throw 'SIDE_EFFECT_START'}
function Stop-Process {throw 'SIDE_EFFECT_KILL'}
function Set-Content {throw 'SIDE_EFFECT_WRITE'}
function Get-Acl {param($LiteralPath);$acl=[Security.AccessControl.FileSecurity]::new();$acl.SetSecurityDescriptorSddlForm('O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FR;;;LS)');return $acl}
& $Source -Action PreflightUpdate -Journal (Join-Path $data 'installer-updates\synthetic-journal')
''', encoding='utf-8-sig')

    def run_preflight(self, mode='normal'):
        fixture = self.root / 'processes.json'
        fixture.write_text(json.dumps(self.processes), encoding='utf-8')
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                                 '-ExecutionPolicy', 'Bypass', '-File', str(self.harness), str(SOURCE),
                                 str(self.program), str(self.data.parent), str(fixture), mode],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual({p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)
        self.assertNotIn('SIDE_EFFECT', result.stdout + result.stderr)
        return result

    def test_complete_setup_without_runtime_is_read_only(self):
        result = self.run_preflight()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('complete-school-setup-and-controlled-runtime', result.stdout)

    def test_missing_or_invalid_school_requires_resume(self):
        for value in (None, 'not-json', '{}'):
            with self.subTest(value=value):
                if value is None:
                    self.school.unlink()
                else:
                    self.school.write_text(value, encoding='utf-8')
                result = self.run_preflight()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Setup-School.ps1 -Resume', result.stderr)

    def test_missing_or_changed_enrollment_requires_resume(self):
        for mode in ('missing-enrollment', 'changed-enrollment'):
            with self.subTest(mode=mode):
                result = self.run_preflight(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Setup-School.ps1 -Resume', result.stderr)

    def test_running_local_station_is_refused_before_any_change(self):
        config = self.data / 'laserstation/laser-installation.json'
        self.processes = [{'ExecutablePath': self.python, 'CommandLine': f'"{self.python}" -B -m app.laser_station --config "{config}"',
                           'ProcessId': 300, 'ParentProcessId': 50}]
        result = self.run_preflight()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Stop laserstation', result.stderr)
        self.assertIn('administratorsession', result.stderr)

    def test_unfinished_foreign_or_invalid_receipt_never_uses_legacy_fallback(self):
        for changes in ({'state': 'preparing'}, {'ownerId': 'another-school'}, {'schemaVersion': 2}, {'settings': {}}, {'product': 'foreign'}):
            with self.subTest(changes=changes):
                self.receipt.write_text(json.dumps({**self.receipt_value, **changes}), encoding='utf-8')
                result = self.run_preflight()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Setup-School.ps1 -Resume', result.stderr)

    def prepare_legacy_artifacts(self, role='vm', remote=False):
        self.receipt.unlink(missing_ok=True)
        self.profile.update(role=role, remote_laser_station=remote)
        if remote:
            self.profile.update(laser_machine='fbb2', laser_station_id='laser-fbb2')
        self.school.write_text(json.dumps(self.profile), encoding='utf-8')
        security = self.data / 'security'; security.mkdir(exist_ok=True)
        ca = '-----BEGIN CERTIFICATE-----\nSYNTHETICCA\n-----END CERTIFICATE-----\n'
        (security / 'ca.crt').write_text(ca, encoding='ascii')
        exports = self.root / 'Public/Documents/Elevvaerkstedet'; exports.mkdir(parents=True, exist_ok=True)
        connection = {'schemaVersion': 1, 'kind': 'elevhub-school-connection', 'school_name': self.profile['school_name'],
                      'server': self.profile['server'], 'enrollment_port': 8774, 'admin_public_key': self.profile['admin_public_key'],
                      'caCertificatePem': ca}
        # A dated matching export must count even when the undated file belongs
        # to another school. Neither export is rewritten by preflight.
        (exports / 'school-connection.json').write_text(json.dumps({**connection, 'school_name': 'Other school'}), encoding='utf-8')
        matching = exports / 'school-connection-20261114-120000.json'
        matching.write_text(json.dumps(connection), encoding='utf-8')
        if remote:
            laser = {'schemaVersion': 1, 'kind': 'makervaerkstedet-laserstation-connection',
                     'school_name': self.profile['school_name'], 'server_url': 'https://192.0.2.10:8780',
                     'caCertificatePem': ca, 'machine': 'fbb2', 'station_id': 'laser-fbb2'}
            (exports / 'laser-station-connection-20261114-120000.json').write_text(json.dumps(laser), encoding='utf-8')
        self.installed.mkdir(parents=True, exist_ok=True)
        desktop = self.root / 'Public/Desktop'; desktop.mkdir(parents=True, exist_ok=True)
        links = [(desktop / 'Elevhub skoleopsætning.lnk', self.installed / 'deployment/school-setup/Start.cmd', ''),
                 (desktop / 'Elevhub skoleadmin.lnk', self.installed / 'deployment/school-setup/Manage-School.cmd', ''),
                 (desktop / 'Makerværkstedet.lnk', self.installed / 'deployment/school-setup/Open-ControlPanel.cmd', '-Port 8780 -Address 127.0.0.1')]
        if role == 'makerspace':
            local_config = self.data / 'laserstation/laser-installation.json'
            local_config.parent.mkdir(exist_ok=True)
            local_config.write_text(json.dumps({'role': 'Station', 'server': 'http://127.0.0.1:8767', 'machine': 'fbb2', 'port': 49160}), encoding='utf-8')
            startup = self.data.parent / 'Microsoft/Windows/Start Menu/Programs/Startup'; startup.mkdir(parents=True, exist_ok=True)
            arguments = f'-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{self.installed / "deployment/school-setup/Start-LaserStation.ps1"}" -ConfigPath "{local_config}" -Root "{self.installed}"'
            links += [(desktop / 'Elevhub Laserstation.lnk', 'powershell.exe', arguments + ' -OpenBrowser'),
                      (startup / 'Elevhub Laserstation.lnk', 'powershell.exe', arguments)]
        fixture = self.root / 'shortcuts.json'
        fixture.write_text(json.dumps([{'path': str(path), 'target': str(target), 'arguments': args} for path, target, args in links]), encoding='utf-8')
        command = r'''$ErrorActionPreference='Stop'; $shell=New-Object -ComObject WScript.Shell
foreach($entry in (Get-Content -LiteralPath $args[0] -Raw|ConvertFrom-Json)){
 $link=$shell.CreateShortcut($entry.path);$link.TargetPath=$entry.target;$link.Arguments=$entry.arguments;$link.WorkingDirectory=$args[1];$link.Save()
}'''
        harness = self.root / 'create-synthetic-shortcuts.ps1'; harness.write_text(command, encoding='utf-8-sig')
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(harness), str(fixture), str(self.installed)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return matching

    def test_legacy_complete_vm_accepts_matching_dated_export_without_new_artifacts(self):
        self.prepare_legacy_artifacts()
        result = self.run_preflight()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_legacy_partial_or_foreign_exports_and_missing_shortcut_are_refused(self):
        matching = self.prepare_legacy_artifacts()
        original = matching.read_text(encoding='utf-8')
        for changes in ({'caCertificatePem': 'other CA'}, {'server': '192.0.2.20'}, {'school_name': 'Other school'}):
            with self.subTest(changes=changes):
                matching.write_text(json.dumps({**json.loads(original), **changes}), encoding='utf-8')
                result = self.run_preflight()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Setup-School.ps1 -Resume', result.stderr)
        matching.write_text(original, encoding='utf-8')
        (self.root / 'Public/Desktop/Elevhub skoleadmin.lnk').unlink()
        self.assertNotEqual(self.run_preflight().returncode, 0)

    def test_legacy_remote_server_requires_its_actual_laser_connection(self):
        self.prepare_legacy_artifacts(remote=True)
        self.assertEqual(self.run_preflight().returncode, 0)
        laser = self.root / 'Public/Documents/Elevvaerkstedet/laser-station-connection-20261114-120000.json'
        laser.write_text(json.dumps({**json.loads(laser.read_text(encoding='utf-8')), 'machine': 'fhexa1'}), encoding='utf-8')
        self.assertNotEqual(self.run_preflight().returncode, 0)

    def test_legacy_all_in_one_requires_station_and_startup_but_no_new_stop_link(self):
        self.prepare_legacy_artifacts(role='makerspace')
        result = self.run_preflight()
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.data.parent / 'Microsoft/Windows/Start Menu/Programs/Startup/Elevhub Laserstation.lnk').unlink()
        self.assertNotEqual(self.run_preflight().returncode, 0)

    def test_other_runtime_is_refused_and_not_mistaken_for_local_station(self):
        for command in ('unknown runtime command', '-B -m app.laser_station --config "C:\\other\\laser-installation.json"'):
            with self.subTest(command=command):
                self.processes = [{'ExecutablePath': self.python, 'CommandLine': command, 'ProcessId': 301, 'ParentProcessId': 50}]
                result = self.run_preflight()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('301', result.stderr)
                self.assertNotIn('Luk laserstationen', result.stderr)

    def test_owned_server_tree_and_external_python_are_allowed(self):
        self.processes = [{'ExecutablePath': str(self.root / 'unrelated/python.exe'), 'CommandLine': 'app.laser_station',
                           'ProcessId': 400, 'ParentProcessId': 50}]
        result = self.run_preflight('owned-tree')
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
