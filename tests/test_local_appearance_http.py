import hashlib
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path


try:
    import paho.mqtt.client  # noqa: F401
    PRINT_AVAILABLE = True
except ImportError:
    PRINT_AVAILABLE = False


def write_manifest(root, css=b'/* local overlay */', logo=b'logo'):
    appearance = root / 'local-appearance'
    files = appearance / 'files'
    files.mkdir(parents=True, exist_ok=True)
    (files / 'teacher.css').write_bytes(css)
    (files / 'logo.png').write_bytes(logo)
    (appearance / 'manifest.json').write_text(json.dumps({
        'schemaVersion': 1, 'school_name': 'Testskolen', 'theme': 'green',
        'style': {'logo': 'files/logo.png', 'logo_sha256': hashlib.sha256(logo).hexdigest(), 'static': {
            '/school-theme.css': {'path': 'files/teacher.css', 'sha256': hashlib.sha256(css).hexdigest()},
            '/laerer.css': {'path': 'files/teacher.css', 'sha256': hashlib.sha256(css).hexdigest()},
            '/assets/klostermarksskolen-logo.png': {'path': 'files/logo.png', 'sha256': hashlib.sha256(logo).hexdigest()},
        }},
    }), encoding='utf-8')


def get(port, path):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    connection.request('GET', path)
    response = connection.getresponse()
    body = response.read()
    connection.close()
    return response.status, body


class LocalAppearanceHTTPTests(unittest.TestCase):
    def test_standalone_hub_ships_the_same_appearance_contract(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(
            (root / 'app' / 'school_appearance.py').read_bytes(),
            (root / 'linux-elevhub/server/elevhub/school_appearance.py').read_bytes())

    def test_neutral_html_renderer_inserts_school_identity_without_replacing_credit(self):
        from app.school_appearance import render_local_html
        appearance = {'school_name': 'A & <Skole>', 'theme': 'green',
                      'style': {'logo': '/school-appearance/logo'}}
        source = ('<title>Designværksted · Makerværkstedet</title>'
                  '<header><span class="brand-mark" aria-hidden="true"></span>'
                  '<span class="brand-school" data-school-name="">PUBLIC</span>'
                  '<span data-school-name="old">OLD</span></header>'
                  '<footer data-school-footer>Makerværkstedet / Designværksted ' + chr(183) +
                  ' Kreditering: Klostermarksskolen</footer>').encode('utf-8')
        rendered = render_local_html(source, appearance).decode('utf-8')
        self.assertEqual(rendered.count('>A &amp; &lt;Skole&gt;</span>'), 2)
        self.assertNotIn('>PUBLIC</span>', rendered)
        self.assertNotIn('>OLD</span>', rendered)
        self.assertIn('data-school-name="old"', rendered)
        self.assertIn('<img class="school-logo" src="/school-appearance/logo"', rendered)
        self.assertIn('Udviklet af Mathias Kusk Damgaard', rendered)
        self.assertIn('Kreditering: Klostermarksskolen', rendered)

    def test_neutral_html_renderer_without_profile_is_unchanged(self):
        from app.school_appearance import render_local_html
        source = '<span class="brand-mark"></span><span data-school-name>Makervrkstedet</span>'.encode('utf-8')
        self.assertEqual(render_local_html(source, None), source)

    @unittest.skipUnless(PRINT_AVAILABLE, 'print-server optional MQTT dependency is unavailable')
    def test_print_http_serves_overlay_and_keeps_unmapped_js(self):
        from app.server import Handler as PrintHandler, Server as PrintServer, Application
        with tempfile.TemporaryDirectory() as folder:
            root, data = Path(folder), Path(folder) / 'data'
            data.mkdir()
            write_manifest(root)
            application = Application(data, demo=True)
            server = PrintServer(('127.0.0.1', 0), PrintHandler)
            server.app = application
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                status, body = get(server.server_port, '/school-theme.css')
                self.assertEqual(status, 200)
                self.assertEqual(body, b'/* local overlay */')
                status, body = get(server.server_port, '/')
                self.assertEqual(status, 200)
                self.assertIn(b'Testskolen', body)
                self.assertIn(b'/school-appearance/logo', body)
                status, body = get(server.server_port, '/app.js')
                self.assertEqual(status, 200)
                expected = (Path(__file__).resolve().parents[1] / 'web' / 'app.js').read_bytes()
                self.assertEqual(hashlib.sha256(body).hexdigest(), hashlib.sha256(expected).hexdigest())
                status, body = get(server.server_port, '/api/school-appearance')
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)['school_name'], 'Testskolen')
                self.assertNotIn(b'files/', body)
                (root / 'local-appearance' / 'files' / 'teacher.css').write_text('tampered', encoding='utf-8')
                for path in ('/', '/school-theme.css', '/app.js', '/api/school-appearance'):
                    self.assertEqual(get(server.server_port, path)[0], 503)
            finally:
                server.shutdown(); server.server_close(); application.shutdown()

    def test_laser_http_serves_overlay_and_rejects_tampering(self):
        from app.laser_server import create_server as create_laser_server
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_manifest(root)
            server = create_laser_server(root / 'data', port=0, code='test-code')
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                status, body = get(server.server_port, '/school-theme.css')
                self.assertEqual(status, 200)
                self.assertEqual(body, b'/* local overlay */')
                status, body = get(server.server_port, '/')
                self.assertEqual(status, 200)
                self.assertIn(b'Testskolen', body)
                self.assertIn(b'/school-appearance/logo', body)
                (root / 'local-appearance' / 'files' / 'teacher.css').write_text('tampered', encoding='utf-8')
                status, _ = get(server.server_port, '/api/school-appearance')
                self.assertEqual(status, 503)
            finally:
                server.shutdown(); server.server_close()

    def test_hub_teacher_http_serves_overlay_and_public_api(self):
        hub_root = Path(__file__).resolve().parents[1] / 'linux-elevhub' / 'server' / 'elevhub'
        sys.path.insert(0, str(hub_root))
        try:
            import elevhub
            from tilstand import Laererkode, Tilstand
            from pause import PauseStore
            with tempfile.TemporaryDirectory() as folder:
                root, data = Path(folder), Path(folder) / 'data'
                data.mkdir()
                write_manifest(root)
                state = Tilstand(str(data), [])
                server = elevhub.Server(('127.0.0.1', 0), elevhub.Laerer)
                server.navn, server.tilstand, server.apps = 'laerer', state, []
                server.kode, server.pause = Laererkode(data), PauseStore(data)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                try:
                    status, body = get(server.server_port, '/')
                    self.assertEqual(status, 200)
                    self.assertIn(b'Testskolen', body)
                    self.assertIn(b'/school-appearance/logo', body)
                    status, body = get(server.server_port, '/laerer.css')
                    self.assertEqual(status, 200)
                    self.assertEqual(body, b'/* local overlay */')
                    status, body = get(server.server_port, '/api/school-appearance')
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(body)['school_name'], 'Testskolen')
                    self.assertEqual(get(server.server_port, '/laerer.js')[0], 200)
                    (root / 'local-appearance' / 'files' / 'teacher.css').write_text('tampered', encoding='utf-8')
                    self.assertEqual(get(server.server_port, '/')[0], 503)
                finally:
                    server.shutdown(); server.server_close()
        finally:
            sys.path.remove(str(hub_root))

    @unittest.skipUnless(sys.platform == 'win32', 'the Windows profile sits beside the data folder')
    def test_laser_api_returns_private_school_colours_from_profile(self):
        from app.laser_server import create_server as create_laser_server
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profile = {'school_name': 'Prøveskolen', 'theme': 'green'}
            (root / 'school.json').write_text(json.dumps(
                {**profile, 'brand': '#1f5a8c', 'brand_dark': '#143c5e'}), encoding='utf-8')
            server = create_laser_server(root / 'data', port=0, code='test-code')
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                status, body = get(server.server_port, '/api/school-appearance')
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body), {**profile, 'brand': '#1f5a8c', 'brand_dark': '#143c5e'})
                (root / 'school.json').write_text(json.dumps({**profile, 'brand': '#1f5a8c'}), encoding='utf-8')
                status, body = get(server.server_port, '/api/school-appearance')
                self.assertEqual((status, json.loads(body)), (200, profile))
            finally:
                server.shutdown(); server.server_close()

    def test_hub_teacher_css_uses_private_colours_or_theme(self):
        hub_root = Path(__file__).resolve().parents[1] / 'linux-elevhub' / 'server' / 'elevhub'
        sys.path.insert(0, str(hub_root))
        try:
            import elevhub
            from tilstand import Laererkode, Tilstand
            from pause import PauseStore
            with tempfile.TemporaryDirectory() as folder:
                root, data = Path(folder), Path(folder) / 'data'
                data.mkdir()
                state = Tilstand(str(data), [])
                server = elevhub.Server(('127.0.0.1', 0), elevhub.Laerer)
                server.navn, server.tilstand, server.apps = 'laerer', state, []
                server.kode, server.pause = Laererkode(data), PauseStore(data)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                try:
                    cases = (({'brand': '#1f5a8c', 'brand_dark': '#143c5e'}, '--brand:#1f5a8c;--brand-dark:#143c5e;'),
                             ({}, '--brand:#28734e;--brand-dark:#1a5037;'),
                             ({'brand': '#1f5a8c'}, '--brand:#28734e;--brand-dark:#1a5037;'),
                             ({'brand': '#1F5A8C', 'brand_dark': '#143c5e'}, '--brand:#28734e;--brand-dark:#1a5037;'))
                    for colours, expected in cases:
                        with self.subTest(colours=colours):
                            (root / 'school.json').write_text(json.dumps(
                                {'school_name': 'Prøveskolen', 'theme': 'green', **colours}), encoding='utf-8')
                            status, body = get(server.server_port, '/laerer.css')
                            self.assertEqual(status, 200)
                            self.assertIn(expected, body.decode('utf-8'))
                finally:
                    server.shutdown(); server.server_close()
        finally:
            sys.path.remove(str(hub_root))

    def test_theme_script_applies_private_colours_on_the_root(self):
        script = (Path(__file__).resolve().parents[1] / 'web' / 'school-theme.js').read_text(encoding='utf-8')
        self.assertIn("/^#[0-9a-f]{6}$/", script)
        for token in ("'--brand','--school-brand'", "'--brand-dark','--school-brand-dark'",
                      "data-school-theme-fixed", "root.style.setProperty"):
            self.assertIn(token, script)


if __name__ == '__main__':
    unittest.main()
