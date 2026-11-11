import json
from pathlib import Path
import socketserver
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import makerspace_printers
from makerspace_printers import PrinterAdministration


KEY = 'mkbridge_' + 'A' * 43


class _JSONHandler(BaseHTTPRequestHandler):
    status = 200
    payload = {}
    body = None
    headers = {}

    def do_GET(self):
        self._reply()

    def do_POST(self):
        self._reply()

    def _reply(self):
        body = self.body if self.body is not None else json.dumps(self.payload).encode()
        self.send_response(type(self).status)
        for name, value in type(self).headers.items():
            self.send_header(name, value)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def log_message(self, *args):
        pass


class _DripHeadersHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(1)
        try:
            while self.rfile.readline().strip():
                pass
        except OSError:
            return
        header = b'HTTP/1.0 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n'
        for byte in header:
            try:
                self.wfile.write(bytes((byte,)))
                self.wfile.flush()
            except OSError:
                return
            time.sleep(0.04)
        try:
            self.wfile.write(b'{}')
        except OSError:
            pass


class _BaseServerMixin:
    allow_reuse_address = True
    daemon_threads = True


class _DripServer(_BaseServerMixin, socketserver.ThreadingTCPServer):
    pass


class PrinterAdapterTests(unittest.TestCase):
    def setUp(self):
        _JSONHandler.status = 200
        _JSONHandler.payload = {'printers': []}
        _JSONHandler.body = None
        _JSONHandler.headers = {}
        self.tempdir = tempfile.TemporaryDirectory()
        key = Path(self.tempdir.name) / 'key'
        key.write_text(KEY, encoding='ascii')
        self.server = None

    def tearDown(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        self.tempdir.cleanup()

    def _admin(self, handler):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return PrinterAdministration({'backend': f'127.0.0.1:{self.server.server_address[1]}',
                                      'key_file': 'key'}, base=self.tempdir.name)

    def test_snapshot_response_is_whitelisted(self):
        printer = {'id': 'p1', 'name': 'Printer', 'location': 'Lab', 'model': 'A1 mini',
                   'host': '127.0.0.1', 'serial': 'SERIAL', 'enabled': False,
                   'bed_type': 'Textured PEI Plate', 'version': 3,
                   'server_url': 'http://secret.invalid', 'access_code': 'do-not-return'}
        _JSONHandler.payload = {'printers': [printer]}
        admin = self._admin(_JSONHandler)
        result = admin.snapshot()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(set(result['printers'][0]), {'id', 'name', 'location', 'model', 'host',
                                                       'serial', 'enabled', 'bed_type', 'version',
                                                       'kind', 'adapter', 'requires_restart'})
        self.assertNotIn('server_url', json.dumps(result))
        self.assertNotIn('access_code', json.dumps(result))
        self.assertEqual(result['printers'][0]['kind'], '3d_print')

    def test_snapshot_preserves_bounded_certificate_observation_without_credentials(self):
        fingerprint = 'a' * 64
        pending = {'printer_id': 'p1', 'channel': 'mqtt', 'old_fingerprint': 'b' * 64,
                   'new_fingerprint': fingerprint, 'host': '192.168.1.40',
                   'serial': 'SERIAL', 'version': 3, 'observed_at': 123.0}
        printer = {'id': 'p1', 'name': 'Printer', 'location': 'Lab', 'model': 'A1 mini',
                   'host': '192.168.1.40', 'serial': 'SERIAL', 'enabled': False,
                   'bed_type': 'Textured PEI Plate', 'version': 3,
                   'certificate_trust': {'identity': {'host': '192.168.1.40', 'serial': 'SERIAL', 'version': 3},
                                         'channels': {'mqtt': {'status': 'confirmation_required', 'pinned_fingerprint': 'b' * 64, 'pending': pending},
                                                      'ftp': {'status': 'untrusted', 'pinned_fingerprint': None}}},
                   'access_code': 'do-not-return'}
        _JSONHandler.payload = {'printers': [printer]}
        admin = self._admin(_JSONHandler)
        result = admin.snapshot()
        trust = result['printers'][0]['certificate_trust']
        self.assertEqual(trust['channels']['mqtt']['pending']['new_fingerprint'], fingerprint)
        self.assertNotIn('access_code', json.dumps(result))

    def test_certificate_confirmation_result_is_version_bound(self):
        fingerprint = 'c' * 64
        _JSONHandler.payload = {'ok': True, 'action': 'printer.certificate.confirm',
                                'result': {'id': 'p1', 'version': 3, 'channel': 'mqtt',
                                           'fingerprint': fingerprint, 'reconnected': True}}
        admin = self._admin(_JSONHandler)
        result = admin.execute('teacher:' + '1' * 32, {
            'action': 'printer.certificate.confirm',
            'params': {'id': 'p1', 'channel': 'mqtt', 'fingerprint': fingerprint},
            'expected_version': 3, 'request_id': 'a' * 32})
        self.assertEqual(result['result']['fingerprint'], fingerprint)
        _JSONHandler.payload['result']['version'] = 4
        with self.assertRaises(OSError):
            admin.execute('teacher:' + '1' * 32, {
                'action': 'printer.certificate.confirm',
                'params': {'id': 'p1', 'channel': 'mqtt', 'fingerprint': fingerprint},
                'expected_version': 3, 'request_id': 'b' * 32})

    def test_certificate_cancel_result_is_version_bound_and_preserves_trust(self):
        _JSONHandler.payload = {'ok': True, 'action': 'printer.certificate.cancel',
                                'result': {'id': 'mini', 'version': 3,
                                           'operation_request_id': 'c' * 32,
                                           'cancelled': True, 'trust_preserved': True}}
        admin = self._admin(_JSONHandler)
        result = admin.execute('teacher:' + '1' * 32, {
            'action': 'printer.certificate.cancel',
            'params': {'id': 'mini', 'operation_request_id': 'c' * 32,
                       'operation_actor': 'teacher:' + '1' * 32},
            'expected_version': 3, 'request_id': 'd' * 32})
        self.assertTrue(result['result']['cancelled'])
        self.assertTrue(result['result']['trust_preserved'])
        _JSONHandler.payload['result']['version'] = 4
        with self.assertRaises(OSError):
            admin.execute('teacher:' + '1' * 32, {
                'action': 'printer.certificate.cancel',
                'params': {'id': 'mini', 'operation_request_id': 'c' * 32,
                           'operation_actor': 'teacher:' + '1' * 32},
                'expected_version': 3, 'request_id': 'e' * 32})

    def test_large_body_is_unavailable(self):
        _JSONHandler.body = b'x' * (256 * 1024 + 1)
        _JSONHandler.payload = {}
        admin = self._admin(_JSONHandler)
        self.assertEqual(admin.snapshot()['status'], 'unavailable')
        _JSONHandler.body = None

    def test_redirect_is_not_followed(self):
        _JSONHandler.status = 302
        _JSONHandler.headers = {'Location': 'http://127.0.0.1:1/secret'}
        _JSONHandler.body = b''
        admin = self._admin(_JSONHandler)
        result = admin.snapshot()
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['printers'], [])
        _JSONHandler.status = 200
        _JSONHandler.headers = {}
        _JSONHandler.body = None

    def test_corrupt_key_is_unavailable_and_execute_errors(self):
        key_path = Path(self.tempdir.name) / 'key'
        key_path.write_text('not-a-key', encoding='ascii')
        admin = self._admin(_JSONHandler)
        self.assertEqual(admin.snapshot()['status'], 'unavailable')
        with self.assertRaises(OSError):
            admin.execute('teacher:1', {'action': 'printer.create'})

    def test_dripping_http10_headers_are_aborted_at_absolute_deadline(self):
        self.server = _DripServer(('127.0.0.1', 0), _DripHeadersHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        admin = PrinterAdministration({'backend': f'127.0.0.1:{self.server.server_address[1]}',
                                      'key_file': 'key'}, base=self.tempdir.name)
        started = time.monotonic()
        with mock.patch.object(makerspace_printers, '_TIMEOUT', 0.15):
            result = admin.snapshot()
        elapsed = time.monotonic() - started
        self.assertEqual(result['status'], 'unavailable')
        self.assertLess(elapsed, 0.8)


if __name__ == '__main__':
    unittest.main()
