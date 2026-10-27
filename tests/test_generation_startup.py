import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


HELPER = Path(__file__).parents[1] / "windows-elevhub" / "server" / "generation-startup.ps1"


class GenerationStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
        if not cls.powershell:
            raise unittest.SkipTest("PowerShell ikke installeret")
        cls.python = Path(sys.executable)

    def _powershell(self, source, *arguments):
        with tempfile.TemporaryDirectory(prefix="generation-startup-") as folder:
            script = Path(folder) / "test.ps1"
            script.write_text(source, encoding="utf-8")
            result = subprocess.run(
                [self.powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(script), *map(str, arguments)],
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            return result.stdout.strip()

    def test_convert_commandline_roundtrips_windows_argv(self):
        values = [
            "plain",
            "two words",
            "",
            'embedded "quote"',
            "trailing\\",
            "two trailing\\\\",
            "quote and slash\\\"tail",
            "Æøå 你好",
        ]
        with tempfile.TemporaryDirectory(prefix="generation-argv-") as folder:
            values_file = Path(folder) / "values.json"
            sentinel = Path(folder) / "argv_sentinel.py"
            values_file.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
            sentinel.write_text(
                "import json, sys\nsys.stdout.write(json.dumps(sys.argv[1:], ensure_ascii=True))\n",
                encoding="utf-8",
            )
            source = r'''
param([string]$Helper, [string]$Python, [string]$Sentinel, [string]$ValuesFile)
. $Helper
$jsonValues = Get-Content -LiteralPath $ValuesFile -Raw -Encoding UTF8 | ConvertFrom-Json
$values = @()
foreach ($value in $jsonValues) { $values += [string]$value }
$argsLine = ConvertTo-GenerationCommandLine -Values $values
$sentinelLine = ConvertTo-GenerationCommandLine -Values @($Sentinel)
$info = [Diagnostics.ProcessStartInfo]::new()
$info.FileName = $Python
$info.Arguments = $sentinelLine + ' ' + $argsLine
$info.UseShellExecute = $false
$info.CreateNoWindow = $true
$info.RedirectStandardOutput = $true
$info.RedirectStandardError = $true
$process = [Diagnostics.Process]::Start($info)
$output = $process.StandardOutput.ReadToEnd()
$errorOutput = $process.StandardError.ReadToEnd()
$process.WaitForExit()
if ($process.ExitCode -ne 0) { throw $errorOutput }
Write-Output $output
'''
            output = self._powershell(source, HELPER, self.python, sentinel, values_file)
        self.assertEqual(json.loads(output), values)

    def test_generation_command_has_exact_flag_pairs_and_rejects_bad_entry(self):
        root = Path(tempfile.mkdtemp(prefix="generation-root with spaces-"))
        try:
            source = r'''
param([string]$Helper, [string]$Root)
. $Helper
$id = '0123456789abcdef0123456789abcdef'
$command = Get-GenerationCommand -InstallRoot $Root -InstallationId $id -Entry 'laser-station' -Arguments @('urls', 'two words')
[pscustomobject]@{ Execute=$command.Execute; WorkingDirectory=$command.WorkingDirectory; ArgumentList=@($command.ArgumentList) } | ConvertTo-Json -Compress
try { Get-GenerationCommand -InstallRoot $Root -InstallationId $id -Entry 'not-an-entry' | Out-Null; 'BAD=accepted' } catch { 'BAD=rejected' }
try { Get-GenerationCommand -InstallRoot $Root -InstallationId 'bad' -Entry 'laser-station' | Out-Null; 'ID=accepted' } catch { 'ID=rejected' }
'''
            output = self._powershell(source, HELPER, root)
            lines = output.splitlines()
            command = json.loads(lines[0])
            args = command["ArgumentList"]
            self.assertEqual(command["WorkingDirectory"], str(root.resolve()))
            self.assertEqual(args[args.index("--runtime-root") + 1], str(root.resolve()))
            self.assertEqual(args[args.index("--expected-installation-id") + 1], "0123456789abcdef0123456789abcdef")
            self.assertEqual(args[args.index("--entry") + 1], "laser-station")
            self.assertEqual(args[-2:], ["urls", "two words"])
            self.assertIn("BAD=rejected", lines)
            self.assertIn("ID=rejected", lines)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_startup_mode_owner_id_unknown_protocol_and_partial_control(self):
        root = Path(tempfile.mkdtemp(prefix="generation-control-"))
        try:
            source = r'''
param([string]$Helper, [string]$Root)
. $Helper
$owner = [pscustomobject]@{ ownerId='0123456789abcdef0123456789abcdef' }
try { 'LEGACY=' + (Get-InstallationStartupMode -Owner $owner -InstallRoot $Root) } catch { 'LEGACY=ERROR' }
New-Item -ItemType Directory -Path (Join-Path $Root 'control') | Out-Null
try { 'PARTIAL=' + (Get-InstallationStartupMode -Owner $owner -InstallRoot $Root) } catch { 'PARTIAL=rejected' }
$generationOwner = [pscustomobject]@{ ownerId='0123456789abcdef0123456789abcdef'; startupProtocol='generation-v1' }
try { 'GEN=' + (Get-InstallationStartupMode -Owner $generationOwner -InstallRoot $Root) } catch { 'GEN=ERROR' }
$unknownOwner = [pscustomobject]@{ ownerId='0123456789abcdef0123456789abcdef'; startupProtocol='future-v9' }
try { Get-InstallationStartupMode -Owner $owner -InstallRoot $Root | Out-Null; 'UNKNOWN=accepted' } catch { 'UNKNOWN=rejected' }
try { Get-InstallationStartupMode -Owner $unknownOwner -InstallRoot $Root | Out-Null; 'UNKNOWN2=accepted' } catch { 'UNKNOWN2=rejected' }
$badOwner = [pscustomobject]@{ ownerId='bad'; startupProtocol='generation-v1' }
try { Get-InstallationStartupMode -Owner $badOwner -InstallRoot $Root | Out-Null; 'ID=accepted' } catch { 'ID=rejected' }
'''
            output = self._powershell(source, HELPER, root)
            self.assertIn("LEGACY=legacy", output)
            self.assertIn("PARTIAL=rejected", output)
            self.assertIn("GEN=generation-v1", output)
            self.assertIn("UNKNOWN2=rejected", output)
            self.assertIn("ID=rejected", output)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
