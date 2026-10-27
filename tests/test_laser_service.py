import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from app.instance import InstanceLock
from app.laser_service import command, load_config, supervise
from app.laser_service import ProcessTree


class FakeProcess:
    def __init__(self, code):
        self.returncode = code
        self.pid = 123
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


class FakeStop:
    def __init__(self): self.calls = 0
    def is_set(self): return False
    def wait(self, _seconds): self.calls += 1


class LaserServiceTests(unittest.TestCase):
    def test_config_and_child_command_are_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'laser.json'
            config = {'data': str(Path(temp) / 'data'), 'host': '0.0.0.0', 'port': 8766,
                      'teacher_host': '10.0.0.2', 'teacher_port': 8767}
            path.write_text(json.dumps(config), encoding='utf-8')
            loaded = load_config(path)
            self.assertEqual(command(loaded)[-8:], ['--host', '0.0.0.0', '--port', '8766', '--teacher-host', '10.0.0.2', '--teacher-port', '8767'])

    def test_invalid_config_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'laser.json'; path.write_text('{"data":"x"}', encoding='utf-8')
            with self.assertRaises(ValueError): load_config(path)

    def test_nonzero_child_restarts_then_clean_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            config = {'data': str(Path(temp) / 'data'), 'host': '127.0.0.1', 'port': 8766,
                      'teacher_host': '127.0.0.1', 'teacher_port': 8767}
            seen = []
            def popen(args, **kwargs):
                seen.append((args, kwargs)); return FakeProcess(1 if len(seen) == 1 else 0)
            stop = FakeStop()
            self.assertEqual(supervise(config, popen=popen, stop_event=stop, retry_seconds=0), 0)
            self.assertEqual(len(seen), 2)
            self.assertEqual(seen[0][0], command(config))
            self.assertTrue((Path(config['data']) / 'service' / 'laser-server.log').exists())

    def test_existing_supervisor_lock_prevents_second_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp) / 'data'; service = data / 'service'; service.mkdir(parents=True)
            lock = InstanceLock(service)
            try:
                config = {'data': str(data), 'host': '127.0.0.1', 'port': 8766,
                          'teacher_host': '127.0.0.1', 'teacher_port': 8767}
                called = []
                self.assertEqual(supervise(config, popen=lambda *a, **k: called.append(1)), 0)
                self.assertEqual(called, [])
            finally:
                lock.close()

    @unittest.skipUnless(os.name == 'nt', 'Windows Job Object ownership is Windows-only')
    def test_windows_job_object_kills_owned_child(self):
        child = subprocess.Popen(['python', '-c', 'import time; time.sleep(60)'])
        tree = ProcessTree(child)
        try:
            self.assertIsNone(child.poll())
        finally:
            tree.close()
        child.wait(timeout=5)
        self.assertIsNotNone(child.returncode)


if __name__ == '__main__': unittest.main()
