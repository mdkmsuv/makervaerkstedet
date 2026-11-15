from pathlib import Path as _LayoutPath
import sys as _layout_sys
_LAYOUT_ROOT = next(p for p in _LayoutPath(__file__).resolve().parents if (p / 'REPOSITORY-LAYOUT.json').is_file())
_layout_sys.path[:0] = [str(_LAYOUT_ROOT), str(_LAYOUT_ROOT / 'system')]
from repository import source_path, repo_root
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / "deployment" / "school-setup" / "Start-LaserStation.ps1"
HELPER = source_path('windows-elevhub/server/generation-startup.ps1', Path(__file__).parents[1])
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

    def _run(self, root, args, *, helper=None, processes=(), browser=False, prelude="", script_path=SCRIPT):
        if helper is not None:
            server = root / "server"
            server.mkdir(exist_ok=True)
            (server / "generation-startup.ps1").write_text(helper, encoding="utf-8")
        process_setup = "\n".join(
            "$global:mockGenerationProcesses += [pscustomobject]@{{ExecutablePath='{}'; CommandLine='{}'; ProcessId=123}}".format(
                self._quote(item["ExecutablePath"]), self._quote(item["CommandLine"]))
            for item in processes)
        script = f"""
$ErrorActionPreference = 'Stop'
$global:mockLaunches = @()
$global:mockBrowsers = @()
$global:mockGenerationProcesses = @()
$global:leaseClosed = 0
$global:mockReads = 0
$global:unreadyReads = 0
$global:mockServer = 'http://127.0.0.1:8767'
$global:mockStationId = 'laser-fbb2'
$global:mockHealthPid = 123
$global:mockPopups = @()
{process_setup}
function Get-CimInstance {{ $global:mockGenerationProcesses }}
function Start-Sleep {{ param([int]$Milliseconds) }}
function Invoke-RestMethod {{
  param([string]$Uri, [int]$TimeoutSec)
  if ($Uri -like '*/api/runtime/health') {{ return [pscustomobject]@{{service='laser_station';pid=$global:mockHealthPid}} }}
  $global:mockReads++
  if ($global:mockReads -le $global:unreadyReads) {{ throw 'not ready' }}
  [pscustomobject]@{{server=$global:mockServer;stationId=$global:mockStationId}}
}}
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
  [pscustomobject]@{{HasExited=$false; ExitCode=0; Id=123}}
}}
__PRELUDE__
try {{ & '__SCRIPT__' {args} }} catch {{ Write-Output ('ERROR=' + $_.Exception.Message) }}
('LAUNCHES=' + $global:mockLaunches.Count)
('BROWSERS=' + ($global:mockBrowsers -join '|'))
('COMMAND=' + ($global:mockLaunches -join '|'))
('CLOSED=' + $global:leaseClosed)
('READS=' + $global:mockReads)
('POPUPS=' + $global:mockPopups.Count)
"""
        script = script.replace("__SCRIPT__", self._quote(script_path)).replace("__PRELUDE__", prelude)
        env = os.environ.copy()
        local = root / "localappdata"
        env["LOCALAPPDATA"] = str(local)
        command = [self.powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]
        return subprocess.run(command, capture_output=True, text=True, timeout=20, env=env)

    def _fixture(self):
        root = Path(tempfile.mkdtemp(prefix="laser-active-"))
        (root / "runtime").mkdir()
        (root / "runtime" / "python.exe").write_bytes(b"fixture")
        config = root / 'data' / 'laserstation' / 'laser-installation.json'
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({'port': 9876}), encoding='utf-8')
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

    def _separate_fixture(self, root, *, running=False):
        active = {'format': 1, 'kind': 'makervaerkstedet-laserstation',
                  'installation_id': INSTALLATION_ID, 'version_directory': 'versions/' + 'a' * 32}
        version = root / active['version_directory']
        program = version / 'program'
        (program / 'runtime').mkdir(parents=True)
        (program / 'runtime' / 'python.exe').write_bytes(b'fixture')
        (root / 'active.json').write_text(json.dumps(active))
        (version / 'laser-installation.json').write_text(json.dumps({
            'port': 8911, 'server': 'http://127.0.0.1:8767', 'station_id': 'laser-fbb2'}))
        descriptor = {'format': 1, 'kind': 'makervaerkstedet-laserstation-running', 'root': str(root),
                      'installation_id': INSTALLATION_ID, 'version_directory': active['version_directory'], 'nonce': 'b' * 32}
        if running:
            (root / 'running.json').write_text(json.dumps(descriptor))
        process = {'ExecutablePath': str(program / 'runtime' / 'python.exe'),
                   'CommandLine': '-B -m app.laser_station_install run --install-root "' + str(root) + '"'}
        return version, descriptor, process

    def test_separate_launcher_reuses_only_own_process_and_waits_without_duplicate(self):
        opener = SCRIPT.with_name('Open-LaserStation.ps1')
        with tempfile.TemporaryDirectory(prefix='laser-open-') as folder:
            root = Path(folder)
            _, _, process = self._separate_fixture(root, running=True)
            result = self._run(root, f"-InstallPath '{self._quote(root)}'", processes=[process], script_path=opener,
                               prelude='$global:unreadyReads = 3')
            self.assertNotIn('ERROR=', result.stdout)
            self.assertIn('LAUNCHES=0', result.stdout)
            self.assertIn('READS=4', result.stdout)
            self.assertIn('BROWSERS=http://127.0.0.1:8911/makervaerkstedet.html', result.stdout)
            result = self._run(root, f"-InstallPath '{self._quote(root)}' -NoBrowser", processes=[process], script_path=opener,
                               prelude='$global:mockHealthPid = 999')
            self.assertIn('ERROR=', result.stdout)
            self.assertIn('ejes ikke', result.stdout)
            self.assertNotIn('BROWSERS=http', result.stdout)

    def test_separate_launcher_starts_own_station_and_follows_repaired_port(self):
        opener = SCRIPT.with_name('Open-LaserStation.ps1')
        with tempfile.TemporaryDirectory(prefix='laser-open-') as folder:
            root = Path(folder)
            version, descriptor, process = self._separate_fixture(root, running=True)
            (root / 'running.json').write_text(json.dumps({**descriptor, 'version_directory': 'versions/' + 'c' * 32}))
            prelude = r'''
function Start-Process {
  param([string]$FilePath, [object[]]$ArgumentList, [string]$WorkingDirectory,
        [string]$WindowStyle, [string]$RedirectStandardOutput,
        [string]$RedirectStandardError, [switch]$PassThru)
  if ($FilePath -like 'http://*') { $global:mockBrowsers += $FilePath; return }
  $global:mockLaunches += $FilePath
  [IO.File]::WriteAllText('__CONFIG__', '__CONFIGJSON__')
  [IO.File]::WriteAllText('__RUNNING__', '__DESCRIPTOR__')
  $global:mockGenerationProcesses += [pscustomobject]@{ExecutablePath=$FilePath;CommandLine=('-B -m app.laser_station_install run --install-root "__ROOT__"');ProcessId=123}
  [pscustomobject]@{HasExited=$false;ExitCode=0;Id=123}
}
'''.replace('__CONFIG__', self._quote(version / 'laser-installation.json')).replace('__CONFIGJSON__',
                self._quote(json.dumps({'port': 8912, 'server': 'http://127.0.0.1:8767', 'station_id': 'laser-fbb2'}))).replace(
                '__RUNNING__', self._quote(root / 'running.json')).replace('__DESCRIPTOR__', self._quote(json.dumps(descriptor))).replace('__ROOT__', self._quote(root))
            result = self._run(root, f"-InstallPath '{self._quote(root)}'", script_path=opener, prelude=prelude)
            self.assertNotIn('ERROR=', result.stdout)
            self.assertIn('LAUNCHES=1', result.stdout)
            self.assertIn('BROWSERS=http://127.0.0.1:8912/makervaerkstedet.html', result.stdout)

    def test_no_browser_failure_exits_nonzero_and_writes_exact_log(self):
        opener = SCRIPT.with_name('Open-LaserStation.ps1')
        with tempfile.TemporaryDirectory(prefix='laser-open-error-') as folder:
            result = subprocess.run([self.powershell, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                     '-File', str(opener), '-InstallPath', folder, '-NoBrowser'], capture_output=True, text=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            logs = list((Path(folder) / 'logs').glob('launcher-*.log'))
            self.assertEqual(len(logs), 1)
            self.assertIn(str(logs[0]), result.stderr)
            self.assertIn('Laserstationen kunne ikke', logs[0].read_text(encoding='utf-8'))

    def test_generation_selector_preserves_explicit_server_runtime_role(self):
        from app.generation_selection import bind_arguments
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            (data / 'laserstation').mkdir()
            plates = data / 'Laserplader'
            plates.mkdir()
            config = data / 'laserstation' / 'laser-installation.json'
            config.write_text(json.dumps({'plates': str(plates)}))
            args = bind_arguments({'data_root': str(data)}, 'laser-station', ['--runtime-role', 'server'])
            self.assertEqual(args, ['--runtime-role', 'server', '--config', str(config)])

    def test_shortcut_errors_show_danish_message_and_still_throw(self):
        with tempfile.TemporaryDirectory(prefix='laser-popup-') as folder:
            root = Path(folder)
            prelude = r'''
function New-Object {
  param([string]$ComObject)
  $shell = [pscustomobject]@{}
  $shell | Add-Member -MemberType ScriptMethod -Name Popup -Value {
    param($message, $seconds, $title, $style)
    $global:mockPopups += $message
    return 1
  }
  return $shell
}
'''
            for script_path, args in (
                (SCRIPT, f"-Root '{self._quote(root)}' -OpenBrowser"),
                (SCRIPT.with_name('Open-LaserStation.ps1'), f"-InstallPath '{self._quote(root)}'"),
            ):
                result = self._run(root, args, prelude=prelude, script_path=script_path)
                self.assertIn('ERROR=Laserstationen kunne ikke', result.stdout)
                self.assertIn('POPUPS=1', result.stdout)
                self.assertIn('Log:', result.stdout)
                self.assertNotIn('BROWSERS=http', result.stdout)

    def test_zero_exit_before_readiness_is_a_launch_failure(self):
        with tempfile.TemporaryDirectory(prefix='laser-exited-') as folder:
            root = Path(folder)
            (root / 'runtime').mkdir()
            (root / 'runtime' / 'python.exe').write_bytes(b'fixture')
            config = root / 'station.json'
            config.write_text('{}')
            prelude = r'''
function Start-Process {
  param([string]$FilePath, [object[]]$ArgumentList, [string]$WorkingDirectory,
        [string]$WindowStyle, [string]$RedirectStandardOutput,
        [string]$RedirectStandardError, [switch]$PassThru)
  [pscustomobject]@{HasExited=$true;ExitCode=0;Id=123}
}
'''
            result = self._run(root, f"-Root '{self._quote(root)}' -ConfigPath '{self._quote(config)}' -OpenBrowser -NoBrowser", prelude=prelude)
            self.assertIn('ERROR=', result.stdout)
            self.assertIn('stoppede under start', result.stdout)
            self.assertIn('.err.log', result.stdout)
            self.assertNotIn('BROWSERS=http', result.stdout)

    def test_stop_does_not_claim_success_for_older_matching_process_without_control(self):
        stop = SCRIPT.with_name('Stop-LaserStation.ps1')
        source = stop.read_text(encoding='utf-8-sig')
        invocation = '$resultText = & $python -B -m app.laser_station --stop-config $config --stop-timeout 15 2> $stopLog'
        self.assertIn(invocation, source)
        source = source.replace(invocation, '$resultText = \'{"stopped":true,"already_stopped":true}\'; $global:LASTEXITCODE = 0')
        with tempfile.TemporaryDirectory(prefix='laser-stop-') as folder:
            root = Path(folder)
            (root / 'runtime').mkdir()
            python = root / 'runtime' / 'python.exe'
            python.write_bytes(b'fixture')
            config = root / 'station.json'
            for own in (False, True):
                process_config = config if own else root / 'another-station.json'
                command_line = '-B -m app.laser_station --config "' + str(process_config) + '"'
                script = """
function Get-CimInstance { [pscustomobject]@{ExecutablePath='__PYTHON__';CommandLine='__COMMAND__'} }
try { & ([ScriptBlock]::Create('__SOURCE__')) -Root '__ROOT__' -ConfigPath '__CONFIG__' -NoDialog } catch { Write-Output ('ERROR=' + $_.Exception.Message) }
"""
                for key, value in (('__PYTHON__', python), ('__COMMAND__', command_line), ('__SOURCE__', source),
                                   ('__ROOT__', root), ('__CONFIG__', config)):
                    script = script.replace(key, self._quote(value))
                env = os.environ.copy()
                env['LOCALAPPDATA'] = str(root / 'localappdata')
                result = subprocess.run([self.powershell, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                         '-Command', script], capture_output=True, text=True, timeout=20, env=env)
                if own:
                    self.assertIn('ERROR=', result.stdout)
                    self.assertIn('Log af Windows', result.stdout)
                else:
                    self.assertNotIn('ERROR=', result.stdout)

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

    def test_browser_waits_for_matching_station_response(self):
        root, helper = self._fixture()
        try:
            result = self._run(root, f"-Root '{self._quote(root)}' -ActiveGeneration -InstallationId {INSTALLATION_ID} -OpenBrowser -NoBrowser", helper=helper,
                               prelude='$global:unreadyReads = 3')
            self.assertNotIn('ERROR=', result.stdout)
            self.assertIn('READS=4', result.stdout)
            self.assertIn('--runtime-role server', result.stdout)
            result = self._run(root, f"-Root '{self._quote(root)}' -ActiveGeneration -InstallationId {INSTALLATION_ID} -OpenBrowser -NoBrowser", helper=helper,
                               prelude="$global:mockStationId = 'another-station'")
            self.assertIn('ERROR=', result.stdout)
            self.assertIn('anden laserstation', result.stdout)
            self.assertNotIn('BROWSERS=http', result.stdout)
            self.assertIn('.log', result.stdout)
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
$cmd = Get-GenerationCommand -InstallRoot '{root}' -InstallationId '{id}' -Entry 'laser-station' -ExpectedSelection ('a' * 64) -Arguments @('--runtime-role','server')
$global:mockGenerationProcesses += [pscustomobject]@{{ExecutablePath=$cmd.Execute; CommandLine=($cmd.Execute + ' ' + $cmd.Arguments);ProcessId=123}}
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
