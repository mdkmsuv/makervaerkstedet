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
             'GUARD_SCRIPT': str(ROOT / 'windows-elevhub' / 'server' / 'school-uninstall-guards.ps1')}
      result = subprocess.run([powershell, '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(harness_path)], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30, env=env)
      self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
      self.assertIn('Setup firewall host-form /32 branch passed.', result.stdout)

  def test_controlpanel_shortcut_carries_teacher_port(self):
    text = (SETUP / 'Setup-School.ps1').read_text(encoding='utf-8')
    self.assertIn("Arguments=('-Port {0} -Address {1}' -f $TeacherPort,$teacherAddress)", text)
    self.assertIn('$shortcut.Arguments', text)

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
      source = (ROOT / 'app' / 'server.py').read_text(encoding='utf-8')
      target.write_text(source, encoding='utf-8')
      builder.neutralize_teacher_code(target)
      self.assertEqual(target.read_text(encoding='utf-8'), source)

if __name__ == '__main__':
  unittest.main()
