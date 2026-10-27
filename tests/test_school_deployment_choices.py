"""Exercise installer choices without elevation, certificate or OS mutations."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / 'deployment/school-setup'


class DeploymentChoicesTests(unittest.TestCase):
    def run_ps(self, script, *args):
        powershell = shutil.which('powershell.exe')
        if not powershell:
            self.skipTest('Windows PowerShell required for installer integration')
        return subprocess.run([powershell, '-NoLogo', '-NoProfile', '-NonInteractive',
                               '-ExecutionPolicy', 'Bypass', '-File', str(script), *args],
                              capture_output=True, text=True, errors='replace', timeout=30)

    def test_choices_dispatch_to_one_central_server_or_station_only(self):
        cases = [('single-computer', 'server', 'Setup-School.ps1', {'Role': 'makerspace'}),
                 ('separate-computers', 'server', 'Setup-School.ps1', {'Role': 'vm', 'RemoteLaserStation': True}),
                 ('separate-computers', 'laser-station', 'Setup-LaserStation.ps1', {'Action': 'Install'})]
        with tempfile.TemporaryDirectory() as folder:
            for deployment, component, entry, expected in cases:
                result = self.run_ps(SETUP / 'Choose-Setup.ps1', '-Deployment', deployment,
                                     '-Component', component, '-PackageRoot', folder, '-PlanOnly')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                value = json.loads(result.stdout)
                self.assertEqual(value['entry'], entry)
                self.assertEqual(value['parameters'], {'PackageRoot': folder, **expected})
                self.assertEqual(list(Path(folder).iterdir()), [])
            result = self.run_ps(SETUP / 'Choose-Setup.ps1', '-Deployment', 'single-computer',
                                 '-Component', 'laser-station', '-PackageRoot', folder, '-PlanOnly')
            self.assertNotEqual(result.returncode, 0)

    def test_package_stages_both_choices_and_station_update_without_runtime_copy(self):
        from scripts.build_school_package import copy_setup_entrypoints, SETUP_FILES
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            copy_setup_entrypoints(target)
            for name in SETUP_FILES:
                self.assertEqual((target / 'deployment/school-setup' / name).read_bytes(),
                                 (SETUP / name).read_bytes())
            for launcher in ('START', 'Install-LaserStation', 'Update-LaserStation', 'Stop-LaserStation'):
                self.assertIn('%*', (target / (launcher + '.cmd')).read_text())
            from scripts.package_documentation import copy_documentation
            copy_documentation(target)
            readme = (target / 'README.md').read_text(encoding='utf-8')
            self.assertIn('(docs/TO-OPSAETNINGER.md)', readme)
            self.assertIn('(deployment/school-setup/README.md)', readme)
            result = self.run_ps(target / 'deployment/school-setup/Choose-Setup.ps1',
                                 '-Deployment', 'separate-computers', '-Component', 'laser-station', '-PlanOnly')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            plan = json.loads(result.stdout)
            self.assertEqual(plan['parameters'], {'Action': 'Install', 'PackageRoot': folder})
            self.assertTrue((target / 'deployment/school-setup' / plan['entry']).is_file())
            result = self.run_ps(SETUP / 'Choose-Setup.ps1', '-Deployment', 'separate-computers', '-PlanOnly')
            self.assertNotEqual(result.returncode, 0)

    def test_public_station_connection_and_listener_choices(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'choices.ps1'
            script.write_text('''param($Helper)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
. $Helper
$single = Get-SchoolTeacherAddress 'makerspace' '192.0.2.10' $false
$legacy = Get-SchoolTeacherAddress 'vm' '192.0.2.10' $false
$split = Get-SchoolTeacherAddress 'vm' '192.0.2.10' $true
$invalid = $false
try { Get-SchoolTeacherAddress 'makerspace' '192.0.2.10' $true } catch { $invalid=$true }
$ca = "-----BEGIN CERTIFICATE-----`nfixture-public-certificate`n-----END CERTIFICATE-----"
$connection = New-LaserStationConnection 'Test school' '192.0.2.10' 18780 $ca 'fhexa1'
$badCa=$false; $badAddress=$false
try { New-LaserStationConnection 'Test' '192.0.2.10' 18780 'PRIVATE KEY' 'fbb2' } catch {$badCa=$true}
try { New-LaserStationConnection 'Test' '127.0.0.1' 18780 $ca 'fbb2' } catch {$badAddress=$true}
@{single=$single;legacy=$legacy;split=$split;invalid=$invalid;badCa=$badCa;badAddress=$badAddress;connection=$connection} | ConvertTo-Json -Depth 5 -Compress
''', encoding='utf-8-sig')
            result = self.run_ps(script, '-Helper', str(SETUP / 'school-layout.ps1'))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data['single'], '127.0.0.1')
            self.assertEqual(data['legacy'], '127.0.0.1')
            self.assertEqual(data['split'], '192.0.2.10')
            self.assertTrue(data['invalid'] and data['badCa'] and data['badAddress'])
            self.assertEqual(data['connection']['server_url'], 'https://192.0.2.10:18780')
            self.assertEqual(data['connection']['station_id'], 'laser-fhexa1')
            self.assertEqual(set(data['connection']), {'schemaVersion', 'kind', 'school_name',
                                                      'server_url', 'caCertificatePem', 'station_id', 'machine'})

    def test_controlpanel_uses_owned_remote_server_address_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data, program = root / 'data/ElevhubServer', root / 'program/ElevhubServer'
            data.mkdir(parents=True); program.mkdir(parents=True)
            owner = {'schemaVersion': 1, 'product': 'ElevhubServer', 'state': 'installed',
                     'installRoot': str(program), 'dataRoot': str(data),
                     'teacherAddress': '192.0.2.10', 'roleIp': '192.0.2.10', 'teacherPort': 18780}
            owner_path = data / 'installation-owner.json'
            harness = root / 'panel.ps1'
            harness.write_text('''param($ProgramLocation,$DataLocation,$Entry)
$env:ProgramFiles=$ProgramLocation; $env:ProgramData=$DataLocation
& $Entry -ResolveOnly
''', encoding='utf-8-sig')
            args = ('-ProgramLocation', str(program.parent), '-DataLocation', str(data.parent),
                    '-Entry', str(SETUP / 'Open-ControlPanel.ps1'))
            owner_path.write_text(json.dumps(owner), encoding='utf-8')
            result = self.run_ps(harness, *args)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), 'https://192.0.2.10:18780/kontrolpanel')
            owner['teacherAddress'] = '192.0.2.99'
            owner_path.write_text(json.dumps(owner), encoding='utf-8')
            self.assertNotEqual(self.run_ps(harness, *args).returncode, 0)

    def test_server_export_is_accepted_by_the_separate_station(self):
        from datetime import datetime, timedelta, timezone
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        from app.laser_station_install import validate_connection
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Isolated export test')])
        now = datetime.now(timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                       .public_key(key.public_key()).serial_number(x509.random_serial_number())
                       .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
                       .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                       .sign(key, hashes.SHA256()))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ca = root / 'public-ca.pem'
            ca.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
            script = root / 'export.ps1'
            script.write_text('''param($Helper,$Certificate)
$ErrorActionPreference='Stop'
. $Helper
$value=New-LaserStationConnection 'Isolated school' '192.0.2.10' 18780 (Get-Content -LiteralPath $Certificate -Raw -Encoding ASCII) 'fhexa1'
$value|ConvertTo-Json -Depth 5 -Compress
''', encoding='utf-8-sig')
            result = self.run_ps(script, '-Helper', str(SETUP / 'school-layout.ps1'), '-Certificate', str(ca))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            connection = validate_connection(json.loads(result.stdout))
            self.assertEqual(connection['server_url'], 'https://192.0.2.10:18780')
            self.assertEqual(connection['station_id'], 'laser-fhexa1')
            self.assertEqual(connection['caCertificatePem'], ca.read_text(encoding='ascii'))
            self.assertNotIn('PRIVATE KEY', result.stdout)

    def test_resume_and_recovery_keep_split_station_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'resume.ps1'
            script.write_text('''param($Helper)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
. $Helper
$legacy=[pscustomobject]@{role='vm'}
$split=[pscustomobject]@{role='vm';remote_laser_station=$true;laser_machine='fhexa1';laser_station_id='laser-fhexa1'}
Assert-SchoolLaserProfile $legacy $false 'fbb2'
Assert-SchoolLaserProfile $split $true 'fhexa1'
$wrongModel=$false; $changedLayout=$false; $missingIdentity=$false
try {Assert-SchoolLaserProfile $split $true 'fbb2'} catch {$wrongModel=$true}
try {Assert-SchoolLaserProfile $split $false 'fhexa1'} catch {$changedLayout=$true}
try {Assert-SchoolLaserProfile $legacy $true 'fbb2'} catch {$missingIdentity=$true}
@{wrongModel=$wrongModel;changedLayout=$changedLayout;missingIdentity=$missingIdentity}|ConvertTo-Json -Compress
''', encoding='utf-8-sig')
            result = self.run_ps(script, '-Helper', str(SETUP / 'school-layout.ps1'))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout), {'wrongModel': True, 'changedLayout': True,
                                                       'missingIdentity': True})


if __name__ == '__main__':
    unittest.main()
