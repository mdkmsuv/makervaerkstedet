"""The Laser, Folie and 3D editors are served from their app packages.

Since the app split neither the school server nor the forwarding servers have
an editor page. These tests bind the real app descriptors on the installed-app
gateway, as the pupil computer does, with no school server behind it.
"""
from pathlib import Path as _LayoutPath
import sys as _layout_sys
_LAYOUT_ROOT = next(p for p in _LayoutPath(__file__).resolve().parents if (p / 'REPOSITORY-LAYOUT.json').is_file())
_layout_sys.path[:0] = [str(_LAYOUT_ROOT), str(_LAYOUT_ROOT / 'system')]
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from app.laser_local import ForwardingInbox, create_installed_servers
from tests.test_local_appearance_http import write_manifest

ROOT = Path(__file__).resolve().parents[1]
PROJECTS_PORT = 31234


class NoLibrary:
    def status(self, *_):
        raise OSError('offline')
    catalogue_response = svg_response = status


class InstalledEditorPageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        apps = []
        for name in ('laser', 'folie', '3dprint'):
            app = json.loads((ROOT / 'apps' / name / 'app.json').read_text(encoding='utf-8'))
            app.update(port=0, assets=str(ROOT / 'apps' / name / 'web'))
            apps.append(app)
        self.school = Path(temporary.name) / 'school'
        inbox = ForwardingInbox('http://127.0.0.1:1')  # nothing listens: no school server
        servers = create_installed_servers(apps, inbox, inbox, runtime={}, projects_port=PROJECTS_PORT,
                                           library_client=NoLibrary(), data_root=self.school)
        self.assertEqual([server.descriptor['id'] for server in servers], ['laser', 'folie', '3dprint'])
        for server in servers:
            server.RequestHandlerClass.log_message = lambda *args: None
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        self.laser, self.folie, self.model = servers

    def request(self, server, path, method='GET', body=None):
        connection = http.client.HTTPConnection('127.0.0.1', server.port, timeout=5)
        origin = f'http://127.0.0.1:{server.port}'
        try:
            connection.request(method, path, body, {'Origin': origin} if method == 'POST' else {})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def page(self, server, path):
        status, body, headers = self.request(server, path)
        self.assertEqual(status, 200, path)
        content = body.decode('utf-8')
        # The host contract every installed editor page gets.
        self.assertIn('/_elevhub/app.js', content)
        self.assertIn('/_elevhub/projekter/bridge.js', content)
        policy = headers['Content-Security-Policy']
        self.assertIn(f'http://127.0.0.1:{PROJECTS_PORT}', policy.split('frame-ancestors', 1)[1].split(';')[0])
        return content, policy

    def test_laser_app_serves_its_editor_and_nothing_from_folie(self):
        for path in ('/', '/laser.html'):
            content, _ = self.page(self.laser, path)
            # Laser opens editable .laser.json projects through its own adapter,
            # without the older SVG handoff receiver.
            self.assertNotIn('/project-import-config.js', content)
            self.assertNotIn('/project-import.js', content)
            editor = content.index('/laservaerksted/laser-editor.js')
            for dependency in ('paper-core.min.js', 'opentype.min.js', 'usb-save.js'):
                self.assertLess(content.index('/laservaerksted/' + dependency), editor)
        self.assertEqual(self.request(self.laser, '/laservaerksted/laser-editor.js')[0], 200)
        for path in ('/folie.html', '/workshop.html', '/vendor/laser.js', '/laser-teacher.html', '/app.json'):
            with self.subTest(path=path):
                self.assertEqual(self.request(self.laser, path)[0], 404)

    def test_folie_app_serves_its_editor_without_laser_pages_or_queue(self):
        for path in ('/', '/folie.html'):
            content, _ = self.page(self.folie, path)
            # Folie still uses the older editor's SVG handoff receiver.
            self.assertLess(content.index('/project-import-config.js'), content.index('/project-import.js'))
        self.assertIn(b'ElevhubProjectOrigins', self.request(self.folie, '/project-import-config.js')[1])
        for path in ('/laservaerksted/index.html', '/workshop.html', '/api/laser/health', '/api/jobs'):
            with self.subTest(path=path):
                self.assertEqual(self.request(self.folie, path)[0], 404)
        for path in ('/api/laser/submit', '/api/jobs'):
            with self.subTest(post=path):
                self.assertEqual(self.request(self.folie, path, 'POST', b'{}')[0], 404)

    def test_3d_app_serves_its_editor_model_assets_and_wasm_policy(self):
        for path in ('/', '/model3d.html'):
            content, policy = self.page(self.model, path)
            self.assertIn('/vendor/model3d-app.js', content)
            self.assertIn("'wasm-unsafe-eval'", policy)
            self.assertIn("worker-src 'self'", policy)
        for path in ('/vendor/model3d-app.js', '/vendor/model3d-geometry-worker.js',
                     '/vendor/model3d-manifold.wasm', '/model3d.css'):
            with self.subTest(path=path):
                self.assertEqual(self.request(self.model, path)[0], 200)
        for path in ('/laerer.html', '/api/settings', '/laser.html', '/../app/core.py', '/api/laser/health'):
            with self.subTest(path=path):
                self.assertEqual(self.request(self.model, path)[0], 404)
        self.assertEqual(self.request(self.model, '/api/laser/submit', 'POST', b'{}')[0], 404)

    def test_school_theme_is_served_and_a_tampered_profile_is_rejected(self):
        write_manifest(self.school, css=b'/* synthetic school theme */')
        for server in (self.laser, self.folie, self.model):
            with self.subTest(app=server.descriptor['id']):
                self.assertEqual(self.request(server, '/school-theme.css')[:2], (200, b'/* synthetic school theme */'))
                self.assertEqual(self.request(server, '/school-appearance/logo')[:2], (200, b'logo'))
                self.assertIn(b'Testskolen', self.request(server, '/api/school-appearance')[1])
                self.assertEqual(self.request(server, '/')[0], 200)
        (self.school / 'local-appearance/files/teacher.css').write_bytes(b'changed')
        for server in (self.laser, self.folie, self.model):
            # Never the default look in place of a profile that fails validation.
            for path in ('/', '/school-theme.css', '/school-appearance/logo', '/api/school-appearance'):
                with self.subTest(app=server.descriptor['id'], path=path):
                    self.assertEqual(self.request(server, path)[0], 503)
            # Startup readiness does not depend on the school profile.
            self.assertEqual(self.request(server, '/api/runtime/health')[0], 200)

    def test_invalid_profile_document_explains_recovery_without_serving_the_app(self):
        write_manifest(self.school, css=b'/* synthetic school theme */')
        (self.school / 'local-appearance/files/teacher.css').write_bytes(b'changed')
        for server, page in ((self.laser, '/laser.html'), (self.folie, '/folie.html'),
                             (self.model, '/model3d.html')):
            for path in ('/', page + '?elevhub_project=synthetic'):
                with self.subTest(app=server.descriptor['id'], path=path):
                    status, body, headers = self.request(server, path)
                    self.assertEqual(status, 503)
                    self.assertIn('text/html', headers['Content-Type'])
                    html = body.decode('utf-8')
                    self.assertIn('<html lang="da">', html)
                    self.assertIn('<title>Værkstedet kan ikke åbnes</title>', html)
                    self.assertIn('<h1>Værkstedet kan ikke åbnes</h1>', html)
                    self.assertIn('Få hjælp af din lærer.', html)
                    self.assertIn('Prøv igen', html)
                    self.assertIn(f'href="http://127.0.0.1:{PROJECTS_PORT}/_elevhub/projekter/home"', html)
                    self.assertIn('Til værkstederne', html)
                    self.assertNotIn('<script', html)
                    self.assertNotIn('<link', html)
                    self.assertNotIn('ElevhubProjectAdapter', html)
            for path in ('/school-theme.css', '/school-appearance/logo',
                         '/api/school-appearance', '/favicon.svg'):
                with self.subTest(app=server.descriptor['id'], asset=path):
                    status, body, headers = self.request(server, path)
                    self.assertEqual(status, 503)
                    self.assertIn('application/json', headers['Content-Type'])
                    self.assertEqual(json.loads(body)['error'], 'Den lokale skoleprofil kunne ikke valideres.')


if __name__ == '__main__':
    unittest.main()
