"""Teacher HTTP and real loopback TLS proof of the printer trust workflow."""
import socket
import threading
import unittest
from unittest.mock import patch

from app.bambu import CertificatePins, CertificateTrustError
from app.server import Handler, Server
from app.teacher_auth import TeacherAuth
from scripts.smoke_slice import cube
from tests import test_bambu_certificates as tls_fixture
from tests import test_printer_bridge as bridge_fixture
from tests.test_library_retention_integration import _HTTP


class RecordingTLSServer(tls_fixture.LocalTLSServer):
    def __init__(self, *args):
        super().__init__(*args)
        self.received = b''

    def _serve(self):
        try:
            connection, _ = self.listener.accept()
            with self.context.wrap_socket(connection, server_side=True) as tls:
                tls.settimeout(2)
                try:
                    self.received = tls.recv(4096)
                except ConnectionResetError:
                    # Windows may report the client's TLS-only close as reset.
                    pass
        except Exception as error:
            self.error = error


class PrinterCertificateHTTPTests(unittest.TestCase):
    def setUp(self):
        self.fixture = bridge_fixture.PrinterBridgeTests('runTest')
        self.fixture.setUp()
        self.app = self.fixture.app
        self.app.token = 'teacher-secret'
        self.app.auth = TeacherAuth(self.app.token)
        self.app.printer_bridge = self.fixture.bridge
        self.printer = self.fixture.create()['result']
        self.id = self.printer['id']
        self.server = Server(('127.0.0.1', 0), Handler)
        self.server.app = self.app
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.http = _HTTP(self.server)
        status, headers, _ = self.http.request('POST', '/api/auth/login', {'code': self.app.token})
        self.assertEqual(status, 200)
        self.cookie = headers['Set-Cookie'].split(';', 1)[0]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.fixture.tearDown()

    def observe(self, channel='mqtt', label='new'):
        cert, key, fingerprint = tls_fixture.make_certificate(self.fixture.path, label)
        pins = CertificatePins(self.fixture.path / 'certificates', self.id, strict=True,
            identity={'host': self.printer['host'], 'serial': self.printer['serial'], 'version': 1})
        with tls_fixture.LocalTLSServer(cert, key) as server:
            with server.client() as sock:
                with self.assertRaises(CertificateTrustError):
                    pins.check(sock, channel)
        return fingerprint

    def confirm_body(self, fingerprint, request_id='1' * 32, **extra):
        return {'id': self.id, 'channel': 'mqtt', 'fingerprint': fingerprint,
                'confirmed': True, 'expected_version': 1, 'request_id': request_id, **extra}

    def test_teacher_confirmation_is_authenticated_exact_and_idempotent(self):
        fingerprint = self.observe()
        body = self.confirm_body(fingerprint, repair=False)
        self.assertEqual(self.http.json('GET', '/api/printers/certificates')[0], 401)
        self.assertEqual(self.http.json('POST', '/api/printers/certificates/confirm', body)[0], 401)
        refused = dict(body, confirmed=False)
        self.assertEqual(self.http.json('POST', '/api/printers/certificates/confirm', refused, cookie=self.cookie)[0], 400)
        status, _, snapshot = self.http.json('GET', '/api/printers/certificates', cookie=self.cookie)
        self.assertEqual(status, 200)
        self.assertNotIn('access_code', str(snapshot))
        self.assertEqual(snapshot['printers'][0]['certificate_trust']['channels']['mqtt']['pending']['new_fingerprint'], fingerprint)
        self.app.queue.printers[self.id]['bed_clear'] = True
        status, _, applied = self.http.json('POST', '/api/printers/certificates/confirm', body, cookie=self.cookie)
        self.assertEqual(status, 200, applied)
        self.assertFalse(self.app.queue.printers[self.id]['bed_clear'])
        status, _, replay = self.http.json('POST', '/api/printers/certificates/confirm', body, cookie=self.cookie)
        self.assertEqual(status, 200)
        self.assertEqual(applied, replay)
        conflict = dict(body, fingerprint='f' * 64)
        self.assertEqual(self.http.json('POST', '/api/printers/certificates/confirm', conflict, cookie=self.cookie)[0], 409)

    def test_paused_repair_preserves_unresolved_job_and_never_clears_plate(self):
        self.app.queue.demo = True
        job = self.app.queue.submit(cube(20), 'job.stl', 'any', 'cert-repair-job-0001')
        self.app.queue.db.execute("UPDATE jobs SET status='recovering',printer_id=? WHERE id=?", (self.id, job['id']))
        self.app.queue.db.commit()
        before = self.app.queue.get_job(job['id'])
        fingerprint = self.observe(label='repair')
        body = self.confirm_body(fingerprint, repair=True)
        self.assertEqual(self.http.json('POST', '/api/printers/certificates/confirm', body, cookie=self.cookie)[0], 409)
        self.app.queue.pause(True)
        status, _, applied = self.http.json('POST', '/api/printers/certificates/confirm', body, cookie=self.cookie)
        self.assertEqual(status, 200, applied)
        self.assertEqual(before, self.app.queue.get_job(job['id']))
        self.assertTrue(self.app.queue.paused)
        self.assertFalse(self.app.queue.printers[self.id]['bed_clear'])

    def test_teacher_can_finish_another_panels_failed_journal_and_confirm_again(self):
        fingerprint = self.observe(label='interrupted')
        original = self.fixture.command('printer.certificate.confirm',
            {'id': self.id, 'channel': 'mqtt', 'fingerprint': fingerprint}, expected=1, request_id='5' * 32)
        with patch.object(self.fixture.bridge, '_confirm_certificate', side_effect=ValueError('fixture interruption')):
            with self.assertRaises(ValueError):
                self.fixture.bridge.execute(bridge_fixture.ACTOR, original)
        status, _, snapshot = self.http.json('GET', '/api/printers/certificates', cookie=self.cookie)
        self.assertEqual(status, 200)
        operation = snapshot['certificate_operations'][0]
        before = self.app.queue.snapshot()
        body = {'id': self.id, 'operation_request_id': operation['request_id'],
                'operation_actor': operation['actor'], 'expected_version': 1, 'request_id': '3' * 32}
        self.assertEqual(self.http.json('POST', '/api/printers/certificates/cancel', body)[0], 401)
        status, _, response = self.http.json('POST', '/api/printers/certificates/cancel', body, cookie=self.cookie)
        self.assertEqual(status, 200, response)
        self.assertTrue(response['result']['cancelled'])
        self.assertEqual(before, self.app.queue.snapshot())
        self.assertEqual(self.http.json('POST', '/api/printers/certificates/cancel', body, cookie=self.cookie)[2], response)
        with self.assertRaises(bridge_fixture.ConfigConflict):
            self.fixture.bridge.execute(bridge_fixture.ACTOR, original)
        self.assertEqual(self.http.json('GET', '/api/printers/certificates', cookie=self.cookie)[2]['certificate_operations'], [])
        self.assertEqual(self.http.json('POST', '/api/printers/certificates/confirm',
            self.confirm_body(fingerprint, request_id='4' * 32), cookie=self.cookie)[0], 200)

    def test_first_setup_probes_both_channels_without_print_or_credentials(self):
        from contextlib import ExitStack
        real_connect = socket.create_connection
        servers = []
        with ExitStack() as stack:
            endpoints, expected = {}, {}
            for channel, port in (('mqtt', 8883), ('ftp', 990)):
                cert, key, fingerprint = tls_fixture.make_certificate(self.fixture.path, 'probe-' + channel)
                endpoint = stack.enter_context(RecordingTLSServer(cert, key))
                servers.append(endpoint)
                endpoints[port], expected[channel] = endpoint.address, fingerprint
            def connect(address, *args, **kwargs):
                self.assertEqual(address[0], self.printer['host'])
                self.assertIn(address[1], endpoints)
                return real_connect(endpoints[address[1]], *args, **kwargs)
            # Only the probe connector is redirected to the real local TLS
            # endpoints; the HTTP client connected before the patch is entered.
            from app import bambu
            with patch.object(bambu, 'socket', wraps=socket) as patched_socket:
                patched_socket.create_connection.side_effect = connect
                status, _, result = self.http.json('POST', '/api/printers/certificates/probe',
                    {'id': self.id, 'expected_version': 1, 'request_id': '2' * 32}, cookie=self.cookie)
            self.assertEqual(status, 200, result)
            for channel in expected:
                observed = result['result']['channels'][channel]
                self.assertEqual(observed['status'], 'confirmation_required')
                self.assertEqual(observed['observed_fingerprint'], expected[channel])
            self.assertEqual(self.app.queue.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)
            self.assertFalse(self.app.queue.printers[self.id]['bed_clear'])
            self.assertNotIn('access_code', str(result))
        for endpoint in servers:
            self.assertFalse(endpoint.thread.is_alive())
            self.assertEqual(endpoint.received, b'', 'Probe must send no credentials or application commands')
