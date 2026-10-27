"""System status uses the real teacher and pupil HTTP authentication boundaries."""
import hashlib
import http.client
import json
import secrets
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from elevhub import Hub, Laerer, Server
from sikkerhed import Enhedsregister, tls_kontekst
from system_overview import CertificateSnapshot, MAX_CERT_BYTES, PrinterSnapshot
from tilstand import Laererkode, Tilstand


OPENSSL = shutil.which('openssl')
if not OPENSSL:
    candidate = Path('C:/Program Files/Git/usr/bin/openssl.exe')
    OPENSSL = str(candidate) if candidate.is_file() else None


@unittest.skipUnless(OPENSSL, 'OpenSSL is needed for temporary test certificates')
class CertificateSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def certificate(self, name, days):
        cert = self.root / f'{name}.crt'
        key = self.root / f'{name}.key'
        subprocess.run([OPENSSL, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-days', str(days), '-subj', '/CN=localhost',
                        '-keyout', str(key), '-out', str(cert),
                        '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1'],
                       capture_output=True, check=True, timeout=30)
        return cert

    def test_actual_leaf_and_ca_statuses_are_decoded_and_cached(self):
        leaf = self.certificate('leaf', 90)
        ca = self.certificate('ca', 1)
        snapshot = CertificateSnapshot({'certificate': leaf, 'ca': ca})
        result = snapshot.snapshot()
        self.assertEqual(result['certificate']['status'], 'valid')
        self.assertEqual(result['ca']['status'], 'expiring_30_days')
        self.assertIsInstance(result['certificate']['not_after'], str)
        self.assertIsInstance(result['ca']['days_remaining'], int)
        cached = snapshot.snapshot()
        self.assertEqual(cached, result)

        replacement = self.certificate('replacement', 1)
        replacement.replace(leaf)
        changed = snapshot.snapshot()
        self.assertEqual(changed['certificate']['status'], 'expiring_30_days')

    def test_missing_invalid_and_oversized_certificates_are_unavailable(self):
        missing = self.root / 'missing.crt'
        snapshot = CertificateSnapshot({'certificate': missing, 'ca': missing})
        self.assertEqual(snapshot.snapshot()['status'], 'unavailable')
        missing.write_bytes(b'not a certificate')
        self.assertEqual(snapshot.snapshot()['certificate']['status'], 'unavailable')
        missing.write_bytes(b'x' * (MAX_CERT_BYTES + 1))
        self.assertEqual(snapshot.snapshot()['certificate']['status'], 'unavailable')

    def test_status_classifier_reports_not_yet_valid_and_expired(self):
        cert = self.root / 'bounded.crt'
        ca = self.root / 'bounded-ca.crt'
        cert.write_bytes(b'bounded')
        ca.write_bytes(b'bounded-ca')
        now = datetime.now(timezone.utc)
        with patch('system_overview._certificate_dates', side_effect=[
                (now + timedelta(days=1), now + timedelta(days=2)),
                (now - timedelta(days=2), now + timedelta(days=60)),
                (now - timedelta(days=2), now - timedelta(seconds=1)),
                (now - timedelta(days=2), now + timedelta(days=60))]):
            snapshot = CertificateSnapshot({'certificate': cert, 'ca': ca})
            first = snapshot.snapshot()
            self.assertEqual(first['certificate']['status'], 'not_yet_valid')
            # Force a fresh identity so the second mocked certificate is read.
            cert.write_bytes(b'bounded2')
            second = snapshot.snapshot()
            self.assertEqual(second['certificate']['status'], 'expired')

    def test_reparse_certificate_path_is_unavailable(self):
        source = self.certificate('source', 90)
        link = self.root / 'link.crt'
        try:
            link.symlink_to(source)
        except (OSError, NotImplementedError):
            self.skipTest('symlink creation is unavailable')
        self.assertEqual(CertificateSnapshot({'certificate': link}).snapshot()['status'], 'unavailable')


@unittest.skipUnless(OPENSSL, 'OpenSSL is needed for temporary test certificates')
class SystemOverviewHTTPTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name)
        cert, key = path / 'cert.pem', path / 'key.pem'
        subprocess.run([OPENSSL, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=localhost', '-keyout', str(key), '-out', str(cert),
                        '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1'],
                       capture_output=True, check=True, timeout=30)
        tls = tls_kontekst({'tls': {'certifikat': str(cert), 'noegle': str(key)}}, path)
        self.client_tls = ssl.create_default_context(cafile=str(cert))
        self.tokens = {name: {role: secrets.token_urlsafe(32) for role in ('root', 'ui')}
                       for name in ('pc1', 'pc2')}
        self.register_path = path / 'register.json'
        self.write_register()
        register = Enhedsregister(self.register_path)
        self.now = 1000.0
        self.apps = [{'id': 'internet', 'navn': 'Internet', 'type': 'browser'}]
        state = Tilstand(path / 'state', self.apps, ur=lambda: self.now)
        self.release_path = path / 'release.json'
        self.release_path.write_text(json.dumps({'release_id': '2026.10.11',
                                                  'expected_client_release': '2026.10.11'}))
        code = Laererkode(path)
        self.teacher_code = 'teacher-code-123'
        code.vaelg(self.teacher_code)
        self.hub = Server(('127.0.0.1', 0), Hub, tls=tls)
        self.hub.navn, self.hub.enheder, self.hub.tilstand = 'hub', register, state
        self.teacher = Server(('127.0.0.1', 0), Laerer, tls=tls)
        self.teacher.navn, self.teacher.enheder, self.teacher.tilstand = 'laerer', register, state
        self.teacher.apps, self.teacher.kode = self.apps, code
        self.teacher.release_path = self.release_path
        self.teacher.printer_snapshot = PrinterSnapshot(self.apps)
        for server in (self.hub, self.teacher):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(self.close_servers)

    def close_servers(self):
        for server in (self.hub, self.teacher):
            server.shutdown()
            server.server_close()

    def write_register(self, omit=None):
        entries = {name: {'token_sha256': hashlib.sha256(tokens['root'].encode()).hexdigest(),
                          'ui_token_sha256': hashlib.sha256(tokens['ui'].encode()).hexdigest()}
                   for name, tokens in self.tokens.items() if name != omit}
        self.register_path.write_text(json.dumps({'enheder': entries}))

    def call(self, server, method, path, body=None, headers=None):
        connection = http.client.HTTPSConnection('127.0.0.1', server.server_address[1],
                                                 context=self.client_tls, timeout=3)
        try:
            payload = json.dumps(body).encode() if body is not None else None
            connection.request(method, path, payload, headers or {})
            reply = connection.getresponse()
            return reply.status, dict(reply.getheaders()), json.loads(reply.read())
        finally:
            connection.close()

    def login(self):
        host = f'127.0.0.1:{self.teacher.server_address[1]}'
        status, headers, _ = self.call(self.teacher, 'POST', '/api/laerer/login',
                                       {'kode': self.teacher_code},
                                       {'Content-Type': 'application/json', 'Origin': 'https://' + host})
        self.assertEqual(status, 200)
        return headers['Set-Cookie'].split(';', 1)[0]

    def report(self, name='pc1', role='root', **fields):
        route = '/api/enhed/status' if role == 'root' else '/api/status'
        return self.call(self.hub, 'POST', route, {'computer': name, 'version': 1, **fields},
                         {'Content-Type': 'application/json', 'X-Elevhub-Computer': name,
                          'Authorization': 'Bearer ' + self.tokens[name][role]})

    def overview(self, cookie=None, host=None):
        headers = {'Cookie': cookie} if cookie else {}
        if host:
            headers['Host'] = host
        return self.call(self.teacher, 'GET', '/api/laerer/system', headers=headers)

    def test_inventory_versions_and_auth(self):
        self.assertEqual(self.overview()[0], 401)
        cookie = self.login()
        status, _, data = self.overview(cookie, 'evil.example')
        self.assertEqual(status, 200)
        self.assertEqual([c['navn'] for c in data['computers']], ['pc1', 'pc2'])
        self.assertEqual(data['tls']['status'], 'unavailable')
        self.assertNotIn('pem', json.dumps(data).lower())
        self.assertTrue(all(not c['online'] and c['version_status'] == 'unknown'
                            for c in data['computers']))
        self.assertNotIn('evil.example', json.dumps(data))
        self.assertNotIn('token_sha256', json.dumps(data))
        self.assertEqual(self.report(software_version='2026.10.11')[0], 200)
        self.assertEqual(self.overview(cookie)[2]['computers'][0]['version_status'], 'current')
        self.assertEqual(self.report(software_version='2026.10.10')[0], 200)
        self.assertEqual(self.overview(cookie)[2]['computers'][0]['version_status'], 'different')
        self.assertEqual(self.report()[0], 200)
        self.assertEqual(self.overview(cookie)[2]['computers'][0]['version_status'], 'unknown')
        self.write_register(omit='pc1')
        self.assertEqual([c['navn'] for c in self.overview(cookie)[2]['computers']], ['pc2'])
        self.register_path.write_text('{broken')
        unknown = self.overview(cookie)[2]
        self.assertEqual(unknown['computers_status'], 'unknown')
        self.assertEqual(unknown['computers'], [])
        self.release_path.unlink()
        self.assertEqual(self.overview(cookie)[2]['server']['release_id'], None)

    def test_panel_alias_and_assets(self):
        for path, content_type in (('/kontrolpanel', 'text/html'),
                                   ('/system-overview.js', 'javascript'),
                                   ('/system-overview.css', 'text/css')):
            connection = http.client.HTTPSConnection('127.0.0.1', self.teacher.server_address[1],
                                                     context=self.client_tls, timeout=3)
            try:
                connection.request('GET', path)
                reply = connection.getresponse()
                self.assertEqual(reply.status, 200)
                self.assertIn(content_type, reply.getheader('Content-Type'))
                self.assertTrue(reply.read())
            finally:
                connection.close()

    def test_root_only_version_and_validation(self):
        cookie = self.login()
        self.assertEqual(self.report(role='ui', software_version='forged')[0], 200)
        self.assertIsNone(self.overview(cookie)[2]['computers'][0]['software_version'])
        self.assertEqual(self.report(software_version='<script>')[0], 400)
        self.assertEqual(self.report(software_version='x' * 65)[0], 400)
        self.assertEqual(self.report(software_version=34)[0], 400)
        self.assertEqual(self.report(software_version='2026.10.11')[0], 200)
        self.assertEqual(self.report(role='ui', software_version='forged')[0], 200)
        self.assertEqual(self.overview(cookie)[2]['computers'][0]['software_version'], '2026.10.11')

    def test_printer_filter_and_unreachable(self):
        secret = 'private-pupil-file.stl'

        class PrintBackend(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                body = json.dumps({'printers': [{'id': 'p1', 'name': 'Printer 1', 'model': 'P1S',
                                                 'status': 'ready', 'enabled': True, 'bed_clear': True,
                                                 'error_code': 0, 'access_code': 'secret'}],
                                   'jobs': [{'filename': secret, 'pupil': 'Alice'}]}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        backend = ThreadingHTTPServer(('127.0.0.1', 0), PrintBackend)
        threading.Thread(target=backend.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (backend.shutdown(), backend.server_close()))
        tick = [0.0]
        self.teacher.printer_snapshot = PrinterSnapshot(
            [{'id': '3dprint', 'regler': 'printvaerksted',
              'bagved': f'127.0.0.1:{backend.server_address[1]}'}],
            monotonic=lambda: tick[0])
        cookie = self.login()
        printers = self.overview(cookie)[2]['printers']
        self.assertEqual(printers['status'], 'ok')
        self.assertTrue(printers['items'][0]['ready'])
        self.assertNotIn(secret, json.dumps(printers))
        self.assertNotIn('secret', json.dumps(printers))
        backend.shutdown()
        backend.server_close()
        tick[0] = 3.0
        printers = self.overview(cookie)[2]['printers']
        self.assertEqual(printers['status'], 'unknown')
        self.assertEqual(printers['items'], [])


if __name__ == '__main__':
    unittest.main()
