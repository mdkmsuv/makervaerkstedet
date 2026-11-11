"""Real TLS station/teacher gateway/loopback queue checks, without laser hardware."""
import http.client
import json
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from app.laser_server import Handler as QueueHandler, Inbox, create_server
from app.laser_station import NotLoggedIn, QueueClient, QueueUnavailable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linux-elevhub/server/elevhub'))
from elevhub import Laerer, Server
import laser_station_gateway as gateway
from makerspace_api import MakerspaceService
from makerspace_laser_admin import LaserAdministration
from sikkerhed import tls_kontekst
from tilstand import Laererkode

OPENSSL = shutil.which('openssl')
if not OPENSSL:
    candidate = Path('C:/Program Files/Git/usr/bin/openssl.exe')
    OPENSSL = str(candidate) if candidate.is_file() else None
CODE = 'isolated-laser-test-code'
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" data-laservaerksted="1" '
       'width="20mm" height="20mm" viewBox="0 0 20 20"><g data-operation="cut">'
       '<path data-operation="cut" d="M1 1H19V19H1Z"/></g></svg>')


class QuietTeacher(Laerer):
    def log_message(self, *args):
        pass


class ObservedQueue(QueueHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.seen.append((self.command, self.path, dict(self.headers)))
        super().do_GET()

    def do_POST(self):
        self.server.seen.append((self.command, self.path, dict(self.headers)))
        super().do_POST()


@unittest.skipUnless(OPENSSL, 'OpenSSL is needed for temporary TLS certificates.')
class LaserStationGatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.certs = tempfile.TemporaryDirectory()
        root = Path(cls.certs.name)
        cls.cert, cls.key = root / 'test.crt', root / 'test.key'
        subprocess.run([OPENSSL, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=localhost', '-keyout', str(cls.key), '-out', str(cls.cert),
                        '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1',
                        '-addext', 'basicConstraints=critical,CA:TRUE',
                        '-addext', 'keyUsage=critical,digitalSignature,keyCertSign,cRLSign'],
                       capture_output=True, check=True, timeout=30)
        cls.tls = tls_kontekst({'tls': {'certifikat': str(cls.cert), 'noegle': str(cls.key)}}, root)
        cls.client_tls = ssl.create_default_context(cafile=str(cls.cert))

    @classmethod
    def tearDownClass(cls):
        cls.certs.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.inbox = Inbox(self.temp.name, code=CODE)
        self.backend = create_server(self.temp.name, port=0, surface='teacher', inbox=self.inbox)
        self.backend.RequestHandlerClass = ObservedQueue
        self.backend.seen = []
        self.teacher = Server(('127.0.0.1', 0), QuietTeacher, tls=self.tls)
        self.teacher.navn = 'isolated station gateway'
        # The teacher surface resolves optional local appearance from its state directory.
        self.teacher.tilstand = SimpleNamespace(mappe=Path(self.temp.name))
        panel = Path(self.temp.name) / 'panel'
        panel.mkdir()
        self.teacher.kode = Laererkode(panel)
        self.teacher.makerspace = MakerspaceService(Path(self.temp.name) / 'hub.sqlite3',
            laser_administration=LaserAdministration({'backend': f'127.0.0.1:{self.backend.server_port}',
                'key_file': 'makerspace-laser.key'}, base=self.temp.name))
        for server in (self.backend, self.teacher):
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(self.close_server, server)
        self.base = f'https://127.0.0.1:{self.teacher.server_port}'
        self.job = self.inbox.submit({'elevnavn': 'Test', 'klasse': '5.A', 'projekt': 'Skilt',
                                     'requestId': uuid.uuid4().hex, 'svg': SVG})['id']

    @staticmethod
    def close_server(server):
        server.shutdown()
        server.server_close()

    def queue(self):
        queue = QueueClient(self.base, ca_file=self.cert, timeout=3)
        queue.login(CODE)
        return queue

    def call(self, method, path, body=None, headers=None):
        connection = http.client.HTTPSConnection('127.0.0.1', self.teacher.server_port,
                                                 context=self.client_tls, timeout=3)
        try:
            raw = json.dumps(body).encode() if body is not None else None
            connection.request(method, path, raw, {'Content-Type': 'application/json', **(headers or {})})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_all_in_one_and_split_station_share_real_queue_and_mutation(self):
        local = QueueClient(f'http://127.0.0.1:{self.backend.server_port}')
        local.login(CODE)
        remote = self.queue()
        self.assertEqual(remote.jobs(summary=True), local.jobs(summary=True))
        self.assertEqual(remote.prepared_svg(self.job), local.prepared_svg(self.job))
        self.assertIsNone(remote.station_configuration('laser-fbb2'))
        self.assertEqual(remote.check_handoff('laser-fbb2', 'fbb2', [self.job]),
                         {'managed': False, 'station_id': 'laser-fbb2', 'ids': [self.job]})
        remote.set_status(self.job, 'ready')
        self.assertEqual(local.jobs()[0]['status'], 'ready')
        remote.set_status(self.job, 'done')
        self.assertEqual(local.jobs()[0]['status'], 'done')
        self.assertTrue(all('Authorization' not in headers for _, _, headers in self.backend.seen))

    def test_existing_authentication_is_required_and_logout_revokes_session(self):
        self.assertEqual(self.call('GET', '/api/laser/jobs')[0], 401)
        self.assertEqual(self.call('POST', '/api/laser/login', {'code': 'wrong'})[0], 401)
        self.assertEqual(self.call('GET', '/api/laser/jobs', headers={'Cookie': 'laser_teacher=invalid'})[0], 401)
        self.teacher.kode.vaelg('isolated-panel-code')
        status, headers, _ = self.call('POST', '/api/laerer/login', {'kode': 'isolated-panel-code'})
        self.assertEqual(status, 200)
        self.assertEqual(self.call('GET', '/api/laser/jobs',
            headers={'Cookie': headers['Set-Cookie'].split(';')[0]})[0], 401)
        queue = self.queue()
        cookie = queue.cookie
        queue.logout()
        self.assertEqual(self.call('GET', '/api/laser/jobs', headers={'Cookie': cookie})[0], 401)

    def test_login_sets_secure_cookie_and_does_not_authenticate_the_panel(self):
        status, headers, _ = self.call('POST', '/api/laser/login', {'code': CODE})
        self.assertEqual(status, 200)
        self.assertIn('Secure', headers['Set-Cookie'])
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        cookie = headers['Set-Cookie'].split(';')[0]
        status, _, payload = self.call('GET', '/api/laerer/session', headers={'Cookie': cookie})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(payload)['logget_ind'])

    def test_unknown_and_pupil_routes_never_reach_backend(self):
        for method, path in [('POST', '/api/laser/submit'), ('POST', '/api/laser/boxes'),
                             ('GET', '/api/laser/jobs/../../makerspace/laser'),
                             ('GET', '/api/laser/stations/x/configuration?unexpected=1'),
                             ('GET', '/api/laser/jobs?summary=1&summary=1'),
                             ('GET', '/api/laser/%6aobs'), ('GET', '/api/laser/login')]:
            with self.subTest(path=path):
                before = len(self.backend.seen)
                self.assertEqual(self.call(method, path, {} if method == 'POST' else None)[0], 404)
                self.assertEqual(len(self.backend.seen), before)

    def test_hostile_origin_and_service_credentials_never_reach_backend(self):
        for headers in ({'Origin': 'https://evil.example'}, {'Authorization': 'Bearer forbidden-service-key'}):
            self.assertEqual(self.call('POST', '/api/laser/login', {'code': CODE}, headers)[0], 403)
        self.assertEqual(self.backend.seen, [])
        self.assertEqual(self.call('POST', '/api/laser/login', {'code': CODE}, {'Origin': self.base})[0], 200)
        received = self.backend.seen[-1][2]
        self.assertEqual(received['Origin'], f'http://127.0.0.1:{self.backend.server_port}')

    def test_oversized_request_is_rejected_without_backend_access(self):
        self.assertEqual(self.call('POST', '/api/laser/login', {'code': 'x' * 17000})[0], 413)
        self.assertEqual(self.backend.seen, [])

    def test_ca_and_hostname_are_actually_verified(self):
        with self.assertRaises(QueueUnavailable):
            QueueClient(self.base, timeout=3).login(CODE)
        self.assertEqual(self.backend.seen, [])
        self.assertTrue(self.queue().logged_in)
        # The certificate contains localhost and 127.0.0.1, but not this peer name.
        with patch('socket.getaddrinfo', wraps=__import__('socket').getaddrinfo) as lookup:
            real_lookup = lookup._mock_wraps
            lookup.side_effect = lambda host, *args, **kwargs: real_lookup('127.0.0.1' if host == 'wrong.invalid' else host, *args, **kwargs)
            with self.assertRaises(QueueUnavailable):
                QueueClient(f'https://wrong.invalid:{self.teacher.server_port}', ca_file=self.cert, timeout=3).login(CODE)

    def test_remote_plain_http_is_rejected_before_credentials_are_forwarded(self):
        replies = []
        handler = SimpleNamespace(connection=object(), client_address=('192.0.2.5', 1234),
                                  close_connection=False, svar=lambda *args: replies.append(args))
        self.assertTrue(gateway.handle(handler, '/api/laser/login', 20))
        self.assertEqual(replies[0][0], 403)
        self.assertTrue(handler.close_connection)

    def test_expired_session_clears_native_client_login(self):
        queue = self.queue()
        with self.inbox.lock:
            self.inbox.sessions.clear()
        with self.assertRaises(NotLoggedIn):
            queue.jobs()
        self.assertFalse(queue.logged_in)

    def test_central_station_configuration_and_handoff_checks_are_preserved(self):
        actor = {'id': 'teacher:' + '1' * 32, 'role': 'teacher', 'scopes': ['machines:write']}
        self.teacher.makerspace.execute(actor, {'action': 'laser.station.create',
            'params': {'id': 'laser-fbb2', 'name': 'Testlaser', 'location': 'Lab',
                       'model': 'fbb2', 'enabled': False},
            'expected_version': 0, 'request_id': uuid.uuid4().hex})
        queue = self.queue()
        self.assertFalse(queue.station_configuration('laser-fbb2')['enabled'])
        with self.assertRaisesRegex(ValueError, 'Stationen er ikke klar'):
            queue.check_handoff('laser-fbb2', 'fbb2', [self.job])

    def test_stalled_backend_response_has_a_total_deadline(self):
        queue = self.queue()
        release = threading.Event()
        def stalled(handler):
            handler.send_response(200)
            handler.send_header('Content-Length', '100')
            handler.end_headers()
            handler.wfile.write(b'{')
            handler.wfile.flush()
            release.wait(2)
        try:
            with patch.object(ObservedQueue, 'do_GET', stalled), patch.object(gateway, 'TIMEOUT', 0.1):
                started = time.monotonic()
                with self.assertRaises(ValueError):
                    queue.jobs()
                self.assertLess(time.monotonic() - started, 1.0)
        finally:
            release.set()

    def test_response_limit_and_backend_loss_are_controlled(self):
        queue = self.queue()
        with patch.object(gateway, 'MAX_RESPONSE', 20):
            with self.assertRaises(ValueError):
                queue.jobs()
        self.teacher.makerspace.laser_admin.backend = None
        self.assertEqual(self.call('GET', '/api/laser/jobs')[0], 503)


if __name__ == '__main__':
    unittest.main()
