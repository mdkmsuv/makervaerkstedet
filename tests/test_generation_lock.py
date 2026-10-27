import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from app.generation_lock import GenerationLock


class GenerationLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "generation.lock"
        self.path.write_bytes(b"marker")

    @staticmethod
    def _python():
        candidate = Path.cwd() / ".venv" / "Scripts" / "python.exe"
        return str(candidate if candidate.is_file() else Path(sys.executable))

    def _reader(self, ready, *, probe=False):
        code = """import sys,time
from pathlib import Path
from app.generation_lock import GenerationLock
try:
    lock=GenerationLock(sys.argv[1])
except RuntimeError:
    Path(sys.argv[2]).write_text('blocked', encoding='ascii')
    raise SystemExit(3)
Path(sys.argv[2]).write_text('ready', encoding='ascii')
if sys.argv[3] == 'probe':
    time.sleep(0.1)
else:
    time.sleep(30)
"""
        return subprocess.Popen([self._python(), "-c", code, str(self.path), str(ready),
                                 "probe" if probe else "hold"], cwd=str(Path.cwd()),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @staticmethod
    def _wait_file(path, value, process=None, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.is_file() and path.read_text(encoding="ascii") == value:
                return True
            if process is not None and process.poll() is not None and not path.is_file():
                return False
            time.sleep(0.05)
        return path.is_file() and path.read_text(encoding="ascii") == value

    @staticmethod
    def _kill_and_wait(process):
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)

    def tearDown(self):
        self.tmp.cleanup()

    def test_readers_coexist_and_writer_is_exclusive(self):
        one = GenerationLock(self.path)
        two = GenerationLock(self.path)
        with self.assertRaises(RuntimeError):
            GenerationLock(self.path, exclusive=True)
        two.close()
        with self.assertRaises(RuntimeError):
            GenerationLock(self.path, exclusive=True)
        one.close()
        with GenerationLock(self.path, exclusive=True):
            with self.assertRaises(RuntimeError):
                GenerationLock(self.path)
            with self.assertRaises(RuntimeError):
                GenerationLock(self.path, exclusive=True)

    def test_subprocess_crash_releases_lock(self):
        ready = Path(self.tmp.name) / "crash.ready"
        child = self._reader(ready)
        try:
            self.assertTrue(self._wait_file(ready, "ready", child))
            with self.assertRaises(RuntimeError):
                GenerationLock(self.path, exclusive=True)
            self._kill_and_wait(child)
            deadline = time.monotonic() + 5
            acquired = None
            while time.monotonic() < deadline:
                try:
                    acquired = GenerationLock(self.path, exclusive=True)
                    break
                except RuntimeError:
                    time.sleep(0.05)
            self.assertIsNotNone(acquired, "LockFileEx was not released after child exit")
            acquired.close()
        finally:
            self._kill_and_wait(child)

    def test_two_reader_processes_coexist_and_writer_waits_for_both(self):
        ready_one = Path(self.tmp.name) / "one.ready"
        ready_two = Path(self.tmp.name) / "two.ready"
        one = self._reader(ready_one)
        two = self._reader(ready_two)
        try:
            self.assertTrue(self._wait_file(ready_one, "ready", one))
            self.assertTrue(self._wait_file(ready_two, "ready", two))
            with self.assertRaises(RuntimeError):
                GenerationLock(self.path, exclusive=True)
            self._kill_and_wait(one)
            with self.assertRaises(RuntimeError):
                GenerationLock(self.path, exclusive=True)
            self._kill_and_wait(two)
            deadline = time.monotonic() + 5
            acquired = None
            while time.monotonic() < deadline:
                try:
                    acquired = GenerationLock(self.path, exclusive=True)
                    break
                except RuntimeError:
                    time.sleep(0.05)
            self.assertIsNotNone(acquired)
            acquired.close()
        finally:
            self._kill_and_wait(one)
            self._kill_and_wait(two)

    def test_writer_rejects_new_reader_process(self):
        with GenerationLock(self.path, exclusive=True):
            result = Path(self.tmp.name) / "probe.result"
            child = self._reader(result, probe=True)
            try:
                self.assertTrue(self._wait_file(result, "blocked", child))
                self.assertEqual(child.wait(timeout=5), 3)
            finally:
                self._kill_and_wait(child)

    def test_marker_is_not_mutated_and_links_are_rejected(self):
        before = self.path.read_bytes()
        with GenerationLock(self.path):
            self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.read_bytes(), before)
        link = Path(self.tmp.name) / "link.lock"
        try:
            link.symlink_to(self.path)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaises(ValueError):
            GenerationLock(link)

    def test_missing_and_nonregular_files_are_rejected(self):
        with self.assertRaises(FileNotFoundError):
            GenerationLock(Path(self.tmp.name) / "missing.lock")
        with self.assertRaises(ValueError):
            GenerationLock(self.tmp.name)


if __name__ == "__main__":
    unittest.main()
