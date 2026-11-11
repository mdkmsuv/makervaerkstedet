"""Regression tests for the Folie SVG-library gateway.

The library is stubbed deliberately: these tests exercise access gates and
forwarding, never the public SVG catalogue.
"""
import json
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ELEVHUB = ROOT / 'linux-elevhub' / 'server' / 'elevhub'
if str(ELEVHUB) not in sys.path:
    sys.path.insert(0, str(ELEVHUB))

from app.laser_local import create_local_servers  # noqa: E402
from elevhub import Indgang, Server, adresse  # noqa: E402
from regler import REGLER  # noqa: E402
from tilstand import Tilstand  # noqa: E402


class StubLibrary:
    def __init__(self):
        self.release = None
        self.started = None

    def catalogue(self):
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            self.release.wait(5)
        return [{'id': 'a' * 64, 'title': 'Foliefigur', 'category': 'test'}]

    def svg(self, item_id):
        if item_id != 'a' * 64:
            raise ValueError('Ukendt biblioteksfil.')
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"/>'


def apps():
    config = json.loads((ELEVHUB / 'konfiguration.json').read_text(encoding='utf-8'))
    return [next(a for a in config['apps'] if a['id'] == name) for name in ('laser', 'folie')]


class FolieLibraryGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.laser_app, self.folie_app = apps()
        self.tilstand = Tilstand(str(Path(self.temp.name) / 'state'), [self.laser_app, self.folie_app])
        self.central = Server(('127.0.0.1', 0), Indgang)
        self.central.navn = 'laser'
        self.central.app = self.laser_app
        self.central.tilstand = self.tilstand
        self.central.regel = REGLER[self.laser_app['regler']]
        self.central.bagved = adresse('127.0.0.1:1')
        self.library = StubLibrary()
        self.central.svg_library = self.library
        self._start(self.central)
        base = f'http://127.0.0.1:{self.central.server_port}'
        self.laser, self.folie = create_local_servers(base, laser_port=0, folie_port=0)
        self._start(self.laser)
        self._start(self.folie)

    def _start(self, server):
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

    def request(self, server, method, route):
        port = getattr(server, 'port', server.server_port)
        request = urllib.request.Request(f'http://127.0.0.1:{port}{route}', method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read()
                return response.status, (json.loads(body) if response.headers.get_content_type() == 'application/json' else body)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_begge_lokale_porte_kan_hente_egen_folie_route(self):
        for server in (self.folie,):
            status, body = self.request(server, 'GET', '/api/folie/library')
            self.assertEqual(status, 200, body)
            self.assertEqual(body['items'][0]['id'], 'a' * 64)
        self.assertEqual(self.request(self.laser, 'GET', '/api/folie/library')[0], 403)
        self.assertEqual(self.request(self.folie, 'GET', '/api/laser/library')[0], 403)

    def test_folie_aaben_laser_lukket_og_omvendt(self):
        self.tilstand.saet({'laser': False, 'folie': True})
        self.assertEqual(self.request(self.folie, 'GET', '/api/folie/library/status')[0], 200)
        self.assertEqual(self.request(self.laser, 'GET', '/api/laser/library/status')[0], 423)
        self.tilstand.saet({'laser': True, 'folie': False})
        self.assertEqual(self.request(self.laser, 'GET', '/api/laser/library/status')[0], 200)
        self.assertEqual(self.request(self.folie, 'GET', '/api/folie/library/status')[0], 423)

    def test_central_folie_route_uses_folie_gate_on_laser_ingress(self):
        self.tilstand.saet({'laser': False, 'folie': True})
        status, body = self.request(self.central, 'GET', '/api/folie/library')
        self.assertEqual(status, 200, body)
        self.assertEqual(self.request(self.central, 'GET', '/api/laser/library')[0], 423)

    def test_central_wrong_app_and_malformed_routes_are_rejected(self):
        for route in ('/api/laser/library', '/api/folie/library'):
            self.assertEqual(self.request(self.central, 'POST', route)[0], 403)
        for route in ('/api/folie/library?x=1', '/api/folie/library%2Fstatus',
                      '/api/folie/library/file/' + 'a' * 63, '/api/folie/library/file/' + 'A' * 64,
                      '/api/folie/library/status/file/' + 'a' * 64):
            self.assertEqual(self.request(self.folie, 'GET', route)[0], 403)

    def test_close_and_reopen_during_fetch_revokes_response(self):
        self.library.started = threading.Event()
        self.library.release = threading.Event()
        result = {}

        def fetch():
            result['response'] = self.request(self.folie, 'GET', '/api/folie/library')

        worker = threading.Thread(target=fetch)
        worker.start()
        self.assertTrue(self.library.started.wait(2))
        self.tilstand.saet({'folie': False})
        self.tilstand.saet({'folie': True})
        self.library.release.set()
        worker.join(5)
        self.assertEqual(result['response'][0], 423)

    def test_offline_returns_503_but_folie_editor_remains_available(self):
        closed = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        closed.bind(('127.0.0.1', 0))
        port = closed.getsockname()[1]
        closed.close()
        self.folie.inbox.server_url = f'http://127.0.0.1:{port}'
        status, _ = self.request(self.folie, 'GET', '/api/folie/library')
        self.assertEqual(status, 503)
        status, html = self.request(self.folie, 'GET', '/folie.html')
        self.assertEqual(status, 200)
        self.assertIn(b'<html', html)


if __name__ == '__main__':
    unittest.main()
