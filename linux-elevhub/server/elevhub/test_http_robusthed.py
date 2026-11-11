"""Regressionstests for HTTP body-framing og afviste requests."""
import http.client
import json
from http.server import BaseHTTPRequestHandler
import socket
import tempfile
import threading
import time
import unittest

import elevhub
import regler
from tilstand import Tilstand


APP = {'id': 'app', 'navn': 'App', 'port': 0, 'bagved': '', 'regler': 'printvaerksted'}


class Kode:
    def gyldig(self, token):
        return True


class FalskVaerksted(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        laengde = int(self.headers.get('Content-Length') or 0)
        self.rfile.read(laengde)
        data = b'{}'
        self.send_response(200)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def raw_svar(port, request, timeout=8):
    forbindelse = socket.create_connection(('127.0.0.1', port), timeout=timeout)
    try:
        forbindelse.sendall(request)
        forbindelse.settimeout(timeout)
        data = b''
        while b'\r\n\r\n' not in data:
            chunk = forbindelse.recv(4096)
            if not chunk:
                raise AssertionError('Serveren lukkede før HTTP-headeren var færdig.')
            data += chunk
        header, _, resten = data.partition(b'\r\n\r\n')
        headers = header.decode('iso-8859-1').splitlines()
        status = int(headers[0].split()[1])
        content_length = next(int(l.split(':', 1)[1].strip()) for l in headers[1:]
                              if l.lower().startswith('content-length:'))
        while len(resten) < content_length:
            chunk = forbindelse.recv(4096)
            if not chunk:
                raise AssertionError('Serveren lukkede før HTTP-bodyen var færdig.')
            resten += chunk
        svar_headers = {l.split(':', 1)[0].strip().lower(): l.split(':', 1)[1].strip()
                        for l in headers[1:]}
        forbindelse.settimeout(1)
        try:
            lukket = forbindelse.recv(1) == b''
        except socket.timeout:
            lukket = False
        return status, svar_headers, resten[:content_length], lukket
    finally:
        forbindelse.close()


class IndgangHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mappe = tempfile.TemporaryDirectory()
        cls.vaerksted = elevhub.Server(('127.0.0.1', 0), FalskVaerksted)
        threading.Thread(target=cls.vaerksted.serve_forever, daemon=True).start()
        app = dict(APP, bagved=f'127.0.0.1:{cls.vaerksted.server_address[1]}')
        cls.tilstand = Tilstand(cls.mappe.name, [app])
        cls.tilstand.saet({'app': True})
        cls.server = elevhub.Server(('127.0.0.1', 0), elevhub.Indgang)
        cls.server.navn, cls.server.app, cls.server.regel = 'app', app, regler.printvaerksted
        cls.server.bagved, cls.server.tilstand = ('127.0.0.1', cls.vaerksted.server_address[1]), cls.tilstand
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for server in (cls.server, cls.vaerksted):
            server.shutdown()
            server.server_close()
        cls.mappe.cleanup()

    def test_ugyldig_negativ_og_for_stor_content_length_giver_svar(self):
        for vaerdi, status in [('nej', 400), ('-1', 400), (str(elevhub.MAKS_UPLOAD + 1), 413)]:
            with self.subTest(vaerdi=vaerdi):
                svar, _, _, _ = raw_svar(self.server.server_address[1], (
                    f'POST /api/jobs HTTP/1.1\r\nHost: localhost\r\nContent-Length: {vaerdi}\r\n\r\n'
                ).encode())
                self.assertEqual(svar, status)

    def test_dubleret_og_ekstremt_lang_content_length_giver_400(self):
        for ekstra, status in (
            (b'Content-Length: 1\r\nContent-Length: 1\r\n\r\n', 400),
            (b'Content-Length: ' + b'7' * 5000 + b'\r\n\r\n', 400),
            (b'Content-Length:\r\n\r\n', 400),
            (b'Transfer-Encoding:\r\n\r\n', 411),
        ):
            with self.subTest(ekstra=ekstra[:30]):
                svar, _, _, _ = raw_svar(self.server.server_address[1],
                                          b'POST /api/jobs HTTP/1.1\r\nHost: localhost\r\n' + ekstra)
                self.assertEqual(svar, status)

    def test_transfer_encoding_afvises_og_forbindelsen_lukkes(self):
        request = (b'POST /api/jobs HTTP/1.1\r\nHost: localhost\r\n'
                   b'Transfer-Encoding: chunked\r\n\r\n')
        status, headers, _, lukket = raw_svar(self.server.server_address[1], request)
        self.assertEqual(status, 411)
        self.assertEqual(headers.get('connection', '').lower(), 'close')
        self.assertTrue(lukket)

    def test_afvist_body_kan_ikke_blive_naeste_request(self):
        request = (b'POST /api/queue/pause HTTP/1.1\r\nHost: localhost\r\n'
                   b'Content-Length: 3\r\n\r\nabc'
                   b'GET /api/health HTTP/1.1\r\nHost: localhost\r\n\r\n')
        status, headers, _, lukket = raw_svar(self.server.server_address[1], request)
        self.assertEqual(status, 403)
        self.assertEqual(headers.get('connection', '').lower(), 'close')
        self.assertTrue(lukket)


class BodyTimeoutAndLaerer(unittest.TestCase):
    def test_manglende_body_giver_timeout_svar(self):
        with tempfile.TemporaryDirectory() as mappe:
            tilstand = Tilstand(mappe, [APP])
            server = elevhub.Server(('127.0.0.1', 0), elevhub.Hub)
            server.navn, server.tilstand = 'hub', tilstand
            threading.Thread(target=server.serve_forever, daemon=True).start()
            gammel = elevhub.BODY_TIMEOUT
            elevhub.BODY_TIMEOUT = 0.2
            try:
                start = time.monotonic()
                status, _, _, _ = raw_svar(server.server_address[1], (
                    b'POST /api/status HTTP/1.1\r\nHost: localhost\r\n'
                    b'Content-Length: 4\r\nContent-Type: application/json\r\n\r\n'
                ), timeout=3)
                self.assertEqual(status, 408)
                self.assertLess(time.monotonic() - start, 2)
            finally:
                elevhub.BODY_TIMEOUT = gammel
                server.shutdown()
                server.server_close()

    def test_laerer_manglende_body_giver_timeout_svar(self):
        with tempfile.TemporaryDirectory() as mappe:
            tilstand = Tilstand(mappe, [APP])
            server = elevhub.Server(('127.0.0.1', 0), elevhub.Laerer)
            server.navn, server.tilstand, server.kode, server.apps = 'laerer', tilstand, Kode(), [APP]
            threading.Thread(target=server.serve_forever, daemon=True).start()
            gammel = elevhub.BODY_TIMEOUT
            elevhub.BODY_TIMEOUT = 0.2
            try:
                status, _, _, _ = raw_svar(server.server_address[1], (
                    b'POST /api/laerer/app HTTP/1.1\r\nHost: localhost\r\n'
                    b'Content-Type: application/json\r\nContent-Length: 4\r\n\r\n'
                ), timeout=3)
                self.assertEqual(status, 408)
            finally:
                elevhub.BODY_TIMEOUT = gammel
                server.shutdown()
                server.server_close()

    def test_laerer_aaben_kraever_bool(self):
        with tempfile.TemporaryDirectory() as mappe:
            tilstand = Tilstand(mappe, [APP])
            server = elevhub.Server(('127.0.0.1', 0), elevhub.Laerer)
            server.navn, server.tilstand, server.kode, server.apps = 'laerer', tilstand, Kode(), [APP]
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                def kald(aaben):
                    krop = json.dumps({'id': 'app', 'aaben': aaben}).encode()
                    forbindelse = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=3)
                    forbindelse.request('POST', '/api/laerer/app', krop,
                                        {'Content-Type': 'application/json', 'Content-Length': str(len(krop))})
                    svar = forbindelse.getresponse()
                    svar.read()
                    forbindelse.close()
                    return svar.status

                self.assertEqual(kald('false'), 400)
                self.assertFalse(tilstand.aaben('app'))
                self.assertEqual(kald(True), 200)
                self.assertTrue(tilstand.aaben('app'))
            finally:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
