"""The pupil device's laser outbox: submit without the school server, send later."""
import http.client
import json
import os
import socket
import tempfile
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from app.laser_local import ForwardingInbox, Outbox, create_local_servers
from app.laser_server import create_server

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" data-laservaerksted="1" width="250mm" height="167mm" '
       'viewBox="0 0 250 167"><title>Nøglering</title><g data-operation="cut">'
       '<path d="M10 10H60V40H10Z" data-operation="cut" data-name="Ramme" fill-rule="evenodd"/></g></svg>')


def payload(**changes):
    data = {'elevnavn': 'Åse', 'klasse': '6.B', 'projekt': 'Nøglering',
            'requestId': str(uuid.uuid4()), 'svg': SVG}
    data.update(changes)
    return data


def start_fake_central(status, body):
    class Fake(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            data = json.dumps(body).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class LaserOutboxTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.central_port = free_port()
        self.laser, folie = create_local_servers(
            f'http://127.0.0.1:{self.central_port}', laser_port=0, folie_port=0,
            runtime={}, outbox_dir=self.root / 'udbakke')
        folie.server_close()
        self.laser.RequestHandlerClass.log_message = lambda *a: None
        threading.Thread(target=self.laser.serve_forever, daemon=True).start()
        self.addCleanup(self.laser.server_close)
        self.addCleanup(self.laser.shutdown)
        self.outbox = self.laser.inbox.outbox

    def start_central(self):
        central = create_server(str(self.root / 'central'), '127.0.0.1', self.central_port,
                                code='test-only-code', surface='student')
        threading.Thread(target=central.serve_forever, daemon=True).start()
        self.addCleanup(central.server_close)
        self.addCleanup(central.shutdown)
        return central

    def submit(self, data):
        conn = http.client.HTTPConnection('127.0.0.1', self.laser.port, timeout=15)
        try:
            conn.request('POST', '/api/laser/submit', json.dumps(data),
                         {'Content-Type': 'application/json',
                          'Origin': f'http://127.0.0.1:{self.laser.port}'})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def test_offline_submission_is_queued_then_delivered_once(self):
        job = payload()
        status, body = self.submit(job)
        self.assertEqual(status, 202)
        self.assertEqual(body, {'queued': True, 'requestId': job['requestId']})
        # Pressing submit again while offline keeps one waiting copy.
        self.assertEqual(self.submit(job)[0], 202)
        self.assertEqual(len(self.outbox.waiting()), 1)

        central = self.start_central()
        self.assertEqual(self.outbox.flush(self.laser.inbox.send), 1)
        self.assertEqual(self.outbox.waiting(), [])
        jobs = central.inbox.jobs()
        self.assertEqual(len(jobs), 1)

        # A late retry from the browser is recognised as the same job.
        status, body = self.submit(job)
        self.assertEqual(status, 200)
        self.assertTrue(body['duplicate'])
        self.assertEqual(body['id'], jobs[0]['id'])

    def test_invalid_drawing_is_rejected_locally_not_queued(self):
        status, body = self.submit(payload(svg='<svg xmlns="http://www.w3.org/2000/svg"/>'))
        self.assertEqual(status, 400)
        self.assertIn('Laserværksted', body['error'])
        status, body = self.submit(payload(klasse=''))
        self.assertEqual(status, 400)
        self.assertEqual(self.outbox.waiting(), [])

    def test_outbox_is_bounded(self):
        small = Outbox(self.root / 'lille', limit=2)
        small.put(payload())
        small.put(payload())
        with self.assertRaises(ValueError):
            small.put(payload())

    def test_flush_stops_while_offline_and_drops_unreadable_files(self):
        broken = self.outbox.directory / ('x' * 20 + '.json')
        broken.write_text('{', encoding='utf-8')
        os.utime(broken, (1, 1))  # oldest, so flush reaches it before going offline
        self.outbox.put(payload())
        self.assertEqual(self.outbox.flush(self.laser.inbox.send), 0)
        self.assertFalse(broken.exists())
        self.assertTrue((self.outbox.directory / 'afvist' / broken.name).exists())
        self.assertEqual(len(self.outbox.waiting()), 1)

    def test_server_answers_decide_keep_skip_or_set_aside(self):
        cases = ((429, {'error': 'Afleveringskøen er fuld. Gem SVG og kontakt læreren.'}, 'waiting'),
                 (404, {'error': 'Siden findes ikke.'}, 'waiting'),
                 (423, {'error': 'Læreren har lukket Laser.'}, 'waiting'),
                 (503, {'error': 'Prøv igen.'}, 'waiting'),
                 (400, {'error': 'En kontur kunne ikke læses.'}, 'afvist'))
        for status, body, expected in cases:
            with self.subTest(status=status):
                fake = start_fake_central(status, body)
                self.addCleanup(fake.server_close)
                self.addCleanup(fake.shutdown)
                outbox = Outbox(self.root / f'case-{status}')
                outbox.put(payload())
                inbox = ForwardingInbox(f'http://127.0.0.1:{fake.server_port}', outbox=outbox)
                self.assertEqual(outbox.flush(inbox.send), 0)
                self.assertEqual(len(outbox.waiting()), 1 if expected == 'waiting' else 0)
                self.assertEqual(len(list((outbox.directory / 'afvist').glob('*.json'))) if expected == 'afvist' else 0,
                                 1 if expected == 'afvist' else 0)

    def test_full_central_queue_gives_429_and_keeps_the_message(self):
        central = self.start_central()
        with patch('app.laser_server.MAX_SENDER_JOBS', 0):
            status, body = self.submit(payload())
        self.assertEqual(status, 429)
        self.assertIn('venter på læreren', body['error'])
        self.assertEqual(self.outbox.waiting(), [])
        self.assertEqual(central.inbox.jobs(), [])


if __name__ == '__main__':
    unittest.main()
