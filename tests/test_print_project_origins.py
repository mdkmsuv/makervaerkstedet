"""Exact local iframe parent contract, without changing a live installation."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import urlopen

from app.server import Server, Handler, configured_project_origins, validate_project_origins, DEFAULT_PROJECT_ORIGINS


class PrintProjectOriginsTests(unittest.TestCase):
    def test_explicit_loopback_ports_and_duplicates(self):
        values = ['http://localhost:30101', 'http://127.0.0.1:30102', 'http://[::1]:30103']
        self.assertEqual(validate_project_origins(values + values), tuple(values))
        self.assertEqual(validate_project_origins(['http://localhost:80', 'http://[::1]:80']),
                         ('http://localhost', 'http://[::1]'))

    def test_malformed_remote_and_ambiguous_origins_fail_closed(self):
        bad = ['http://localhost', 'https://localhost:8791', 'http://localhost:0',
               'http://localhost:65536', 'http://localhost:08791', 'http://localhost:8791/',
               'http://localhost:8791?x=1', 'http://localhost:8791#fragment',
               'http://user@localhost:8791', 'http://*.localhost:8791',
               'http://192.168.1.4:8791', 'http://127.1:8791', 'null',
               'http://localhost:8791\n', 'http://localhost:8791; script-src *']
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                Server(('127.0.0.1', 0), Handler, project_origins=[value])
        for values in ('http://localhost:8791', [None], ['http://localhost:8791'] * 33):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_project_origins(values)

    def test_file_contract_defaults_and_explicit_disable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'origins.json'
            self.assertEqual(configured_project_origins(path), DEFAULT_PROJECT_ORIGINS)
            path.write_text(json.dumps({'allowed_origins': []}))
            self.assertEqual(configured_project_origins(path), ())
            path.write_text('{"allowed_origins":[],"extra":true}')
            with self.assertRaises(ValueError):
                configured_project_origins(path)

    def test_real_http_csp_and_external_config_agree_teacher_is_never_embedded(self):
        origins = ['http://127.0.0.1:30201']
        server = Server(('127.0.0.1', 0), Handler, project_origins=origins)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            for path in ('/', '/project-import-config.js', '/project-import.js'):
                with urlopen(base + path, timeout=3) as response:
                    policy = response.headers['Content-Security-Policy']
                    self.assertIn('frame-ancestors ' + origins[0] + ';', policy)
                    self.assertIn("script-src 'self';", policy)
                    self.assertNotIn('8791', policy)
                    if path == '/project-import-config.js':
                        self.assertIn(json.dumps(origins), response.read().decode())
            with urlopen(base + '/laerer', timeout=3) as response:
                self.assertIn("frame-ancestors 'none';", response.headers['Content-Security-Policy'])
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
