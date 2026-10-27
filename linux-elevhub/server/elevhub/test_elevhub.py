"""Tests for Elevhub-serveren.  Kør:  python -m unittest test_elevhub  (fra denne mappe)

Integrationstestene starter rigtige servere på ledige porte mod en falsk bagvedliggende
værkstedsserver. Lærerkoden i testene er tilfældig og findes kun under testen.
"""
import http.client
from email.message import Message
import io
import json
import secrets
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import SimpleNamespace

import elevhub
import regler
from tilstand import Laererkode, Tilstand

APPS = [
    {'id': '3dprint', 'navn': '3D-print', 'port': 0, 'bagved': '', 'regler': 'printvaerksted', 'beskrivelse': 'Print',
     'skjul': ['a[href="/laerer"]']},
    {'id': 'laser', 'navn': 'Laser', 'port': 0, 'bagved': '', 'regler': 'laser'},
]


class TeacherSetupLocalityTests(unittest.TestCase):
    def test_first_code_setup_checks_peer_against_the_actual_listener_bind(self):
        cases = [
            ('192.0.2.10', '192.0.2.10', True),
            ('192.0.2.11', '192.0.2.10', False),
            ('127.0.0.1', '192.0.2.10', True),
            ('192.0.2.10', '127.0.0.1', False),
            ('192.0.2.10', '0.0.0.0', False),
            ('127.0.0.1', '0.0.0.0', True),
        ]
        for peer, bound, allowed in cases:
            with self.subTest(peer=peer, bound=bound), tempfile.TemporaryDirectory() as folder:
                # Execute the real setup handler and persist a real disposable
                # code. Only socket addresses and the response sink are supplied.
                handler = object.__new__(elevhub.Laerer)
                handler.command, handler.path = 'POST', '/api/laerer/opsaet'
                handler.client_address = (peer, 50000)
                handler.server = SimpleNamespace(server_address=(bound, 8780), kode=Laererkode(folder))
                handler.connection = SimpleNamespace(gettimeout=lambda: None, settimeout=lambda _: None)
                handler.close_connection = False
                body = json.dumps({'kode': 'isolated-setup-code', 'kode_igen': 'isolated-setup-code'}).encode()
                handler.rfile = io.BytesIO(body)
                handler.headers = Message()
                handler.headers['Content-Type'] = 'application/json'
                handler.headers['Content-Length'] = str(len(body))
                # A caller-supplied Host cannot grant local setup privileges.
                handler.headers['Host'] = '127.0.0.1:8780'
                replies = []
                handler.svar = lambda status, *args, **kwargs: replies.append(status)
                handler.do_POST()
                self.assertEqual(replies, [200 if allowed else 403])
                self.assertEqual(handler.server.kode.er_valgt(), allowed)


class Regler(unittest.TestCase):
    def test_school_appearance_is_read_only_public_metadata(self):
        for rule in (regler.printvaerksted, regler.laser, regler.folie):
            self.assertEqual(rule('GET', '/api/school-appearance')[0], 'tillad')
            self.assertEqual(rule('POST', '/api/school-appearance')[0], 'afvis')
            self.assertEqual(rule('GET', '/api/settings')[0], 'afvis')

    def test_printvaerksted(self):
        r = regler.printvaerksted
        for metode, sti, forventet in [
            ('GET', '/', 'tillad'), ('GET', '/api/state', 'tillad'), ('GET', '/app.js', 'tillad'),
            ('POST', '/api/jobs', 'tillad'), ('GET', '/laerer', 'afvis'), ('GET', '/%6caerer', 'afvis'),
            ('GET', '/api/settings', 'afvis'), ('GET', '/laser-teacher.html', 'afvis'),
            ('POST', '/api/queue/pause', 'afvis'), ('GET', '/../laerer', 'afvis'), ('GET', '/a\\b', 'afvis'),
            ('PUT', '/api/jobs', 'afvis'),
        ]:
            with self.subTest(metode=metode, sti=sti):
                self.assertEqual(r(metode, sti)[0], forventet)

    def test_laser_og_folie_holdes_adskilt(self):
        self.assertEqual(regler.laser('GET', '/'), ('omdiriger', '/laser.html'))
        self.assertEqual(regler.folie('GET', '/'), ('omdiriger', '/folie.html'))
        self.assertEqual(regler.laser('GET', '/folie.html')[0], 'afvis')
        self.assertEqual(regler.folie('GET', '/laser.html')[0], 'afvis')
        self.assertEqual(regler.laser('POST', '/api/laser/submit')[0], 'tillad')
        self.assertEqual(regler.folie('POST', '/api/laser/submit')[0], 'afvis')
        self.assertEqual(regler.laser('POST', '/api/laser/boxes')[0], 'tillad')
        self.assertEqual(regler.folie('POST', '/api/laser/boxes')[0], 'afvis')
        self.assertEqual(regler.laser('GET', '/api/laser/jobs')[0], 'afvis')
        self.assertEqual(regler.folie('GET', '/api/laser/health')[0], 'tillad')
        self.assertEqual(regler.laser('GET', '/laser-teacher.html')[0], 'afvis')


class TilstandTest(unittest.TestCase):
    def setUp(self):
        self.mappe = tempfile.TemporaryDirectory()
        self.nu = [1000.0]
        self.t = Tilstand(self.mappe.name, APPS, ur=lambda: self.nu[0])

    def tearDown(self):
        self.mappe.cleanup()

    def test_apps_starter_lukkede_og_huskes(self):
        self.assertFalse(self.t.aaben('3dprint'))
        v = self.t.saet({'3dprint': True})
        self.assertEqual(v, 2)
        self.assertEqual(self.t.saet({'3dprint': True}), 2, 'samme valg giver ikke ny version')
        igen = Tilstand(self.mappe.name, APPS, ur=lambda: self.nu[0])
        self.assertTrue(igen.aaben('3dprint'))
        self.assertEqual(igen.version, 3, 'den gemte laserlukning får en frist ved migration')
        self.assertEqual(igen.lukket_indtil, {'laser': self.nu[0] + 7200})
        with self.assertRaises(KeyError):
            self.t.saet({'ukendt': True})

    def test_standarder_bruges_ved_foerste_opstart(self):
        apps = [
            {'id': '3dprint', 'navn': '3D-print', 'standard_aaben': True},
            {'id': 'internet', 'navn': 'Internet', 'type': 'browser',
             'standard_aaben': False, 'luk_ved_genstart': True},
        ]
        t = Tilstand(self.mappe.name, apps)
        self.assertTrue(t.aaben('3dprint'))
        self.assertFalse(t.aaben('internet'))
        self.assertEqual(json.loads((Path(self.mappe.name) / 'tilstand.json').read_text())['version'], 2)

    def test_begge_konfigurationer_aabner_vaerksteder_og_lukker_internet(self):
        rod = Path(__file__).resolve().parents[2]
        for konfig_sti in (rod / 'server/elevhub/konfiguration.json',
                           rod / 'testmiljoe/vaert/elevhub-test.json'):
            with self.subTest(konfiguration=konfig_sti.name), tempfile.TemporaryDirectory() as mappe:
                konfiguration = json.loads(konfig_sti.read_text(encoding='utf-8'))
                t = Tilstand(mappe, konfiguration['apps'])
                self.assertEqual([t.aaben(app_id) for app_id in ('3dprint', 'laser', 'folie', 'internet')],
                                 [True, True, True, False])

    def test_gemt_laerer_lukning_bevares_ved_genstart(self):
        apps = [{'id': '3dprint', 'navn': '3D-print', 'standard_aaben': True}]
        sti = Path(self.mappe.name) / 'tilstand.json'
        gemt = {'aabne': {'3dprint': False}, 'version': 7,
                'lukket_indtil': {'3dprint': self.nu[0] + 7200}}
        sti.write_text(json.dumps(gemt))
        t = Tilstand(self.mappe.name, apps, ur=lambda: self.nu[0])
        self.assertFalse(t.aaben('3dprint'))
        self.assertEqual(t.version, 7)
        self.assertEqual(json.loads(sti.read_text()), gemt)

    def test_internet_lukkes_ved_genstart_og_version_gemmes(self):
        apps = [{'id': 'internet', 'navn': 'Internet', 'type': 'browser', 'standard_aaben': False,
                 'luk_ved_genstart': True}]
        sti = Path(self.mappe.name) / 'tilstand.json'
        sti.write_text(json.dumps({'aabne': {'internet': True}, 'version': 7}))
        t = Tilstand(self.mappe.name, apps)
        self.assertFalse(t.aaben('internet'))
        self.assertEqual(t.version, 8)
        self.assertEqual(json.loads(sti.read_text()),
                         {'aabne': {'internet': False}, 'version': 8, 'lukket_indtil': {}})
        igen = Tilstand(self.mappe.name, apps)
        self.assertFalse(igen.aaben('internet'))
        self.assertEqual(igen.version, 8)

    def test_fejlagtig_gemt_status_aabner_ikke_app(self):
        apps = [{'id': '3dprint', 'navn': '3D-print', 'standard_aaben': True}]
        sti = Path(self.mappe.name) / 'tilstand.json'
        sti.write_text(json.dumps({'aabne': {'3dprint': 'false'}, 'version': 7}))
        t = Tilstand(self.mappe.name, apps, ur=lambda: self.nu[0])
        self.assertFalse(t.aaben('3dprint'))
        self.assertEqual(json.loads(sti.read_text()),
                         {'aabne': {'3dprint': False}, 'version': 8,
                          'lukket_indtil': {'3dprint': self.nu[0] + 7200}})

    def test_elev_sendes_hjem_naar_app_lukkes(self):
        self.t.saet({'laser': True})
        self.t.status_fra_elev('vaerksted-01', '10.0.0.11', 'start', visning='laser')
        self.assertFalse(self.t.status_fra_elev('vaerksted-01', '10.0.0.11', 'bjaelke', version=2)['gaa_hjem'])
        self.t.saet({'laser': False})
        self.assertTrue(self.t.status_fra_elev('vaerksted-01', '10.0.0.11', 'bjaelke', version=3)['gaa_hjem'])
        self.assertFalse(self.t.status_fra_elev('vaerksted-01', '10.0.0.11', 'bjaelke', version=3)['gaa_hjem'])

    def test_oversigt_viser_bekraeftelse_og_offline(self):
        self.t.status_fra_elev('vaerksted-02', '10.0.0.12', 'bjaelke', version=1, usb='MIN USB')
        o = self.t.oversigt()['computere'][0]
        self.assertTrue(o['online'] and o['bekraeftet'])
        self.assertEqual(o['usb'], 'MIN USB')
        self.t.saet({'3dprint': True})
        self.assertFalse(self.t.oversigt()['computere'][0]['bekraeftet'])
        self.nu[0] += 60
        o = self.t.oversigt()['computere'][0]
        self.assertFalse(o['online'])
        self.assertIsNone(o['visning'])

    def test_ugyldigt_computernavn(self):
        for navn in ('', 'Værksted', '<script>', 'a' * 40):
            with self.assertRaises(ValueError):
                self.t.status_fra_elev(navn, '1.2.3.4', 'start')


class LaererkodeTest(unittest.TestCase):
    def test_kode_login_og_forsoeg(self):
        with tempfile.TemporaryDirectory() as mappe:
            k = Laererkode(mappe)
            with self.assertRaises(ValueError):
                k.vaelg('kort')
            kode = secrets.token_urlsafe(12)
            k.vaelg(kode)
            self.assertNotIn(kode, (Path(mappe) / 'laerer.json').read_text())
            with self.assertRaises(PermissionError):
                k.vaelg(secrets.token_urlsafe(12))
            token = k.log_ind(kode, '10.0.0.5')
            self.assertTrue(k.gyldig(token))
            k.log_ud(token)
            self.assertFalse(k.gyldig(token))
            for _ in range(10):
                with self.assertRaises(PermissionError):
                    k.log_ind('forkert-kode', '10.0.0.6')
            with self.assertRaisesRegex(PermissionError, 'For mange'):
                k.log_ind(kode, '10.0.0.6')


class FalskVaerksted(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def svar(self):
        laengde = int(self.headers.get('Content-Length') or 0)
        krop = self.rfile.read(laengde) if laengde else b''
        if self.path == '/side.html':
            html = b'<html><head><title>x</title></head><body><a href="/laerer">Til laereren</a></body></html>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return
        data = json.dumps({'sti': self.path, 'metode': self.command, 'bytes': len(krop),
                           'autorisation': self.headers.get('Authorization'), 'vaert': self.headers.get('Host'),
                           'print_source': self.headers.get('X-Print-Source'),
                           'origin': self.headers.get('Origin')}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = svar


class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mappe = tempfile.TemporaryDirectory()
        cls.vaerksted = elevhub.Server(('127.0.0.1', 0), FalskVaerksted)
        threading.Thread(target=cls.vaerksted.serve_forever, daemon=True).start()
        apps = [dict(a, bagved=f'127.0.0.1:{cls.vaerksted.server_address[1]}') for a in APPS]
        cls.tilstand = Tilstand(cls.mappe.name, apps)
        cls.pause = elevhub.PauseStore(cls.mappe.name)
        cls.kode = Laererkode(cls.mappe.name)
        cls.servere = {}
        for app in apps:
            s = elevhub.Server(('127.0.0.1', 0), elevhub.Indgang)
            s.navn, s.app, s.regel, s.bagved, s.tilstand = app['id'], app, regler.REGLER[app['regler']], \
                ('127.0.0.1', cls.vaerksted.server_address[1]), cls.tilstand
            cls.servere[app['id']] = s
        hub = elevhub.Server(('127.0.0.1', 0), elevhub.Hub)
        hub.navn, hub.tilstand, hub.pause = 'hub', cls.tilstand, cls.pause
        hub.usikker_test = True  # Eksisterende HTTP-regressioner i isolerede loopback-tests.
        laerer = elevhub.Server(('127.0.0.1', 0), elevhub.Laerer)
        laerer.navn, laerer.tilstand, laerer.kode, laerer.apps, laerer.pause = \
            'laerer', cls.tilstand, cls.kode, apps, cls.pause
        cls.servere.update(hub=hub, laerer=laerer)
        for s in cls.servere.values():
            threading.Thread(target=s.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for s in [*cls.servere.values(), cls.vaerksted]:
            s.shutdown()
            s.server_close()
        cls.mappe.cleanup()

    def kald(self, server, metode, sti, krop=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.servere[server].server_address[1], timeout=10)
        c.request(metode, sti, body=krop, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    def test_1_lukket_app_afviser_alt(self):
        self.tilstand.saet({'3dprint': False})
        status, _, data = self.kald('3dprint', 'POST', '/api/jobs', b'x' * 1000, {'Content-Length': '1000'})
        self.assertEqual(status, 423)
        self.assertIn('lukket 3D-print', json.loads(data)['error'])
        status, h, data = self.kald('3dprint', 'GET', '/', headers={'Accept': 'text/html'})
        self.assertEqual(status, 423)
        self.assertIn('3D-print er lukket lige nu', data.decode())
        self.assertEqual(json.loads(self.kald('3dprint', 'GET', '/_elevhub/aaben')[2]), {'aaben': False})

    def test_2_aaben_app_sender_elevdelen_videre(self):
        self.tilstand.saet({'3dprint': True, 'laser': True})
        status, _, data = self.kald('3dprint', 'POST', '/api/jobs', b'stl' * 100,
                                    {'Content-Length': '300', 'Authorization': 'Bearer hemmelig',
                                     'Origin': f'http://127.0.0.1:{self.servere["3dprint"].server_address[1]}',
                                     'X-Print-Source': '192.0.2.99', 'X-Forwarded-For': '192.0.2.98'})
        d = json.loads(data)
        self.assertEqual((status, d['sti'], d['bytes']), (200, '/api/jobs', 300))
        self.assertIsNone(d['autorisation'], 'lærerkoden må aldrig sendes videre')
        self.assertEqual(d['print_source'], '127.0.0.1', 'elevens selvvalgte identitet skal overskrives')
        backend_host = f'127.0.0.1:{self.vaerksted.server_address[1]}'
        self.assertEqual(d['vaert'], backend_host)
        self.assertEqual(d['origin'], 'http://' + backend_host)
        self.assertEqual(self.kald('3dprint', 'GET', '/api/auth/status')[0], 403)
        self.assertEqual(self.kald('3dprint', 'POST', '/api/auth/login', b'{"code":"test-code"}')[0], 403)
        self.assertEqual(self.kald('3dprint', 'POST', '/api/auth/logout', b'{}')[0], 403)
        self.assertEqual(self.kald('3dprint', 'POST', '/api/queue/limits', b'{}')[0], 403)
        self.assertEqual(self.kald('3dprint', 'GET', '/api/print-access')[0], 403)
        self.assertEqual(self.kald('3dprint', 'POST', '/api/print-access/settings', b'{}')[0], 403)
        self.assertEqual(self.kald('3dprint', 'POST', '/api/print-access/unblock', b'{}')[0], 403)
        self.assertEqual(self.kald('3dprint', 'GET', '/laerer')[0], 403)
        self.assertEqual(self.kald('3dprint', 'POST', '/api/queue/pause', b'{}', {'Content-Length': '2'})[0], 403)
        status, h, _ = self.kald('laser', 'GET', '/')
        self.assertEqual((status, h['Location']), (302, '/laser.html'))
        status, h, data = self.kald('3dprint', 'GET', '/side.html')
        self.assertIn(b'<style id="elevhub">a[href="/laerer"] { display: none !important; }</style></head>', data)
        self.assertEqual(int(h['Content-Length']), len(data))

    def test_3_hub(self):
        status, h, data = self.kald('hub', 'GET', '/api/apps')
        self.assertEqual(h['Access-Control-Allow-Origin'], '*')
        self.assertEqual([a['id'] for a in json.loads(data)['apps']], ['3dprint', 'laser'])
        krop = json.dumps({'computer': 'vaerksted-03', 'kilde': 'start', 'visning': 'start'}).encode()
        status, _, data = self.kald('hub', 'POST', '/api/status', krop,
                                    {'Content-Type': 'text/plain', 'Content-Length': str(len(krop))})
        self.assertEqual(status, 200)
        self.assertIn('gaa_hjem', json.loads(data))
        krop = b'{"computer": "<b>"}'
        self.assertEqual(self.kald('hub', 'POST', '/api/status', krop, {'Content-Length': str(len(krop))})[0], 400)

    def test_school_identity_is_escaped_without_exposing_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / 'school.json'
            with patch.object(self.tilstand, 'mappe', root / 'hub'):
                legacy = self.kald('laerer', 'GET', '/')[2]
                self.assertIn(b'KLOSTERMARKSSKOLEN', legacy)
                profile.write_text(json.dumps({'school_name': '<Test & skole>', 'theme': 'green',
                                               'admin_public_key': 'NOT-FOR-HTML'}), encoding='utf-8')
                body = self.kald('laerer', 'GET', '/')[2]
                self.assertIn(b'&lt;Test &amp; skole&gt;', body)
                self.assertIn(b'<img class="school-logo"', body)
                self.assertNotIn(b'NOT-FOR-HTML', body)
                self.assertIn(b'--brand:#28734e', self.kald('laerer', 'GET', '/laerer.css')[2])
                profile.write_text('{"school_name":"Test","theme":[]}', encoding='utf-8')
                self.assertEqual(self.kald('laerer', 'GET', '/')[2], legacy)

    def test_4_laererpanel(self):
        j = {'Content-Type': 'application/json'}
        status, _, data = self.kald('laerer', 'GET', '/api/laerer/session')
        self.assertEqual(json.loads(data), {'logget_ind': False, 'kode_valgt': False, 'lokal': True})
        self.assertEqual(self.kald('laerer', 'GET', '/api/laerer/oversigt')[0], 401)
        kode = secrets.token_urlsafe(12)
        type(self).test_kode = kode
        krop = json.dumps({'kode': kode, 'kode_igen': kode}).encode()
        self.assertEqual(self.kald('laerer', 'POST', '/api/laerer/opsaet', krop, {**j, 'Content-Length': str(len(krop))})[0], 200)
        krop = json.dumps({'kode': 'forkert-kode'}).encode()
        self.assertEqual(self.kald('laerer', 'POST', '/api/laerer/login', krop, {**j, 'Content-Length': str(len(krop))})[0], 403)
        krop = json.dumps({'kode': kode}).encode()
        status, h, _ = self.kald('laerer', 'POST', '/api/laerer/login', krop, {**j, 'Content-Length': str(len(krop))})
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly; SameSite=Strict', h['Set-Cookie'])
        kage = {'Cookie': h['Set-Cookie'].split(';')[0]}
        status, _, data = self.kald('laerer', 'GET', '/api/laerer/oversigt', headers=kage)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)['apps'][0]['id'], '3dprint')
        krop = json.dumps({'id': 'laser', 'aaben': False}).encode()
        fremmed = {**j, **kage, 'Content-Length': str(len(krop)), 'Origin': 'http://ondsindet.example'}
        self.assertEqual(self.kald('laerer', 'POST', '/api/laerer/app', krop, fremmed)[0], 403)
        origin = f"http://127.0.0.1:{self.servere['laerer'].server_address[1]}"
        status, _, _ = self.kald('laerer', 'POST', '/api/laerer/app', krop,
                                 {**j, **kage, 'Content-Length': str(len(krop)), 'Origin': origin})
        self.assertEqual(status, 200)
        self.assertFalse(self.tilstand.aaben('laser'))
        status, h, data = self.kald('laerer', 'GET', '/')
        self.assertIn("frame-ancestors 'none'", h['Content-Security-Policy'])
        self.assertIn('Lærerpanel', data.decode())

    def test_5_elevindgang_aabner_ved_frist_uden_laererpoll(self):
        nu = [1_800_000_000.0]
        with patch.object(self.tilstand, 'ur', lambda: nu[0]):
            self.tilstand.saet({'3dprint': True})
            version = self.tilstand.saet({'3dprint': False})
            nu[0] += 7199
            self.assertEqual(self.kald('3dprint', 'GET', '/api/state')[0], 423)
            nu[0] += 1
            self.assertEqual(self.kald('3dprint', 'GET', '/api/state')[0], 200)
            self.assertEqual(self.tilstand.version, version + 1)
            svar = json.loads(self.kald('hub', 'GET', '/api/apps')[2])
            app = next(a for a in svar['apps'] if a['id'] == '3dprint')
            self.assertTrue(app['aaben'])
            self.assertIsNone(app['lukket_indtil'])

    def test_6_pause_er_laererautentificeret_og_kvitteres_fra_elevstatus(self):
        kode = self.__class__.test_kode
        j = {'Content-Type': 'application/json'}
        login = json.dumps({'kode': kode}).encode()
        status, headers, _ = self.kald('laerer', 'POST', '/api/laerer/login', login,
                                       {**j, 'Content-Length': str(len(login))})
        self.assertEqual(status, 200)
        cookie = headers['Set-Cookie'].split(';')[0]
        self.assertEqual(self.kald('laerer', 'POST', '/api/laerer/pause',
                                   b'{"paused":true}',
                                   {**j, 'Cookie': cookie, 'Content-Length': 15,
                                    'Origin': 'http://ondsindet.example'})[0], 403)
        origin = f"http://127.0.0.1:{self.servere['laerer'].server_address[1]}"
        body = json.dumps({'paused': True, 'message': 'Gem først', 'expires_in': 120}).encode()
        status, _, data = self.kald('laerer', 'POST', '/api/laerer/pause', body,
                                    {**j, 'Cookie': cookie, 'Content-Length': str(len(body)),
                                     'Origin': origin})
        self.assertEqual(status, 200)
        pause = json.loads(data)['pause']
        self.assertTrue(pause['paused'])
        self.assertLessEqual(pause['until'] - pause['issued_at'], 120)

        body = json.dumps({'computer': 'vaerksted-04', 'kilde': 'bjaelke',
                           'pause_ack': {'version': pause['version'], 'shown': True}}).encode()
        status, _, data = self.kald('hub', 'POST', '/api/status', body,
                                    {'Content-Length': str(len(body))})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)['pause']['version'], pause['version'])
        overview = json.loads(self.kald('laerer', 'GET', '/api/laerer/oversigt',
                                        headers={'Cookie': cookie})[2])
        computer = next(c for c in overview['computere'] if c['navn'] == 'vaerksted-04')
        self.assertEqual(computer['pause_ack']['shown'], True)

        body = b'{"paused":false}'
        status, _, data = self.kald('laerer', 'POST', '/api/laerer/pause', body,
                                    {**j, 'Cookie': cookie, 'Content-Length': str(len(body)),
                                     'Origin': origin})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(data)['pause']['paused'])


if __name__ == '__main__':
    unittest.main()
