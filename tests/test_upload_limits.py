import types
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.server import (Handler, Server, UploadBudget, UploadDeadlineExceeded,
                        UploadOverloaded)


class UploadLimitTests(unittest.TestCase):
    def test_budget_rejects_exhaustion_and_releases_after_operation_failure(self):
        budget = UploadBudget(10)
        handler = object.__new__(Handler)
        handler.server = types.SimpleNamespace(upload_budget=budget)

        reservation = budget.reserve(10)
        with self.assertRaises(UploadOverloaded):
            budget.reserve(1)
        reservation.release()

        with self.assertRaisesRegex(RuntimeError, 'parse failed'):
            handler._with_upload_budget(10, lambda: (_ for _ in ()).throw(RuntimeError('parse failed')))
        with budget.reserve(10):
            pass

    def test_budget_has_a_request_count_limit_for_tiny_uploads(self):
        budget = UploadBudget(100, max_uploads=2)
        first = budget.reserve(1)
        second = budget.reserve(1)
        with self.assertRaises(UploadOverloaded):
            budget.reserve(1)
        first.release()
        second.release()
        with budget.reserve(1):
            pass

    def test_slow_upload_has_total_deadline_even_when_each_read_progresses(self):
        now = [0.0]

        class SlowReader:
            def read(self, length):
                now[0] += 0.6
                return b'x'

        class Connection:
            def __init__(self):
                self.timeouts = []

            def settimeout(self, value):
                self.timeouts.append(value)

        handler = object.__new__(Handler)
        handler.rfile = SlowReader()
        handler.connection = Connection()
        with patch('app.server.time.monotonic', side_effect=lambda: now[0]), \
             patch('app.server.UPLOAD_TOTAL_SECONDS', 1):
            with self.assertRaises(UploadDeadlineExceeded):
                handler._read_upload(3)
        self.assertTrue(handler.connection.timeouts)
        self.assertLessEqual(max(handler.connection.timeouts), 1)

    def test_buffered_socket_read1_obeys_total_deadline_during_drip_feed(self):
        reader_socket, writer_socket = socket.socketpair()
        reader = reader_socket.makefile('rb')
        handler = object.__new__(Handler)
        handler.rfile = reader
        handler.connection = reader_socket
        writer_socket.sendall(b'a')

        def drip():
            try:
                for value in (b'b', b'c', b'd'):
                    threading.Event().wait(0.08)
                    writer_socket.sendall(value)
            except OSError:
                pass

        thread = threading.Thread(target=drip)
        thread.start()
        try:
            with patch('app.server.UPLOAD_TOTAL_SECONDS', 0.2):
                with self.assertRaises(UploadDeadlineExceeded):
                    handler._read_upload(4)
        finally:
            writer_socket.close()
            reader.close()
            reader_socket.close()
            thread.join(timeout=1)
        self.assertFalse(thread.is_alive())

    def test_final_chunk_must_also_arrive_before_the_total_deadline(self):
        handler = object.__new__(Handler)
        handler.rfile = types.SimpleNamespace(read=lambda length: b'x')
        handler.connection = types.SimpleNamespace(settimeout=lambda value: None)
        with patch('app.server.time.monotonic', side_effect=(0.0, 0.0, 2.0)), \
             patch('app.server.UPLOAD_TOTAL_SECONDS', 1):
            with self.assertRaises(UploadDeadlineExceeded):
                handler._read_upload(1)

    def test_idle_timeout_aborts_instead_of_retrying_a_broken_buffered_reader(self):
        def timeout(length):
            raise TimeoutError('socket inactivity timeout')
        handler = object.__new__(Handler)
        handler.rfile = types.SimpleNamespace(read=timeout)
        handler.connection = types.SimpleNamespace(settimeout=lambda value: None)
        with patch('app.server.time.monotonic', return_value=0.0):
            with self.assertRaises(UploadDeadlineExceeded):
                handler._read_upload(1)

    def test_status_endpoint_works_when_upload_budget_is_full(self):
        with tempfile.TemporaryDirectory() as folder:
            app = types.SimpleNamespace(
                demo=False,
                folder=Path(folder),
                auth=types.SimpleNamespace(session=lambda cookie: False, verify=lambda token, source: False),
                queue=types.SimpleNamespace(
                    snapshot=lambda: {'printers': [], 'jobs': []},
                    print_access=lambda source: {'allowed': True},
                ),
            )
            server = Server(('127.0.0.1', 0), Handler)
            server.app = app
            reservation = server.upload_budget.reserve(server.upload_budget.capacity)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                with urlopen(f'http://127.0.0.1:{server.server_port}/api/state', timeout=2) as response:
                    self.assertEqual(response.status, 200)
            finally:
                reservation.release()
                server.shutdown()
                thread.join(timeout=2)
                server.server_close()
            self.assertFalse(thread.is_alive())

    def test_upload_overload_returns_retryable_service_response(self):
        with tempfile.TemporaryDirectory() as folder:
            app = types.SimpleNamespace(
                demo=False,
                folder=Path(folder),
                auth=types.SimpleNamespace(session=lambda cookie: False, verify=lambda token, source: False),
                queue=types.SimpleNamespace(
                    submit=lambda *args, **kwargs: self.fail('overloaded upload reached queue'),
                ),
            )
            server = Server(('127.0.0.1', 0), Handler)
            server.app = app
            reservation = server.upload_budget.reserve(server.upload_budget.capacity)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                request = Request(
                    f'http://127.0.0.1:{server.server_port}/api/jobs',
                    data=b'x',
                    headers={'X-Filename': 'model.stl', 'X-Request-ID': 'upload-overload-0001'},
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=2)
                error = raised.exception
                try:
                    self.assertEqual(error.status, 503)
                    self.assertEqual(error.headers['Retry-After'], '1')
                    self.assertIn('Uploadkøen', error.read().decode('utf-8'))
                finally:
                    error.close()
            finally:
                reservation.release()
                server.shutdown()
                thread.join(timeout=2)
                server.server_close()
            self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()
