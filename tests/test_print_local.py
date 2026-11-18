"""Local 3D and durable offline delivery, against a real isolated print queue."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app import laser_local as local
from app.server import Application, Handler, Server
from scripts.smoke_slice import cube
from tests.test_laser_outbox import free_port, start_fake_central


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

    def request(self, path='/api/jobs', body=None, headers=None, *, server=None):
        port = (server or self.server).port
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        defaults = {'Origin': f'http://127.0.0.1:{port}',
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

    def test_print_forwarding_server_serves_no_pages(self):
        # The 3D editor, its model assets and the WASM policy are covered on
        # the installed-app gateway in test_installed_editor_pages.py.
        status, body, _ = self.request('/api/runtime/health')
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)['local'])
        for path in ('/', '/model3d.html', '/model3d.css', '/vendor/model3d-app.js', '/project-import-config.js',
                     '/laerer.html', '/api/settings', '/laser.html', '/../app/core.py'):
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

    def test_invalid_or_conflicting_upload_never_replaces_waiting_model(self):
        self.assertEqual(self.request(body=b'not an STL')[0], 400)
        self.assertEqual(self.server.inbox.outbox.waiting(), [])
        self.assertEqual(self.request(body=cube())[0], 202)
        before = self.server.inbox.outbox.waiting()[0].read_bytes()
        self.assertEqual(self.request(body=cube(30))[0], 400)
        self.assertEqual(self.server.inbox.outbox.waiting()[0].read_bytes(), before)

    def test_wrong_origin_unknown_route_and_invalid_options_are_rejected(self):
        for headers in ({'Origin': 'https://unrelated.invalid'}, {'X-Quality': 'fast'},
                        {'X-Colour': '#abcdef'}, {'X-Request-ID': 'bad'}):
            with self.subTest(headers=headers):
                self.assertIn(self.request(body=cube(), headers=headers)[0], (400, 403))
        self.assertEqual(self.request('/api/laser/submit', b'{}')[0], 404)
        self.assertEqual(self.server.inbox.outbox.waiting(), [])

    def test_print_colour_catalogue_contains_only_current_pupil_choices(self):
        self.assertEqual(self.request('/api/print/colours')[0], 503)
        queue = self.central()
        snapshot = {
            'printers': [
                {'id': 'idle', 'name': 'Private printer label', 'status': 'ready', 'enabled': True, 'colours': ['#E84142']},
                {'id': 'busy', 'status': 'printing', 'enabled': True, 'colours': ['#E84142', '#3678DC']},
                {'id': 'offline', 'status': 'offline', 'enabled': True, 'colours': ['#F4F4F4']},
                {'id': 'disabled', 'status': 'disabled', 'enabled': False, 'colours': ['#20242A']},
            ],
            'colours': [
                {'hex': '#E84142', 'name': 'Rød', 'printers': ['idle', 'busy'], 'ready': 1},
                {'hex': '#3678DC', 'name': 'Blå', 'printers': ['busy'], 'ready': 0},
                {'hex': '#F4F4F4', 'name': 'Hvid', 'printers': ['offline'], 'ready': 0},
                {'hex': '#20242A', 'name': 'Sort', 'printers': ['disabled'], 'ready': 0},
            ],
            'jobs': [{'filename': 'Private pupil filename.stl'}],
        }
        with patch.object(queue, 'snapshot', return_value=snapshot):
            status, body, headers = self.request('/api/print/colours')
            self.assertEqual(status, 200)
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertEqual(json.loads(body), {'colours': [
                {'hex': '#E84142', 'name': 'Rød', 'ready': 1},
                {'hex': '#3678DC', 'name': 'Blå', 'ready': 0},
            ]})
            self.assertEqual(self.request('/api/print/colours?extra=1')[0], 404)

    def test_only_installed_print_capability_exposes_filament_choices(self):
        apps = []
        root = Path(__file__).resolve().parents[1]
        for app_id in ('3dprint', 'laser'):
            descriptor = json.loads((root / 'apps' / app_id / 'app.json').read_text(encoding='utf-8'))
            apps.append({**descriptor, 'port': 0})
        printer, laser = local.create_installed_servers(apps, self.server.inbox, self.server.inbox,
                                                        runtime={}, data_root=self.root, library_client=object())
        for server in (printer, laser):
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        self.assertEqual(self.request('/api/print/colours', server=printer)[0], 503)
        self.assertEqual(self.request('/api/print/colours', server=laser)[0], 404)

    def test_offline_chosen_colour_survives_missing_spool_and_retries(self):
        body = cube()
        choice = {'X-Colour': '#E84142'}
        self.assertEqual(self.request(body=body, headers=choice)[0], 202)
        waiting, = self.server.inbox.outbox.waiting()
        original = waiting.read_bytes()
        self.assertEqual(json.loads(original)['colour'], choice['X-Colour'])
        self.assertEqual(self.request(body=body)[0], 400, 'same request must not change the colour')
        restarted = local.Outbox(self.root / 'outbox', validator=local.validate_print_submission)
        queue = self.central()
        with patch.object(queue, 'available_trays', return_value=[]):
            status, payload, _ = self.request(body=body, headers={**choice, 'X-Request-ID': 'other-print-request-0001'})
            self.assertEqual(status, 409)
            self.assertEqual(json.loads(payload)['code'], 'colour_unavailable')
            self.assertEqual(restarted.flush(self.server.inbox.send), 0)
            status, payload, _ = self.request(body=body, headers=choice)
            self.assertEqual(status, 202, 'a repeat must not pretend the queued model was refused')
            self.assertEqual(json.loads(payload)['code'], 'colour_unavailable')
            self.assertEqual(waiting.read_bytes(), original)
        self.assertEqual(restarted.flush(self.server.inbox.send), 1)
        self.assertEqual(self.request(body=body, headers=choice)[0], 201)
        jobs = queue.db.execute('SELECT colour FROM jobs').fetchall()
        self.assertEqual([job['colour'] for job in jobs], [choice['X-Colour']])

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

    def test_full_central_queue_keeps_the_waiting_model_until_there_is_room(self):
        self.assertEqual(self.request(body=cube())[0], 202)
        queue = self.central()
        outbox = self.server.inbox.outbox
        with patch.object(queue, '_upload_bytes', return_value=1024 ** 3):
            self.assertEqual(outbox.flush(self.server.inbox.send), 0)
        self.assertEqual(len(outbox.waiting()), 1, 'a full queue must not reject the pupil\'s model')
        self.assertFalse(list((outbox.directory / 'afvist').glob('*.json')))
        self.assertEqual(outbox.flush(self.server.inbox.send), 1)
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
