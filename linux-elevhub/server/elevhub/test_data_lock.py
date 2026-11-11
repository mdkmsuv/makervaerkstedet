import json
import os
from pathlib import Path
import subprocess
import socket
import threading
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

from data_lock import InstanceLock
import elevhub


class DataLockTests(unittest.TestCase):
    def test_lock_is_compatible_with_app_instance_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            ours = InstanceLock(folder)
            try:
                code = (
                    "from app.instance import InstanceLock; "
                    "import sys; "
                    "lock=InstanceLock(sys.argv[1]); print('acquired', flush=True)"
                )
                env = dict(os.environ)
                env['PYTHONPATH'] = str(Path.cwd())
                child = subprocess.run([sys.executable, '-c', code, folder],
                                       env=env, capture_output=True, text=True, timeout=5)
                self.assertNotEqual(child.returncode, 0)
                self.assertNotIn('acquired', child.stdout)
            finally:
                ours.close()

    def test_lock_is_released_after_close_and_process_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            first = InstanceLock(folder)
            first.close()
            second = InstanceLock(folder)
            second.close()
            code = (
                "from data_lock import InstanceLock; import sys; "
                "InstanceLock(sys.argv[1])"
            )
            env = dict(os.environ)
            env['PYTHONPATH'] = str(Path.cwd() / 'linux-elevhub' / 'server' / 'elevhub')
            subprocess.run([sys.executable, '-c', code, folder], env=env, check=True, timeout=5)
            third = InstanceLock(folder)
            third.close()

    def test_lock_file_has_shared_one_byte_protocol(self):
        with tempfile.TemporaryDirectory() as folder:
            lock = InstanceLock(folder)
            lock.close()
            self.assertEqual(Path(folder, 'instance.lock').read_bytes(), b'1')

    def test_restore_hold_blocks_before_capability_write(self):
        with tempfile.TemporaryDirectory() as folder:
            hold = Path(folder, '.restore-hold.json')
            hold.write_text('{}', encoding='utf-8')
            args = SimpleNamespace(data=Path(folder), konfiguration=Path(folder, 'missing.json'),
                                   nulstil_laererkode=True)
            with self.assertRaises(RuntimeError):
                elevhub.run(args)
            self.assertFalse(Path(folder, 'backup-protocol.json').exists())

    def test_restore_hold_in_ancestor_blocks_nested_data_path(self):
        with tempfile.TemporaryDirectory() as root:
            parent = Path(root, 'restore-root')
            data = parent / 'hub-data'
            data.mkdir(parents=True)
            (parent / '.restore-hold.json').write_text('{}', encoding='utf-8')
            args = SimpleNamespace(data=data, konfiguration=data / 'missing.json',
                                   nulstil_laererkode=True)
            with self.assertRaises(RuntimeError):
                elevhub.run(args)
            self.assertFalse((data / 'backup-protocol.json').exists())

    def test_hub_server_close_drains_active_request_before_lock_can_close(self):
        started = threading.Event()
        release = threading.Event()

        class Handler(elevhub.Grund):
            def do_GET(self):
                started.set()
                release.wait(5)
                self.svar(200, b'ok')

        server = elevhub.Server(('127.0.0.1', 0), Handler)
        server.navn = 'test'
        serving = threading.Thread(target=server.serve_forever)
        serving.start()
        client = socket.create_connection(server.server_address, timeout=2)
        client.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
        self.assertTrue(started.wait(2))
        server.shutdown()
        serving.join(2)
        closing = threading.Thread(target=server.server_close)
        closing.start()
        try:
            self.assertTrue(closing.is_alive(), 'server_close must wait for active handlers')
        finally:
            release.set()
            closing.join(3)
            client.close()
        self.assertFalse(closing.is_alive())


if __name__ == '__main__':
    unittest.main()
