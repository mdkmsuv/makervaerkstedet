"""Exact project-parent policy and configuration on real design HTTP surfaces."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import urlopen
from urllib.error import HTTPError
from unittest.mock import patch

from app.laser_server import create_server
from app.project_origins import DEFAULT_PROJECT_ORIGINS


class LaserProjectOriginsTests(unittest.TestCase):
    def test_shared_receiver_legacy_defaults_match_server_defaults_exactly(self):
        receiver = (Path(__file__).resolve().parents[1] / 'web/project-import.js').read_text(encoding='utf-8')
        expected = 'configured===undefined?[' + ','.join(repr(origin) for origin in DEFAULT_PROJECT_ORIGINS) + ']'
        self.assertIn(expected, receiver)

    def test_default_and_private_configuration_and_explicit_disable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch('app.laser_server.ROOT', root):
                server = create_server(root / 'data', port=0)
                try:
                    self.assertEqual(server.project_origins, DEFAULT_PROJECT_ORIGINS)
                finally:
                    server.server_close()
                config = root / 'laser-project-origins.json'
                config.write_text(json.dumps({'allowed_origins': ['http://localhost:31234']}))
                server = create_server(root / 'data', port=0)
                try:
                    self.assertEqual(server.project_origins, ('http://localhost:31234',))
                finally:
                    server.server_close()
                config.write_text('{"allowed_origins":[]}')
                server = create_server(root / 'data', port=0)
                try:
                    self.assertEqual(server.project_origins, ())
                finally:
                    server.server_close()

    def test_invalid_or_ambiguous_configuration_fails_before_binding_or_inbox(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for origins in (['http://localhost:31234/'], ['https://localhost:31234'],
                            ['http://evil.test:31234'], ['null'], ['http://localhost:65536']):
                with self.subTest(origins=origins), self.assertRaises(ValueError):
                    create_server(root / 'data', port=0, project_origins=origins)
            self.assertFalse((root / 'data').exists())
            config = root / 'origins.json'
            for value in ('{"allowed_origins": [], "extra": true}', '[]', 'x' * 8193):
                config.write_text(value)
                with self.subTest(value=value[:60]), self.assertRaises(ValueError):
                    create_server(root / 'data', port=0, project_origin_config=config)
            with self.assertRaises(ValueError):
                create_server(root / 'data', port=0, project_origins=[], project_origin_config=config)

    def test_real_http_csp_config_and_script_order_teacher_never_embedded(self):
        with tempfile.TemporaryDirectory() as folder:
            origin = 'http://127.0.0.1:31234'
            student = create_server(folder, port=0, project_origins=[origin])
            teacher = create_server(folder, port=0, surface='teacher', inbox=student.inbox,
                                    project_origins=[origin])
            teacher.frame_ancestors = origin  # even an old embedding override cannot expose teacher
            workers = []
            for server in (student, teacher):
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                workers.append(thread)
            try:
                base = f'http://127.0.0.1:{student.server_port}'
                for path in ('/laser.html', '/folie.html', '/project-import-config.js'):
                    with urlopen(base + path, timeout=3) as response:
                        content = response.read().decode()
                        self.assertIn('frame-ancestors ' + origin + ';', response.headers['Content-Security-Policy'])
                        self.assertNotIn('8791', response.headers['Content-Security-Policy'])
                        if path.endswith('.html'):
                            self.assertLess(content.index('/project-import-config.js'), content.index('/project-import.js'))
                        else:
                            self.assertIn(json.dumps([origin]), content)
                teacher_base = f'http://127.0.0.1:{teacher.server_port}'
                with urlopen(teacher_base + '/', timeout=3) as response:
                    self.assertIn("frame-ancestors 'none';", response.headers['Content-Security-Policy'])
                with self.assertRaises(HTTPError) as error:
                    urlopen(teacher_base + '/project-import-config.js', timeout=3)
                self.assertEqual(error.exception.code, 404)
                error.exception.close()
            finally:
                for server, worker in zip((student, teacher), workers):
                    server.shutdown()
                    worker.join()
                    server.server_close()
