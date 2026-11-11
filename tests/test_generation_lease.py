"""Bounded PowerShell/embedded-runtime tests for the shared generation lease."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

from app.generation_lock import GenerationLock
from tests import test_generation_embedded as embedded


HELPER = embedded.ROOT / 'windows-elevhub/server/generation-startup.ps1'
POWERSHELL = shutil.which('powershell.exe') or shutil.which('pwsh')


@unittest.skipUnless(POWERSHELL and os.name == 'nt' and embedded.ARCHIVE.is_file(),
                     'kræver Windows embedded Python og PowerShell')
class GenerationLeaseTests(unittest.TestCase):
    def setUp(self):
        from tests.test_school_admin import SchoolAdminActiveTests
        self.fixture = SchoolAdminActiveTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.data.mkdir(parents=True, exist_ok=True)
        self.fixed = self.fixture.root / 'Program Files' / 'ElevhubServer'
        shutil.copytree(self.fixture.stable, self.fixed)
        shutil.rmtree(self.fixed / 'control')
        (self.fixed / 'server').mkdir()
        shutil.copy2(HELPER, self.fixed / 'server/generation-startup.ps1')
        self.control = self.fixed / 'control'
        self.control.mkdir()
        self.lock_path = self.control / 'generation.lock'
        self.lock_path.write_bytes(b'1')
        record = dict(self.fixture.record, runtime_root=str(self.fixed), revision=1)
        raw = json.dumps(record, sort_keys=True).encode('utf-8')
        with GenerationLock(self.lock_path, exclusive=True):
            pending = self.control / 'active-generation.tmp'
            pending.write_bytes(raw)
            os.replace(pending, self.control / 'active-generation.json')
        self.installation_id = record['installation_id']
        self.selection_hash = hashlib.sha256(raw).hexdigest()

    def _start(self, expected=None):
        script = self.fixture.root / 'lease.ps1'
        expected_arg = " -ExpectedSelection '" + expected + "'" if expected else ''
        script.write_text(
            "$ErrorActionPreference='Stop'\n"
            ". '" + str(self.fixed / 'server/generation-startup.ps1').replace("'", "''") + "'\n"
            "$lease=New-GenerationLease -InstallRoot '" + str(self.fixed).replace("'", "''") +
            "' -InstallationId '" + self.installation_id + "'" + expected_arg + "\n"
            "$lease.Snapshot | ConvertTo-Json -Compress\n"
            "$null=[Console]::In.ReadLine()\n"
            "Close-GenerationLease $lease\n",
            encoding='utf-8')
        return subprocess.Popen([POWERSHELL, '-NoLogo', '-NoProfile', '-NonInteractive',
                                 '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                                cwd=self.fixed, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_lease_holds_generation_lock_until_close_and_validates_snapshot(self):
        process = self._start(self.selection_hash)
        try:
            line = process.stdout.readline().strip()
            self.assertTrue(line)
            snapshot = json.loads(line)
            self.assertEqual(snapshot['runtime_root'], str(self.fixed))
            self.assertEqual(snapshot['selection_sha256'], self.selection_hash)
            with self.assertRaises(RuntimeError):
                GenerationLock(self.lock_path, exclusive=True)
            process.stdin.write('\n')
            process.stdin.flush()
            self.assertEqual(process.wait(timeout=15), 0, process.stderr.read())
            with GenerationLock(self.lock_path, exclusive=True):
                pass
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def test_wrong_expected_selection_fails_without_leaking_lease(self):
        process = self._start('0' * 64)
        try:
            process.wait(timeout=15)
            self.assertNotEqual(process.returncode, 0)
            with GenerationLock(self.lock_path, exclusive=True):
                pass
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def test_no_expected_selection_uses_active_snapshot(self):
        """The optional switch must be omitted, while the lease still validates the active copy."""
        process = self._start()
        try:
            line = process.stdout.readline().strip()
            self.assertTrue(line)
            snapshot = json.loads(line)
            self.assertEqual(snapshot['installation_id'], self.installation_id)
            self.assertEqual(snapshot['selection_sha256'], self.selection_hash)
            with self.assertRaises(RuntimeError):
                GenerationLock(self.lock_path, exclusive=True)
            process.stdin.write('\n')
            process.stdin.flush()
            self.assertEqual(process.wait(timeout=15), 0, process.stderr.read())
            with GenerationLock(self.lock_path, exclusive=True):
                pass
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def test_killing_powerShell_supervisor_releases_child_lease(self):
        process = self._start(self.selection_hash)
        try:
            self.assertTrue(process.stdout.readline().strip())
            process.kill()
            process.wait(timeout=10)
            with GenerationLock(self.lock_path, exclusive=True):
                pass
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


if __name__ == '__main__':
    unittest.main()
