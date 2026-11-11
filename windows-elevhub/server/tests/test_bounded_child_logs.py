"""Actual anonymous pipes and bounded files, with no service or hardware start."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from app.instance import InstanceLock

SPEC = importlib.util.spec_from_file_location('bounded_bootstrap', Path(__file__).parents[1] / 'bootstrap.py')
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


class BoundedChildLogTests(unittest.TestCase):
    def test_reader_start_failure_cleans_child_even_if_tree_close_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            process = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(30)'],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
            class Tree:
                def close(self):
                    raise OSError('tree close failed')
            try:
                with patch.object(bootstrap, 'read_runtime_config', return_value={}), \
                     patch.object(bootstrap, 'check'), \
                     patch.object(bootstrap, '_commands', return_value=[('fixture', ['fixture'])]), \
                     patch.object(bootstrap.signal, 'signal'), \
                     patch.object(bootstrap.subprocess, 'Popen', return_value=process), \
                     patch('app.process_tree.ProcessTree', return_value=Tree()), \
                     patch.object(bootstrap, 'BoundedChildLog', side_effect=OSError('reader start failed')):
                    with self.assertRaisesRegex(OSError, 'tree close failed'):
                        bootstrap.run(root, root)
                self.assertIsNotNone(process.poll())
                self.assertTrue(process.stdout.closed)
                InstanceLock(root).close()
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                process.stdout.close()

    def test_supervisor_log_failure_stops_child_before_unlocking_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            process = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(30)'],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
            logs = []
            class Tree:
                def close(self):
                    # Data must remain leased while children are terminated.
                    with self_test.assertRaises(RuntimeError):
                        InstanceLock(root)
                    process.kill()
            self_test = self
            try:
                real_log = bootstrap.BoundedChildLog
                def create_log(pipe, _path):
                    log = real_log(pipe, root / 'missing' / 'service.log')
                    logs.append(log)
                    return log
                with patch.object(bootstrap, 'read_runtime_config', return_value={}), \
                     patch.object(bootstrap, 'check'), \
                     patch.object(bootstrap, '_commands', return_value=[('fixture', ['fixture'])]), \
                     patch.object(bootstrap.signal, 'signal'), \
                     patch.object(bootstrap.subprocess, 'Popen', return_value=process), \
                     patch('app.process_tree.ProcessTree', return_value=Tree()), \
                     patch.object(bootstrap, 'BoundedChildLog', side_effect=create_log):
                    with self.assertRaisesRegex(RuntimeError, 'skrive sin log'):
                        bootstrap.run(root, root)
                self.assertIsNotNone(process.poll())
                self.assertTrue(process.stdout.closed)
                self.assertTrue(all(not log.thread.is_alive() for log in logs))
                InstanceLock(root).close()
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                for log in logs:
                    log.close()
                process.stdout.close()

    def test_restart_bounds_old_logs_and_keeps_their_latest_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'service.log'
            for index in range(4):
                target = path if index == 0 else Path(str(path) + '.' + str(index))
                target.write_bytes(b'old-data' * 1000 + bytes([index]) * 32)
            reader, writer = os.pipe()
            os.close(writer)
            log = bootstrap.BoundedChildLog(os.fdopen(reader, 'rb', buffering=0), path, 32, 3)
            try:
                log.thread.join(timeout=3)
                self.assertIsNone(log.error)
                for index in range(4):
                    target = path if index == 0 else Path(str(path) + '.' + str(index))
                    self.assertEqual(target.read_bytes(), bytes([index]) * 32)
            finally:
                log.close()

    def test_large_binary_line_rotates_and_retains_exact_tail(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'service.log'
            # This line exceeds pipe capacity and never contains a newline.
            payload = bytes(range(256)) * 1200
            process = subprocess.Popen([sys.executable, '-B', '-c',
                'import os; os.write(1, bytes(range(256))*1200)'],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
            log = bootstrap.BoundedChildLog(process.stdout, path, max_bytes=4096, backups=3)
            try:
                self.assertEqual(process.wait(timeout=10), 0)
                log.thread.join(timeout=5)
                self.assertFalse(log.thread.is_alive())
                self.assertIsNone(log.error)
                files = sorted(Path(folder).glob('service.log*'))
                self.assertEqual(len(files), 4)
                self.assertTrue(all(file.stat().st_size <= 4096 for file in files))
                combined = b''.join(Path(str(path) + '.' + str(i)).read_bytes() for i in (3, 2, 1)) + path.read_bytes()
                self.assertEqual(combined, payload[-len(combined):])
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                log.close()
            self.assertTrue(process.stdout.closed)

    def test_close_does_not_wait_for_a_retained_idle_writer(self):
        with tempfile.TemporaryDirectory() as folder:
            reader, writer = os.pipe()
            pipe = os.fdopen(reader, 'rb', buffering=0)
            log = bootstrap.BoundedChildLog(pipe, Path(folder) / 'service.log')
            try:
                started = time.monotonic()
                log.close()
                self.assertLess(time.monotonic() - started, 2)
                self.assertFalse(log.thread.is_alive())
                self.assertTrue(pipe.closed)
                self.assertFalse(log.thread.daemon)
            finally:
                os.close(writer)
                log.close()

    def test_log_io_failure_is_exposed_and_pipe_is_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            reader, writer = os.pipe()
            pipe = os.fdopen(reader, 'rb', buffering=0)
            log = bootstrap.BoundedChildLog(pipe, Path(folder) / 'missing' / 'service.log')
            try:
                log.thread.join(timeout=3)
                self.assertFalse(log.thread.is_alive())
                self.assertIsInstance(log.error, OSError)
                self.assertTrue(pipe.closed)
            finally:
                os.close(writer)
                log.close()


if __name__ == '__main__':
    unittest.main()
