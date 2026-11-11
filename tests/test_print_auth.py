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


class SharedTeacherCodeTests(unittest.TestCase):
    """One teacher code, chosen in the control panel, opens print and laser too."""

    SHARED = 'Fælles kode 2026'

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'hub').mkdir()
        # The control panel's own class writes the file the services read.
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'linux-elevhub/server/elevhub'))
        self.addCleanup(sys.path.pop, 0)
        from tilstand import Laererkode
        self.panel = Laererkode(self.root / 'hub')
        self.panel.vaelg(self.SHARED)

    def test_services_find_the_control_panel_code_beside_their_data_folder(self):
        from app.teacher_auth import shared_teacher_code_file, shared_teacher_code_matches
        for folder in ('printvaerksted', 'designvaerksted'):
            file = shared_teacher_code_file(self.root / folder)
            self.assertEqual(file, (self.root / 'hub' / 'laerer.json').resolve())
            self.assertTrue(shared_teacher_code_matches(file, self.SHARED))
            self.assertFalse(shared_teacher_code_matches(file, 'en anden kode'))
        self.assertFalse(shared_teacher_code_matches(file, None))
        self.assertFalse(shared_teacher_code_matches(self.root / 'mangler.json', self.SHARED))
        for broken in ('{broken', '[]', json.dumps({'salt': 'x', 'iterationer': 1, 'hash': 'y'})):
            (self.root / 'broken.json').write_text(broken, encoding='utf-8')
            self.assertFalse(shared_teacher_code_matches(self.root / 'broken.json', self.SHARED))

    def test_print_login_accepts_shared_and_own_code_but_tools_only_the_own(self):
        from app.teacher_auth import shared_teacher_code_file
        auth = TeacherAuth('print-own-code', shared_file=shared_teacher_code_file(self.root / 'printvaerksted'))
        self.assertTrue(auth.session('print_teacher=' + auth.login(self.SHARED, 'source')))
        self.assertTrue(auth.session('print_teacher=' + auth.login('print-own-code', 'source')))
        self.assertEqual(dict(auth.failures), {})
        with self.assertRaises(PermissionError):
            auth.login('forkert kode', 'source')
        self.assertEqual(len(auth.failures['source']), 1)
        # Local launch/stop tools keep using the service's own code only.
        self.assertFalse(auth.verify(self.SHARED, 'tool'))
        # A newly chosen control panel code applies at once, without a restart.
        self.panel.nulstil()
        with self.assertRaises(PermissionError):
            auth.login(self.SHARED, 'other')
        self.panel.vaelg('Ny fælles kode')
        self.assertTrue(auth.login('Ny fælles kode', 'other'))

    def test_laser_queue_login_accepts_shared_and_own_code_over_http(self):
        from app.laser_server import create_server
        student = create_server(self.root / 'designvaerksted', port=0, code='laser-own-code')
        teacher = create_server(self.root / 'designvaerksted', port=0, inbox=student.inbox, surface='teacher')
        teacher.RequestHandlerClass.log_message = lambda *args: None
        threading.Thread(target=teacher.serve_forever, daemon=True).start()
        self.addCleanup(student.server_close)
        self.addCleanup(teacher.server_close)
        self.addCleanup(teacher.shutdown)

        def login(code):
            connection = http.client.HTTPConnection('127.0.0.1', teacher.server_port, timeout=10)
            try:
                connection.request('POST', '/api/laser/login', json.dumps({'code': code}),
                                   {'Content-Type': 'application/json'})
                response = connection.getresponse()
                response.read()
                return response.status, response.getheader('Set-Cookie') or ''
            finally:
                connection.close()

        for code in (self.SHARED, 'laser-own-code'):
            status, cookie = login(code)
            self.assertEqual(status, 200, code)
            self.assertIn('laser_teacher=', cookie)
        self.assertEqual(login('forkert kode')[0], 401)


if __name__ == '__main__':
    unittest.main()
