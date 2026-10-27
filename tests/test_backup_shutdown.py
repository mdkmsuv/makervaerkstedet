from pathlib import Path
import importlib.util
import subprocess
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler

from app.instance import InstanceLock


class BackupShutdownTests(unittest.TestCase):
    def test_both_laser_servers_drain_active_requests_before_close_returns(self):
        from app import laser_server, laser_station
        for module in (laser_server, laser_station):
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as folder:
                entered, release, closed = threading.Event(), threading.Event(), threading.Event()

                class Handler(BaseHTTPRequestHandler):
                    def do_GET(self):
                        entered.set()
                        release.wait(5)

                with mock.patch.object(module, 'Handler', Handler):
                    server = (module.create_server(folder, port=0, inbox=object()) if module is laser_server
                              else module.create_station_server(object(), port=0))
                serving = threading.Thread(target=server.serve_forever)
                serving.start()
                client = socket.create_connection(server.server_address, timeout=2)
                closing = None
                try:
                    client.sendall(b'GET / HTTP/1.0\r\nHost: localhost\r\n\r\n')
                    self.assertTrue(entered.wait(2))
                    server.shutdown()
                    serving.join(2)
                    def close():
                        server.server_close()
                        closed.set()
                    closing = threading.Thread(target=close)
                    closing.start()
                    self.assertFalse(closed.wait(0.15), 'active request must finish before server_close returns')
                finally:
                    release.set()
                    server.shutdown()
                    serving.join(3)
                    if closing is not None:
                        closing.join(3)
                    server.server_close()
                    client.close()
                self.assertTrue(closed.is_set())

    @staticmethod
    def _load_bootstrap():
        path = Path(__file__).parents[1] / "windows-elevhub" / "server" / "bootstrap.py"
        spec = importlib.util.spec_from_file_location("backup_shutdown_bootstrap", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def test_print_server_drains_socket_before_application_releases_lock_and_log(self):
        import app.server as module
        events = []
        app = module.Application.__new__(module.Application)
        app.folder = None
        app.demo = True
        app.instance_lock = None
        app.queue = mock.Mock()
        app.worker = app.firmware = app.cameras = None
        app.drivers = {}
        app.log_handler = None
        app._closed = False

        class Server:
            def __init__(self, address, handler, *, project_origin_config):
                if project_origin_config != app.folder / 'print-project-origins.json':
                    raise AssertionError('Production print config must use component data')
                self.app = None
            def serve_forever(self):
                events.append("serve")
            def server_close(self):
                try:
                    InstanceLock(self.app.folder)
                except RuntimeError:
                    events.append("lock-held-during-server-close")
                else:
                    raise AssertionError("server socket closed after application lock release")
                events.append("server-close")

        with tempfile.TemporaryDirectory() as folder, mock.patch.object(module, "Application", return_value=app), \
             mock.patch.object(module, "Server", Server), mock.patch.object(module.logging, "FileHandler") as handler, \
             mock.patch.object(sys, "argv", ["server", "--demo", "--data", folder]):
            app.folder = Path(folder)
            app.instance_lock = InstanceLock(app.folder)
            original_handlers = list(module.logging.getLogger().handlers)
            original_level = module.logging.getLogger().level
            try:
                def close_log():
                    with self.assertRaises(RuntimeError):
                        InstanceLock(app.folder)
                    events.append("log-handler-close")
                handler.return_value.close.side_effect = close_log
                module.main()
                InstanceLock(app.folder).close()
            finally:
                module.logging.getLogger().handlers[:] = original_handlers
                module.logging.getLogger().setLevel(original_level)
        self.assertLess(events.index("server-close"), events.index("log-handler-close"))
        self.assertIn("lock-held-during-server-close", events)
        self.assertEqual(app._closed, True)

    def test_laser_teacher_server_creation_failure_closes_first_socket_and_releases_lock(self):
        import app.laser_server as module
        events = []

        class Server:
            inbox = object()
            def server_close(self):
                events.append("server-close")

        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(module, "create_server", side_effect=[Server(), RuntimeError("teacher bind")]) as create, \
                 mock.patch.object(sys, "argv", ["laser", "--data", folder, "--port", "1", "--teacher-port", "2"]):
                with self.assertRaisesRegex(RuntimeError, "teacher bind"):
                    module.main()
                self.assertEqual(len(create.call_args_list), 2)
                for call in create.call_args_list:
                    self.assertEqual(call.kwargs['project_origin_config'], Path(folder) / 'laser-project-origins.json')
            self.assertEqual(events, ["server-close"])
            lock = InstanceLock(folder)
            lock.close()

    def test_laser_teacher_thread_start_failure_does_not_shutdown_unstarted_server(self):
        import app.laser_server as module
        events = []

        class Server:
            inbox = object()
            def __init__(self, name):
                self.name = name
                self.shutdown = mock.Mock(side_effect=lambda: events.append(self.name + "-shutdown"))
                self.serve_forever = mock.Mock()
            def server_close(self):
                events.append(self.name + "-close")

        thread = mock.Mock()
        thread.start.side_effect = RuntimeError("thread start")
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(module, "create_server", side_effect=[Server("main"), Server("teacher")]), \
                 mock.patch.object(module.threading, "Thread", return_value=thread), \
                 mock.patch.object(sys, "argv", ["laser", "--data", folder, "--port", "1", "--teacher-port", "2"]):
                with self.assertRaisesRegex(RuntimeError, "thread start"):
                    module.main()
            self.assertEqual(events, ["main-close", "teacher-close"])
            InstanceLock(folder).close()

    def test_bootstrap_popen_failure_closes_stream_and_waits_existing_child(self):
        module = self._load_bootstrap()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            process = mock.Mock()
            process.wait.return_value = 0
            tree = mock.Mock()
            commands = [("one", ["one"]), ("two", ["two"])]
            streams = []
            log = mock.Mock()
            log.error = None
            def start(*args, **kwargs):
                streams.append(kwargs['stdout'])
                if len(streams) == 2:
                    raise OSError('start')
                return process
            with mock.patch.object(module, "read_runtime_config", return_value={}), \
                 mock.patch.object(module, "check"), mock.patch.object(module, "_commands", return_value=commands), \
                 mock.patch.object(module.signal, 'signal'), \
                 mock.patch("app.process_tree.ProcessTree", return_value=tree), \
                 mock.patch.object(module, "BoundedChildLog", return_value=log), \
                 mock.patch.object(module.subprocess, "Popen", side_effect=start):
                with self.assertRaisesRegex(OSError, "start"):
                    module.run(root, root)
            tree.close.assert_called_once_with()
            process.wait.assert_called_once_with(timeout=10)
            self.assertEqual(len(streams), 2)
            self.assertEqual(streams, [subprocess.PIPE, subprocess.PIPE])
            log.close.assert_called_once_with()
            lock = InstanceLock(root)
            lock.close()

    def test_bootstrap_shutdown_kills_timed_out_child_before_releasing_lock(self):
        module = self._load_bootstrap()
        events = []
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            process = mock.Mock()
            def wait_side_effect(*, timeout=None):
                with self.assertRaises(RuntimeError):
                    InstanceLock(root)
                if timeout == 10:
                    raise subprocess.TimeoutExpired("child", 10)
                events.append("wait-after-kill")
            process.wait.side_effect = wait_side_effect
            process.kill.side_effect = lambda: events.append("kill")
            tree = mock.Mock()
            log = mock.Mock()
            log.error = None
            def close_log():
                with self.assertRaises(RuntimeError):
                    InstanceLock(root)
                events.append('log-close')
            log.close.side_effect = close_log
            def install_signal(_signal, handler):
                handler(None, None)
            with mock.patch.object(module, "read_runtime_config", return_value={}), \
                 mock.patch.object(module, "check"), \
                 mock.patch.object(module, "_commands", return_value=[("one", ["one"])]), \
                 mock.patch.object(module.signal, "signal", side_effect=install_signal), \
                 mock.patch("app.process_tree.ProcessTree", return_value=tree), \
                 mock.patch.object(module, "BoundedChildLog", return_value=log), \
                 mock.patch.object(module.subprocess, "Popen", return_value=process):
                self.assertEqual(module.run(root, root), 0)
            tree.close.assert_called_once_with()
            self.assertEqual(process.wait.call_args_list, [mock.call(timeout=10), mock.call()])
            self.assertEqual(events, ["kill", "wait-after-kill", "log-close"])
            InstanceLock(root).close()


if __name__ == "__main__":
    unittest.main()
