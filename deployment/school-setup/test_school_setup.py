import json
import ast
import importlib.util
import os
import tempfile
import unittest
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / 'deployment' / 'school-setup'

class SchoolSetupTests(unittest.TestCase):
  def powershell(self, script, *arguments, env=None):
    executable = shutil.which('powershell.exe')
    if not executable:
      self.skipTest('Windows PowerShell is required for the setup contract')
    return subprocess.run([executable, '-NoProfile', '-ExecutionPolicy', 'Bypass',
                           '-File', str(script), *arguments], capture_output=True,
                          text=True, env=env, timeout=20)

  def test_unattended_and_port_parameter_validation(self):
    # Execute the production parameter block and preflight statements in an
    # isolated script: no administrator, installer, scheduled task or firewall.
    harness = SETUP / 'test_school_setup_params.ps1'
    result = self.powershell(harness)
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

  def test_controlpanel_resolves_installed_teacher_port(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      data = root / 'data' / 'ElevhubServer'
      installed = root / 'program' / 'ElevhubServer'
      data.mkdir(parents=True); installed.mkdir(parents=True)
      owner = {'schemaVersion': 1, 'product': 'ElevhubServer', 'state': 'installed',
               'installRoot': str(installed), 'dataRoot': str(data),
               'teacherAddress': '127.0.0.1', 'teacherPort': 18780}
      path = data / 'installation-owner.json'
      path.write_text(json.dumps(owner), encoding='utf-8')
      # Windows PowerShell reconstructs ProgramFiles at process startup, so set
      # the isolated roots inside its test harness before resolving the URL.
      harness = root / 'resolve.ps1'
      harness.write_text("param($ProgramLocation,$DataLocation,$Entry)\n"
                          "$ErrorActionPreference='Stop'\n"
                          "$env:ProgramFiles=$ProgramLocation; $env:ProgramData=$DataLocation\n"
                         "& $Entry -ResolveOnly\n", encoding='utf-8')
      arguments = ('-ProgramLocation', str(installed.parent), '-DataLocation', str(data.parent),
                   '-Entry', str(SETUP / 'Open-ControlPanel.ps1'))
      result = self.powershell(harness, *arguments)
      self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
      self.assertEqual(result.stdout.strip(), 'https://127.0.0.1:18780/kontrolpanel')
      owner['state'] = 'removed'
      path.write_text(json.dumps(owner), encoding='utf-8')
      result = self.powershell(harness, *arguments)
      self.assertNotEqual(result.returncode, 0)
      owner['state'] = 'installed'; owner['teacherPort'] = 0
      path.write_text(json.dumps(owner), encoding='utf-8')
      result = self.powershell(harness, *arguments)
      self.assertNotEqual(result.returncode, 0)

  def test_controlpanel_explicit_port_does_not_need_owner(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      harness = root / 'resolve-explicit.ps1'
      harness.write_text("param($ProgramLocation,$DataLocation,$Entry,$Port)\n"
                         "$ErrorActionPreference='Stop'\n"
                         "$env:ProgramFiles=$ProgramLocation; $env:ProgramData=$DataLocation\n"
                         "& $Entry -ResolveOnly -Port $Port\n", encoding='utf-8')
      arguments = ('-ProgramLocation', str(root / 'missing-program'),
                   '-DataLocation', str(root / 'missing-data'),
                   '-Entry', str(SETUP / 'Open-ControlPanel.ps1'),
                   '-Port', '18780')
      result = self.powershell(harness, *arguments)
      self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
      self.assertEqual(result.stdout.strip(), 'https://127.0.0.1:18780/kontrolpanel')
      for invalid in ('0', '65536'):
        bad_arguments = arguments[:-1] + ('-Port', invalid)
        result = self.powershell(harness, *bad_arguments)
        self.assertNotEqual(result.returncode, 0)

  def test_setup_has_neutral_contract(self):
    text = (SETUP / 'Setup-School.ps1').read_text(encoding='utf-8')
    self.assertIn('StudentSubnet', text); self.assertIn('admin-ssh', text)
    self.assertIn('school.json', text); self.assertIn('8774', text)
    self.assertIn("$Role -eq 'makerspace'", text)
    # Network parameters must be provided by the installing school.
    # Check the contract without retaining another school's old addresses.
    for parameter in ('RoleIp', 'StudentSubnet'):
      self.assertRegex(text, rf'(?m)^\s*\[string\]\${parameter},\s*$')

  def test_existing_enrollment_firewall_accepts_windows_host_form_for_32(self):
    # Evaluate the production existing-rule branch only.  The fixture has no
    # administrator, task, firewall or file side effects.
    harness = r'''$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:SETUP_SCRIPT,[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors | Out-String)}
$nodes=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.IfStatementAst] -and
    $n.Extent.Text.TrimStart() -match '^if \(\$existingRule\)'},$true))
if($nodes.Count -ne 1){throw "Expected one existing enrollment-firewall branch, found $($nodes.Count)."}
. $env:GUARD_SCRIPT
$RoleIp='192.0.2.1'; $StudentSubnet='192.0.2.1/32'
$installedPython='C:\Program Files\ElevhubServer\runtime\python.exe'
$existingRule=[pscustomobject]@{Description='ElevhubSchoolEnrollment';Direction='Inbound';Action='Allow';Enabled='True';Profile='Any'}
function Get-NetFirewallPortFilter { [pscustomobject]@{Protocol='TCP';LocalPort='8774'} }
function Get-NetFirewallAddressFilter { [pscustomobject]@{LocalAddress=@('192.0.2.1');RemoteAddress=@('192.0.2.1')} }
function Get-NetFirewallApplicationFilter { [pscustomobject]@{Program=$installedPython} }
Invoke-Expression $nodes[0].Extent.Text
Write-Output 'Setup firewall host-form /32 branch passed.'
'''
    powershell = shutil.which('powershell.exe') or shutil.which('pwsh')
    if not powershell:
      self.skipTest('PowerShell is required for the setup firewall branch contract')
    with tempfile.TemporaryDirectory() as temporary:
      harness_path = Path(temporary) / 'setup-firewall-branch.ps1'
      harness_path.write_text(harness, encoding='utf-8')
      env = {**os.environ, 'SETUP_SCRIPT': str(SETUP / 'Setup-School.ps1'),
             'GUARD_SCRIPT': str(ROOT / 'system' / 'windows-elevhub' / 'server' / 'school-uninstall-guards.ps1')}
      result = subprocess.run([powershell, '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(harness_path)], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30, env=env)
      self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
      self.assertIn('Setup firewall host-form /32 branch passed.', result.stdout)

  def test_controlpanel_shortcut_carries_teacher_port(self):
    text = (SETUP / 'Setup-School.ps1').read_text(encoding='utf-8')
    self.assertIn("Arguments=('-Port {0} -Address {1}' -f $TeacherPort,$teacherAddress)", text)
    self.assertIn('$shortcut.Arguments', text)

  def test_laser_connection_export_has_matching_ca_reference_and_preserves_exports(self):
    from datetime import datetime, timedelta, timezone
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Synthetic school CA')])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                   .public_key(key.public_key()).serial_number(1)
                   .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
                   .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                   .sign(key, hashes.SHA256()))
    fingerprint = certificate.fingerprint(hashes.SHA1()).hex().upper()
    harness_text = r'''param($Source,$Layout,$Root)
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors | Out-String)}
$function=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Export-LaserConnectionReference'},$true)
Invoke-Expression $function.Extent.Text
$branches=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.IfStatementAst] -and $n.Extent.Text.StartsWith('if ($RemoteLaserStation)') -and $n.Extent.Text.Contains('Export-LaserConnectionReference')},$true))
if($branches.Count -ne 1){throw 'Expected one remote laser export branch'}
. $Layout
$RemoteLaserStation=$true; $SchoolName='Synthetic school'; $RoleIp='192.0.2.10'; $TeacherPort=8780; $Machine='fbb2'
$security=Join-Path $Root 'security'; $publicDir=Join-Path $Root 'exports'
Invoke-Expression $branches[0].Extent.Text
Invoke-Expression $branches[0].Extent.Text
'''
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      security = root / 'security'; security.mkdir()
      exports = root / 'exports'; exports.mkdir()
      previous = exports / 'laser-station-connection.json'
      previous.write_bytes(b'previous school export')
      previous_reference = previous.with_suffix('.fingerprint.txt')
      previous_reference.write_bytes(b'previous school reference')
      ca = security / 'ca.crt'
      pem = certificate.public_bytes(serialization.Encoding.PEM)
      ca.write_bytes(pem)
      private = security / 'ca.key'; private.write_bytes(b'private sentinel - never read or exported')
      harness = root / 'export.ps1'; harness.write_text(harness_text, encoding='utf-8-sig')
      result = self.powershell(harness, str(SETUP / 'Setup-School.ps1'), str(SETUP / 'school-layout.ps1'), str(root))
      self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
      self.assertEqual(result.stdout.count('MKV_LASER_CA_SHA1=' + fingerprint), 2)
      self.assertEqual(previous.read_bytes(), b'previous school export')
      self.assertEqual(previous_reference.read_bytes(), b'previous school reference')
      connections = [p for p in exports.glob('*.json') if p != previous]
      self.assertEqual(len(connections), 2)
      expected = {'schemaVersion': 1, 'kind': 'makervaerkstedet-laserstation-connection',
                  'school_name': 'Synthetic school', 'server_url': 'https://192.0.2.10:8780',
                  'caCertificatePem': pem.decode('ascii'), 'station_id': 'laser-fbb2', 'machine': 'fbb2'}
      for connection in connections:
        self.assertEqual(json.loads(connection.read_text(encoding='utf-8')), expected)
        reference = connection.with_suffix('.fingerprint.txt').read_text(encoding='utf-8')
        self.assertIn(connection.name, reference)
        self.assertIn('SHA-1: ' + fingerprint, reference)
        self.assertNotIn('PRIVATE KEY', reference)
      self.assertEqual(ca.read_bytes(), pem)
      self.assertEqual(private.read_bytes(), b'private sentinel - never read or exported')

  def test_all_in_one_stop_shortcut_uses_normal_user_entry(self):
    text = (SETUP / 'Setup-School.ps1').read_text(encoding='utf-8')
    self.assertIn("Desktop\\Stop laserstation.lnk", text)
    self.assertIn("deployment\\school-setup\\Stop-LaserStation.ps1", text)
    self.assertIn('-Root "{1}" -ConfigPath "{2}"', text)

  def test_owned_setup_receipt_stays_preparing_on_failure_and_resumes_atomically(self):
    harness_text = r'''param($Source,$Root)
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors | Out-String)}
foreach($name in @('Get-SchoolSetupReceipt','Assert-SchoolSetupChoices','Set-SchoolSetupState')){
 $fn=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$true)
 Invoke-Expression $fn.Extent.Text
}
$path=Join-Path $Root 'school-setup-state.json'
$choices=[ordered]@{school_name='Synthetic school';role='makerspace';server='192.0.2.10'}
Set-SchoolSetupState $path 'fixture-owner' preparing $choices
# An exception in any intervening export/shortcut step never reaches complete.
try{throw 'synthetic export failure'; Set-SchoolSetupState $path 'fixture-owner' complete $choices}catch{}
$failedState=(Get-SchoolSetupReceipt $path 'fixture-owner').state
$before=[IO.File]::ReadAllBytes($path)
$changed=[ordered]@{school_name='Different school';role='makerspace';server='192.0.2.10'}
$changedRejected=$false
try{Set-SchoolSetupState $path 'fixture-owner' preparing $changed}catch{$changedRejected=$true}
if([Convert]::ToBase64String([IO.File]::ReadAllBytes($path)) -cne [Convert]::ToBase64String($before)){throw 'Changed choices overwrote receipt'}
Set-SchoolSetupState $path 'fixture-owner' preparing $choices
Set-SchoolSetupState $path 'fixture-owner' complete $choices
$complete=Get-SchoolSetupReceipt $path 'fixture-owner'
$foreignRejected=$false; $before=[IO.File]::ReadAllBytes($path)
try{Set-SchoolSetupState $path 'different-owner' preparing $choices}catch{$foreignRejected=$true}
if([Convert]::ToBase64String([IO.File]::ReadAllBytes($path)) -cne [Convert]::ToBase64String($before)){throw 'Foreign owner overwrote receipt'}
$invalidPath=Join-Path $Root 'invalid.json'; [IO.File]::WriteAllText($invalidPath,'{}')
$invalidRejected=$false
try{Set-SchoolSetupState $invalidPath 'fixture-owner' preparing $choices}catch{$invalidRejected=$true}
if([IO.File]::ReadAllText($invalidPath) -cne '{}'){throw 'Invalid receipt was overwritten'}
@{failedState=$failedState;resumedState=$complete.state;changedRejected=$changedRejected;foreignRejected=$foreignRejected;invalidRejected=$invalidRejected;temporaryFiles=@(Get-ChildItem $Root -Filter '*.tmp').Count}|ConvertTo-Json -Compress
'''
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      harness = root / 'receipt.ps1'; harness.write_text(harness_text, encoding='utf-8-sig')
      result = self.powershell(harness, str(SETUP / 'Setup-School.ps1'), str(root))
      self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
      self.assertEqual(json.loads(result.stdout), {'failedState': 'preparing', 'resumedState': 'complete',
                       'changedRejected': True, 'foreignRejected': True, 'invalidRejected': True, 'temporaryFiles': 0})
      source = (SETUP / 'Setup-School.ps1').read_text(encoding='utf-8')
      preparing = source.index('Set-SchoolSetupState $setupReceiptPath ([string]$owner.ownerId) preparing')
      complete = source.index('Set-SchoolSetupState $setupReceiptPath ([string]$owner.ownerId) complete')
      self.assertLess(source.index('Assert-SchoolSetupChoices $setupReceipt $setupChoices'), preparing)
      self.assertLess(source.index('if ($CheckOnly)'), preparing)
      self.assertLess(preparing, source.index('$privateKey = Join-Path'))
      self.assertLess(source.index('Export-LaserConnectionReference $laserConnectionValue'), complete)
      self.assertLess(source.rindex('$shortcut.Save()'), complete)

  def test_builder_declares_no_secrets(self):
    marker = (ROOT / 'scripts' / 'build_school_package.py').read_text(encoding='utf-8')
    self.assertIn('containsCredentials', marker); self.assertIn('False', marker)
    self.assertIn('privateKeyGeneratedOnInstall', marker)

  def test_start_is_noninteractive_launcher(self):
    self.assertIn('Choose-Setup.ps1', (SETUP / 'Start.cmd').read_text(encoding='utf-8'))

  def test_builder_neutralizes_teacher_code(self):
    spec = importlib.util.spec_from_file_location('school_builder', ROOT / 'scripts' / 'build_school_package.py')
    builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)
    with tempfile.TemporaryDirectory() as temporary:
      target = Path(temporary) / 'server.py'
      target.write_text("TEACHER_CODE = 'old-example-code'\n", encoding='utf-8')
      builder.neutralize_teacher_code(target)
      self.assertEqual(target.read_text(encoding='utf-8'), 'TEACHER_CODE = None\n')
      # The current implementation generates a code on first start and preserves
      # existing local codes. Packaging must leave that behavior intact.
      source = (ROOT / 'system' / 'app' / 'server.py').read_text(encoding='utf-8')
      target.write_text(source, encoding='utf-8')
      builder.neutralize_teacher_code(target)
      self.assertEqual(target.read_text(encoding='utf-8'), source)

if __name__ == '__main__':
  unittest.main()
