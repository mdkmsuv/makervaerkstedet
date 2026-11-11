"""Sikkerhedsgrænser med rigtige lokale TLS-forbindelser og midlertidige nøgler."""
import hashlib
import importlib.util
import http.client
from http.server import BaseHTTPRequestHandler
import json
import secrets
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from elevhub import Hub, Indgang, Laerer, PauseStore, Server
from regler import laser
from sikkerhed import Enhedsregister, tls_kontekst
from tilstand import Laererkode, Tilstand

OPENSSL = shutil.which('openssl')
if not OPENSSL:
    kandidat = Path('C:/Program Files/Git/usr/bin/openssl.exe')
    OPENSSL = str(kandidat) if kandidat.is_file() else None


class RegisterTest(unittest.TestCase):
    def test_standard_kraever_tls_og_ingen_stille_nedgradering(self):
        for konf in ({}, {'usikker_test': 'true'}, {'usikker_test': True, 'tls': {'certifikat': 'x'}}):
            with self.subTest(konf=konf), self.assertRaises(ValueError):
                tls_kontekst(konf, '.')
        self.assertIsNone(tls_kontekst({'usikker_test': True}, '.'))

    def test_register_afviser_genbrugte_noegler(self):
        with tempfile.TemporaryDirectory() as d:
            sti = Path(d) / 'register.json'
            sti.write_text(json.dumps({'enheder': {'pc1': {'token_sha256': 'a' * 64, 'ui_token_sha256': 'a' * 64}}}))
            with self.assertRaises(ValueError):
                Enhedsregister(sti)


@unittest.skipUnless(OPENSSL, 'OpenSSL kræves til midlertidige TLS-testcertifikater.')
class SikkerIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.cert, cls.key = cls.root / 'test.crt', cls.root / 'test.key'
        subprocess.run([OPENSSL, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=localhost', '-keyout', str(cls.key), '-out', str(cls.cert),
                        '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1',
                        '-addext', 'basicConstraints=critical,CA:TRUE',
                        '-addext', 'keyUsage=critical,digitalSignature,keyCertSign,cRLSign'],
                       capture_output=True, check=True, timeout=30)
        cls.tls = tls_kontekst({'tls': {'certifikat': str(cls.cert), 'noegle': str(cls.key)}}, cls.root)
        cls.client_tls = ssl.create_default_context(cafile=str(cls.cert))
        cls.register_sti = cls.root / 'enheder.json'
        cls.tokens = {navn: {rolle: secrets.token_urlsafe(32) for rolle in ('root', 'ui')}
                      for navn in ('pc1', 'pc2')}
        cls.skriv_register()
        cls.register = Enhedsregister(cls.register_sti)
        cls.hub = Server(('127.0.0.1', 0), Hub, tls=cls.tls)
        cls.hub.navn, cls.hub.enheder = 'hub', cls.register
        cls.teacher = Server(('127.0.0.1', 0), Laerer, tls=cls.tls)
        cls.teacher.navn = 'laerer'
        for s in (cls.hub, cls.teacher):
            threading.Thread(target=s.serve_forever, daemon=True).start()

    @classmethod
    def skriv_register(cls, fjern=None):
        register = {'enheder': {navn: {
            'token_sha256': hashlib.sha256(t['root'].encode()).hexdigest(),
            'ui_token_sha256': hashlib.sha256(t['ui'].encode()).hexdigest()}
            for navn, t in cls.tokens.items() if navn != fjern}}
        cls.register_sti.write_text(json.dumps(register))

    @classmethod
    def tearDownClass(cls):
        for s in (cls.hub, cls.teacher):
            s.shutdown()
            s.server_close()
        cls.temp.cleanup()

    def setUp(self):
        self.skriv_register()
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.data.cleanup)
        self.nu = 1000.0
        self.apps = [{'id': 'internet', 'navn': 'Internet', 'type': 'browser'}]
        self.t = Tilstand(self.data.name, self.apps, ur=lambda: self.nu)
        self.hub.tilstand = self.teacher.tilstand = self.t
        self.hub.pause = self.teacher.pause = PauseStore(self.data.name, clock=lambda: self.nu)
        self.teacher.kode = Laererkode(self.data.name)
        self.teacher.apps = self.apps

    def kald(self, sti, data=None, navn='pc1', rolle='root', headers=None, server=None):
        s = server or self.hub
        h = {'Content-Type': 'application/json'}
        if rolle:
            h.update({'X-Elevhub-Computer': navn,
                      'Authorization': 'Bearer ' + self.tokens[navn][rolle]})
        h.update(headers or {})
        c = http.client.HTTPSConnection('127.0.0.1', s.server_address[1], context=self.client_tls, timeout=3)
        try:
            c.request('GET' if data is None else 'POST', sti,
                      body=None if data is None else json.dumps(data).encode(), headers=h)
            r = c.getresponse()
            return r.status, dict(r.getheaders()), json.loads(r.read())
        finally:
            c.close()

    def test_politik_kraever_root_identitet_og_afviser_forkert_computer(self):
        self.assertEqual(self.kald('/api/enhed/politik')[0], 200)
        for rolle in (None, 'ui'):
            self.assertEqual(self.kald('/api/enhed/politik', rolle=rolle)[0], 401)
        self.assertEqual(self.kald('/api/enhed/politik', headers={'X-Elevhub-Computer': 'pc2'})[0], 401)
        self.assertEqual(self.kald('/api/enhed/status', {'computer': 'pc2', 'version': 1})[0], 403)
        self.assertEqual(self.t.oversigt()['computere'], [])

    def test_ui_kan_ikke_oprette_computer_bekraefte_eller_holde_den_online(self):
        body = {'computer': 'pc1', 'kilde': 'bjaelke', 'version': 1, 'visning': 'start'}
        self.assertEqual(self.kald('/api/status', body, rolle=None)[0], 401)
        self.assertEqual(self.kald('/api/status', body, rolle='ui')[0], 200)
        self.assertEqual(self.t.oversigt()['computere'], [])
        self.assertEqual(self.kald('/api/enhed/status', body, rolle='ui')[0], 401)
        self.assertEqual(self.kald('/api/enhed/status', body)[0], 200)
        self.assertTrue(self.t.oversigt()['computere'][0]['bekraeftet'])
        self.nu += 16
        self.kald('/api/status', body, rolle='ui')
        computer = self.t.oversigt()['computere'][0]
        self.assertFalse(computer['online'])
        self.assertFalse(computer['bekraeftet'])

    def test_rootpoll_fjerner_ikke_bjaelkens_gaa_hjem_besked(self):
        self.t.saet({'internet': True})
        self.kald('/api/enhed/status', {'computer': 'pc1', 'version': 2})
        self.kald('/api/status', {'computer': 'pc1', 'visning': 'internet'}, rolle='ui')
        self.t.saet({'internet': False})
        self.kald('/api/enhed/status', {'computer': 'pc1', 'version': 3})
        self.assertTrue(self.kald('/api/status', {'computer': 'pc1'}, rolle='ui')[2]['gaa_hjem'])
        self.assertFalse(self.kald('/api/status', {'computer': 'pc1'}, rolle='ui')[2]['gaa_hjem'])

    def test_faktiske_appkataloger_accepterer_lokal_microbit_uden_at_aabne_den(self):
        root = Path(__file__).resolve().parents[3]
        for config in (root / 'linux-elevhub/server/elevhub/konfiguration.json',
                       root / 'windows-elevhub/server/hub/konfiguration.windows.json'):
            with self.subTest(config=config.name), tempfile.TemporaryDirectory() as directory:
                catalog = json.loads(config.read_text(encoding='utf-8-sig'))['apps']
                self.t = self.hub.tilstand = Tilstand(directory, catalog, ur=lambda: self.nu)
                self.t.saet({'microbit': False})
                self.kald('/api/enhed/status', {'computer': 'pc1', 'version': self.t.version})
                body = {'computer': 'pc1', 'visning': 'microbit', 'local_apps': [
                    {'id': app, 'ready': True} for app in ('scratch', 'datatraener', 'kredsloeb', 'microbit')]}
                status, _, reply = self.kald('/api/status', body, rolle='ui')
                self.assertEqual(status, 200, reply)
                self.assertEqual(reply['lukket_app'], 'microbit')
                self.assertFalse(next(a for a in reply['apps'] if a['id'] == 'microbit')['aaben'])
                previous = dict(self.t.computere['pc1'])
                for invalid in ([{'id': 'unknown', 'ready': True}],
                                [{'id': 'microbit'}],
                                [{'id': 'microbit', 'ready': True}] * 2):
                    body['local_apps'] = invalid
                    body['visning'] = 'start'
                    self.assertEqual(self.kald('/api/status', body, rolle='ui')[0], 400)
                    self.assertEqual(self.t.computere['pc1'], previous)

    def test_laerervalg_naar_rigtig_bjaelke_gennem_tls_og_flere_statuskald(self):
        root = Path(__file__).resolve().parents[3]
        client_path = root / 'linux-elevhub/ansible/roles/kiosk/files/elevbjaelke.py'
        spec = importlib.util.spec_from_file_location('contract_elevbjaelke', client_path)
        client = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(client)
        catalog = json.loads((root / 'linux-elevhub/server/elevhub/konfiguration.json').read_text(encoding='utf-8'))['apps']
        self.t = self.hub.tilstand = self.teacher.tilstand = Tilstand(self.data.name, catalog, ur=lambda: self.nu)
        self.teacher.apps = catalog
        self.kald('/api/enhed/status', {'computer': 'pc1', 'version': self.t.version})

        password = secrets.token_urlsafe(20)
        self.teacher.kode.vaelg(password)
        origin = f'https://127.0.0.1:{self.teacher.server_address[1]}'
        status, headers, _ = self.kald('/api/laerer/login', {'kode': password}, rolle=None,
                                      headers={'Origin': origin}, server=self.teacher)
        self.assertEqual(status, 200)
        teacher_headers = {'Origin': origin, 'Cookie': headers['Set-Cookie'].split(';')[0]}

        def teacher(path, body):
            status, _, response = self.kald(path, body, rolle=None, headers=teacher_headers, server=self.teacher)
            self.assertEqual(status, 200, response)

        class Overlay:
            closed = False
            paused = False
            def acknowledgement(self): return None
            def update_pause(self, state): self.paused = state['paused']
            def update_workshop_closed(self, closed, name): self.closed, self.name = closed, name
            def offline(self): self.closed = self.paused = False

        bar = client.Bjaelke.__new__(client.Bjaelke)
        bar.konf = {'computer': 'pc1', 'hub': f'https://127.0.0.1:{self.hub.server_address[1]}',
                    'ui_token': self.tokens['pc1']['ui'], 'ca_fil': str(self.cert), 'internet_app': 'internet'}
        bar.local_navigation_version = None
        bar.rod = type('Root', (), {'after': lambda self, delay, callback: callback()})()
        bar.pause_overlay = Overlay()
        bar.cadoodle = type('Cad', (), {'opdater': lambda self, state: setattr(self, 'state', state)})()
        local = {'apps': [{'id': app, 'ready': True} for app in ('scratch', 'datatraener', 'kredsloeb', 'microbit')],
                 'navigation': {'id': 'scratch', 'version': 'a' * 32}}
        with patch.object(client, 'lokal_status', return_value=local), \
                patch.object(client, 'usb_noegler', return_value=[]):
            teacher('/api/laerer/app', {'id': 'scratch', 'aaben': True})
            bar._meld()
            self.assertFalse(bar.pause_overlay.closed)
            teacher('/api/laerer/app', {'id': 'scratch', 'aaben': False})
            # Root heartbeat, then several UI polls without fresh navigation.
            self.kald('/api/enhed/status', {'computer': 'pc1', 'version': self.t.version})
            for _ in range(3):
                bar._meld()
                self.assertTrue(bar.pause_overlay.closed)
                self.assertEqual(bar.pause_overlay.name, 'scratch')
            teacher('/api/laerer/pause', {'paused': True, 'expires_in': 60})
            bar._meld()
            self.assertTrue(bar.pause_overlay.paused)
            # Transport loss opens offline access; restoring contact reapplies
            # the current teacher choices, not a saved client-side closure.
            with patch.object(client, 'hent_json', side_effect=OSError('offline')):
                bar._meld()
            self.assertTrue(bar.internet_aaben)
            self.assertFalse(bar.pause_overlay.closed)
            self.assertFalse(bar.pause_overlay.paused)
            bar._meld()
            self.assertTrue(bar.pause_overlay.closed)
            self.assertTrue(bar.pause_overlay.paused)
            teacher('/api/laerer/pause', {'paused': False})
            teacher('/api/laerer/app', {'id': 'scratch', 'aaben': True})
            teacher('/api/laerer/app', {'id': 'internet', 'aaben': True})
            bar._meld()
            self.assertFalse(bar.pause_overlay.closed)
            self.assertFalse(bar.pause_overlay.paused)
            self.assertTrue(bar.internet_aaben)
            self.assertIsInstance(bar.cadoodle.state, dict)
            # Reclosing the still-open document works without navigation.
            teacher('/api/laerer/app', {'id': 'scratch', 'aaben': False})
            bar._meld()
            self.assertTrue(bar.pause_overlay.closed)
            local['navigation'] = {'id': 'start', 'version': 'b' * 32}
            bar._meld()
            self.assertFalse(bar.pause_overlay.closed)

    def test_spaerring_og_defekt_register_gaelder_uden_genstart(self):
        self.skriv_register(fjern='pc1')
        self.assertEqual(self.kald('/api/enhed/politik')[0], 401)
        self.register_sti.write_text('{defekt')
        self.assertEqual(self.kald('/api/enhed/politik', navn='pc2')[0], 401)

    def test_diskrettighedsfejl_er_503_uden_filnavn_eller_aendret_pause(self):
        password = secrets.token_urlsafe(20)
        self.teacher.kode.vaelg(password)
        origin = f'https://127.0.0.1:{self.teacher.server_address[1]}'
        status, headers, _ = self.kald('/api/laerer/login', {'kode': password}, rolle=None,
                                      headers={'Origin': origin}, server=self.teacher)
        self.assertEqual(status, 200)
        teacher_headers = {'Origin': origin, 'Cookie': headers['Set-Cookie'].split(';')[0]}
        for original in (False, True):
            with self.subTest(original=original):
                before = self.teacher.pause.set(original, expires_in=60)
                error = PermissionError(13, 'Access denied', 'private-fixture-path.json')
                with patch.object(self.teacher.pause, '_write_locked', side_effect=error), \
                        self.assertLogs(level='ERROR'):
                    status, _, reply = self.kald('/api/laerer/pause', {'paused': not original},
                                                rolle=None, headers=teacher_headers, server=self.teacher)
                self.assertEqual(status, 503)
                self.assertNotIn('private-fixture-path', json.dumps(reply))
                self.assertEqual(self.teacher.pause.state(), before)

    def test_rootkvittering_bevarer_ui_usb_og_udtagning_rydder_den(self):
        self.kald('/api/enhed/status', {'computer': 'pc1', 'version': 1})
        self.kald('/api/status', {'computer': 'pc1', 'usb': 'Elevens USB'}, rolle='ui')
        self.kald('/api/enhed/status', {'computer': 'pc1', 'version': 1})
        self.kald('/api/status', {'computer': 'pc1', 'visning': 'start'}, rolle='ui')
        self.assertEqual(self.t.oversigt()['computere'][0]['usb'], 'Elevens USB')
        self.kald('/api/status', {'computer': 'pc1', 'usb': None}, rolle='ui')
        self.assertIsNone(self.t.oversigt()['computere'][0]['usb'])

    def test_tls_afviser_ukendt_ca_og_forkert_servernavn(self):
        with self.assertRaises(ssl.SSLCertVerificationError):
            c = http.client.HTTPSConnection('127.0.0.1', self.hub.server_address[1], timeout=3)
            try:
                c.request('GET', '/api/apps')
            finally:
                c.close()
        with socket.create_connection(self.hub.server_address, timeout=3) as raw:
            with self.assertRaises(ssl.SSLCertVerificationError):
                self.client_tls.wrap_socket(raw, server_hostname='forkert.invalid')

    def test_https_kasseformular_til_http_backend_bevarer_origin_kontrol(self):
        received = []

        class HttpWorkshop(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                origin, host = self.headers.get('Origin'), self.headers['Host']
                received.append((self.path, origin, host))
                # Same-origin check used by the actual HTTP laser backend.
                status = 200 if not origin or origin == 'http://' + host else 403
                body = json.dumps({'origin': origin}).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        backend = Server(('127.0.0.1', 0), HttpWorkshop)
        gateway = Server(('127.0.0.1', 0), Indgang, tls=self.tls)
        app = {'id': 'laser', 'navn': 'Laser', 'standard_aaben': True, 'regler': 'laser'}
        gateway.navn, gateway.app, gateway.regel = 'laser', app, laser
        gateway.bagved = backend.server_address
        gateway.tilstand = Tilstand(Path(self.data.name) / 'proxy', [app])
        for server in (backend, gateway):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            host = f'127.0.0.1:{gateway.server_address[1]}'
            body = {'level': 3, 'kind': 'abox', 'x': 80, 'y': 60, 'h': 40,
                    'thickness': 3, 'burn': 0.1}
            status, _, _ = self.kald('/api/laser/boxes', body, rolle=None,
                                    headers={'origin': 'https://' + host}, server=gateway)
            self.assertEqual(status, 200)
            self.assertEqual(received[-1], ('/api/laser/boxes', 'http://' + host, host))
            for origin in ('https://foreign.invalid', 'http://' + host, 'null'):
                with self.subTest(origin=origin):
                    before = len(received)
                    status, _, _ = self.kald('/api/laser/boxes', body, rolle=None,
                                            headers={'Origin': origin, 'X-Forwarded-Proto': 'https'},
                                            server=gateway)
                    self.assertEqual(status, 403)
                    self.assertEqual(len(received), before, 'Rejected requests must not reach the backend')
            # Native clients do not send a browser Origin; retain that contract.
            self.assertEqual(self.kald('/api/laser/boxes', body, rolle=None, server=gateway)[0], 200)
            self.assertIsNone(received[-1][1])
        finally:
            for server in (gateway, backend):
                server.shutdown()
                server.server_close()

    def test_laerer_https_login_secure_cookie_og_origin(self):
        kode = secrets.token_urlsafe(20)
        self.teacher.kode.vaelg(kode)
        host = f'127.0.0.1:{self.teacher.server_address[1]}'
        status, h, _ = self.kald('/api/laerer/login', {'kode': kode}, rolle=None,
                                  headers={'Origin': 'https://' + host}, server=self.teacher)
        self.assertEqual(status, 200)
        self.assertIn('; Secure', h['Set-Cookie'])
        cookie = h['Set-Cookie'].split(';')[0]
        self.assertEqual(self.kald('/api/laerer/app', {'id': 'internet', 'aaben': True}, rolle=None,
                                  headers={'Cookie': cookie, 'Origin': 'http://' + host}, server=self.teacher)[0], 403)
        self.assertFalse(self.t.aaben('internet'))
        pause_body = {'paused': True, 'message': 'Gem først', 'expires_in': 60}
        status, _, pause_reply = self.kald('/api/laerer/pause', pause_body, rolle=None,
                                           headers={'Cookie': cookie, 'Origin': 'https://' + host},
                                           server=self.teacher)
        self.assertEqual(status, 200)
        pause_version = pause_reply['pause']['version']
        status, _, reply = self.kald('/api/status',
                                     {'computer': 'pc1', 'kilde': 'bjaelke',
                                      'pause_ack': {'version': pause_version, 'shown': True}},
                                     rolle='ui')
        self.assertEqual(status, 200)
        self.assertEqual(reply['pause']['version'], pause_version)
        self.assertTrue(self.hub.pause.acknowledgement('pc1')['shown'])
        # The root-token endpoint may report policy, but cannot forge the UI's
        # on-screen pause acknowledgement.
        self.kald('/api/enhed/status',
                  {'computer': 'pc1', 'version': 1,
                   'pause_ack': {'version': pause_version, 'shown': False}},
                  rolle='root')
        self.assertTrue(self.hub.pause.acknowledgement('pc1')['shown'])

    def test_langsom_tls_handshake_blokerer_ikke_andre(self):
        with socket.create_connection(self.hub.server_address, timeout=3):
            self.assertEqual(self.kald('/api/enhed/politik')[0], 200)


if __name__ == '__main__':
    unittest.main()
