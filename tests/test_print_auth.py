"""Exercise authentication, expiry, throttling and Host/Origin over HTTP."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.server import Handler, Server, configured_print_hosts
from app.teacher_auth import TeacherAuth, LoginLimited


class PrintAuthTests(unittest.TestCase):
    def setUp(self):
        self.auth = TeacherAuth('local-test-code')
        self.server = Server(('127.0.0.1', 0), Handler)
        self.server.app = SimpleNamespace(auth=self.auth, public_settings=lambda: {'printers': []})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def request(self, route, body=None, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request('GET' if body is None else 'POST', route,
                               body=None if body is None else json.dumps(body), headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read())
        finally:
            connection.close()

    def test_login_cookie_authorizes_then_logout_revokes(self):
        self.assertEqual(self.request('/api/settings')[0], 401)
        self.assertEqual(self.request('/api/auth/login', {'code': 'wrong'})[0], 401)
        status, headers, body = self.request('/api/auth/login', {'code': 'local-test-code'})
        self.assertEqual(status, 200)
        self.assertNotIn('local-test-code', json.dumps(body) + str(headers))
        cookie = headers['Set-Cookie']
        for flag in ('HttpOnly', 'SameSite=Strict', 'Max-Age=28800'):
            self.assertIn(flag, cookie)
        headers = {'Cookie': cookie.split(';')[0]}
        self.assertEqual(self.request('/api/settings', headers=headers)[0], 200)
        self.assertTrue(self.request('/api/auth/status', headers=headers)[2]['teacher'])
        self.assertEqual(self.request('/api/auth/logout', {}, headers)[0], 200)
        self.assertEqual(self.request('/api/settings', headers=headers)[0], 401)

    def test_wrong_bearer_and_login_share_rate_limit(self):
        for attempt in range(10):
            if attempt % 2:
                response = self.request('/api/settings', headers={'Authorization': 'Bearer wrong'})
            else:
                response = self.request('/api/auth/login', {'code': 'wrong'})
            self.assertEqual(response[0], 401)
        status, headers, _ = self.request('/api/auth/login', {'code': 'local-test-code'})
        self.assertEqual(status, 429)
        self.assertEqual(headers['Retry-After'], '60')
        self.assertEqual(self.request('/api/settings', headers={'Authorization': 'Bearer local-test-code'})[0], 429)

    def test_untrusted_host_and_cross_origin_are_rejected(self):
        for route, body in (('/api/auth/login', {'code': 'local-test-code'}), ('/api/settings', None)):
            status, _, _ = self.request(route, body, {'Host': f'evil.invalid:{self.server.server_port}',
                                                     'Origin': f'http://evil.invalid:{self.server.server_port}',
                                                     'Authorization': 'Bearer local-test-code'})
            self.assertEqual(status, 403)
        for headers in ({'Origin': 'https://other.invalid'}, {'Sec-Fetch-Site': 'cross-site'}):
            self.assertEqual(self.request('/api/auth/login', {'code': 'local-test-code'}, headers)[0], 401)
        token = self.auth.login('local-test-code', 'test')
        headers = {'Cookie': 'print_teacher=' + token, 'Origin': 'https://other.invalid'}
        self.assertEqual(self.request('/api/auth/logout', {}, headers)[0], 401)
        self.assertTrue(self.auth.session(headers['Cookie']))

    def test_duplicate_host_is_rejected(self):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.putrequest('GET', '/api/auth/status', skip_host=True)
            connection.putheader('Host', f'127.0.0.1:{self.server.server_port}')
            connection.putheader('Host', 'evil.invalid')
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
        finally:
            connection.close()

    def test_explicit_school_address_accepts_only_configured_ports(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'print-hosts.json'
            config.write_text(json.dumps({'allowed_hosts': [
                'print.example.test', 'print.example.test:80']}), encoding='utf-8')
            probe = Server(('127.0.0.1', 0), Handler, host_config=config)
            try:
                self.server.allowed_authorities = probe.allowed_authorities
            finally:
                probe.server_close()
        for host in ('print.example.test', 'print.example.test:80'):
            status, _, _ = self.request('/api/auth/login', {'code': 'local-test-code'},
                                        {'Host': host, 'Origin': 'http://' + host})
            self.assertEqual(status, 200)
        for host in ('print.example.test:81', 'print.example.test.evil.invalid', 'evil.invalid:80'):
            self.assertEqual(self.request('/api/auth/status', headers={'Host': host})[0], 403)
        self.assertEqual(self.request('/api/auth/login', {'code': 'local-test-code'},
                         {'Host': 'print.example.test:80', 'Origin': 'http://evil.invalid'})[0], 401)

    def test_invalid_host_config_fails_before_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'print-hosts.json'
            self.assertEqual(configured_print_hosts(config), set())
            for host in ('*', '*.example.test', 'http://example.test', 'a/b', 'a@b',
                         'a:0', 'a:65536', 'a:', 'a b', '0.0.0.0', '[::]'):
                config.write_text(json.dumps({'allowed_hosts': [host]}), encoding='utf-8')
                with self.subTest(host=host), self.assertRaises(ValueError):
                    Server(('127.0.0.1', 0), Handler, host_config=config)


class TeacherAuthTests(unittest.TestCase):
    def test_expiry_and_throttle_recovery(self):
        auth = TeacherAuth('test-teacher-code')
        with patch('app.teacher_auth.time.monotonic', return_value=100):
            token = auth.login('test-teacher-code', 'source')
            for _ in range(10):
                self.assertFalse(auth.verify('wrong', 'source'))
            with self.assertRaises(LoginLimited):
                auth.verify('test-teacher-code', 'source')
            # Existing sessions are unaffected by someone guessing a code.
            self.assertTrue(auth.session('print_teacher=' + token))
        with patch('app.teacher_auth.time.monotonic', return_value=161):
            self.assertTrue(auth.verify('test-teacher-code', 'source'))
        with patch('app.teacher_auth.time.monotonic', return_value=100 + auth.lifetime):
            self.assertFalse(auth.session('print_teacher=' + token))


if __name__ == '__main__':
    unittest.main()
