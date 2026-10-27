"""The non-privileged test runner owns and bounds its entire fixture tree."""
import ctypes
from ctypes import wintypes
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import signal
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

from scripts import run_tests


def _alive(pid):
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A reaped-orphan test child can remain as a harmless zombie briefly.
    stat = Path(f'/proc/{pid}/stat')
    return not stat.exists() or stat.read_text().split()[2] != 'Z'


class TestRunnerProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='runner-tree-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _fixture(self, *, parent_exits):
        pid_file = self.root / 'grandchild.pid'
        script = (
            'import pathlib,subprocess,sys,time\n'
            'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"])\n'
            'pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n'
            'print("fixture-ready",flush=True)\n'
            + ('' if parent_exits else 'time.sleep(60)\n')
        )
        return [sys.executable, '-u', '-c', script, str(pid_file)], pid_file

    def _assert_child_reaped(self, pid_file):
        self.assertTrue(pid_file.is_file(), 'fixture did not launch its grandchild')
        pid = int(pid_file.read_text())
        self.addCleanup(lambda: os.kill(pid, signal.SIGTERM) if _alive(pid) else None)
        deadline = time.monotonic() + 3
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(_alive(pid), f'grandchild {pid} escaped the test lifetime')

    def test_timeout_kills_grandchild_and_keeps_capture_bounded(self):
        command, pid_file = self._fixture(parent_exits=False)
        started = time.monotonic()
        code, expired, output, errors = run_tests._capture(command, cwd=self.root, timeout=1.5)
        self.assertLess(time.monotonic() - started, 7)
        self.assertIsNone(code)
        self.assertTrue(expired)
        self.assertIn(b'fixture-ready', output)
        self.assertEqual(errors, b'')
        self._assert_child_reaped(pid_file)

    def test_successful_parent_also_reaps_its_grandchild(self):
        command, pid_file = self._fixture(parent_exits=True)
        started = time.monotonic()
        code, expired, output, errors = run_tests._capture(command, cwd=self.root, timeout=5)
        self.assertLess(time.monotonic() - started, 7)
        self.assertEqual(code, 0)
        self.assertFalse(expired)
        self.assertIn(b'fixture-ready', output)
        self.assertEqual(errors, b'')
        self._assert_child_reaped(pid_file)

    def test_one_failed_file_does_not_hide_later_test_file(self):
        suite = self.root / 'tests'
        suite.mkdir()
        (suite / 'test_a_fail.py').write_text(
            'import unittest\nclass Case(unittest.TestCase):\n'
            ' def test_failure(self): self.fail("synthetic failure")\n', encoding='utf-8')
        (suite / 'test_b_pass.py').write_text(
            'import unittest\nclass Case(unittest.TestCase):\n'
            ' def test_success(self): self.assertTrue(True)\n', encoding='utf-8')
        output = io.StringIO()
        with (mock.patch.object(run_tests, 'ROOT', self.root),
              mock.patch.object(run_tests, 'SUITES', (('windows-shared', 'tests', 'test_*.py'),)),
              mock.patch.object(sys, 'argv', ['run_tests.py', '--timeout', '10']),
              redirect_stdout(output)):
            result = run_tests.main()
        text = output.getvalue()
        self.assertEqual(result, 1)
        self.assertIn('[FAILED ] windows-shared:test_a_fail.py', text)
        self.assertIn('[PASSED ] windows-shared:test_b_pass.py', text)
        self.assertIn('Ran 1 test', text)
        self.assertIn('Checks: 2; failed/timeouts: 1', text)

    def test_public_suites_exclude_retired_camera_service(self):
        self.assertNotIn('platecam', {name for name, _, _ in run_tests.SUITES})
        self.assertFalse(any(path and path.startswith('pladekamera/')
                             for _, path, _ in run_tests.SUITES))

    @unittest.skipUnless(os.name == 'nt' and shutil.which('wsl.exe'), 'requires WSL on Windows')
    def test_wsl_timeout_reaps_linux_grandchild(self):
        wsl_root = run_tests._wsl_root()
        if not wsl_root:
            self.skipTest('WSL project root is unavailable')
        probe = run_tests._capture(['wsl.exe', '--cd', wsl_root, '--', 'python3',
                                    '--version'], cwd=None, timeout=10)
        if probe[0] != 0:
            self.skipTest('WSL Python is unavailable')
        with tempfile.TemporaryDirectory(prefix='runner-wsl-', dir=run_tests.ROOT) as folder:
            pid_file = Path(folder) / 'grandchild.pid'
            relative = pid_file.relative_to(run_tests.ROOT)
            wsl_file = relative.as_posix()
            script = (
                'import pathlib,subprocess,sys,time\n'
                'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"])\n'
                'stat=pathlib.Path(f"/proc/{child.pid}/stat").read_text().split()\n'
                'pathlib.Path(sys.argv[1]).write_text(f"{child.pid} {stat[21]}")\n'
                'print("linux-fixture-ready",flush=True)\n'
                'time.sleep(60)\n'
            )
            (Path(folder) / 'fixture.py').write_text(script, encoding='utf-8')
            command = ['wsl.exe', '--cd', wsl_root, '--', 'python3', '-B',
                       'scripts/test_wsl_supervisor.py', '1.5', 'python3', '-u',
                       (relative.parent / 'fixture.py').as_posix(), wsl_file]
            started = time.monotonic()
            with mock.patch.object(run_tests, '_command', return_value=command):
                result = run_tests._run('wsl-fixture', 'tests', 'unused', timeout=1.5, wsl=True)
            self.assertLess(time.monotonic() - started, 12)
            self.assertEqual(result['status'], 'timeout', result['stderr'])
            self.assertIn('WSL_TEST_TIMEOUT:', result['stderr'])
            self.assertIn('linux-fixture-ready', result['stdout'])
            pid, birth = pid_file.read_text().split()
            # The birth tick guards against accidentally querying or cleaning
            # an unrelated Linux process if a PID has already been reused.
            check = (
                'import pathlib,sys,os,signal\n'
                'pid,start=map(int,sys.argv[1:])\n'
                'path=pathlib.Path(f"/proc/{pid}/stat")\n'
                'parts=path.read_text().split() if path.exists() else []\n'
                'alive=bool(parts and int(parts[21])==start and parts[2]!="Z")\n'
                'print("alive" if alive else "dead")\n'
                'if alive: os.kill(pid,signal.SIGKILL)\n'
            )
            (Path(folder) / 'check.py').write_text(check, encoding='utf-8')
            observed = run_tests._capture(['wsl.exe', '--cd', wsl_root, '--',
                                           'python3', (relative.parent / 'check.py').as_posix(),
                                           pid, birth],
                                          cwd=None, timeout=10)
            self.assertEqual(observed[0], 0, observed[3])
            self.assertEqual(observed[2].strip(), b'dead')


if __name__ == '__main__':
    unittest.main()
