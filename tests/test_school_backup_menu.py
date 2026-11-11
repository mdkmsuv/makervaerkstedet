import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'deployment' / 'school-setup' / 'Manage-School.ps1'


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'),
                     'school backup menu tests require Windows PowerShell')
class SchoolBackupMenuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.package = self.root / 'Program Files' / 'Makerværkstedet'
        self.package.mkdir(parents=True)
        self.data = self.root / 'Skole data æøå'
        self.data.mkdir()
        self.source = self.root / 'Beskyttet backup æøå'
        self.destination = self.root / 'Isoleret prøve med mellemrum'
        self.powershell = shutil.which('powershell.exe')
        self.functions = self._functions('Invoke-SchoolBackup', 'Read-BackupPath', 'Invoke-BackupMenu', 'Invoke-SchoolAdmin')

    def tearDown(self):
        self.temp.cleanup()

    def _functions(self, *names):
        names_ps = '@(' + ','.join("'" + n + "'" for n in names) + ')'
        command = (
            "$t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($env:SOURCE,[ref]$t,[ref]$e); "
            "if($e.Count){throw ($e|Out-String)}; "
            "$wanted=" + names_ps + "; "
            "$wanted|ForEach-Object{$n=$_;$node=$ast.Find({param($x) "
            "$x -is [Management.Automation.Language.FunctionDefinitionAst] -and $x.Name -eq $n},$true); "
            "if($null -eq $node){throw \"missing $_\"}; $node.Extent.Text}"
        )
        result = self._powershell([command], env={**os.environ, 'SOURCE': str(SCRIPT)})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def _run(self, body, *, timeout=20):
        command = "$ErrorActionPreference='Stop';" + self.functions + body
        return self._powershell([command], timeout=timeout)

    def _powershell(self, commands, *, env=None, timeout=20):
        """Run Windows PowerShell and decode its actual console code page.

        Windows PowerShell 5.1 can emit the active OEM code page even when
        the child is asked for UTF-8.  Capturing bytes first prevents a
        non-admin test run from failing before it can assert Danish text.
        """
        result = subprocess.run(
            [self.powershell, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', *commands],
            capture_output=True, env=env, timeout=timeout)
        result.stdout = self._decode_output(result.stdout)
        result.stderr = self._decode_output(result.stderr)
        return result

    @staticmethod
    def _decode_output(value):
        if not value:
            return ''
        for encoding in ('utf-8-sig', 'cp850', 'cp1252'):
            try:
                return value.decode(encoding)
            except UnicodeDecodeError:
                continue
        return value.decode('utf-8', errors='replace')

    def _fake_python(self, *, exit_code=0, payload=None, malformed=False):
        script = self.root / 'fake-python.ps1'
        if malformed:
            output = 'not-json'
        else:
            output = json.dumps(payload or {'ok': True, 'folder': str(self.destination)}, ensure_ascii=False)
        script.write_text(
            "$args | ConvertTo-Json -Compress | Set-Content -LiteralPath $env:ARGS -Encoding UTF8; "
            "(Get-Location).Path | Set-Content -LiteralPath $env:CWD -Encoding UTF8; "
            "Write-Output $env:PAYLOAD; exit [int]$env:EXIT_CODE\n", encoding='utf-8')
        wrapper = self.root / 'fake-python.cmd'
        wrapper.write_text('@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0fake-python.ps1" %*\r\n', encoding='utf-8')
        env = {'ARGS': str(self.root / 'args.json'), 'CWD': str(self.root / 'cwd.txt'),
               'PAYLOAD': output, 'EXIT_CODE': str(exit_code)}
        return wrapper, env

    def test_legacy_create_passes_absolute_unicode_paths_and_restores_cwd(self):
        wrapper, env = self._fake_python()
        body = ("$startupMode='legacy';$PackageRoot=" + self._q(self.package) + ";$DataRoot=" + self._q(self.data) +
                ";$python=" + self._q(wrapper) + ";Set-Location -LiteralPath " + self._q(self.root) +
                ";$result=Invoke-SchoolBackup -Action create -Destination " + self._q(self.destination) +
                ";$result|ConvertTo-Json -Compress; @{cwd=(Get-Location).Path;args=(Get-Content -Raw $env:ARGS)}|ConvertTo-Json -Compress")
        result = self._powershell(["$env:ARGS=" + self._q(env['ARGS']) + ";$env:CWD=" + self._q(env['CWD']) +
                                  ";$env:PAYLOAD=" + self._q(env['PAYLOAD']) + ";$env:EXIT_CODE='0';" +
                                  "$env:PATH=$env:PATH;" + "$ErrorActionPreference='Stop';" + self.functions + body])
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(Path(env['ARGS']).read_text(encoding='utf-8-sig'))
        self.assertEqual(args, ['-B', '-m', 'app.school_backup', 'create', '--runtime-root', str(self.package),
                                '--data-root', str(self.data), '--destination', str(self.destination)])
        self.assertEqual(Path(env['CWD']).read_text(encoding='utf-8-sig').strip(), str(self.package))
        # The PowerShell process itself remains in its original directory.
        self.assertEqual(json.loads(result.stdout.splitlines()[-1])['cwd'], str(self.root))

    def test_legacy_verify_and_restore_bind_source_and_destination(self):
        wrapper, env = self._fake_python()
        body = ("$startupMode='legacy';$PackageRoot=" + self._q(self.package) + ";$DataRoot=" + self._q(self.data) +
                ";$python=" + self._q(wrapper) + ";$x=Invoke-SchoolBackup -Action restore -Source " + self._q(self.source) +
                " -Destination " + self._q(self.destination) + ";$x.folder")
        command = ("$env:ARGS=" + self._q(env['ARGS']) + ";$env:CWD=" + self._q(env['CWD']) +
                   ";$env:PAYLOAD=" + self._q(env['PAYLOAD']) + ";$env:EXIT_CODE='0';" + self.functions + body)
        result = self._powershell([command])
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(Path(env['ARGS']).read_text(encoding='utf-8-sig'))
        self.assertEqual(args[-4:], ['--source', str(self.source), '--destination', str(self.destination)])

    def test_generation_routes_backup_to_school_admin_without_legacy_arguments(self):
        body = ("$startupMode='generation-v1';$calls=@();function Invoke-GenerationEntry { param([string]$Entry,[string[]]$Arguments,[switch]$Json) "
                "$script:calls += [pscustomobject]@{entry=$Entry;args=$Arguments}; return [pscustomobject]@{ok=$true;folder='generation-folder'} };" +
                "$r=Invoke-SchoolBackup -Action restore -Source 'C:\\wrongcaller' -Destination 'D:\\target æøå'; "
                "@{result=$r.folder;calls=$script:calls}|ConvertTo-Json -Compress -Depth 6")
        result = self._run(body)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['result'], 'generation-folder')
        self.assertEqual(value['calls'][0]['entry'], 'school-admin')
        self.assertEqual(value['calls'][0]['args'], ['backup-restore', '--source', 'C:\\wrongcaller', '--destination', 'D:\\target æøå'])

    def test_legacy_invalid_json_and_nonzero_exit_are_reported(self):
        for exit_code, malformed, expected in ((0, True, 'Backupværktøjet returnerede ikke et læsbart resultat.'),
                                                (7, False, 'Backuphandlingen fejlede: bad')):
            wrapper, env = self._fake_python(exit_code=exit_code, malformed=malformed,
                                              payload={'ok': False, 'error': 'bad'})
            body = ("$startupMode='legacy';$PackageRoot=" + self._q(self.package) + ";$DataRoot=" + self._q(self.data) +
                    ";$python=" + self._q(wrapper) + ";Invoke-SchoolBackup -Action verify -Source " + self._q(self.source))
            command = ("$env:ARGS=" + self._q(env['ARGS']) + ";$env:CWD=" + self._q(env['CWD']) +
                       ";$env:PAYLOAD=" + self._q(env['PAYLOAD']) + ";$env:EXIT_CODE=" + str(exit_code) + ';' +
                       "$ErrorActionPreference='Stop';" + self.functions + body)
            result = self._powershell([command])
            self.assertNotEqual(result.returncode, 0)
            message = result.stderr + result.stdout
            self.assertIn('Backup', message)
            if not malformed:
                self.assertIn('fejlede: bad', message)

    def test_backup_menu_uses_read_paths_and_prints_operator_messages(self):
        body = ("function Read-BackupPath { param([string]$Prompt) if($Prompt -like '*Ny backupmappe*'){return 'C:\\backup æøå'};return 'D:\\restore folder'}; "
                "function Invoke-SchoolBackup { param($Action,$Source,$Destination) "
                "[pscustomobject]@{folder=if($Destination){$Destination}else{$Source}} }; "
                "Invoke-BackupMenu '9'; Invoke-BackupMenu '10'; Invoke-BackupMenu '11'")
        result = self._run(body)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Backup oprettet og kontrolleret: C:\\backup æøå', result.stdout)
        self.assertIn('Backupens filer og databaser er kontrolleret: D:\\restore folder', result.stdout)
        self.assertIn('er oprettet: D:\\restore folder', result.stdout)

    def test_read_backup_path_rejects_empty_relative_and_unc_values(self):
        for supplied, expected in (('', 'Der blev ikke valgt en mappe.'),
                                   ('relative-folder', 'absolut sti'),
                                   ('\\\\server\\share', 'absolut sti')):
            with self.subTest(supplied=supplied):
                body = ("function Read-Host { param([string]$Prompt) return " + self._q(supplied) + " }; "
                        "Read-BackupPath 'backup path'")
                result = self._run(body)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr + result.stdout)

    @staticmethod
    def _q(value):
        return "'" + str(value).replace("'", "''") + "'"


if __name__ == '__main__':
    unittest.main()
