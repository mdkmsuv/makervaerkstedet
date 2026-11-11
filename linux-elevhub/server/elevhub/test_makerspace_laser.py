import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import makerspace_laser
from makerspace_laser import LaserSnapshots


class _SlowHttp10Station(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def do_GET(self):
        body = json.dumps({'status': 'ok', 'checked_at': 1700000000,
                           'items': [{'id': 'laser-one', 'model': 'fhexa1', 'name': 'HEXA'}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body[:8])
        self.wfile.flush()
        time.sleep(1.5)
        self.wfile.write(body[8:])
        self.wfile.flush()

    def log_message(self, *args):
        pass


class _SlowHeadersHttp10Station(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def do_GET(self):
        time.sleep(1.5)
        self.send_response(200)
        self.end_headers()
        try:
            self.wfile.write(b'{"status":"ok","items":[]}')
        except OSError:
            pass

    def log_message(self, *args):
        pass


class _Station(BaseHTTPRequestHandler):
    payload = {}
    calls = 0

    def do_GET(self):
        type(self).calls += 1
        body = json.dumps(type(self).payload).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class MakerspaceLaserTests(unittest.TestCase):
    def setUp(self):
        _Station.calls = 0
        _Station.payload = {'status': 'ok', 'checked_at': 1700000000,
                            'items': [{'id': 'laser-one', 'model': 'fhexa1', 'name': 'HEXA',
                                       'software': {'beam_studio_configured': True, 'flux_configured': True}}]}
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), _Station)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.backend = f'127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown(); self.server.server_close()

    def test_snapshot_whitelists_and_caches(self):
        snapshots = LaserSnapshots([{'id': 'laser-one', 'backend': self.backend}], clock=lambda: 1700000001, monotonic=lambda: 1)
        result = snapshots.snapshot()
        again = snapshots.snapshot()
        self.assertEqual(result, again)
        self.assertEqual(_Station.calls, 1)
        row = result['items'][0]
        self.assertEqual((row['model'], row['name'], row['status'], row['online']), ('fhexa1', 'HEXA', 'unknown', False))
        self.assertTrue(row['supported'])
        self.assertTrue(row['software']['flux_configured'])
        self.assertNotIn('server', row)

    def test_configuration_rejects_remote_or_ambiguous_backends(self):
        with self.assertRaises(ValueError):
            LaserSnapshots([{'id': 'laser', 'backend': 'example.com:8790'}])
        with self.assertRaises(ValueError):
            LaserSnapshots([{'id': 'laser', 'backend': '127.0.0.1:8790', 'extra': True}])
        with self.assertRaises(ValueError):
            LaserSnapshots([{'id': str(i), 'backend': '127.0.0.1:8790'} for i in range(9)])

    def test_configuration_rejects_duplicate_backend_under_different_ids(self):
        with self.assertRaises(ValueError):
            LaserSnapshots([{'id': 'laser-one', 'backend': '127.0.0.1:8790'},
                            {'id': 'laser-two', 'backend': '127.0.0.1:8790'}])

    def test_observed_at_uses_local_time_even_for_untrusted_checked_at(self):
        _Station.payload['checked_at'] = float('nan')
        snapshots = LaserSnapshots([{'id': 'laser-one', 'backend': self.backend}],
                                    clock=lambda: 1700000001)
        result = snapshots.snapshot()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['items'][0]['observed_at'], 1700000001)

    def test_slow_http10_headers_are_bounded_by_read_deadline(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), _SlowHeadersHttp10Station)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            snapshots = LaserSnapshots([{'id': 'laser-one',
                                         'backend': f'127.0.0.1:{server.server_port}'}])
            started = time.monotonic()
            result = snapshots.snapshot()
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 1.5)
            self.assertEqual(result['status'], 'unknown')
        finally:
            server.shutdown()
            server.server_close()

    def test_slow_http10_body_is_bounded_by_read_deadline(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), _SlowHttp10Station)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            snapshots = LaserSnapshots([{'id': 'laser-one',
                                         'backend': f'127.0.0.1:{server.server_port}'}])
            started = time.monotonic()
            result = snapshots.snapshot()
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 1.5)
            self.assertEqual(result['status'], 'unknown')
            self.assertIsNone(result['items'][0]['observed_at'])
        finally:
            server.shutdown()
            server.server_close()

    def test_inflight_requests_block_new_executor_after_cache_expiry(self):
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        calls = []
        original_one = LaserSnapshots._one
        original_total = makerspace_laser._TOTAL_SECONDS
        clock_value = [0.0]

        def blocked(self, identifier, backend, observed_at):
            calls.append(1)
            entered.set()
            release.wait(2)
            finished.set()
            return self._unknown(identifier, None)

        makerspace_laser._TOTAL_SECONDS = 0.01
        LaserSnapshots._one = blocked
        try:
            snapshots = LaserSnapshots([{'id': 'laser-one', 'backend': self.backend}],
                                       monotonic=lambda: clock_value[0])
            first = snapshots.snapshot()
            self.assertEqual(first['status'], 'unknown')
            self.assertTrue(entered.wait(1))
            clock_value[0] = 3.0
            second = snapshots.snapshot()
            self.assertEqual(second['status'], 'unknown')
            self.assertEqual(len(calls), 1)
            release.set()
            self.assertTrue(finished.wait(1))
            clock_value[0] = 6.0
            snapshots.snapshot()
            self.assertEqual(len(calls), 2)
        finally:
            release.set()
            LaserSnapshots._one = original_one
            makerspace_laser._TOTAL_SECONDS = original_total

    def test_empty_configuration_is_not_configured(self):
        result = LaserSnapshots([], clock=lambda: 1700000001).snapshot()
        self.assertEqual((result['status'], result['items']), ('not_configured', []))

    def test_concurrent_read_does_not_block_on_another_snapshot(self):
        entered = threading.Event()
        release = threading.Event()
        original = LaserSnapshots._one

        def slow(self, identifier, backend, observed_at):
            entered.set(); release.wait(2); return self._unknown(identifier, None)

        LaserSnapshots._one = slow
        try:
            snapshots = LaserSnapshots([{'id': 'laser-one', 'backend': self.backend}])
            first = threading.Thread(target=snapshots.snapshot)
            first.start(); self.assertTrue(entered.wait(1))
            second = snapshots.snapshot()
            self.assertEqual(second['status'], 'unknown')
            release.set(); first.join(2)
        finally:
            LaserSnapshots._one = original


if __name__ == '__main__':
    unittest.main()
