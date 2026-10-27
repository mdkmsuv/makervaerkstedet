import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / "deployment" / "school-setup" / "Start-LaserStation.ps1"
HELPER = Path(__file__).parents[1] / "windows-elevhub" / "server" / "generation-startup.ps1"
INSTALLATION_ID = "0123456789abcdef0123456789abcdef"


class StartLaserStationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
        if not cls.powershell:
            raise unittest.SkipTest("PowerShell ikke installeret")

    @staticmethod
    def _quote(value):
        return str(value).replace("'", "''")

    def _run(self, root, args, *, helper=None, processes=(), browser=False, prelude=""):
        if helper is not None:
            server = root / "server"
            server.mkdir(exist_ok=True)
            (server / "generation-startup.ps1").write_text(helper, encoding="utf-8")
        process_setup = "\n".join(
            "$global:mockGenerationProcesses += [pscustomobject]@{{ExecutablePath='{}'; CommandLine='{}'}}".format(
                self._quote(item["ExecutablePath"]), self._quote(item["CommandLine"]))
            for item in processes)
        script = f"""
$ErrorActionPreference = 'Stop'
$global:mockLaunches = @()
$global:mockBrowsers = @()
$global:mockGenerationProcesses = @()
$global:leaseClosed = 0
{process_setup}
function Get-CimInstance {{ $global:mockGenerationProcesses }}
function Start-Sleep {{ param([int]$Milliseconds) }}
function Start-Process {{
  param([string]$FilePath, [object[]]$ArgumentList, [string]$WorkingDirectory,
        [string]$WindowStyle, [string]$RedirectStandardOutput,
        [string]$RedirectStandardError, [switch]$PassThru, [switch]$Wait)
  if ($FilePath -like 'http://*' -or $FilePath -like 'https://*') {{ $global:mockBrowsers += $FilePath; return }}
  if ([string]$ArgumentList -match 'school-admin') {{
    Set-Content -LiteralPath $RedirectStandardOutput -Value '{{"laser":"http://127.0.0.1:9876/"}}' -Encoding UTF8
    return [pscustomobject]@{{HasExited=$true; ExitCode=0}}
  }}
  $global:mockLaunches += (($FilePath + ' ' + (($ArgumentList | ForEach-Object {{ [string]$_ }}) -join ' ')).Trim())
  [pscustomobject]@{{HasExited=$false; ExitCode=0}}
}}
__PRELUDE__
try {{ & '__SCRIPT__' {args} }} catch {{ Write-Output ('ERROR=' + $_.Exception.Message) }}
('LAUNCHES=' + $global:mockLaunches.Count)
('BROWSERS=' + ($global:mockBrowsers -join '|'))
('COMMAND=' + ($global:mockLaunches -join '|'))
('CLOSED=' + $global:leaseClosed)
"""
        script = script.replace("__SCRIPT__", self._quote(SCRIPT)).replace("__PRELUDE__", prelude)
        env = os.environ.copy()
        local = root / "localappdata"
        env["LOCALAPPDATA"] = str(local)
        command = [self.powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]
        return subprocess.run(command, capture_output=True, text=True, timeout=20, env=env)

    def _fixture(self):
        root = Path(tempfile.mkdtemp(prefix="laser-active-"))
        (root / "runtime").mkdir()
        (root / "runtime" / "python.exe").write_bytes(b"fixture")
        helper = r'''
function Get-GenerationCommand {
  param([string]$InstallRoot, [string]$InstallationId, [string]$Entry, [object[]]$Arguments, [string]$ExpectedSelection)
  $a = @('-B','-I','app/generation_runtime.py','--runtime-root',$InstallRoot,'--active','--expected-installation-id',$InstallationId)
  if ($ExpectedSelection) { $a += @('--expected-selection',$ExpectedSelection) }
  $a += @('--entry',$Entry,'--') + @($Arguments)
  [pscustomobject]@{Execute=(Join-Path $InstallRoot 'runtime\python.exe'); ArgumentList=$a; Arguments=($a -join ' '); WorkingDirectory=$InstallRoot}
}
function ConvertTo-GenerationCommandLine {
  param([object[]]$Values)
  ($Values | ForEach-Object { [string]$_ }) -join ' '
}
function New-GenerationLease {
  param([string]$InstallRoot,[string]$InstallationId)
  [pscustomobject]@{Process=[pscustomobject]@{}; Snapshot=[pscustomobject]@{runtime_root=$InstallRoot; program_root=(Join-Path $InstallRoot 'program'); data_root=(Join-Path $InstallRoot 'data'); installation_id=$InstallationId; revision=1; selection_sha256=('a' * 64)}}
}
function Close-GenerationLease { param($Lease) $global:leaseClosed++ }
'''
        return root, helper

    def test_legacy_browser_uses_configured_port(self):
        with tempfile.TemporaryDirectory(prefix="laser-legacy-") as folder:
            root = Path(folder)
            (root / "runtime").mkdir()
            (root / "runtime" / "python.exe").write_bytes(b"fixture")
            config = root / "station.json"
            config.write_text(json.dumps({"port": 8911}), encoding="utf-8")
            result = self._run(root, f"-ConfigPath '{self._quote(config)}' -Root '{self._quote(root)}' -OpenBrowser")
            self.assertNotIn("ERROR=", result.stdout)
            self.assertIn("http://127.0.0.1:8911/", result.stdout)

    def test_active_generation_builds_fixed_command_and_url_metadata(self):
        root, helper = self._fixture()
        try:
            result = self._run(root, f"-Root '{self._quote(root)}' -ActiveGeneration -InstallationId {INSTALLATION_ID} -OpenBrowser", helper=helper)
            self.assertNotIn("ERROR=", result.stdout)
            self.assertIn("LAUNCHES=1", result.stdout)
            self.assertIn("--active", result.stdout)
            self.assertIn("--expected-installation-id " + INSTALLATION_ID, result.stdout)
            self.assertIn("--entry laser-station", result.stdout)
            self.assertIn("http://127.0.0.1:9876/", result.stdout)
            self.assertIn("CLOSED=1", result.stdout)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_active_rejects_config_override_and_bad_id(self):
        root, helper = self._fixture()
        config = root / "old.json"
        config.write_text("{}", encoding="utf-8")
        try:
            result = self._run(root, f"-Root '{self._quote(root)}' -ConfigPath '{self._quote(config)}' -ActiveGeneration -InstallationId {INSTALLATION_ID}", helper=helper)
            self.assertIn("ConfigPath", result.stdout)
            result = self._run(root, f"-Root '{self._quote(root)}' -ActiveGeneration -InstallationId bad", helper=helper)
            self.assertIn("32 hex", result.stdout)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_active_pins_both_commands_to_the_lease_selection_hash(self):
        root, helper = self._fixture()
        try:
            expected = 'a' * 64
            result = self._run(root, f"-Root '{self._quote(root)}' -ActiveGeneration -InstallationId {INSTALLATION_ID} -ExpectedSelectionSha256 {expected}", helper=helper)
            self.assertNotIn("ERROR=", result.stdout)
            self.assertIn('--expected-selection ' + expected, result.stdout)
            self.assertIn('CLOSED=1', result.stdout)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_active_rejects_command_coherence_mismatches(self):
        cases = {
            'arguments': "Arguments='tampered'",
            'executable': "Execute=(Join-Path $InstallRoot 'runtime\\other.exe')",
            'working_directory': "WorkingDirectory=(Join-Path $InstallRoot 'other')",
        }
        for name, replacement in cases.items():
            with self.subTest(name=name):
                root, helper = self._fixture()
                helper = helper.replace(
                    "[pscustomobject]@{Execute=(Join-Path $InstallRoot 'runtime\\python.exe'); ArgumentList=$a; Arguments=($a -join ' '); WorkingDirectory=$InstallRoot}",
                    "[pscustomobject]@{Execute=(Join-Path $InstallRoot 'runtime\\python.exe'); ArgumentList=$a; Arguments=($a -join ' '); WorkingDirectory=$InstallRoot}".replace(
                        "Execute=(Join-Path $InstallRoot 'runtime\\python.exe')", replacement if name == 'executable' else "Execute=(Join-Path $InstallRoot 'runtime\\python.exe')"
                    ).replace("Arguments=($a -join ' ')", replacement if name == 'arguments' else "Arguments=($a -join ' ')").replace("WorkingDirectory=$InstallRoot", replacement if name == 'working_directory' else "WorkingDirectory=$InstallRoot")
                )
                try:
                    result = self._run(root, f"-Root '{self._quote(root)}' -ActiveGeneration -InstallationId {INSTALLATION_ID}", helper=helper)
                    self.assertIn('ERROR=', result.stdout)
                    self.assertIn('forkert' if name != 'arguments' else 'uoverensstemmende', result.stdout)
                    self.assertIn('LAUNCHES=0', result.stdout)
                finally:
                    shutil.rmtree(root, ignore_errors=True)

    def test_active_rejects_missing_or_malformed_helper_before_launch(self):
        root, _ = self._fixture()
        try:
            result = self._run(root, f"-Root '{self._quote(root)}' -ActiveGeneration -InstallationId {INSTALLATION_ID}")
            self.assertIn("starthelper", result.stdout)
            helper = "function Get-GenerationCommand { [pscustomobject]@{Execute='x'} }"
            result = self._run(root, f"-Root '{self._quote(root)}' -ActiveGeneration -InstallationId {INSTALLATION_ID}", helper=helper)
            self.assertIn("leasehelper", result.stdout)
            self.assertIn("LAUNCHES=0", result.stdout)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_control_directory_blocks_legacy_fallback(self):
        root, _ = self._fixture()
        try:
            (root / "control").mkdir()
            config = root / "station.json"
            config.write_text("{}", encoding="utf-8")
            result = self._run(root, f"-Root '{self._quote(root)}' -ConfigPath '{self._quote(config)}'")
            self.assertIn("legacy-start er afvist", result.stdout)
            self.assertIn("LAUNCHES=0", result.stdout)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_active_legacy_process_must_be_closed(self):
        root, helper = self._fixture()
        process = {"ExecutablePath": str(root / "runtime" / "python.exe"), "CommandLine": "-m app.laser_station --config old.json"}
        try:
            result = self._run(root, f"-Root '{self._quote(root)}' -ActiveGeneration -InstallationId {INSTALLATION_ID}", helper=helper, processes=(process,))
            self.assertIn("legacy-laserstation", result.stdout)
            self.assertIn("LAUNCHES=0", result.stdout)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_active_existing_exact_process_is_not_restarted(self):
        root, helper = self._fixture()
        helper_path = root / "server" / "generation-startup.ps1"
        helper_path.parent.mkdir()
        helper_path.write_text(helper, encoding="utf-8")
        prelude = """
. '{helper}'
$cmd = Get-GenerationCommand -InstallRoot '{root}' -InstallationId '{id}' -Entry 'laser-station' -ExpectedSelection ('a' * 64) -Arguments @()
$global:mockGenerationProcesses += [pscustomobject]@{{ExecutablePath=$cmd.Execute; CommandLine=($cmd.Execute + ' ' + $cmd.Arguments)}}
function ConvertTo-GenerationCommandLine {{
  param([object[]]$Values)
  ($Values | ForEach-Object {{ [string]$_ }}) -join ' '
}}
function New-GenerationLease {{ param([string]$InstallRoot,[string]$InstallationId) [pscustomobject]@{{Process=[pscustomobject]@{{}};Snapshot=[pscustomobject]@{{runtime_root=$InstallRoot;program_root=(Join-Path $InstallRoot 'program');data_root=(Join-Path $InstallRoot 'data');installation_id=$InstallationId;revision=1;selection_sha256=('a'*64)}}}} }}
function Close-GenerationLease {{ param($Lease) $global:leaseClosed++ }}
""".format(helper=self._quote(helper_path), root=self._quote(root), id=INSTALLATION_ID)
        try:
            result = self._run(root, f"-Root '{self._quote(root)}' -ActiveGeneration -InstallationId {INSTALLATION_ID} -ExpectedSelectionSha256 {'a' * 64}", prelude=prelude)
            self.assertNotIn("ERROR=", result.stdout)
            self.assertIn("LAUNCHES=0", result.stdout)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_active_process_matching_requires_exact_id_root_and_entry(self):
        cases = (
            (f"--entry laser-station-{INSTALLATION_ID}", False),
            (f"--expected-installation-id {INSTALLATION_ID}0 --entry laser-station", True),
        )
        for command_suffix, conflict in cases:
            with self.subTest(command_suffix=command_suffix):
                root, helper = self._fixture()
                process = {"ExecutablePath": str(root / "runtime" / "python.exe"),
                           "CommandLine": f"app.generation_runtime.py --runtime-root {root} --active {command_suffix}"}
                try:
                    result = self._run(root, f"-Root '{self._quote(root)}' -ActiveGeneration -InstallationId {INSTALLATION_ID}", helper=helper, processes=(process,))
                    if conflict:
                        self.assertIn("legacy-laserstation", result.stdout)
                        self.assertIn("LAUNCHES=0", result.stdout)
                    else:
                        self.assertNotIn("ERROR=", result.stdout)
                        self.assertIn("LAUNCHES=1", result.stdout)
                finally:
                    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
