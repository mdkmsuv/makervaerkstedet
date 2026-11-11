"""Regression tests for local, web and browser workshop configuration.

These tests deliberately use temporary directories and loopback HTTP servers.
They do not start any of the deployed workshop services.
"""
import http.client
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import apps
import elevhub
import regler
from lokale_filer import find_fil
from tilstand import Laererkode, Tilstand


def _local_app(path=None, **extra):
    app = {'id': 'scratch', 'navn': 'Scratch', 'type': 'lokal',
           'port': 8774, 'mappe': path}
    app.update(extra)
    return app


def _web_app(**extra):
    app = {'id': 'web', 'navn': 'Web', 'type': 'web',
           'start_url': 'https://example.com/start',
           'domaener': ['example.com']}
    app.update(extra)
    return app


def _browser_app(**extra):
    app = {'id': 'internet', 'navn': 'Internet', 'type': 'browser',
           'start_url': 'https://www.example.org/'}
    app.update(extra)
    return app


def _configuration(app_list):
    return {'hub_port': 8773, 'laerer_port': 8780, 'apps': app_list}


def _raw_request(port, request):
    """Send an exact request line, preserving dot segments in the path."""
    with socket.create_connection(('127.0.0.1', port), timeout=5) as sock:
        sock.sendall(request)
        chunks = []
        while True:
            try:
                data = sock.recv(65536)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
            if b'\r\n\r\n' in b''.join(chunks):
                # A local response has a bounded Content-Length.  Reading to
                # EOF also works because malformed requests close the socket.
                continue
        return b''.join(chunks)


class KonfigurationTest(unittest.TestCase):
    def test_standardkonfigurationens_lokale_apps_bruger_elevservice(self):
        basis = json.loads((Path(__file__).with_name('konfiguration.json')).read_text(encoding='utf-8'))
        apps.valider(basis, regler.REGLER)
        apps.klargoer_lokale_apps(basis, tempfile.gettempdir())
        lokale = {a['id']: a for a in basis['apps'] if a.get('type') == 'lokal'}
        self.assertEqual(set(lokale), {'scratch', 'datatraener', 'kredsloeb'})
        self.assertTrue(all(a['klar'] and a['mappe'] is None for a in lokale.values()))

    def test_lokal_og_web_er_gyldige_samt_browser_uden_port(self):
        konf = _configuration([
            _local_app(), _web_app(), _browser_app(),
            {'id': 'v', 'navn': 'Værksted', 'port': 8770,
             'regler': 'printvaerksted'},
        ])
        self.assertIs(apps.valider(konf, regler.REGLER), konf)

    def test_ports_og_ids_kolliderer_ikke_lydlost(self):
        cases = [
            _configuration([_local_app(port=8773), _web_app()]),
            _configuration([_local_app(), _local_app(id='other', port=8774)]),
            _configuration([_local_app(), _web_app(id='scratch')]),
        ]
        for konf in cases:
            with self.assertRaises(ValueError):
                apps.valider(konf, regler.REGLER)

    def test_web_adresse_og_domæner_er_strengt_https_og_præcise(self):
        ugyldige = [
            {'start_url': 'http://example.com/start'},
            {'start_url': 'https://user:password@example.com/'},
            {'start_url': 'https://example.com:444/'},
            {'domaener': ['example.com', 'example.com']},
            {'domaener': ['other.example.com']},
            {'domaener': ['scratch.local']},
        ]
        for ændring in ugyldige:
            with self.subTest(ændring=ændring):
                konf = _configuration([_web_app(**ændring)])
                with self.assertRaises(ValueError):
                    apps.valider(konf, regler.REGLER)
    def test_web_port_443_accepteres(self):
        konf = _configuration([_web_app(start_url='https://example.com:443/')])
        self.assertIs(apps.valider(konf, regler.REGLER), konf)

    def test_elevcomputer_lokal_service_kraever_ikke_central_mappe(self):
        konf = _configuration([_local_app(lokal_service=True, lokal_port=8774)])
        apps.klargoer_lokale_apps(konf, tempfile.gettempdir())
        self.assertIsNone(konf['apps'][0]['mappe'])
        self.assertTrue(konf['apps'][0]['klar'])

    def test_legacy_central_lokal_app_stadig_kraever_mappe(self):
        konf = _configuration([_local_app()])
        apps.klargoer_lokale_apps(konf, tempfile.gettempdir())
        self.assertIsNone(konf['apps'][0]['mappe'])
        self.assertFalse(konf['apps'][0]['klar'])

    def test_lokal_service_health_styrer_klarhed_pr_elevcomputer(self):
        local = _local_app(lokal_service=True, lokal_port=8774)
        with tempfile.TemporaryDirectory() as directory:
            state = Tilstand(directory, [local], ur=lambda: 1000.0)
            self.assertTrue(state.apps_til_elever()['apps'][0]['klar'])
            closed = state.status_fra_elev('elev-01', '127.0.0.1', 'bjaelke', local_apps=[])
            self.assertFalse(closed['apps'][0]['klar'])
            ready = state.status_fra_elev('elev-01', '127.0.0.1', 'bjaelke',
                                          local_apps=[{'id': 'scratch', 'ready': True}])
            self.assertTrue(ready['apps'][0]['klar'])


class LokalFilTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'index.html').write_text('<h1>Scratch</h1>', encoding='utf-8')
        (self.root / 'app.js').write_text('console.log(1);', encoding='utf-8')
        self.outside = Path(self.temp.name).parent / (self.root.name + '-secret.txt')
        self.outside.write_text('secret', encoding='utf-8')

    def tearDown(self):
        try:
            self.outside.unlink()
        except FileNotFoundError:
            pass
        self.temp.cleanup()

    def test_index_query_nested_og_ukendt_fil(self):
        self.assertEqual(find_fil(str(self.root), '/'), self.root / 'index.html')
        self.assertEqual(find_fil(str(self.root), '/?cache=1'), self.root / 'index.html')
        self.assertEqual(find_fil(str(self.root), '/app.js?v=1'), self.root / 'app.js')
        self.assertIsNone(find_fil(str(self.root), '/missing.js'))
        self.assertIsNone(find_fil(str(self.root), '/secret.py'))

    def test_traversal_backslash_host_og_symlink_ud_afvises(self):
        for sti in ('/../secret.txt', '/%2e%2e/secret.txt', '/..%2fsecret.txt',
                    '/%5csecret.txt', '//other-host/secret.txt', '/C:/secret.txt'):
            with self.subTest(sti=sti):
                self.assertIsNone(find_fil(str(self.root), sti))
        if hasattr(os, 'symlink'):
            link = self.root / 'outside.txt'
            try:
                link.symlink_to(self.outside)
            except (OSError, NotImplementedError):
                pass
            else:
                self.assertIsNone(find_fil(str(self.root), '/outside.txt'))


class LokalHttpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        body = b'<html><body>lokalt</body></html>'
        (self.root / 'index.html').write_bytes(body)
        (self.root / 'app.js').write_bytes(b'console.log(1)')
        self.apps = [_local_app(str(self.root), klar=True, standard_aaben=True)]
        self.state = Tilstand(self.temp.name, self.apps, ur=lambda: 1000.0)
        self.server = elevhub.Server(('127.0.0.1', 0), elevhub.LokaltVaerksted)
        self.server.navn = 'scratch'
        self.server.app = self.apps[0]
        self.server.tilstand = self.state
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def kald(self, metode, sti, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=5)
        connection.request(metode, sti, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        headers = dict(response.getheaders())
        connection.close()
        return response.status, headers, data

    def test_get_head_og_cachekontrol(self):
        status, headers, data = self.kald('GET', '/')
        self.assertEqual(status, 200)
        self.assertEqual(data, (self.root / 'index.html').read_bytes())
        self.assertEqual(int(headers['Content-Length']), len(data))
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        status, headers, data = self.kald('HEAD', '/')
        self.assertEqual((status, data), (200, b''))
        self.assertEqual(int(headers['Content-Length']), len((self.root / 'index.html').read_bytes()))

    def test_post_og_traversal_giver_ikke_adgang(self):
        status, headers, _ = self.kald('POST', '/', headers={})
        self.assertEqual(status, 405)
        self.assertEqual(headers['Allow'], 'GET, HEAD')
        raw = (b'POST /../secret.txt HTTP/1.1\r\nHost: 127.0.0.1\r\n'
               b'Content-Length: 0\r\nConnection: close\r\n\r\n')
        response = _raw_request(self.server.server_address[1], raw)
        self.assertIn(b' 405 ', response.split(b'\r\n', 1)[0])
        self.assertNotIn(b'secret', response)
        raw = (b'GET /../secret.txt HTTP/1.1\r\nHost: 127.0.0.1\r\n'
               b'Connection: close\r\n\r\n')
        response = _raw_request(self.server.server_address[1], raw)
        self.assertIn(b' 404 ', response.split(b'\r\n', 1)[0])
        self.assertNotIn(b'secret', response)

    def test_lukket_og_manglende_mappe(self):
        self.state.saet({'scratch': False})
        status, _, data = self.kald('GET', '/')
        self.assertEqual(status, 423)
        self.assertIn('lukket', data.decode())
        status, _, data = self.kald('GET', '/_elevhub/aaben')
        self.assertEqual((status, json.loads(data)), (200, {'aaben': False}))
        self.state.saet({'scratch': True})
        self.server.app.update(mappe=None, klar=False)
        status, _, _ = self.kald('GET', '/')
        self.assertEqual(status, 503)


class LaererBulkTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = [1000.0]
        self.apps = [
            _local_app(port=8774, mappe=None, klar=False),
            _web_app(),
            _browser_app(),
            {'id': 'vaerksted', 'navn': 'Værksted', 'port': 8770,
             'regler': 'printvaerksted'},
        ]
        self.state = Tilstand(self.temp.name, self.apps, ur=lambda: self.now[0])
        self.assertFalse(self.state.aaben('internet'))
        self.kode = Laererkode(self.temp.name)
        self.kode.vaelg('korrekt-kode-123')
        self.server = elevhub.Server(('127.0.0.1', 0), elevhub.Laerer)
        self.server.navn = 'laerer'
        self.server.tilstand = self.state
        self.server.kode = self.kode
        self.server.apps = self.apps
        self.server.usikker_test = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def test_alle_aabner_web_lokal_vaerksted_men_ikke_browser(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=5)
        payload = json.dumps({'kode': 'korrekt-kode-123'}).encode()
        connection.request('POST', '/api/laerer/login', payload,
                           {'Content-Type': 'application/json', 'Content-Length': str(len(payload))})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        cookie = response.getheader('Set-Cookie').split(';', 1)[0]
        response.read()
        origin = f"http://127.0.0.1:{self.server.server_address[1]}"
        payload = b'{"aaben": true}'
        connection.request('POST', '/api/laerer/alle', payload,
                           {'Content-Type': 'application/json', 'Content-Length': str(len(payload)),
                            'Cookie': cookie, 'Origin': origin})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        self.assertEqual(response.status, 200, data)
        self.assertTrue(all(self.state.aaben(app_id)
                            for app_id in ('scratch', 'web', 'vaerksted')))
        self.assertFalse(self.state.aaben('internet'))

    def test_lukning_af_web_og_lokal_faar_to_timer(self):
        self.state.saet({'scratch': False, 'web': False, 'vaerksted': False})
        self.assertEqual(self.state.lukket_indtil['scratch'], 1000.0 + 7200)
        self.assertEqual(self.state.lukket_indtil['web'], 1000.0 + 7200)
        self.assertEqual(self.state.lukket_indtil['vaerksted'], 1000.0 + 7200)
        self.assertNotIn('internet', self.state.lukket_indtil)


if __name__ == '__main__':
    unittest.main()
