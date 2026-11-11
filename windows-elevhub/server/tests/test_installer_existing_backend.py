import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest


INSTALL = Path(__file__).parents[1] / "install.ps1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")


class _HealthHandler(BaseHTTPRequestHandler):
    payload = None
    status = 200
    redirect = False
    delay = 0

    def do_GET(self):  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path != "/api/health":
            self.send_error(404)
            return
        if self.redirect:
            self.send_response(302)
            self.send_header("Location", "/api/health")
            self.end_headers()
            return
        if self.delay:
            time.sleep(self.delay)
        body = json.dumps(self.payload).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            if not self.delay:
                raise
            # The timeout fixture deliberately closes its connection first.

    def log_message(self, *_args):
        pass


@unittest.skipUnless(POWERSHELL, "PowerShell is required for the Windows installer test")
class ExistingPrintBackendInstallerTest(unittest.TestCase):
    @staticmethod
    def _invoke(*, payload=None, redirect=False, delay=0):
        handler = type("HealthHandler", (_HealthHandler,), {
            "payload": payload,
            "redirect": redirect,
            "delay": delay,
        })
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        script = r'''
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$path = $env:INSTALL_SCRIPT
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ('PowerShell syntax errors: ' + (($errors | ForEach-Object Message) -join '; ')) }
$function = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Assert-ExistingPrintBackend' }, $true)
if ($null -eq $function) { throw 'Assert-ExistingPrintBackend was not found in the installer AST.' }
Invoke-Expression $function.Extent.Text
$script:PrintBackendPort = [int]$env:HEALTH_PORT
try { Assert-ExistingPrintBackend; 'OK' }
catch { 'ERR:' + $_.Exception.Message; exit 1 }
'''
        env = {
            **os.environ,
            "INSTALL_SCRIPT": str(INSTALL),
            "HEALTH_PORT": str(server.server_address[1]),
        }
        try:
            return subprocess.run(
                [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12, env=env,
            )
        finally:
            server.shutdown()
            server.server_close()

    def test_real_printvaerksted_health_is_accepted(self):
        result = self._invoke(payload={"ok": True, "demo": False, "service": "printvaerksted"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OK", result.stdout)

    def test_demo_false_string_and_missing_fields_are_rejected(self):
        for payload in (
            {"ok": True, "demo": True, "service": "printvaerksted"},
            {"ok": False, "demo": False, "service": "printvaerksted"},
            {"ok": True, "demo": "false", "service": "printvaerksted"},
            {"ok": "true", "demo": False, "service": "printvaerksted"},
            {"ok": True, "service": "printvaerksted"},
        ):
            with self.subTest(payload=payload):
                result = self._invoke(payload=payload)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ERR:", result.stdout)

    def test_wrong_service_is_rejected(self):
        result = self._invoke(payload={"ok": True, "demo": False, "service": "other"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ERR:", result.stdout)

    def test_redirect_is_refused(self):
        result = self._invoke(redirect=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ERR:", result.stdout)

    def test_health_timeout_is_bounded(self):
        started = time.monotonic()
        result = self._invoke(payload={"ok": True, "demo": False, "service": "printvaerksted"}, delay=20)
        elapsed = time.monotonic() - started
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 12)


class GeneratedPrinterReferenceTest(unittest.TestCase):
    @staticmethod
    def _run_reference_block(existing):
        if not POWERSHELL:
            raise unittest.SkipTest("PowerShell is required for the functional installer block test")
        script = r'''
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$path = $env:INSTALL_SCRIPT
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ('PowerShell syntax errors: ' + (($errors | ForEach-Object Message) -join '; ')) }
$matches = @($ast.FindAll({ param($node)
    $node -is [System.Management.Automation.Language.IfStatementAst] -and
    $node.Clauses[0].Item1.Extent.Text -eq '$UseExistingPrintBackend' -and
    $node.Extent.Text -match 'makerspace_printers' -and
    $node.Extent.Text -match 'NotePropertyName'
}, $true))
if ($matches.Count -ne 1) { throw "Expected exactly one printer reference IfStatementAst, got $($matches.Count)." }
$block = $matches[0].Extent.Text
function Run-Case([bool]$Existing) {
    $baseConfig = [pscustomobject]@{
        makerspace_printers = [pscustomobject]@{ backend = '127.0.0.1:8765'; key_file = 'old.key' }
    }
    $DataRoot = Join-Path $env:REFERENCE_TEST_ROOT 'server-data-not-created'
    $PrintBackendPort = 9876
    $UseExistingPrintBackend = $Existing
    $before = @(Get-ChildItem -LiteralPath $env:REFERENCE_TEST_ROOT -Force | ForEach-Object FullName)
    Invoke-Expression $block
    $after = @(Get-ChildItem -LiteralPath $env:REFERENCE_TEST_ROOT -Force | ForEach-Object FullName)
    if (($before -join "`n") -cne ($after -join "`n")) { throw 'Printer reference block created filesystem entries.' }
    [pscustomobject]@{ config = $baseConfig; data_root = $DataRoot }
}
Run-Case ([bool]::Parse($env:EXISTING)) | ConvertTo-Json -Depth 10 -Compress
'''
        with tempfile.TemporaryDirectory(prefix='makerspace-install-reference-') as test_root:
            env = {**os.environ, "INSTALL_SCRIPT": str(INSTALL), "EXISTING": str(existing).lower(),
                   "REFERENCE_TEST_ROOT": test_root}
            return subprocess.run([POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12, env=env)

    def test_internal_backend_reference_is_functional_and_side_effect_free(self):
        result = self._run_reference_block(False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["config"]["makerspace_printers"]["backend"], "127.0.0.1:9876")
        self.assertEqual(value["config"]["makerspace_printers"]["key_file"],
                         value["data_root"] + "\\printvaerksted\\makerspace-bridge.key")

    def test_existing_backend_removes_reference_and_is_side_effect_free(self):
        result = self._run_reference_block(True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        value = json.loads(result.stdout)
        self.assertNotIn("makerspace_printers", value["config"])


class InstallerSafetyContractTest(unittest.TestCase):
    """Regression checks for installer contracts that require a Windows host."""

    @classmethod
    def setUpClass(cls):
        cls.installer = INSTALL.read_text(encoding="utf-8")
        cls.school_setup = (INSTALL.parents[2] / "deployment" / "school-setup" / "Setup-School.ps1").read_text(encoding="utf-8")

    def test_registry_lock_is_created_with_localservice_modify_acl(self):
        self.assertIn("$enhederLockPath = Join-Path $security 'enheder.lock'", self.installer)
        self.assertIn("'*S-1-5-19:M'", self.installer)
        self.assertIn("[switch]$Resume", self.installer)

    def test_resume_requires_owned_marker_and_validated_tls_recovery(self):
        expected_states = {'rolledBack', 'installing', 'rollbackFailed', 'recoveryPrepared'}
        for script, owner in ((self.installer, 'resumeMarker'), (self.school_setup, 'owner')):
            match = re.search(r'\$' + owner + r'\.state -notin @\(([^)]*)\)', script)
            self.assertIsNotNone(match, 'Resume must have an explicit state allowlist')
            self.assertEqual(set(re.findall(r"'([^']+)'", match.group(1))), expected_states)
        self.assertIn("if ($resumeMarker.state -eq 'recoveryPrepared' -and -not $RecoveryBackup)", self.installer)
        self.assertIn("-m app.school_recovery check --package-root $PackageRoot --runtime-root $InstallRoot --data-root $DataRoot", self.installer)
        self.assertIn("$resumeMarker.printBackendPort", self.installer)
        self.assertIn("$resumeMarker.generationLayout", self.installer)
        self.assertIn("$resumeMarker.externalBackends", self.installer)
        self.assertIn("$resumeMarker.packageManifestSha256 -ne", self.installer)
        self.assertIn("$resumeTlsFiles", self.installer)
        self.assertIn("'tls-initialization.json'", self.installer)
        self.assertIn("--resume --check", self.installer)
        self.assertIn("ingen data blev overskrevet", self.installer)
        self.assertIn("'-Resume'", self.school_setup)
        self.assertIn("if (-not $resumeExisting)", self.school_setup)

    def test_installer_json_reads_are_explicit_utf8(self):
        for line in self.installer.splitlines():
            if "ConvertFrom-Json" in line and "Get-Content" in line:
                self.assertIn("-Encoding UTF8", line, line)
        for line in self.school_setup.splitlines():
            if "ConvertFrom-Json" in line and "Get-Content" in line:
                self.assertIn("-Encoding UTF8", line, line)


if __name__ == "__main__":
    unittest.main()
