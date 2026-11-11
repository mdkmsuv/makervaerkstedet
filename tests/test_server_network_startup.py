import errno
import importlib.util
import io
from pathlib import Path
import signal
import socket
import sys
import tempfile
import time
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

REPO_ROOT = Path(__file__).parents[1]
SERVER_ROOT = REPO_ROOT / 'windows-elevhub' / 'server'
sys.path.insert(0, str(REPO_ROOT))
from app import process_tree


def load_bootstrap():
    spec = importlib.util.spec_from_file_location(
        'server_bootstrap_network_test', SERVER_ROOT / 'bootstrap.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bootstrap = load_bootstrap()
bootstrap.socket = socket
bootstrap.time = time


class FakeStopEvent:
    def __init__(self):
        self.stopped = False
        self.waited = 0.0

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, timeout):
        self.waited += timeout
        return self.stopped


class BindSocket:
    def __init__(self, bind, on_ready=None):
        self.bind_call = bind
        self.on_ready = on_ready

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def bind(self, address):
        self.bind_call(address)


class ServerNetworkStartupTests(unittest.TestCase):
    def test_wait_retries_past_two_hours_until_exact_role_ip_can_bind(self):
        attempts = 0
        now = [0.0]
        stopped = FakeStopEvent()

        def bind(address):
            nonlocal attempts
            attempts += 1
            self.assertEqual(address, ('198.51.100.42', 0))
            if attempts <= 1450:
                raise OSError(errno.EADDRNOTAVAIL, 'address not available')

        output = io.StringIO()
        with patch.object(bootstrap.socket, 'socket', side_effect=lambda *_: BindSocket(bind)), \
             patch.object(bootstrap.time, 'monotonic', side_effect=lambda: now[0]), \
             patch.object(stopped, 'wait', side_effect=lambda seconds: now.__setitem__(0, now[0] + seconds) or False), \
             redirect_stdout(output):
            ready = bootstrap.wait_for_role_ip('198.51.100.42', stopped, retry_interval=6)

        self.assertTrue(ready)
        self.assertEqual(attempts, 1451)
        self.assertGreater(now[0], 7200)
        self.assertEqual(len(output.getvalue().splitlines()), 2)
        self.assertIn('ready', output.getvalue().lower())

    def test_ready_address_does_not_wait(self):
        stopped = FakeStopEvent()
        with patch.object(bootstrap.socket, 'socket', side_effect=lambda *_: BindSocket(lambda _address: None)):
            self.assertTrue(bootstrap.wait_for_role_ip('198.51.100.42', stopped))
        self.assertEqual(stopped.waited, 0)

    def test_non_address_error_is_not_retried(self):
        stopped = FakeStopEvent()

        def fail(_address):
            raise OSError(errno.EADDRINUSE, 'address already in use')

        with patch.object(bootstrap.socket, 'socket', side_effect=lambda *_: BindSocket(fail)):
            with self.assertRaises(OSError) as caught:
                bootstrap.wait_for_role_ip('198.51.100.42', stopped)
        self.assertEqual(caught.exception.errno, errno.EADDRINUSE)
        self.assertEqual(stopped.waited, 0)

    def test_windows_address_not_available_code_is_retried(self):
        attempts = 0
        stopped = FakeStopEvent()

        def bind(_address):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                error = OSError('address not available')
                error.winerror = 10049
                raise error

        with patch.object(bootstrap.socket, 'socket', side_effect=lambda *_: BindSocket(bind)), \
             patch.object(stopped, 'wait', side_effect=lambda _seconds: False):
            self.assertTrue(bootstrap.wait_for_role_ip('198.51.100.42', stopped, retry_interval=0))
        self.assertEqual(attempts, 2)

    def test_run_launches_no_child_until_address_is_ready(self):
        bind_attempts = 0
        ready = [False]
        signal_handlers = {}
        stopped = FakeStopEvent()

        def bind(address):
            nonlocal bind_attempts
            self.assertEqual(address[0], '198.51.100.42')
            bind_attempts += 1
            if bind_attempts < 3:
                raise OSError(errno.EADDRNOTAVAIL, 'address not available')
            ready[0] = True

        class Process:
            stdout = io.BytesIO()

            def poll(self):
                return None

            def wait(self, timeout=None):
                return 0

        process = Process()

        class Tree:
            def __init__(self, _process):
                signal_handlers[signal.SIGTERM]()

            def close(self):
                pass

        class Log:
            error = None

            def __init__(self, *_args):
                pass

            def close(self):
                pass

        launched = []
        output = io.StringIO()

        def popen(*_args, **_kwargs):
            self.assertTrue(ready[0], 'a child was launched before the role IP became bindable')
            launched.append(True)
            return process

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(process_tree, 'ProcessTree', Tree), \
             patch.object(bootstrap, 'InstanceLock', lambda _path: types.SimpleNamespace(close=lambda: None)), \
             patch.object(bootstrap, 'assert_not_restored'), \
             patch.object(bootstrap, 'write_protocol'), \
             patch.object(bootstrap, 'read_runtime_config', return_value={'elev_adresse': '198.51.100.42'}), \
             patch.object(bootstrap, 'check'), \
             patch.object(bootstrap, '_commands', return_value=[('hub', ['server'])]), \
             patch.object(bootstrap, 'BoundedChildLog', Log), \
             patch.object(bootstrap.subprocess, 'Popen', side_effect=popen), \
             patch.object(bootstrap.signal, 'signal', side_effect=lambda sig, handler: signal_handlers.__setitem__(sig, handler)), \
             patch.object(bootstrap, 'ROLE_IP_RETRY_SECONDS', 0.001, create=True), \
             patch.object(bootstrap.threading, 'Event', return_value=stopped), \
             patch.object(bootstrap.socket, 'socket', side_effect=lambda *_: BindSocket(bind)), \
             patch.object(bootstrap.time, 'sleep', side_effect=lambda _seconds: None), \
             redirect_stdout(output):
            # SIGTERM is delivered after the first child starts, just as a
            # normal service stop should interrupt the supervisor cleanly.
            result = bootstrap.run(Path(directory), Path(directory), role_ip='198.51.100.42')

        self.assertEqual(result, 0)
        self.assertEqual(bind_attempts, 3)
        self.assertEqual(launched, [True])

    def test_run_cancelled_during_wait_launches_no_child(self):
        signal_handlers = {}
        launches = []
        stopped = FakeStopEvent()
        def unavailable(_address):
            raise OSError(errno.EADDRNOTAVAIL, 'address not available')

        def cancel_on_wait(_seconds):
            signal_handlers[signal.SIGTERM]()
            return True

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(process_tree, 'ProcessTree', object), \
             patch.object(bootstrap, 'InstanceLock', lambda _path: types.SimpleNamespace(close=lambda: None)), \
             patch.object(bootstrap, 'assert_not_restored'), \
             patch.object(bootstrap, 'write_protocol'), \
             patch.object(bootstrap, 'read_runtime_config', return_value={'elev_adresse': '198.51.100.42'}), \
             patch.object(bootstrap, 'check'), \
             patch.object(bootstrap, '_commands', return_value=[('hub', ['server'])]), \
             patch.object(bootstrap.subprocess, 'Popen', side_effect=lambda *_a, **_k: self.fail('child launched before cancellation')), \
             patch.object(bootstrap.signal, 'signal', side_effect=lambda sig, handler: signal_handlers.__setitem__(sig, handler)), \
             patch.object(bootstrap, 'ROLE_IP_RETRY_SECONDS', 0.001, create=True), \
             patch.object(bootstrap.threading, 'Event', return_value=stopped), \
             patch.object(bootstrap.socket, 'socket', side_effect=lambda *_: BindSocket(unavailable)), \
             patch.object(stopped, 'wait', side_effect=cancel_on_wait):
            result = bootstrap.run(Path(directory), Path(directory), role_ip='198.51.100.42')

        self.assertEqual(result, 0)
        self.assertEqual(launches, [])

    def test_check_command_does_not_wait_for_network(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(sys, 'argv', ['bootstrap', '--package-root', directory,
                                        '--data-root', directory, '--check']), \
             patch.object(bootstrap, 'check', return_value={'ok': True}), \
             patch.object(bootstrap, 'wait_for_role_ip', side_effect=AssertionError('check must not wait')), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(bootstrap.main(), 0)


if __name__ == '__main__':
    unittest.main()
