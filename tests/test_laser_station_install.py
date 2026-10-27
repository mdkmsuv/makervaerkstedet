"""Small real-file installer fixtures; no desktop, certificates, services or LAN."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import unittest
import urllib.request
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app import laser_station_install as station
from app.instance import InstanceLock
from tests import test_release_compatibility as fixtures

ROOT = Path(__file__).resolve().parents[1]


class LaserStationInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Isolated station test CA')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
                .not_valid_after(now + timedelta(days=2)).add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
                .sign(key, hashes.SHA256()))
        cls.pem = cert.public_bytes(serialization.Encoding.PEM).decode('ascii')

    def setUp(self):
        self.fixture = fixtures.ReleaseCompatibilityTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        self.package = self.fixture._minimal_program({'hub': 1, 'print': 1, 'laser': 1})
        for name in station.REQUIRED:
            path = self.package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(b'fixture' if not name.endswith('.py') else b'')
        (self.package / 'school-package.json').write_text(json.dumps({
            'format': 'elevhub-school-windows-v1', 'containsCredentials': False}), encoding='utf-8')
        self.seal()
        self.connection = self.root / 'laser-station-connection.json'
        self.connection_value = {'schemaVersion': 1, 'kind': station.CONNECTION_KIND,
                                 'school_name': 'Testskolen', 'server_url': 'https://192.0.2.50:8780',
                                 'caCertificatePem': self.pem, 'station_id': 'laser-fbb2', 'machine': 'fbb2'}
        self.connection.write_text(json.dumps(self.connection_value), encoding='utf-8')
        self.install_root = self.root / 'Installed Laser Station'
        self.plates = self.root / 'Preserved Laser Plates'
        self.probe = patch.object(station, 'probe_runtime')
        self.probe.start()
        self.addCleanup(self.probe.stop)

    def seal(self, release='test-release'):
        files = {path.relative_to(self.package).as_posix() for path in self.package.rglob('*')
                 if path.is_file() and path.name != 'makerspace-release.json'}
        manifest = self.fixture._manifest(self.package, files, {'hub': 1, 'print': 1, 'laser': 1})
        manifest['release'] = release
        (self.package / 'makerspace-release.json').write_text(json.dumps(manifest), encoding='utf-8')

    def fresh(self, **kwargs):
        return station.install(self.package, self.install_root, action='Install',
                               connection_file=self.connection, plates=self.plates, **kwargs)

    def update(self, **kwargs):
        return station.install(self.package, self.install_root, action='Update', **kwargs)

    def test_fresh_install_selects_only_station_and_shared_runtime(self):
        result = self.fresh()
        active, version, config, connection = station.active_installation(self.install_root)
        self.assertEqual(result['release'], 'test-release')
        self.assertEqual(config['server'], 'https://192.0.2.50:8780')
        self.assertEqual(config['station_id'], 'laser-fbb2')
        self.assertEqual(connection, self.connection_value)
        self.assertFalse((version / 'program/app/laser_server.py').exists())
        self.assertFalse((version / 'program/server').exists())
        self.assertFalse((self.install_root / 'school.json').exists())
        self.assertFalse(self.plates.exists())
        self.assertEqual(len(active['installation_id']), 32)

    def test_check_only_reads_package_without_creating_station_or_plates(self):
        result = self.fresh(check_only=True)
        self.assertTrue(result['check_only'])
        self.assertFalse(self.install_root.exists())
        self.assertFalse(self.plates.exists())

    def test_update_preserves_identity_data_connection_and_optional_drivers(self):
        self.fresh()
        before, old_version, config, _ = station.active_installation(self.install_root)
        config.update({'beam_studio': 'C:/Beam/Beam Studio.exe', 'flux_api': 'C:/Beam/flux_api.exe',
                       'vision': {'backend': 'server', 'url': 'https://192.0.2.50:8792'},
                       'direct_transfer': {'enabled': False, 'machine': 'fbb2'}})
        station.atomic_json(old_version / 'laser-installation.json', config)
        self.plates.mkdir()
        sentinel = self.plates / 'student-project.bvg'
        sentinel.write_bytes(b'preserve exact existing plate')
        self.seal('test-release-2')
        self.update()
        after, new_version, new_config, _ = station.active_installation(self.install_root)
        self.assertEqual(after['installation_id'], before['installation_id'])
        self.assertEqual(after['release'], 'test-release-2')
        self.assertNotEqual(old_version, new_version)
        self.assertTrue(old_version.is_dir())
        for key in config.keys() - {'ca_file'}:
            self.assertEqual(new_config[key], config[key])
        self.assertEqual((old_version / 'school-ca.pem').read_bytes(), (new_version / 'school-ca.pem').read_bytes())
        self.assertEqual(sentinel.read_bytes(), b'preserve exact existing plate')

    def test_fresh_refuses_existing_station_and_update_refuses_missing_station(self):
        with self.assertRaisesRegex(ValueError, 'Opdatering kræver'):
            self.update()
        self.fresh()
        before = (self.install_root / 'active.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'Ren installation kræver'):
            self.fresh()
        self.assertEqual(before, (self.install_root / 'active.json').read_bytes())

    def test_legacy_install_and_existing_plates_are_refused_without_changes(self):
        self.install_root.mkdir()
        legacy = self.install_root / 'laser-installation.json'
        legacy.write_text('{"role":"Station"}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'historiske'):
            self.update()
        self.assertEqual(legacy.read_text(encoding='utf-8'), '{"role":"Station"}')
        legacy.unlink()
        self.plates.mkdir()
        (self.plates / 'existing.bvg').write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'tom plademappe'):
            self.fresh()
        self.assertEqual((self.plates / 'existing.bvg').read_bytes(), b'keep')

    def test_update_rejects_connection_and_data_replacement(self):
        self.fresh()
        for values in ({'connection_file': self.connection}, {'plates': self.root / 'other'}, {'port': 8911}):
            with self.subTest(values=values), self.assertRaisesRegex(ValueError, 'bevarer'):
                self.update(**values)

    def test_tampered_source_rejected_before_station_creation(self):
        (self.package / 'app/laser_station.py').write_bytes(b'# tampered')
        with self.assertRaisesRegex(ValueError, 'kontrolsum'):
            self.fresh()
        self.assertFalse(self.install_root.exists())

    def test_failed_copy_keeps_active_version_config_and_plates(self):
        self.fresh()
        before = (self.install_root / 'active.json').read_bytes()
        versions = set((self.install_root / 'versions').iterdir())
        copy = shutil.copy2
        count = 0
        def fail_halfway(source, target):
            nonlocal count
            count += 1
            if count == 4:
                raise OSError('simulated interrupted copy')
            return copy(source, target)
        with patch.object(station.shutil, 'copy2', side_effect=fail_halfway), self.assertRaisesRegex(OSError, 'interrupted'):
            self.update()
        self.assertEqual(before, (self.install_root / 'active.json').read_bytes())
        self.assertEqual(versions, set((self.install_root / 'versions').iterdir()))

    def test_candidate_cleanup_holds_lifetime_lease_against_resume(self):
        original_delete = shutil.rmtree
        observed = []
        def checked_delete(path, *args, **kwargs):
            if Path(path).parent == self.install_root / 'versions':
                with self.assertRaisesRegex(RuntimeError, 'kører allerede'):
                    InstanceLock(self.install_root, filename='station.lock')
                observed.append(str(path))
            return original_delete(path, *args, **kwargs)
        with patch.object(station, 'probe_runtime', side_effect=ValueError('interrupted validation')), \
                patch.object(station.shutil, 'rmtree', side_effect=checked_delete):
            with self.assertRaisesRegex(ValueError, 'interrupted validation'):
                self.fresh()
        self.assertEqual(len(observed), 1)
        station.install(self.package, self.install_root, action='ResumeInstall')
        station.active_installation(self.install_root)

    def test_activation_failure_keeps_previous_complete_selection(self):
        self.fresh()
        before = (self.install_root / 'active.json').read_bytes()
        writer = station.atomic_json
        def fail_pointer(path, value):
            if path == self.install_root / 'active.json':
                raise OSError('simulated activation failure')
            return writer(path, value)
        with patch.object(station, 'atomic_json', side_effect=fail_pointer), self.assertRaisesRegex(OSError, 'activation'):
            self.update()
        self.assertEqual(before, (self.install_root / 'active.json').read_bytes())
        station.active_installation(self.install_root)

    def test_bad_candidate_hash_or_runtime_never_activates(self):
        self.fresh()
        before = (self.install_root / 'active.json').read_bytes()
        for function in ('verify_candidate', 'probe_runtime'):
            with self.subTest(function=function), patch.object(station, function, side_effect=ValueError('candidate rejected')):
                with self.assertRaisesRegex(ValueError, 'candidate rejected'):
                    self.update()
            self.assertEqual(before, (self.install_root / 'active.json').read_bytes())

    def test_running_station_lease_refuses_update_before_copy(self):
        self.fresh()
        before = (self.install_root / 'active.json').read_bytes()
        with InstanceLock(self.install_root, filename='station.lock'):
            with self.assertRaisesRegex(RuntimeError, 'kører allerede'):
                self.update()
        self.assertEqual(before, (self.install_root / 'active.json').read_bytes())

    def test_remote_connection_requires_https_public_ca_and_no_extra_secrets(self):
        for change in ({'server_url': 'http://192.0.2.50:8767'}, {'server_url': 'https://user:secret@192.0.2.50:8780'},
                       {'server_url': 'https://192.0.2.50:8780/private'}, {'station_id': '../other'},
                       {'machine': 'unsupported'}, {'teacher_code': 'must not travel'},
                       {'caCertificatePem': self.pem + '-----BEGIN PRIVATE KEY-----'}):
            with self.subTest(change=change), self.assertRaises((ValueError, KeyError)):
                station.validate_connection({**self.connection_value, **change})

    def test_actual_station_dependency_selection_imports_without_server_module(self):
        # Real source modules, isolated folder, existing Python dependencies. No
        # runtime is copied and no listener/Beam Studio process is started.
        selected = self.root / 'real-station-sources'
        for name in station.APP_FILES:
            target = selected / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        result = subprocess.run([sys.executable, '-I', '-B', '-c',
                                 'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); '
                                 'import app, app.laser_station, app.laser_station_install; '
                                 'assert Path(app.__file__).resolve().is_relative_to(Path(sys.argv[1])); '
                                 'assert "app.laser_server" not in sys.modules', str(selected)],
                                cwd=selected, capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr)

    def kill_during_copy(self, action):
        signal = self.root / ('copy-started-' + action)
        code = '''import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from app import laser_station_install as installer
original = installer.shutil.copy2
def blocked_copy(source, target):
    original(source, target)
    Path(sys.argv[6]).write_text('copy started', encoding='ascii')
    while True: time.sleep(0.1)
installer.shutil.copy2 = blocked_copy
installer.probe_runtime = lambda program: None
options = {'connection_file': Path(sys.argv[4]), 'plates': Path(sys.argv[5])} if sys.argv[7] == 'Install' else {}
installer.install(Path(sys.argv[2]), Path(sys.argv[3]), action=sys.argv[7], **options)
'''
        child = subprocess.Popen([sys.executable, '-I', '-B', '-c', code, str(ROOT), str(self.package),
                                  str(self.install_root), str(self.connection), str(self.plates), str(signal), action],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not signal.exists() and time.monotonic() < deadline and child.poll() is None:
                time.sleep(0.025)
            if not signal.exists():
                self.fail('Subprocess did not reach copy boundary: ' + str(child.communicate(timeout=2)))
            child.kill()
            child.communicate(timeout=5)
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)

    def test_hard_killed_fresh_install_resumes_only_its_journaled_release(self):
        self.kill_during_copy('Install')
        self.assertFalse((self.install_root / 'active.json').exists())
        pending = station.read_json(self.install_root / 'installation-pending.json')
        with self.assertRaisesRegex(ValueError, 'Ren installation kræver'):
            self.fresh()
        station.install(self.package, self.install_root, action='ResumeInstall')
        active, _, _, _ = station.active_installation(self.install_root)
        self.assertEqual(active['installation_id'], pending['installation_id'])
        self.assertFalse((self.install_root / 'installation-pending.json').exists())
        self.assertFalse(any(p.name.startswith('.pending') for p in (self.install_root / 'versions').iterdir()))

    def test_hard_killed_update_keeps_old_selection_and_allows_new_update(self):
        self.fresh()
        before = (self.install_root / 'active.json').read_bytes()
        self.kill_during_copy('Update')
        self.assertEqual(before, (self.install_root / 'active.json').read_bytes())
        station.active_installation(self.install_root)
        self.update()
        station.active_installation(self.install_root)

    def test_resume_refuses_changed_release_and_unknown_staged_files(self):
        self.kill_during_copy('Install')
        journal = station.read_json(self.install_root / 'installation-pending.json')
        foreign = self.install_root / 'versions' / ('.pending-' + journal['candidate_id']) / 'keep-me.txt'
        foreign.write_bytes(b'unknown')
        with self.assertRaisesRegex(ValueError, 'ukendte filer'):
            station.install(self.package, self.install_root, action='ResumeInstall')
        self.assertEqual(foreign.read_bytes(), b'unknown')
        self.seal('different-release')
        with self.assertRaisesRegex(ValueError, 'præcis den journalførte'):
            station.install(self.package, self.install_root, action='ResumeInstall')

    def test_resume_rechecks_journal_under_lease_before_writing(self):
        self.kill_during_copy('Install')
        journal_path = self.install_root / 'installation-pending.json'
        original = station.package_info
        def changed_journal(package):
            result = original(package)
            journal = station.read_json(journal_path)
            journal['candidate_id'] = 'a' * 32
            station.atomic_json(journal_path, journal)
            return result
        with patch.object(station, 'package_info', side_effect=changed_journal):
            with self.assertRaisesRegex(ValueError, 'journalen ændredes'):
                station.install(self.package, self.install_root, action='ResumeInstall')
        self.assertFalse((self.install_root / 'active.json').exists())
        self.assertFalse((self.install_root / 'versions' / ('.pending-' + 'a' * 32)).exists())

    def test_install_rechecks_empty_root_under_lease_without_removing_foreign_file(self):
        original = station.package_info
        foreign = self.install_root / 'keep-me.txt'
        def changed_root(package):
            result = original(package)
            self.install_root.mkdir()
            foreign.write_bytes(b'preserve external change')
            return result
        with patch.object(station, 'package_info', side_effect=changed_root):
            with self.assertRaisesRegex(ValueError, 'Installationsmappen ændredes'):
                self.fresh()
        self.assertEqual(foreign.read_bytes(), b'preserve external change')
        self.assertFalse((self.install_root / 'installation-pending.json').exists())

    def test_concurrent_fresh_preflights_cannot_replace_winners_journal_or_lock(self):
        # Both real processes finish the empty-root preflight before either
        # acquires station.lock. The winner pauses during its first copy while
        # the loser exits; killing the winner then exercises its own journal.
        release_barrier = self.root / 'release-installers'
        code = '''import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from app import laser_station_install as installer
original_info = installer.package_info
def barrier_info(package):
    result = original_info(package)
    Path(sys.argv[7]).write_text('ready', encoding='ascii')
    while not Path(sys.argv[8]).exists(): time.sleep(0.01)
    return result
original_copy = installer.shutil.copy2
def blocked_copy(source, target):
    original_copy(source, target)
    Path(sys.argv[6]).write_text('copying', encoding='ascii')
    while True: time.sleep(0.1)
installer.package_info = barrier_info
installer.shutil.copy2 = blocked_copy
installer.probe_runtime = lambda program: None
installer.install(Path(sys.argv[2]), Path(sys.argv[3]), action='Install',
                  connection_file=Path(sys.argv[4]), plates=Path(sys.argv[5]))
'''
        children = []
        try:
            for name in ('a', 'b'):
                connection = self.root / ('connection-' + name + '.json')
                connection.write_text(json.dumps({**self.connection_value, 'station_id': 'laser-' + name}), encoding='utf-8')
                copying = self.root / ('copying-' + name)
                ready = self.root / ('ready-' + name)
                child = subprocess.Popen([sys.executable, '-I', '-B', '-c', code, str(ROOT), str(self.package),
                                          str(self.install_root), str(connection), str(self.plates), str(copying),
                                          str(ready), str(release_barrier)],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                children.append((name, child, copying, ready))
            deadline = time.monotonic() + 10
            while not all(ready.exists() for _, _, _, ready in children) and time.monotonic() < deadline:
                time.sleep(0.025)
            self.assertTrue(all(ready.exists() for _, _, _, ready in children), 'Both processes must reach the preflight barrier')
            release_barrier.write_bytes(b'go')
            deadline = time.monotonic() + 10
            while not any(copying.exists() for _, _, copying, _ in children) and time.monotonic() < deadline:
                time.sleep(0.025)
            winners = [(name, child) for name, child, copying, _ in children if copying.exists()]
            self.assertEqual(len(winners), 1)
            winner_name, winner = winners[0]
            loser = next(child for name, child, _, _ in children if name != winner_name)
            _, loser_error = loser.communicate(timeout=5)
            self.assertNotEqual(loser.returncode, 0, loser_error)
            pending = station.read_json(self.install_root / 'installation-pending.json')
            self.assertEqual(pending['connection']['station_id'], 'laser-' + winner_name)
            self.assertTrue((self.install_root / 'station.lock').is_file())
            self.assertTrue((self.install_root / 'versions' / ('.pending-' + pending['candidate_id'])).is_dir())
            winner.kill()
            winner.communicate(timeout=5)
            station.install(self.package, self.install_root, action='ResumeInstall')
            active, _, config, _ = station.active_installation(self.install_root)
            self.assertEqual(active['installation_id'], pending['installation_id'])
            self.assertEqual(config['station_id'], 'laser-' + winner_name)
        finally:
            for _, child, _, _ in children:
                if child.poll() is None:
                    child.kill()
                    child.communicate(timeout=5)

    def test_entry_scripts_parse_and_expose_separate_actions_without_admin(self):
        powershell = shutil.which('powershell.exe') or shutil.which('pwsh')
        if not powershell:
            self.skipTest('PowerShell parser unavailable')
        setup = ROOT / 'deployment/school-setup/Setup-LaserStation.ps1'
        launcher = ROOT / 'deployment/school-setup/Open-LaserStation.ps1'
        for path in (setup, launcher, setup.parent / 'Stop-LaserStation.ps1'):
            command = "$errors=$null; $tokens=$null; [void][Management.Automation.Language.Parser]::ParseFile('" + str(path).replace("'", "''") + "',[ref]$tokens,[ref]$errors); if($errors.Count){$errors | Out-String | Write-Output; exit 1}"
            result = subprocess.run([powershell, '-NoProfile', '-Command', command], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        source = setup.read_text(encoding='utf-8-sig')
        self.assertNotIn('-Verb RunAs', source)
        self.assertNotIn('LocalMachine', source)
        self.assertIn("X509Store('Root','CurrentUser')", source)
        self.assertIn('NoTrust', source)
        for action in ('Install', 'Update'):
            self.assertIn('-Action ' + action, (setup.parent / (action + '-LaserStation.cmd')).read_text())

    def test_wrapper_finishes_current_user_trust_and_both_shortcuts_with_mocked_windows(self):
        powershell = shutil.which('powershell.exe') or shutil.which('pwsh')
        if not powershell:
            self.skipTest('PowerShell unavailable')
        self.fresh()
        before = (self.install_root / 'active.json').read_bytes()
        active, version, config, connection = station.active_installation(self.install_root)
        info = self.root / 'info.json'
        info.write_text(json.dumps({'active': active, 'version': str(version), 'config': config, 'connection': connection}), encoding='utf-8')
        # The Python boundary was exercised above. Here only that executable
        # invocation and Windows COM/certificate store are replaced by mocks.
        source = (ROOT / 'deployment/school-setup/Setup-LaserStation.ps1').read_text(encoding='utf-8-sig')
        original = "    $infoText = & (Join-Path $program 'runtime\\python.exe') -B -m app.laser_station_install info --install-root $InstallPath"
        self.assertIn(original, source)
        source = source.replace(original, "    $infoText = Get-Content -LiteralPath '" + str(info).replace("'", "''") + "' -Raw; $global:LASTEXITCODE = 0")
        wrapper = self.root / 'Setup-Fixture.ps1'
        wrapper.write_text(source, encoding='utf-8-sig')
        harness = r'''
$global:saved = @(); $global:trust = @()
function New-Object {
  [CmdletBinding(DefaultParameterSetName='Type')]
  param([Parameter(Position=0,ParameterSetName='Type')][string]$TypeName,
        [Parameter(Position=1,ParameterSetName='Type')][object[]]$ArgumentList,
        [Parameter(ParameterSetName='Com')][string]$ComObject)
  if ($ComObject) {
    $shell = [pscustomobject]@{}
    $shell | Add-Member ScriptMethod CreateShortcut {
      param($path)
      $s = [pscustomobject]@{Path=$path;TargetPath='';Arguments='';WorkingDirectory=''}
      $s | Add-Member ScriptMethod Save { $global:saved += ($this.Path + '|' + $this.TargetPath + '|' + $this.Arguments) }
      return $s
    }
    return $shell
  }
  if ($TypeName -match 'X509Store$') {
    $global:trust += ($ArgumentList -join ',')
    $store = [pscustomobject]@{}
    $store | Add-Member ScriptMethod Open { param($mode) }
    $store | Add-Member ScriptMethod Add { param($certificate) if ($global:failTrust) { throw 'mock trust failure' }; $global:trust += 'added-public-ca' }
    $store | Add-Member ScriptMethod Close { }
    return $store
  }
  Microsoft.PowerShell.Utility\New-Object @PSBoundParameters
}
$global:failTrust = __FAIL__
try { & '__WRAPPER__' -Action Update -FinishSetup -PackageRoot '__PACKAGE__' -InstallPath '__INSTALL__' -NoStart } catch { Write-Output ('ERROR=' + $_.Exception.Message) }
Write-Output ('TRUST=' + ($global:trust -join '|'))
Write-Output ('SHORTCUTS=' + ($global:saved -join '|'))
'''
        for failure in (False, True):
            script = harness.replace('__FAIL__', '$true' if failure else '$false')
            for key, value in (('__WRAPPER__', wrapper), ('__PACKAGE__', self.package), ('__INSTALL__', self.install_root)):
                script = script.replace(key, str(value).replace("'", "''"))
            # A file keeps Unicode paths/messages correct with Windows PS 5.1.
            command = self.root / ('mock-wrapper-' + str(failure) + '.ps1')
            command.write_text(script, encoding='utf-8-sig')
            result = subprocess.run([powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(command)],
                                    capture_output=True, text=True, timeout=25)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('Root,CurrentUser', result.stdout)
            if failure:
                self.assertIn('Programmet er installeret', result.stdout)
                self.assertIn('-FinishSetup', result.stdout)
            else:
                self.assertNotIn('ERROR=', result.stdout)
                self.assertIn('Laserstation.lnk', result.stdout)
                self.assertIn('Stop laserstation.lnk', result.stdout)
                self.assertIn('https://192.0.2.50:8780/kontrolpanel', result.stdout)
            self.assertEqual(before, (self.install_root / 'active.json').read_bytes())

    def test_shortcut_to_retained_launcher_resolves_new_active_runtime(self):
        powershell = shutil.which('powershell.exe') or shutil.which('pwsh')
        if not powershell:
            self.skipTest('PowerShell unavailable')
        self.fresh()
        old_version = station.active_installation(self.install_root)[1]
        self.update()
        _, current, config, _ = station.active_installation(self.install_root)
        old_launcher = old_version / 'program' / station.LAUNCHER
        shutil.copy2(ROOT / station.LAUNCHER, old_launcher)
        script = r'''
$global:requests = 0; $global:launched = ''
function Invoke-RestMethod {
  param($Uri, $TimeoutSec)
  $global:requests++
  if ($global:requests -eq 1) { throw 'not running yet' }
  [pscustomobject]@{server='__SERVER__';stationId='__ID__'}
}
function Start-Process {
  param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,[switch]$PassThru,$RedirectStandardOutput,$RedirectStandardError)
  $global:launched = $FilePath + '|' + $ArgumentList + '|' + $WorkingDirectory
  [pscustomobject]@{HasExited=$false}
}
try { & '__LAUNCHER__' -InstallPath '__INSTALL__' -NoBrowser } catch { Write-Output ('ERROR=' + $_.Exception.Message) }
Write-Output ('LAUNCHED=' + $global:launched)
'''
        for key, value in (('__SERVER__', config['server']), ('__ID__', config['station_id']),
                           ('__LAUNCHER__', old_launcher), ('__INSTALL__', self.install_root)):
            script = script.replace(key, str(value).replace("'", "''"))
        harness = self.root / 'old-shortcut-test.ps1'
        harness.write_text(script, encoding='utf-8-sig')
        result = subprocess.run([powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(harness)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('ERROR=', result.stdout)
        self.assertIn(str(current / 'program/runtime/python.exe'), result.stdout)
        self.assertIn('--install-root "' + str(self.install_root) + '"', result.stdout)

    def start_real_station(self, *, hold_request=False):
        # Use real station sources and our existing test interpreter. No bundled
        # runtime/build, queue/server connection, Beam Studio or FLUX is started.
        for name in station.APP_FILES + station.WEB_FILES + (station.LAUNCHER, station.STOP_LAUNCHER):
            target = self.package / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        self.seal()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        self.fresh(port=port)
        active, version, _, _ = station.active_installation(self.install_root)
        entered = self.root / 'request-entered'
        release = self.root / 'request-release'
        code = '''import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from app import laser_station, laser_station_install
if sys.argv[3] == 'hold':
    original = laser_station.Handler.do_GET
    def held_get(self):
        if self.path == '/test/held':
            Path(sys.argv[4]).write_text('request active', encoding='ascii')
            while not Path(sys.argv[5]).exists(): time.sleep(0.02)
            return self.reply(200, {'finished': True})
        return original(self)
    laser_station.Handler.do_GET = held_get
laser_station_install.run_station(Path(sys.argv[2]))
'''
        env = os.environ.copy()
        env['LOCALAPPDATA'] = str(self.root / 'isolated-localappdata')
        child = subprocess.Popen([sys.executable, '-I', '-B', '-c', code, str(version / 'program'), str(self.install_root),
                                  'hold' if hold_request else 'normal', str(entered), str(release)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        def cleanup():
            if child.poll() is None:
                release.write_bytes(b'release held test request')
                try:
                    station.stop_station(self.install_root, timeout=3)
                except (ValueError, OSError, RuntimeError):
                    # Only this test-owned subprocess may be reclaimed by the
                    # harness after a failed assertion, never by product Stop.
                    child.kill()
                child.communicate(timeout=5)
        self.addCleanup(cleanup)
        base = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if child.poll() is not None:
                self.fail('Real station exited before readiness: ' + str(child.communicate(timeout=2)))
            try:
                with urllib.request.urlopen(base + '/api/station/state', timeout=.5) as response:
                    if response.status == 200:
                        return child, base, entered, release
            except OSError:
                time.sleep(.03)
        self.fail('Real station failed to become ready')

    def test_real_station_stops_own_instance_then_update_preserves_plate(self):
        child, _, _, _ = self.start_real_station()
        descriptor = station.running_descriptor(self.install_root)
        sentinel = self.plates / 'preserved.bvg'
        sentinel.write_bytes(b'teacher plate must survive stop/update')
        with self.assertRaisesRegex(RuntimeError, 'kører allerede'):
            self.update()
        result = station.stop_station(self.install_root, expected_nonce=descriptor['nonce'], timeout=5)
        output, error = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 0, output + error)
        self.assertTrue(result['stopped'])
        self.assertFalse((self.install_root / 'running.json').exists())
        self.assertFalse((self.install_root / ('stop-' + descriptor['nonce'] + '.json')).exists())
        self.update()
        self.assertEqual(sentinel.read_bytes(), b'teacher plate must survive stop/update')

    def test_running_descriptor_disappearance_is_normal_during_stop_wait(self):
        self.fresh()
        active = station.active_record(self.install_root)
        descriptor = {'format': 1, 'kind': station.KIND + '-running', 'root': str(self.install_root),
                      'installation_id': active['installation_id'], 'version_directory': active['version_directory'],
                      'nonce': 'd' * 32}
        station.atomic_json(self.install_root / 'running.json', descriptor)
        lease = InstanceLock(self.install_root, filename='station.lock')
        self.addCleanup(lambda: lease.file.close() if not lease.file.closed else None)
        original = station.running_descriptor
        calls = 0
        def disappearing(root):
            nonlocal calls
            calls += 1
            if calls == 3:
                (self.install_root / 'running.json').unlink()
                lease.close()
                raise FileNotFoundError('normal owner shutdown between stat and open')
            return original(root)
        with patch.object(station, 'running_descriptor', side_effect=disappearing):
            result = station.stop_station(self.install_root, timeout=1)
        self.assertTrue(result['stopped'])

    def test_stop_does_not_depend_on_network_ca_after_process_started(self):
        child, _, _, _ = self.start_real_station()
        with patch.object(station, 'validate_connection', side_effect=ValueError('expired public CA')):
            result = station.stop_station(self.install_root, timeout=5)
        self.assertTrue(result['stopped'])
        child.communicate(timeout=5)

    def test_idle_and_incomplete_http_sockets_do_not_prevent_graceful_stop(self):
        child, base, _, _ = self.start_real_station()
        port = int(base.rsplit(':', 1)[1])
        idle = socket.create_connection(('127.0.0.1', port), timeout=1)
        partial_header = socket.create_connection(('127.0.0.1', port), timeout=1)
        partial_body = socket.create_connection(('127.0.0.1', port), timeout=1)
        try:
            partial_header.sendall(b'GET /api/station/state HTTP/1.1\r\nHost: ')
            partial_body.sendall((f'POST /api/station/login HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
                                  f'Origin: http://127.0.0.1:{port}\r\nContent-Type: application/json\r\n'
                                  'Content-Length: 100\r\n\r\n{').encode('ascii'))
            started = time.monotonic()
            result = station.stop_station(self.install_root, timeout=6)
            output, error = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0, output + error)
            self.assertTrue(result['stopped'])
            self.assertLess(time.monotonic() - started, 6)
        finally:
            for connection in (idle, partial_header, partial_body):
                connection.close()

    def test_stale_nonce_and_other_station_control_are_refused_without_stopping(self):
        child, base, _, _ = self.start_real_station()
        descriptor = station.running_descriptor(self.install_root)
        with self.assertRaisesRegex(ValueError, 'matcher ikke'):
            station.stop_station(self.install_root, expected_nonce='0' * 32, timeout=.2)
        stale_path, stale_request = station.stop_request(self.install_root, {**descriptor, 'nonce': '0' * 32})
        station.atomic_json(stale_path, stale_request)
        wrong_root = self.root / 'another-station'
        wrong_plates = self.root / 'another-plates'
        station.install(self.package, wrong_root, action='Install', connection_file=self.connection, plates=wrong_plates)
        station.atomic_json(wrong_root / 'running.json', descriptor)
        with self.assertRaisesRegex(ValueError, 'tilhører ikke'):
            station.stop_station(wrong_root, timeout=.2)
        time.sleep(.15)
        self.assertIsNone(child.poll())
        with urllib.request.urlopen(base + '/api/station/state', timeout=1) as response:
            self.assertEqual(response.status, 200)
        station.stop_station(self.install_root, timeout=5)
        child.communicate(timeout=5)
        self.assertTrue(stale_path.exists(), 'A request with another nonce is not cleaned up as our own')

    def test_stop_timeout_has_no_success_and_does_not_release_another_owner(self):
        self.fresh()
        active = station.active_installation(self.install_root)[0]
        descriptor = {'format': 1, 'kind': station.KIND + '-running', 'root': str(self.install_root),
                      'installation_id': active['installation_id'], 'version_directory': active['version_directory'],
                      'nonce': 'c' * 32}
        station.atomic_json(self.install_root / 'running.json', descriptor)
        with InstanceLock(self.install_root, filename='station.lock'):
            with self.assertRaisesRegex(TimeoutError, 'endnu ikke stoppet'):
                station.stop_station(self.install_root, timeout=.1)
            self.assertEqual(station.running_descriptor(self.install_root), descriptor)
        with self.assertRaisesRegex(ValueError, 'forældet'):
            station.stop_station(self.install_root, timeout=.1)

    def test_real_stop_drains_existing_request_before_reporting_success(self):
        child, base, entered, release = self.start_real_station(hold_request=True)
        request_code = 'import urllib.request,sys; print(urllib.request.urlopen(sys.argv[1], timeout=8).read())'
        request = subprocess.Popen([sys.executable, '-I', '-B', '-c', request_code, base + '/test/held'],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not entered.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(entered.exists())
            with self.assertRaisesRegex(TimeoutError, 'endnu ikke stoppet'):
                station.stop_station(self.install_root, timeout=.25)
            self.assertIsNone(child.poll(), 'Outstanding request must keep lifetime lease')
            release.write_bytes(b'finish existing request')
            result = station.stop_station(self.install_root, timeout=5)
            self.assertTrue(result['stopped'])
            child.communicate(timeout=5)
            output, error = request.communicate(timeout=5)
            self.assertEqual(request.returncode, 0, error)
            self.assertIn('finished', output)
        finally:
            release.write_bytes(b'finish test request')
            if request.poll() is None:
                request.kill()
                request.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
