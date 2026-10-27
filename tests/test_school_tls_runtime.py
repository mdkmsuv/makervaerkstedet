"""The installer's exact fixed-module copy must load without source fallback."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which('powershell.exe') or shutil.which('pwsh')


@unittest.skipUnless(POWERSHELL, 'PowerShell is required for the actual installer branch')
class SchoolTLSRuntimeTests(unittest.TestCase):
    def test_exact_installed_fixed_modules_load_tls_operator_in_isolated_python(self):
        with tempfile.TemporaryDirectory(prefix='fixed-tls-runtime-') as temporary:
            runtime = Path(temporary)
            (runtime / 'app').mkdir()
            script = r'''
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $env:TLS_SOURCE_ROOT 'windows-elevhub/server/install.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Installer parse failure'}
$nodes=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.ForEachStatementAst] -and $n.Extent.Text -match "'school_tls.py'" -and $n.Extent.Text -match 'Copy-Item'},$true))
if($nodes.Count -ne 1){throw 'Expected one fixed-runtime copy branch'}
$programRoot=$env:TLS_SOURCE_ROOT; $InstallRoot=$env:TLS_RUNTIME_ROOT
Invoke-Expression $nodes[0].Extent.Text
@(Get-ChildItem -LiteralPath (Join-Path $InstallRoot 'app') -File | ForEach-Object {$_.Name}) | ConvertTo-Json -Compress
'''
            result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', script],
                env={**os.environ, 'TLS_SOURCE_ROOT': str(ROOT), 'TLS_RUNTIME_ROOT': str(runtime)},
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            copied = set(json.loads(result.stdout))
            spec = importlib.util.spec_from_file_location('tls_provision_contract', ROOT / 'windows-elevhub/server/provision.py')
            provision = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(provision)
            self.assertEqual(copied, provision.FIXED_APP)
            loaded = subprocess.run([sys.executable, '-I', '-B', '-c',
                "import sys; sys.path.insert(0, sys.argv[1]); from app import school_tls; print(school_tls.__file__)", str(runtime)],
                cwd=runtime, capture_output=True, text=True, timeout=20)
            self.assertEqual(loaded.returncode, 0, loaded.stderr)
            self.assertEqual(Path(loaded.stdout.strip()), runtime / 'app/school_tls.py')


if __name__ == '__main__':
    unittest.main()
