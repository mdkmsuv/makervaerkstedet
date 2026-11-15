"""Exercise fixed HTTP routes and the real AF_UNIX client/server boundary."""
import http.client
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.svg_library import CacheSecurityError, Library, ORIGIN
from app.svg_library_service import UnixLibraryClient, create_server, MAX_RESPONSE_BYTES
from tests.test_svg_library_cache import HTML, SVG, ITEM_ID, online, offline


@unittest.skipUnless(hasattr(socket, 'AF_UNIX') and os.name != 'nt', 'Linux AF_UNIX service')
class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='svg-')
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'library.sock')
        self.library = Library(cache_dir=Path(self.temp.name) / 'cache')
        self.library.read = online
        self.server = create_server(self.path, self.library)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.client = UnixLibraryClient(self.path)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def raw(self, method, path):
        conn = http.client.HTTPConnection('localhost', timeout=3)
        conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.sock.settimeout(3)
        conn.sock.connect(self.path)
        try:
            conn.request(method, path)
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def test_client_returns_safe_metadata_and_real_sanitized_content(self):
        self.assertEqual(self.client.status()['cache_state'], 'empty')
        catalogue = self.client.catalogue_response()
        self.assertEqual(catalogue['items'][0]['id'], ITEM_ID)
        result = self.client.svg_response(ITEM_ID)
        self.assertIn('height="80mm"', result['svg'])
        self.assertEqual(result['version'], catalogue['version'])
        self.assertEqual(result['cache_state'], 'ready')
        self.assertNotIn('paths', catalogue)
        self.assertNotIn('cache_dir', catalogue)
        self.assertEqual(Path(self.path).stat().st_mode & 0o777, 0o666)

    def test_only_fixed_get_routes_are_available(self):
        for method, path in [('POST', '/catalogue'), ('DELETE', '/status'), ('HEAD', '/status'), ('GET', '/catalogue?url=https://evil.test'), ('GET', '/file/../secret'), ('GET', '/file/' + 'A' * 64), ('GET', 'https://evil.test/status'), ('GET', '/unknown')]:
            with self.subTest(method=method, path=path):
                self.assertIn(self.raw(method, path)[0], (400, 404, 405))

    def test_offline_file_miss_returns_safe_error(self):
        self.client.catalogue_response()
        self.library.read = offline
        status, payload = self.raw('GET', '/file/' + ITEM_ID)
        self.assertEqual(status, 503)
        self.assertNotIn('synthetic', payload.decode())
        self.assertEqual(self.client.status()['cache_state'], 'stale')

    def test_client_rejects_non_id_before_connecting(self):
        with self.assertRaises(ValueError):
            UnixLibraryClient('/not/present').svg_response('../secret')

    def test_refuses_replacing_existing_file_or_socket(self):
        for path in (self.path, str(Path(self.temp.name) / 'plain')):
            if path != self.path:
                Path(path).write_text('sentinel')
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                create_server(path, self.library)

    def test_failed_library_initialization_removes_its_new_socket(self):
        path = str(Path(self.temp.name) / 'failed.sock')
        with patch('app.svg_library_service.Library', side_effect=ValueError('invalid cache')):
            with self.assertRaises(ValueError):
                create_server(path)
        self.assertFalse(os.path.lexists(path))

    def test_default_cache_opens_dynamic_user_state_without_following_public_link(self):
        # Model systemd's DynamicUser + StateDirectory layout without touching
        # host state. The service must open the private directory directly.
        root = Path(self.temp.name) / 'system-root'
        private = root / 'var/lib/private/elevhub-svg-library'
        private.mkdir(parents=True)
        public = root / 'var/lib/elevhub-svg-library'
        public.symlink_to('private/elevhub-svg-library', target_is_directory=True)
        seed = Library(cache_dir=private)
        seed.read = online
        expected_svg = seed.svg(ITEM_ID)
        with self.assertRaises(CacheSecurityError):
            Library(cache_dir=public)

        abspath = os.path.abspath

        def in_system_root(path):
            absolute = abspath(path)
            return str(root) + absolute if absolute.startswith('/var/lib/') else absolute

        path = str(Path(self.temp.name) / 'default.sock')
        with patch('app.svg_library.os.path.abspath', side_effect=in_system_root):
            with create_server(path) as server:
                server.library.read = offline
                self.assertEqual(server.library.status()['cache_state'], 'stale')
                self.assertEqual(server.library.svg(ITEM_ID), expected_svg)
        self.assertTrue(public.is_symlink())
        self.assertFalse(os.path.lexists(path))

    def test_server_refuses_oversized_response(self):
        self.library.svg = lambda item_id: 'x' * MAX_RESPONSE_BYTES
        status, payload = self.raw('GET', '/file/' + ITEM_ID)
        self.assertEqual(status, 503)
        self.assertLess(len(payload), 1024)


@unittest.skipUnless(hasattr(socket, 'AF_UNIX') and os.name != 'nt', 'Linux AF_UNIX service')
class ClientResponseTests(unittest.TestCase):
    def reply(self, raw):
        # A hostile local peer exercises the real parser and bounded transport.
        with tempfile.TemporaryDirectory(prefix='svg-peer-') as directory:
            path = str(Path(directory) / 'peer.sock')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(path)
                listener.listen(1)
                listener.settimeout(3)
                def serve():
                    conn, _ = listener.accept()
                    with conn:
                        conn.settimeout(3)
                        conn.recv(4096)
                        try:
                            conn.sendall(raw)
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                thread = threading.Thread(target=serve, daemon=True)
                thread.start()
                try:
                    return UnixLibraryClient(path).catalogue_response()
                finally:
                    thread.join(3)

    def test_client_rejects_redirect_oversize_truncation_and_malformed_data(self):
        cases = [
            b'HTTP/1.0 302 Found\r\nLocation: https://evil.test/\r\nContent-Length: 0\r\n\r\n',
            b'HTTP/1.0 200 OK\r\nContent-Length: 99999999\r\n\r\n',
            b'HTTP/1.0 200 OK\r\nContent-Length: 20\r\n\r\n{}',
            b'HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\n[]',
            b'HTTP/1.0 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n',
        ]
        for raw in cases:
            with self.subTest(response=raw[:60]), self.assertRaises(ValueError):
                self.reply(raw)

    def test_client_strips_unknown_fields_from_valid_response(self):
        value = {'enabled': True, 'version': 7, 'source_url': ORIGIN + '/svg-bibliotek/', 'cache_state': 'stale', 'last_success': None, 'items': [{'id': ITEM_ID, 'title': 'Cat', 'category': 'Animals', 'path': '/private'}], 'cache_dir': '/private'}
        body = json.dumps(value).encode()
        response = self.reply(b'HTTP/1.0 200 OK\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body)
        self.assertNotIn('cache_dir', response)
        self.assertNotIn('path', response['items'][0])


if __name__ == '__main__':
    unittest.main()
