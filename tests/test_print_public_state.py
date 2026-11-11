"""The shared print status endpoint does not leak student job details."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from app.server import Application, Handler, Server
from scripts.smoke_slice import cube


class PrintPublicStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='print-public-state-')
        self.root = Path(self.temp.name)
        (self.root / 'laererkode.txt').write_text('teacher-test-code', encoding='ascii')
        (self.root / 'settings.json').write_text(json.dumps({
            'studio_path': str(self.root / 'missing-studio.exe'),
            'printers': [{'id': 'p1', 'name': 'Testprinter', 'model': 'P1S',
                          'host': '192.0.2.10', 'serial': 'SERIALP1',
                          'access_code': '', 'enabled': False}],
        }), encoding='utf-8')
        self.server = Server(('127.0.0.1', 0), Handler)
        self.server.app = Application(self.root, False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = ('127.0.0.1', self.server.server_port)
        self.addCleanup(self.close)

    def close(self):
        if getattr(self, 'server', None) is None:
            return
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.app.shutdown()
        self.server.app.queue.close()
        self.server.server_close()
        self.temp.cleanup()
        self.server = None

    def request(self, method='GET', path='/api/state', body=None, headers=None):
        connection = http.client.HTTPConnection(*self.base, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def add_job(self):
        # Seed a real job with a disabled, disconnected fixture printer.
        # Production admission rejects such a fleet; this suite verifies
        # redaction and must never connect to equipment just to create data.
        queue = self.server.app.queue
        queue.demo = True
        try:
            queue.submit(cube(), 'elev-hemmelig.stl', 'any', 'public-state-job-001')
        finally:
            queue.demo = False

    def test_anonymous_state_redacts_shared_job_details(self):
        self.add_job()
        self.server.app.queue.printers['p1']['message'] = 'elev-hemmelig.stl'
        status, _, body = self.request()
        self.assertEqual(status, 200)
        value = json.loads(body)
        self.assertEqual(value['jobs'][0]['status'], 'queued')
        self.assertNotIn('filename', value['jobs'][0])
        self.assertNotIn(b'elev-hemmelig.stl', body)
        self.assertNotIn('message', value['printers'][0])
        self.assertNotIn(b'filename', body)
        self.assertIn('printers', value)
        self.assertIn('print_access', value)

    def test_teacher_session_receives_full_operational_snapshot(self):
        self.add_job()
        status, headers, _ = self.request(
            'POST', '/api/auth/login', json.dumps({'code': 'teacher-test-code'}).encode(),
            {'Content-Type': 'application/json'})
        self.assertEqual(status, 200)
        cookie = headers['Set-Cookie'].split(';', 1)[0]
        status, _, body = self.request('GET', '/api/state', headers={'Cookie': cookie})
        self.assertEqual(status, 200)
        value = json.loads(body)
        self.assertEqual(value['jobs'][0]['filename'], 'elev-hemmelig.stl')
        self.assertIn('meta', value['jobs'][0])

    def test_isolated_status_is_public_without_private_details_or_resolution_actions(self):
        self.add_job()
        queue = self.server.app.queue
        job = queue.snapshot()['jobs'][0]
        # The queue closes its record, without claiming a physical stop.
        queue._change(job['id'], status='cancelled', isolated=1,
                      message='Printerens fysiske status er uafklaret.')
        status, _, body = self.request()
        self.assertEqual(status, 200)
        public = json.loads(body)['jobs'][0]
        self.assertEqual(public['status'], 'cancelled')
        self.assertEqual(public['isolated'], 1)
        self.assertNotIn('message', public)
        self.assertNotIn('resolution_options', public)
        self.assertNotIn('filename', public)
        self.assertNotIn(b'elev-hemmelig.stl', body)

    def test_invalid_or_expired_cookie_gets_redacted_state(self):
        self.add_job()
        status, _, body = self.request('GET', '/api/state', headers={'Cookie': 'print_teacher=invalid'})
        self.assertEqual(status, 200)
        value = json.loads(body)
        self.assertEqual(value['jobs'][0]['status'], 'queued')
        self.assertNotIn('filename', value['jobs'][0])
        self.assertNotIn(b'elev-hemmelig.stl', body)


if __name__ == '__main__':
    unittest.main()
