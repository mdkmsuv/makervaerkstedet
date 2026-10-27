import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from app.laser_local import ForwardingInbox, OFFLINE, create_local_servers
from app.laser_server import create_server

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" data-laservaerksted="1" width="100mm" height="60mm" '
       'viewBox="0 0 100 60"><title>Navneskilt</title><g data-operation="cut">'
       '<path d="M10 10H60V40H10Z" data-operation="cut"/></g></svg>')


def payload():
    return {'elevnavn': 'Åse', 'klasse': '3.A', 'projekt': 'Navneskilt',
            'requestId': str(uuid.uuid4()), 'svg': SVG}


class _FakeCentral(BaseHTTPRequestHandler):
    """Overrides pr. testklasse via klasseattributter status/body."""
    status = 200
    body = b'{}'

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        self.rfile.read(length)
        body = self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode('utf-8')
        self.send_response(self.status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def start_fake_central(status, body):
    handler = type('Fake', (_FakeCentral,), {'status': status, 'body': body})
    server = HTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class LaserLocalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.central = create_server(self.temp.name, '127.0.0.1', 0, code='test-only-code', surface='student')
        threading.Thread(target=self.central.serve_forever, daemon=True).start()
        self.addCleanup(self.central.server_close)
        self.addCleanup(self.central.shutdown)
        self.laser, self.folie = create_local_servers(
            f'http://127.0.0.1:{self.central.server_port}', host='127.0.0.1', laser_port=0, folie_port=0)
        for server in (self.laser, self.folie):
            server.RequestHandlerClass.log_message = lambda *a: None
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)

    def request(self, server, method, route, data=None, host=None):
        conn = http.client.HTTPConnection('127.0.0.1', server.port, timeout=5)
        headers = {}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        if host is not None:
            headers['Host'] = host
        body = json.dumps(data) if data is not None else None
        conn.request(method, route, body, headers)
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    # 1. Submit via lokal laserport ender i det centrale inbox.
    def test_submit_lands_in_central_inbox_and_dedupes(self):
        job = payload()
        status, _, body = self.request(self.laser, 'POST', '/api/laser/submit', job)
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertFalse(result['duplicate'])
        jobs = self.central.inbox.jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['id'], result['id'])
        status, _, body = self.request(self.laser, 'POST', '/api/laser/submit', job)
        retry = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(retry['duplicate'])
        self.assertEqual(retry['id'], result['id'])

    # 2. Central valideringsfejl bliver ført igennem.
    def test_central_validation_error_propagates(self):
        job = payload()
        job['elevnavn'] = ''
        status, _, body = self.request(self.laser, 'POST', '/api/laser/submit', job)
        self.assertEqual(status, 400)
        self.assertIn('Udfyld elevnavn', json.loads(body)['error'])

    # 3. Central utilgængelig -> OFFLINE.
    def test_central_unreachable_gives_offline(self):
        closed = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        closed.bind(('127.0.0.1', 0))
        port = closed.getsockname()[1]
        closed.close()
        laser, folie = create_local_servers(f'http://127.0.0.1:{port}', laser_port=0, folie_port=0)
        threading.Thread(target=laser.serve_forever, daemon=True).start()
        self.addCleanup(laser.server_close)
        self.addCleanup(laser.shutdown)
        folie.server_close()
        status, _, body = self.request(laser, 'POST', '/api/laser/submit', payload())
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)['error'], OFFLINE)

    # 4. Falsk central der lukker værkstedet / svarer sludder / omdirigerer.
    def test_fake_central_responses(self):
        closed_workshop_msg = 'Læreren har lukket Laser lige nu. Gå tilbage til start.'
        fake = start_fake_central(423, {'error': closed_workshop_msg})
        self.addCleanup(fake.server_close)
        self.addCleanup(fake.shutdown)
        laser, folie = create_local_servers(f'http://127.0.0.1:{fake.server_port}', laser_port=0, folie_port=0)
        threading.Thread(target=laser.serve_forever, daemon=True).start()
        self.addCleanup(laser.server_close)
        self.addCleanup(laser.shutdown)
        folie.server_close()
        status, _, body = self.request(laser, 'POST', '/api/laser/submit', payload())
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)['error'], closed_workshop_msg)

        fake2 = start_fake_central(200, {'junk': True})
        self.addCleanup(fake2.server_close)
        self.addCleanup(fake2.shutdown)
        laser2, folie2 = create_local_servers(f'http://127.0.0.1:{fake2.server_port}', laser_port=0, folie_port=0)
        threading.Thread(target=laser2.serve_forever, daemon=True).start()
        self.addCleanup(laser2.server_close)
        self.addCleanup(laser2.shutdown)
        folie2.server_close()
        status, _, body = self.request(laser2, 'POST', '/api/laser/submit', payload())
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)['error'], OFFLINE)

        class RedirectFake(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get('Content-Length', '0'))
                self.rfile.read(length)
                self.send_response(302)
                self.send_header('Location', '/somewhere')
                self.send_header('Content-Length', '0')
                self.end_headers()

            def log_message(self, *args):
                pass

        fake4 = HTTPServer(('127.0.0.1', 0), RedirectFake)
        threading.Thread(target=fake4.serve_forever, daemon=True).start()
        self.addCleanup(fake4.server_close)
        self.addCleanup(fake4.shutdown)
        laser4, folie4 = create_local_servers(f'http://127.0.0.1:{fake4.server_port}', laser_port=0, folie_port=0)
        threading.Thread(target=laser4.serve_forever, daemon=True).start()
        self.addCleanup(laser4.server_close)
        self.addCleanup(laser4.shutdown)
        folie4.server_close()
        status, _, body = self.request(laser4, 'POST', '/api/laser/submit', payload())
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)['error'], OFFLINE)

    # 5. Laserport: sider og routing.
    def test_laser_port_routes(self):
        status, headers, _ = self.request(self.laser, 'GET', '/')
        self.assertEqual(status, 302)
        self.assertEqual(headers['Location'], '/laser.html')
        status, _, body = self.request(self.laser, 'GET', '/laser.html')
        self.assertEqual(status, 200)
        self.assertIn(b'id="elevhub"', body)
        self.assertIn(b'#workshop-home', body)
        self.assertEqual(self.request(self.laser, 'GET', '/folie.html')[0], 404)
        self.assertEqual(self.request(self.laser, 'GET', '/workshop.html')[0], 404)

    # 6. Folieport: sider og routing.
    def test_folie_port_routes(self):
        status, headers, _ = self.request(self.folie, 'GET', '/')
        self.assertEqual(status, 302)
        self.assertEqual(headers['Location'], '/folie.html')
        status, _, body = self.request(self.folie, 'GET', '/folie.html')
        self.assertEqual(status, 200)
        self.assertEqual(self.request(self.folie, 'GET', '/laser.html')[0], 404)
        self.assertEqual(self.request(self.folie, 'POST', '/api/laser/submit', payload())[0], 404)
        self.assertEqual(self.request(self.folie, 'GET', '/api/laser/health')[0], 200)

    @patch('app.laser_server.school_appearance', return_value={'school_name': 'Testskolen', 'theme': 'green'})
    def test_school_appearance_is_available_on_both_local_workshops(self, appearance):
        for server in (self.laser, self.folie):
            status, _, body = self.request(server, 'GET', '/api/school-appearance')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {'school_name': 'Testskolen', 'theme': 'green'})
            self.assertEqual(server.data_root, Path('/etc/elevhub'))
        self.assertEqual(appearance.call_count, 2)

    # 7. Forkert Host-header afvises.
    def test_wrong_host_header_is_blocked(self):
        status, _, _ = self.request(self.laser, 'GET', '/laser.html', host='evil.example:%d' % self.laser.port)
        self.assertEqual(status, 403)
        other_port = self.laser.port + 1
        status, _, _ = self.request(self.laser, 'GET', '/laser.html', host=f'127.0.0.1:{other_port}')
        self.assertEqual(status, 403)
        status, _, _ = self.request(self.laser, 'POST', '/api/laser/submit', payload(),
                                     host=f'127.0.0.1:{other_port}')
        self.assertEqual(status, 403)

    # 8. Ugyldige konstruktørparametre.
    def test_invalid_configuration_raises(self):
        with self.assertRaises(ValueError):
            create_local_servers(f'http://127.0.0.1:{self.central.server_port}', host='0.0.0.0',
                                  laser_port=0, folie_port=0)
        with self.assertRaises(ValueError):
            ForwardingInbox('ftp://x')
        with self.assertRaises(ValueError):
            ForwardingInbox('http://x?y=1')

    # 9. Kasseboksen kører lokalt, kun hvis afhængighederne findes.
    def test_boxes_local_when_dependencies_available(self):
        try:
            import shapely  # noqa: F401
            import qrcode  # noqa: F401
        except ImportError:
            self.skipTest('shapely/qrcode ikke installeret i dette miljø')
        data = {'level': 3, 'kind': 'abox', 'x': 50, 'y': 40, 'h': 30, 'thickness': 3, 'burn': 0.1}
        status, _, body = self.request(self.laser, 'POST', '/api/laser/boxes', data)
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertIn('svg', result)

    def test_boxes_requires_level_3(self):
        data = {'level': 1, 'kind': 'abox', 'x': 50, 'y': 40, 'h': 30}
        status, _, _ = self.request(self.laser, 'POST', '/api/laser/boxes', data)
        self.assertEqual(status, 403)


if __name__ == '__main__':
    unittest.main()
