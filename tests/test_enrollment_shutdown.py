"""Lifecycle and bounded-shutdown tests for the school enrollment service."""
import base64
import importlib.util
import json
from pathlib import Path
import secrets
import socket
import ssl
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


enrollment = load('enrollment_shutdown_contract', ROOT / 'deployment/school-setup/enrollment.py')
provision = load('enrollment_shutdown_provision', ROOT / 'windows-elevhub/server/provision.py')


def ssh_key():
    payload = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + secrets.token_bytes(32)
    return 'ssh-ed25519 ' + base64.b64encode(payload).decode('ascii')


class LoopbackServer(enrollment.EnrollmentServer):
    def server_bind(self):
        self.server_address = ('127.0.0.1', 0)
        return super().server_bind()


class EnrollmentShutdownTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='enrollment-shutdown-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'hub').mkdir()
        (self.root / 'admin-ssh').mkdir()
        # The provisioning helper deliberately rejects loopback as a server
        # identity; the socket itself remains loopback-only in this test.
        # The fixture creates private TLS material in a temporary tree.  Keep
        # the production ACL boundary enabled; only this isolated test fixture
        # supplies its own explicit boundary on the generated paths.
        with patch.object(provision, '_restrict_private_path', return_value=None):
            provision._tls(self.root / 'security', '192.0.2.10')
        self.school = {
            'schemaVersion': 1, 'school_name': 'Testskolen', 'server': '127.0.0.1',
            'role': 'vm', 'theme': 'yellow', 'student_subnet': '127.0.0.0/8',
            'admin_public_key': ssh_key(),
        }
        enrollment.write_json(self.root / 'school.json', self.school)
        enrollment.write_json(self.root / 'security/enheder.json', {'enheder': {}})
        enrollment.write_json(self.root / 'security/enrollment.json', {'tickets': {}})
        enrollment.write_json(self.root / 'security/ssh-pins.json', {})

    def start(self):
        cfg = dict(self.school, server='127.0.0.1', student_subnet='127.0.0.0/8')
        school_patch = patch.object(enrollment, 'school', return_value=cfg)
        school_patch.start()
        self.addCleanup(school_patch.stop)
        server = LoopbackServer(self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.stop, server, thread)
        return server, thread

    @staticmethod
    def stop(server, thread):
        if getattr(server, 'service_lock', None) is not None:
            server.shutdown()
            server.server_close()
        thread.join(timeout=3)

    def client(self, server):
        context = ssl.create_default_context(cadata=(self.root / 'security/ca.crt').read_text())
        raw = socket.create_connection(('127.0.0.1', server.server_port), timeout=2)
        connection = context.wrap_socket(raw, server_hostname='192.0.2.10')
        self.addCleanup(connection.close)
        return connection

    def request(self, server, payload):
        connection = self.client(server)
        try:
            body = json.dumps(payload).encode()
            connection.sendall(
                b'POST /enroll HTTP/1.1\r\nHost: 127.0.0.1\r\n'
                + f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode() + body)
            response = connection.recv(65536)
            return int(response.split(b' ', 2)[1]), response
        finally:
            connection.close()

    def issue_request(self):
        request = {'computer': 'rum-a-01', 'installation_id': secrets.token_hex(32),
                   'ssh_host_key': ssh_key()}
        request['code'] = enrollment.issue_ticket(self.root, request['computer'])
        return request

    def test_ticket_and_revoke_remain_available_while_service_lifetime_lock_is_held(self):
        server, _ = self.start()
        request = self.issue_request()
        result = enrollment.redeem(self.root, request, '127.0.0.2', now=1001)
        self.assertEqual(result['device']['computer'], 'rum-a-01')
        enrollment.revoke_device(self.root, 'rum-a-01')
        self.assertEqual(enrollment.read_json(self.root / 'security/enheder.json'), {'enheder': {}})

    def test_busy_registry_returns_503_then_succeeds_after_operations_lock_release(self):
        server, _ = self.start()
        request = self.issue_request()
        busy = enrollment.EnrollmentInstanceLock(self.root, enrollment.OPERATIONS_LOCK).acquire()
        try:
            status, _ = self.request(server, request)
            self.assertEqual(status, 503)
        finally:
            busy.close()
        status, response = self.request(server, request)
        self.assertEqual(status, 200, response)

    def test_server_close_keeps_lifetime_lock_until_slow_handler_drains(self):
        server, _ = self.start()
        entered = threading.Event()
        release = threading.Event()

        def blocked_redeem(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(2))
            return {'ok': True}

        redeem_patch = patch.object(enrollment, 'redeem', side_effect=blocked_redeem)
        redeem_patch.start()
        self.addCleanup(redeem_patch.stop)
        client = self.client(server)
        client.sendall(b'POST /enroll HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 2\r\n\r\n{}')
        self.assertTrue(entered.wait(1))
        server.shutdown()
        closing = threading.Thread(target=server.server_close)
        closing.start()
        contender = enrollment.EnrollmentInstanceLock(self.root)
        try:
            time.sleep(0.1)
            self.assertTrue(closing.is_alive())
            with self.assertRaises(RuntimeError):
                contender.acquire()
        finally:
            release.set()
            closing.join(timeout=2)
            client.close()
        self.assertFalse(closing.is_alive())
        contender.acquire().close()

    def test_truncated_request_has_bounded_shutdown(self):
        server, _ = self.start()
        old = enrollment.REQUEST_SECONDS
        enrollment.REQUEST_SECONDS = 0.3
        self.addCleanup(setattr, enrollment, 'REQUEST_SECONDS', old)
        server.handle_error = Mock()
        client = self.client(server)
        client.sendall(b'POST /enroll HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 200\r\n\r\n')
        stop_writer = threading.Event()
        stopped = threading.Event()

        def trickle():
            try:
                for _ in range(100):
                    if stop_writer.wait(0.04):
                        break
                    client.send(b'x')
            except OSError:
                pass
            finally:
                stopped.set()

        writer = threading.Thread(target=trickle)
        writer.start()
        started = time.monotonic()
        server.shutdown()
        server.server_close()
        stop_writer.set()
        client.close()
        writer.join(timeout=1)
        self.assertTrue(stopped.is_set())
        self.assertLess(time.monotonic() - started, 1.5)
        server.handle_error.assert_not_called()

    def test_tls_initialization_failure_releases_lifetime_lock(self):
        (self.root / 'security/server.key').unlink()
        with patch.object(LoopbackServer, 'server_bind', side_effect=AssertionError('bind must follow TLS')) as bind:
            with self.assertRaises((OSError, ValueError)):
                LoopbackServer(self.root)
            bind.assert_not_called()
        enrollment.EnrollmentInstanceLock(self.root).acquire().close()


if __name__ == '__main__':
    unittest.main()
