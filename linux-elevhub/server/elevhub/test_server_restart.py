"""Server reboot boundaries; all host operations are replaced with test doubles."""
import http.client
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import elevhub
from tilstand import Laererkode, Tilstand


class ServerRestartHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.code = Laererkode(Path(cls.temp.name))
        cls.code.vaelg('teacher-code-123')
        cls.server = elevhub.Server(('127.0.0.1', 0), elevhub.Laerer)
        cls.server.kode = cls.code
        cls.server.navn = 'restart-test'
        cls.server.apps = []
        cls.server.tilstand = Tilstand(cls.temp.name, [])
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def call(self, method, body=None, cookie=None, origin=True, content_type='application/json', **extra):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        headers = {'Content-Type': content_type, **extra}
        if cookie:
            headers['Cookie'] = cookie
        if origin:
            headers['Origin'] = (f'http://127.0.0.1:{self.server.server_port}'
                                 if origin is True else origin)
        try:
            connection.request(method, '/api/laerer/server-restart',
                               json.dumps(body) if body is not None else None, headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def login(self):
        token = self.code.log_ind('teacher-code-123', '127.0.0.1')
        return f'elevhub_laerer={token}'

    def test_status_and_request_require_teacher_session(self):
        for headers in ({}, {'Authorization': 'Bearer student-token'},
                        {'Authorization': 'Bearer enrollment-token'}, {'cookie': 'elevhub_laerer=invalid'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.call('GET', **headers)[0], 401)
                self.assertEqual(self.call('POST', {'confirmation': 'GENSTART HELE SERVEREN'}, **headers)[0], 401)

    def test_request_requires_same_origin_json_and_exact_confirmation(self):
        cookie = self.login()
        with patch('elevhub.server_restart_request') as request:
            self.assertEqual(self.call('POST', {}, cookie, origin=False)[0], 403)
            self.assertEqual(self.call('POST', {}, cookie, origin='https://other.example')[0], 403)
            self.assertEqual(self.call('POST', {}, cookie, content_type='text/plain')[0], 415)
            for body in ({}, {'confirmation': True}, {'confirmation': 'genstart hele serveren'},
                         {'confirmation': 'GENSTART HELE SERVEREN', 'command': 'shutdown'}, []):
                with self.subTest(body=body):
                    self.assertEqual(self.call('POST', body, cookie)[0], 400)
            request.assert_not_called()

    def test_status_is_filtered_and_accepted_request_returns_202(self):
        cookie = self.login()
        with patch('elevhub.server_restart_status', return_value={'available': True}):
            self.assertEqual(self.call('GET', cookie=cookie), (200, {'available': True}))
        with patch('elevhub.server_restart_request', return_value={'accepted': True}):
            self.assertEqual(self.call('POST', {'confirmation': 'GENSTART HELE SERVEREN'}, cookie),
                             (202, {'accepted': True}))

    def test_unavailable_request_is_not_reported_as_accepted(self):
        with patch('elevhub.server_restart_request', return_value={'accepted': False}):
            status, result = self.call('POST', {'confirmation': 'GENSTART HELE SERVEREN'}, self.login())
        self.assertEqual(status, 503)
        self.assertFalse(result['accepted'])


class ServerRestartHelperTest(unittest.TestCase):
    def module(self):
        import server_restart
        return server_restart

    def test_linux_or_missing_helper_never_launches_a_process(self):
        module = self.module()
        with patch.object(module.sys, 'platform', 'linux'), patch.object(module.subprocess, 'run') as run:
            self.assertEqual(module.status(), {'available': False})
            self.assertEqual(module.request(), {'accepted': False})
            run.assert_not_called()
        with patch.object(module.sys, 'platform', 'win32'), patch.object(module.Path, 'is_file', return_value=False), \
                patch.object(module.subprocess, 'run') as run:
            self.assertEqual(module.status(), {'available': False})
            self.assertEqual(module.request(), {'accepted': False})
            run.assert_not_called()

    def test_fixed_helper_command_and_only_explicit_json_success_are_accepted(self):
        module = self.module()
        with patch.object(module.sys, 'platform', 'win32'), patch.object(module.Path, 'is_file', return_value=True), \
                patch.dict(module.os.environ, {'ProgramFiles': r'C:\Program Files', 'SystemRoot': r'C:\Windows'}), \
                patch.object(module.subprocess, 'run') as run:
            run.return_value = subprocess.CompletedProcess([], 0, '{"available":true,"private":"secret"}', '')
            self.assertEqual(module.status(), {'available': True})
            args, kwargs = run.call_args
            self.assertEqual(args[0], [r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe',
                                      '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                      '-File', r'C:\Program Files\ElevhubServer\server\server-restart.ps1',
                                      '-Mode', 'Status'])
            self.assertFalse(kwargs.get('shell', False))
            self.assertLessEqual(kwargs['timeout'], 10)
            self.assertTrue(kwargs['capture_output'])
            run.return_value = subprocess.CompletedProcess([], 0, '{"accepted":true}', '')
            self.assertEqual(module.request(), {'accepted': True})
            self.assertEqual(run.call_args.args[0][-1], 'Request')
            for stdout, code in (('{"accepted":"true"}', 0), ('{"accepted":true}', 1),
                                 ('not json', 0), ('[]', 0), ('x' * 9000, 0)):
                run.return_value = subprocess.CompletedProcess([], code, stdout, 'private path')
                self.assertEqual(module.request(), {'accepted': False})

    def test_subprocess_failures_remain_unavailable_without_private_details(self):
        module = self.module()
        with patch.object(module.sys, 'platform', 'win32'), patch.object(module.Path, 'is_file', return_value=True), \
                patch.object(module.subprocess, 'run') as run:
            for failure in (OSError('private path'), subprocess.TimeoutExpired('private command', 5),
                            UnicodeError('private output')):
                run.side_effect = failure
                self.assertEqual(module.status(), {'available': False})
                self.assertEqual(module.request(), {'accepted': False})


if __name__ == '__main__':
    unittest.main()
