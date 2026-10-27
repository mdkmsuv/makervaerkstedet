"""Focused regressions for the Elevhub network and login hardening."""
import http.client
import json
from http.server import BaseHTTPRequestHandler
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

import elevhub
import regler
from tilstand import Laererkode, Tilstand


def _raw(port, request):
    with socket.create_connection(('127.0.0.1', port), timeout=3) as conn:
        conn.sendall(request)
        conn.settimeout(3)
        data = b''
        while b'\r\n\r\n' not in data:
            data += conn.recv(4096)
        header, _, body = data.partition(b'\r\n\r\n')
        lines = header.decode('iso-8859-1').splitlines()
        status = int(lines[0].split()[1])
        length = int(next(line.split(':', 1)[1] for line in lines[1:]
                          if line.lower().startswith('content-length:')))
        while len(body) < length:
            body += conn.recv(4096)
        return status, body[:length]


class _HugeBackend(BaseHTTPRequestHandler):
    payload = b''

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Length', str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)


class NetworkHardeningTests(unittest.TestCase):
    def test_legacy_http09_and_duplicate_host_are_controlled_errors(self):
        server = elevhub.Server(('127.0.0.1', 0), elevhub.Hub)
        server.navn = 'hub'
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, _ = _raw(server.server_address[1], b'GET /api/health\r\n')
            self.assertEqual(status, 400)
            status, _ = _raw(server.server_address[1],
                             b'GET /api/health HTTP/1.1\r\nHost: a\r\nHost: b\r\n\r\n')
            self.assertEqual(status, 400)
        finally:
            server.shutdown()
            server.server_close()

    def test_backend_response_has_a_hard_limit(self):
        backend = elevhub.Server(('127.0.0.1', 0), _HugeBackend)
        _HugeBackend.payload = b'x' * 33
        threading.Thread(target=backend.serve_forever, daemon=True).start()
        with tempfile.TemporaryDirectory() as data:
            app = {'id': 'app', 'navn': 'App', 'port': 0,
                   'bagved': f'127.0.0.1:{backend.server_address[1]}',
                   'regler': 'printvaerksted'}
            state = Tilstand(data, [app])
            state.saet({'app': True})
            server = elevhub.Server(('127.0.0.1', 0), elevhub.Indgang)
            server.navn, server.app, server.regel = 'app', app, regler.printvaerksted
            server.bagved, server.tilstand = ('127.0.0.1', backend.server_address[1]), state
            threading.Thread(target=server.serve_forever, daemon=True).start()
            old_limit = elevhub.MAKS_BACKEND_SVAR
            elevhub.MAKS_BACKEND_SVAR = 32
            try:
                conn = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=3)
                conn.request('GET', '/')
                response = conn.getresponse()
                self.assertEqual(response.status, 502)
                response.read()
                conn.close()
            finally:
                elevhub.MAKS_BACKEND_SVAR = old_limit
                server.shutdown()
                server.server_close()
        backend.shutdown()
        backend.server_close()

    def test_cookie_mutation_requires_origin_and_host_cannot_create_links(self):
        app = {'id': 'app', 'navn': 'App', 'port': 0, 'bagved': '',
               'regler': 'printvaerksted', 'laererside': 'http://{vaert}:99/teacher'}
        with tempfile.TemporaryDirectory() as data:
            state, code = Tilstand(data, [app]), Laererkode(data)
            code.vaelg('abcdefgh')
            server = elevhub.Server(('127.0.0.1', 0), elevhub.Laerer)
            server.navn, server.tilstand, server.kode, server.apps = 'laerer', state, code, [app]
            threading.Thread(target=server.serve_forever, daemon=True).start()
            origin = f'http://127.0.0.1:{server.server_address[1]}'
            try:
                conn = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=3)
                body = b'{"kode":"abcdefgh"}'
                conn.request('POST', '/api/laerer/login', body,
                             {'Content-Type': 'application/json', 'Origin': origin})
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                cookie = response.getheader('Set-Cookie').split(';', 1)[0]
                response.read()
                body = json.dumps({'id': 'app', 'aaben': True}).encode()
                conn.request('POST', '/api/laerer/app', body,
                             {'Content-Type': 'application/json', 'Cookie': cookie})
                response = conn.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                body = json.dumps({'id': 'app', 'aaben': True}).encode()
                conn.request('POST', '/api/laerer/app', body,
                             {'Content-Type': 'application/json', 'Cookie': cookie, 'Origin': origin})
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                conn.close()
                conn = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=3)
                conn.request('GET', '/api/laerer/oversigt', headers={'Cookie': cookie, 'Host': 'evil.example'})
                payload = json.loads(conn.getresponse().read())
                self.assertNotIn('evil.example', json.dumps(payload))
                self.assertEqual(payload['apps'][0]['laererside'], '')
                conn.close()
            finally:
                server.shutdown()
                server.server_close()

    def test_failed_login_state_has_global_and_source_bounds(self):
        with tempfile.TemporaryDirectory() as data, patch('tilstand.ITERATIONER', 1):
            code = Laererkode(data)
            code.vaelg('abcdefgh')
            for index in range(256):
                with self.assertRaises(PermissionError):
                    code.log_ind('wrong', f'192.0.2.{index % 256}')
            self.assertLessEqual(len(code.forsoeg), 256)
            self.assertLessEqual(sum(map(len, code.forsoeg.values())), 4096)
            with self.assertRaises(PermissionError):
                code.log_ind('wrong', '198.51.100.1')


if __name__ == '__main__':
    unittest.main()
