import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from app.laser_station import NotLoggedIn, QueueClient, QueueUnavailable, Station
from app.laser_server import Inbox, create_server

CODE = 'laser-test-code'


class _Queue:
    base_url = 'http://127.0.0.1:8767'
    logged_in = True

    def __init__(self):
        self.calls = []
        self.fail_once = True

    def set_status(self, job_id, status):
        self.calls.append(job_id)
        if job_id == 'b' and self.fail_once:
            self.fail_once = False
            raise QueueUnavailable('offline')


class _QueueReply(BaseHTTPRequestHandler):
    mode = 'json'
    sink_cookie = None

    def do_GET(self):
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/sink')
            self.end_headers()
        elif self.path == '/sink':
            type(self).sink_cookie = self.headers.get('Cookie')
            self.send_response(200)
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'not-json' if self.mode == 'json' else b'x' * 3_000_001)

    def log_message(self, *args):
        pass


class QueueClientGuardTests(unittest.TestCase):
    @staticmethod
    def _summary_queue(responses):
        queue = QueueClient('http://127.0.0.1:8767')
        queue.cookie = 'laser_teacher=test'
        calls = []

        def fake_json(path):
            calls.append(path)
            return responses[len(calls) - 1]

        queue._json = fake_json
        return queue, calls

    def test_summary_rejects_duplicate_job_across_pages(self):
        queue, _ = self._summary_queue([
            {'jobs': [{'id': 'a'}], 'nextCursor': 'cursor-1'},
            {'jobs': [{'id': 'a'}], 'nextCursor': None},
        ])
        with self.assertRaises(QueueUnavailable):
            queue.jobs(summary=True)

    def test_summary_rejects_repeated_cursor(self):
        queue, _ = self._summary_queue([
            {'jobs': [{'id': 'a'}], 'nextCursor': 'cursor-1'},
            {'jobs': [{'id': 'b'}], 'nextCursor': 'cursor-1'},
        ])
        with self.assertRaises(QueueUnavailable):
            queue.jobs(summary=True)

    def test_summary_rejects_empty_page_with_next_cursor(self):
        queue, _ = self._summary_queue([
            {'jobs': [{'id': 'a'}], 'nextCursor': 'cursor-1'},
            {'jobs': [], 'nextCursor': 'cursor-2'},
        ])
        with self.assertRaises(QueueUnavailable):
            queue.jobs(summary=True)

    def test_summary_rejects_more_than_one_page_of_jobs(self):
        queue, _ = self._summary_queue([
            {'jobs': [{'id': str(i)} for i in range(11)], 'nextCursor': None},
        ])
        with self.assertRaises(QueueUnavailable):
            queue.jobs(summary=True)

    def test_summary_has_a_bounded_page_loop(self):
        queue, calls = self._summary_queue([
            {'jobs': [{'id': 'a'}], 'nextCursor': 'cursor-1'},
            {'jobs': [{'id': 'b'}], 'nextCursor': 'cursor-2'},
        ])
        with patch('app.laser_station.SUMMARY_MAX_PAGES', 2):
            with self.assertRaises(QueueUnavailable):
                queue.jobs(summary=True)
        self.assertEqual(len(calls), 2)

    def test_summary_keyset_snapshot_survives_new_job_before_cursor(self):
        with tempfile.TemporaryDirectory() as folder:
            inbox = Inbox(Path(folder), code=CODE)
            origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
            original_ids = [f'{i:032x}' for i in range(21)]
            rows = [
                (job_id, f'snapshot-{i}', f'fingerprint-{i}',
                 (origin + timedelta(minutes=i)).isoformat(), 'Elev', '3.A',
                 f'Projekt {i}', 'ready', '<svg/>', json.dumps({}), 1)
                for i, job_id in enumerate(original_ids)
            ]
            with inbox.db() as db:
                db.executemany(
                    'INSERT INTO jobs (id,request_id,fingerprint,created_at,student,classroom,project,status,svg,details,payload_available) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    rows)

            calls = []

            def fake_json(path):
                query = parse_qs(urlparse(path).query)
                cursor = query.get('cursor', [None])[0]
                page = inbox.summary_page(limit=10, cursor=cursor)
                calls.append(path)
                if len(calls) == 1:
                    with inbox.db() as db:
                        db.execute(
                            'INSERT INTO jobs (id,request_id,fingerprint,created_at,student,classroom,project,status,svg,details,payload_available) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                            ('f' * 32, 'inserted-before-cursor', 'inserted-fingerprint',
                             (origin + timedelta(minutes=100)).isoformat(), 'Ny', '3.A',
                             'Indsat under hentning', 'ready', '<svg/>', json.dumps({}), 1))
                return page

            queue = QueueClient('http://127.0.0.1:8767')
            queue.cookie = 'laser_teacher=test'
            queue._json = fake_json
            jobs = queue.jobs(summary=True)
            self.assertEqual([job['id'] for job in jobs], list(reversed(original_ids)))
            self.assertNotIn('f' * 32, [job['id'] for job in jobs])
            self.assertEqual(len(calls), 3)

    def test_server_must_be_a_plain_http_origin(self):
        for url in ('ftp://127.0.0.1:8767', 'http://user:pass@127.0.0.1:8767',
                    'http://127.0.0.1:8767/api', 'http://127.0.0.1:8767/?x=1'):
            with self.assertRaises(ValueError):
                QueueClient(url)
        self.assertEqual(QueueClient('http://127.0.0.1:8767/').base_url, 'http://127.0.0.1:8767')

    def test_redirect_does_not_forward_teacher_cookie_to_local_sink(self):
        _QueueReply.sink_cookie = None
        server = ThreadingHTTPServer(('127.0.0.1', 0), _QueueReply)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            queue = QueueClient(f'http://127.0.0.1:{server.server_port}')
            queue.cookie = 'laser_teacher=secret'
            with self.assertRaises(ValueError):
                queue._request('/redirect')
            self.assertIsNone(_QueueReply.sink_cookie)
        finally:
            server.shutdown()
            server.server_close()

    def test_malformed_and_oversized_queue_replies_are_controlled(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), _QueueReply)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            queue = QueueClient(f'http://127.0.0.1:{server.server_port}')
            queue.cookie = 'laser_teacher=secret'
            _QueueReply.mode = 'json'
            with self.assertRaises(QueueUnavailable):
                queue.jobs()
            _QueueReply.mode = 'large'
            with self.assertRaises(QueueUnavailable):
                queue.jobs()
        finally:
            server.shutdown()
            server.server_close()

    def test_server_restart_expires_station_session(self):
        with tempfile.TemporaryDirectory() as folder:
            old = create_server(Path(folder), port=0, code=CODE, surface='teacher')
            threading.Thread(target=old.serve_forever, daemon=True).start()
            queue = QueueClient(f'http://127.0.0.1:{old.server_port}')
            queue.login(CODE)
            old.shutdown()
            old.server_close()
            restarted = create_server(Path(folder), port=0, code=CODE, surface='teacher')
            threading.Thread(target=restarted.serve_forever, daemon=True).start()
            try:
                queue.base_url = f'http://127.0.0.1:{restarted.server_port}'
                with self.assertRaises(NotLoggedIn):
                    queue.jobs()
                self.assertFalse(queue.logged_in)
            finally:
                restarted.shutdown()
                restarted.server_close()


class StationRecoveryTests(unittest.TestCase):
    def test_last_plate_survives_station_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            queue = _Queue()
            first = Station(queue, Path(folder), beam_studio=None)
            first.last_plate = {'file': str(Path(folder) / 'plate.bvg'), 'ids': ['a'],
                                'material': 'Træ', 'names': ['A'], 'created': 'now'}
            first._save_last_plate()
            second = Station(queue, Path(folder), beam_studio=None)
            self.assertEqual(second.last_plate['ids'], ['a'])

    def test_partial_status_update_keeps_failed_items_for_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            queue = _Queue()
            station = Station(queue, Path(folder), beam_studio=None)
            station.last_plate = {'file': str(Path(folder) / 'plate.bvg'), 'ids': ['a', 'b'],
                                  'material': 'Træ', 'names': ['A', 'B'], 'created': 'now'}
            station._save_last_plate()
            with self.assertRaises(QueueUnavailable):
                station.mark_done({'ids': ['a', 'b']})
            self.assertEqual(station.last_plate['ids'], ['b'])
            self.assertEqual(station.last_plate['names'], ['B'])
            restored = Station(queue, Path(folder), beam_studio=None)
            self.assertEqual(restored.last_plate['ids'], ['b'])
            self.assertEqual(restored.last_plate['names'], ['B'])
            self.assertEqual(queue.calls, ['a', 'b'])
            self.assertEqual(station.mark_done({'ids': ['b']})['marked'], 1)
            self.assertIsNone(station.last_plate)


if __name__ == '__main__':
    unittest.main()
