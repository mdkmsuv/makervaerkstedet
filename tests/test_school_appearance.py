import http.client
import json
import hashlib
from pathlib import Path
import tempfile
import threading
import unittest

from app.laser_server import create_server
from app.school_appearance import school_appearance, school_appearance_asset


class SchoolAppearanceTests(unittest.TestCase):
    def request(self, server):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        connection.request('GET', '/api/school-appearance')
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, body

    def test_legacy_student_endpoint_is_public_json_null(self):
        with tempfile.TemporaryDirectory() as folder:
            server = create_server(folder, port=0, code='secret-code')
            server.RequestHandlerClass.log_message = lambda *args: None
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                status, body = self.request(server)
            finally:
                server.shutdown()
                server.server_close()
            self.assertEqual(status, 200)
            self.assertIsNone(json.loads(body))

    def test_windows_file_exposes_only_valid_public_metadata(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent)
            data = root / 'data'
            data.mkdir()
            (root / 'school.json').write_text(json.dumps({
                'school_name': 'Søndervang <Skole>',
                'theme': 'green',
                'teacher_code': 'MÅ IKKE RETURNERES',
                'server_url': 'https://secret.invalid',
            }), encoding='utf-8')
            self.assertEqual(school_appearance(data), {
                'school_name': 'Søndervang <Skole>',
                'theme': 'green',
            })

            (root / 'school.json').write_text(json.dumps({'school_name': 'x', 'theme': ['red']}), encoding='utf-8')
            self.assertIsNone(school_appearance(data))
            (root / 'school.json').write_text('x' * 65537, encoding='utf-8')
            self.assertIsNone(school_appearance(data))

    def test_hashed_local_overlay_exposes_only_declared_assets(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent)
            data = root / 'data'
            data.mkdir()
            overlay = root / 'local-appearance'
            overlay.mkdir()
            css = b':root { --brand: #28734e; }\n'
            logo = b'PNG fixture bytes'
            (overlay / 'site.css').write_bytes(css)
            (overlay / 'logo.png').write_bytes(logo)
            (overlay / 'manifest.json').write_text(json.dumps({
                'schemaVersion': 1, 'school_name': 'Lokalskolen', 'theme': 'green',
                'style': {
                    'website_css': 'site.css', 'website_css_sha256': hashlib.sha256(css).hexdigest(),
                    'logo': 'logo.png', 'logo_sha256': hashlib.sha256(logo).hexdigest(),
                },
            }), encoding='utf-8')
            self.assertEqual(school_appearance(data), {
                'school_name': 'Lokalskolen', 'theme': 'green',
                'style': {'website_css': '/school-appearance/website.css', 'logo': '/school-appearance/logo'},
            })
            self.assertEqual(school_appearance_asset(data, 'website.css'), (css, 'text/css; charset=utf-8'))
            self.assertEqual(school_appearance_asset(data, 'logo'), (logo, 'image/png'))
            self.assertIsNone(school_appearance_asset(data, 'private.txt'))

            (overlay / 'manifest.json').write_text(json.dumps({
                'schemaVersion': 1, 'school_name': 'Lokalskolen', 'theme': 'green',
                'style': {'website_css': '../private.css', 'website_css_sha256': '0' * 64},
            }), encoding='utf-8')
            self.assertIsNone(school_appearance(data))

if __name__ == '__main__':
    unittest.main()
