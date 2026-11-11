"""Real Windows byte locks, isolated paths, no tasks/firewall/host installation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from app.generation_lock import GenerationLock
from app.instance import InstanceLock

PS = shutil.which('powershell.exe') or shutil.which('pwsh')
GUARDS = Path(__file__).resolve().parents[1] / 'school-uninstall-guards.ps1'


@unittest.skipUnless(os.name == 'nt' and PS, 'Windows lock interoperability is required')
class UninstallLockProtocolTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = self.root / 'runtime'
        self.data = self.root / 'data'
        (self.runtime / 'control').mkdir(parents=True)
        self.data.mkdir()
        (self.runtime / 'control/generation.lock').write_bytes(b'1')
        (self.data / 'instance.lock').write_bytes(b'1')
        (self.data / 'private.key').write_bytes(b'retained')
        self.env = {**os.environ, 'GUARDS': str(GUARDS), 'RUNTIME': str(self.runtime), 'DATA': str(self.data)}

    def tearDown(self):
        self.temp.cleanup()

    def run_ps(self, body):
        result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command',
                                 "$ErrorActionPreference='Stop'; . $env:GUARDS; " + body],
                                env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_python_shared_generation_reader_and_instance_owner_block_remover(self):
        body = r'''
$rejected=$false
try { $locks=New-OfflineRemovalLocks $env:RUNTIME $env:DATA $true; foreach($handle in $locks){$handle.Dispose()} } catch {$rejected=$true}
@{rejected=$rejected; program=(Test-Path $env:RUNTIME); key=[string](Get-Content (Join-Path $env:DATA 'private.key') -Raw)} | ConvertTo-Json -Compress
'''
        with GenerationLock(self.runtime / 'control/generation.lock'):
            result = self.run_ps(body)
            self.assertEqual(result, {'rejected': True, 'program': True, 'key': 'retained'})
        with InstanceLock(self.data):
            result = self.run_ps(body)
            self.assertTrue(result['rejected'])
        # Failure to take the second lock must release the first.
        with GenerationLock(self.runtime / 'control/generation.lock', exclusive=True):
            pass

    def test_remover_excludes_new_python_readers_and_can_delete_only_program_tree(self):
        body = r'''
$ErrorActionPreference='Stop'; . $env:GUARDS
$locks=New-OfflineRemovalLocks $env:RUNTIME $env:DATA $true
try {
  [Console]::WriteLine('READY')
  $null=[Console]::ReadLine()
  Remove-Item -LiteralPath $env:RUNTIME -Recurse -Force
  [Console]::WriteLine('REMOVED')
} finally { foreach($handle in $locks){$handle.Dispose()} }
'''
        process = subprocess.Popen([PS, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', body],
                                   env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'READY')
            for exclusive in (False, True):
                with self.assertRaises((RuntimeError, OSError)):
                    GenerationLock(self.runtime / 'control/generation.lock', exclusive=exclusive)
            with self.assertRaises((RuntimeError, OSError)):
                InstanceLock(self.data)
            stdout, stderr = process.communicate('\n', timeout=15)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertIn('REMOVED', stdout)
            self.assertFalse(self.runtime.exists())
            self.assertEqual((self.data / 'private.key').read_bytes(), b'retained')
            with InstanceLock(self.data):
                pass
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
