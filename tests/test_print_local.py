"""Local 3D and durable offline delivery, against a real isolated print queue."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from app import laser_local as local
from app.server import Application, Handler, Server
from scripts.smoke_slice import cube
from tests.test_laser_outbox import free_port, start_fake_central
from tests.test_local_appearance_http import write_manifest


class LocalPrintTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(getattr(local, 'create_print_server', None)),
                        'The local service must provide the 3D workshop and print outbox')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.central_port = free_port()
        self.server = local.create_print_server(
            f'http://127.0.0.1:{self.central_port}', port=0,
            outbox_dir=self.root / 'outbox', runtime={})
        self.server.RequestHandlerClass.log_message = lambda *args: None
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path='/api/jobs', body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.port, timeout=5)
        defaults = {'Origin': f'http://127.0.0.1:{self.server.port}',
                    'X-Filename': 'figur.stl', 'X-Colour': 'any',
                    'X-Quality': 'standard', 'X-Request-ID': 'local-print-request-0001',
                    'Content-Type': 'application/octet-stream'}
        defaults.update(headers or {})
        try:
            connection.request('GET' if body is None else 'POST', path, body, defaults)
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def central(self):
        application = Application(str(self.root / 'central'), demo=True)
        self.addCleanup(application.shutdown)
        server = Server(('127.0.0.1', self.central_port), Handler)
        server.app = application
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return application.queue

    def test_offline_model_assets_and_wasm_policy_are_available(self):
        status, body, headers = self.request('/model3d.html')
        self.assertEqual(status, 200)
        self.assertIn(b'/vendor/model3d-app.js', body)
        self.assertNotIn(b'/_elevhub/projekter/bridge.js', body)
        self.assertIn("'wasm-unsafe-eval'", headers['Content-Security-Policy'])
        self.assertIn("worker-src 'self'", headers['Content-Security-Policy'])
        for path in ('/vendor/model3d-app.js', '/vendor/model3d-geometry-worker.js',
                     '/vendor/model3d-manifold.wasm', '/model3d.css'):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 200)
        for path in ('/laerer.html', '/api/settings', '/laser.html', '/../app/core.py'):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)

    def test_offline_job_survives_restart_and_is_delivered_exactly_once(self):
        body = cube()
        self.assertEqual(self.request(body=body)[0], 202)
        self.assertEqual(self.request(body=body)[0], 202)
        self.assertEqual(len(self.server.inbox.outbox.waiting()), 1)
        restarted = local.Outbox(self.root / 'outbox', validator=local.validate_print_submission)
        queue = self.central()
        self.assertEqual(restarted.flush(self.server.inbox.send), 1)
        self.assertEqual(restarted.waiting(), [])
        status, response, _ = self.request(body=body)
        self.assertEqual(status, 201)
        self.assertTrue(json.loads(response)['id'])
        self.assertEqual(queue.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)

    def test_local_school_theme_is_preserved_and_tampering_is_rejected(self):
        self.server.data_root = self.root / 'appearance'
        write_manifest(self.server.data_root, css=b'/* synthetic school theme */')
        self.assertEqual(self.request('/school-theme.css')[:2], (200, b'/* synthetic school theme */'))
        self.assertEqual(self.request('/school-appearance/logo')[:2], (200, b'logo'))
        (self.server.data_root / 'local-appearance/files/teacher.css').write_bytes(b'changed')
        self.assertEqual(self.request('/model3d.html')[0], 503)

    def test_invalid_or_conflicting_upload_never_replaces_waiting_model(self):
        self.assertEqual(self.request(body=b'not an STL')[0], 400)
        self.assertEqual(self.server.inbox.outbox.waiting(), [])
        self.assertEqual(self.request(body=cube())[0], 202)
        before = self.server.inbox.outbox.waiting()[0].read_bytes()
        self.assertEqual(self.request(body=cube(30))[0], 400)
        self.assertEqual(self.server.inbox.outbox.waiting()[0].read_bytes(), before)

    def test_wrong_origin_unknown_route_and_invalid_options_are_rejected(self):
        for headers in ({'Origin': 'https://unrelated.invalid'}, {'X-Quality': 'fast'},
                        {'X-Colour': '#123456'}, {'X-Request-ID': 'bad'}):
            with self.subTest(headers=headers):
                self.assertIn(self.request(body=cube(), headers=headers)[0], (400, 403))
        self.assertEqual(self.request('/api/laser/submit', b'{}')[0], 404)
        self.assertEqual(self.server.inbox.outbox.waiting(), [])

    def test_reconnection_cannot_replace_a_pending_request_with_another_model(self):
        self.assertEqual(self.request(body=cube())[0], 202)
        before = self.server.inbox.outbox.waiting()[0].read_bytes()
        queue = self.central()
        self.assertEqual(self.request(body=cube(30))[0], 400)
        self.assertEqual(queue.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)
        self.assertEqual(self.server.inbox.outbox.waiting()[0].read_bytes(), before)
        self.assertEqual(self.server.inbox.outbox.flush(self.server.inbox.send), 1)
        self.assertEqual(self.request(body=cube())[0], 201)
        self.assertEqual(queue.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)

    def test_retry_response_rules_preserve_or_set_aside_the_model(self):
        for status, error, expected, rejected in (
            (503, 'offline', 0, False), (400, 'Ugyldig model.', 0, True),
            (413, 'For stor.', 0, True), (423, 'Lukket.', 0, False),
            (429, 'Vent.', 0, False), (403, 'Ikke nu.', 0, False)):
            with self.subTest(status=status):
                fake = start_fake_central(status, {'error': error})
                try:
                    directory = self.root / str(status)
                    inbox = local.PrintForwardingInbox(
                        f'http://127.0.0.1:{fake.server_address[1]}',
                        outbox=local.Outbox(directory, validator=local.validate_print_submission))
                    inbox.outbox.put(local.print_submission(cube(), 'figur.stl', 'retry-print-request-0001'))
                    self.assertEqual(inbox.outbox.flush(inbox.send), expected)
                    self.assertEqual(bool(list((directory / 'afvist').glob('*.json'))), rejected)
                    self.assertEqual(len(inbox.outbox.waiting()), 0 if rejected else 1)
                finally:
                    fake.shutdown()
                    fake.server_close()


if __name__ == '__main__':
    unittest.main()
