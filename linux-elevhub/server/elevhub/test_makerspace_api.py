"""HTTP boundary tests for the teacher-only makerspace facade."""

import hashlib
from copy import deepcopy
import http.client
import json
import secrets
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from elevhub import Hub, Laerer, Server
from makerspace_api import MakerspaceService
from sikkerhed import Enhedsregister, tls_kontekst
from system_overview import PrinterSnapshot
from tilstand import Laererkode, Tilstand


OPENSSL = shutil.which('openssl')
if not OPENSSL:
    candidate = Path('C:/Program Files/Git/usr/bin/openssl.exe')
    OPENSSL = str(candidate) if candidate.is_file() else None


@unittest.skipUnless(OPENSSL, 'OpenSSL is needed for temporary test certificates')
class MakerspaceHTTPTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name)
        cert, key = path / 'cert.pem', path / 'key.pem'
        subprocess.run([OPENSSL, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=localhost', '-keyout', str(key), '-out', str(cert),
                        '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1'],
                       capture_output=True, check=True, timeout=30)
        tls = tls_kontekst({'tls': {'certifikat': str(cert), 'noegle': str(key)}}, path)
        self.client_tls = ssl.create_default_context(cafile=str(cert))
        self.tokens = {name: {role: secrets.token_urlsafe(32) for role in ('root', 'ui')}
                       for name in ('pc1', 'pc2')}
        self.register_path = path / 'register.json'
        self.register_path.write_text(json.dumps({'enheder': {
            name: {'token_sha256': hashlib.sha256(values['root'].encode()).hexdigest(),
                   'ui_token_sha256': hashlib.sha256(values['ui'].encode()).hexdigest()}
            for name, values in self.tokens.items()}}))
        register = Enhedsregister(self.register_path)
        self.apps = [{'id': 'internet', 'navn': 'Internet', 'type': 'browser'}]
        state = Tilstand(path / 'state', self.apps, ur=lambda: 1000.0)
        code = Laererkode(path)
        self.teacher_code = 'teacher-code-123'
        code.vaelg(self.teacher_code)
        self.hub = Server(('127.0.0.1', 0), Hub, tls=tls)
        self.hub.navn, self.hub.enheder, self.hub.tilstand = 'hub', register, state
        self.teacher = Server(('127.0.0.1', 0), Laerer, tls=tls)
        self.teacher.navn, self.teacher.enheder, self.teacher.tilstand = 'laerer', register, state
        self.teacher.apps, self.teacher.kode = self.apps, code
        self.teacher.printer_snapshot = PrinterSnapshot(self.apps)
        overview = lambda: {'printers': {'status': 'ok', 'checked_at': 1000,
                                         'items': []}, 'computers': [],
                            'computers_status': 'ok'}
        self.teacher.makerspace = MakerspaceService(path / 'makerspace.sqlite3', overview=overview)
        for server in (self.hub, self.teacher):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(self.close_servers)

    def close_servers(self):
        for server in (self.hub, self.teacher):
            server.shutdown()
            server.server_close()
        self.temp.cleanup()

    def call(self, server, method, path, body=None, headers=None, raw=False):
        connection = http.client.HTTPSConnection('127.0.0.1', server.server_address[1],
                                                 context=self.client_tls, timeout=3)
        try:
            payload = body if raw else (json.dumps(body).encode() if body is not None else None)
            connection.request(method, path, payload, headers or {})
            reply = connection.getresponse()
            data = reply.read()
            try:
                data = json.loads(data)
            except (ValueError, UnicodeDecodeError):
                pass
            return reply.status, dict(reply.getheaders()), data
        finally:
            connection.close()

    def login(self):
        host = f'127.0.0.1:{self.teacher.server_address[1]}'
        status, headers, _ = self.call(self.teacher, 'POST', '/api/laerer/login',
                                       {'kode': self.teacher_code},
                                       {'Content-Type': 'application/json',
                                        'Origin': 'https://' + host})
        self.assertEqual(status, 200)
        return headers['Set-Cookie'].split(';', 1)[0]

    def get(self, path, cookie=None, token=None):
        headers = {}
        if cookie:
            headers['Cookie'] = cookie
        if token:
            headers['Authorization'] = 'Bearer ' + token
        return self.call(self.teacher, 'GET', path, headers=headers)

    def post(self, path, body, cookie=None, token=None, origin=True):
        headers = {'Content-Type': 'application/json'}
        if cookie:
            headers['Cookie'] = cookie
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if origin:
            headers['Origin'] = 'https://127.0.0.1:' + str(self.teacher.server_address[1])
        return self.call(self.teacher, 'POST', path, body, headers)

    @staticmethod
    def command(action, params, expected=0, request_id=None):
        return {'action': action, 'params': params,
                'expected_version': expected, 'request_id': request_id or uuid_hex()}

    def test_auth_state_actions_audit_and_zero_pupil_computers(self):
        self.assertEqual(self.get('/api/makerspace/state')[0], 401)
        self.assertEqual(self.get('/api/makerspace/actions')[0], 401)
        cookie = self.login()
        status, _, state = self.get('/api/makerspace/state', cookie)
        self.assertEqual(status, 200)
        self.assertEqual(state['computers'], [])
        self.assertEqual(state['computers_status'], 'ok')
        self.assertIn('manual', state['templates'])
        self.assertIn('Klostermarksskolen', state['credits'])
        self.assertEqual(self.get('/api/makerspace/actions', cookie)[0], 200)
        self.assertEqual(self.get('/api/makerspace/audit', cookie)[0], 200)
        self.assertEqual(self.get('/api/makerspace/agents', cookie)[0], 404)

    def test_no_pupil_configuration_and_credit_survives_other_school_identity(self):
        self.teacher.enheder = None
        self.teacher.apps = []
        self.teacher.tilstand = Tilstand(Path(self.temp.name) / 'hub', [])
        self.teacher.makerspace = MakerspaceService(Path(self.temp.name) / 'independent.sqlite3')
        cookie = self.login()
        state = self.get('/api/makerspace/state', cookie)
        self.assertEqual(state[0], 200)
        self.assertEqual(state[2]['computers'], [])
        self.assertEqual(self.post('/api/makerspace/commands', self.command('machine.create',
            {'name': 'Uden elevcomputer', 'location': '', 'enabled': False, 'workflow_id': None}), cookie)[0], 200)
        (Path(self.temp.name) / 'school.json').write_text(json.dumps(
            {'school_name': '<En anden skole>', 'theme': 'green'}), encoding='utf-8')
        for route in ('/makerspace', '/om'):
            page = self.get(route)[2].decode('utf-8')
            self.assertIn('Kreditering: Klostermarksskolen.', page)
            self.assertIn('&lt;En anden skole&gt;', page)
        self.assertIn(b'--brand:#28734e', self.get('/makerspace.css')[2])

    def test_commands_work_cookie_origin_idempotency_and_stale_conflict(self):
        cookie = self.login()
        definition = self.get('/api/makerspace/state', cookie)[2]['templates']['manual']
        request_id = uuid_hex()
        command = self.command('workflow.create', {'definition': definition}, request_id=request_id)
        first = self.post('/api/makerspace/commands', command, cookie)
        self.assertEqual(first[0], 200)
        self.assertEqual(first[2], self.post('/api/makerspace/commands', command, cookie)[2])
        workflow_id = first[2]['result']['id']
        published = self.post('/api/makerspace/commands', self.command(
            'workflow.publish', {'id': workflow_id}, expected=1), cookie)
        self.assertEqual(published[0], 200)
        machine = self.post('/api/makerspace/commands', self.command(
            'machine.create', {'name': 'Bord', 'location': '', 'enabled': True,
                               'workflow_id': workflow_id}), cookie)
        self.assertEqual(machine[0], 200)
        job = self.post('/api/makerspace/commands', self.command(
            'job.create', {'machine_id': machine[2]['result']['id'], 'title': 'Opgave'}), cookie)
        self.assertEqual(job[0], 200)
        stale = self.command('workflow.publish', {'id': workflow_id}, expected=0)
        self.assertEqual(self.post('/api/makerspace/commands', stale, cookie)[0], 409)
        self.assertEqual(self.post('/api/makerspace/workflows/validate',
                                   {'definition': definition}, cookie)[0], 200)

    def test_cookie_mutation_requires_origin_and_wrong_origin_is_forbidden(self):
        cookie = self.login()
        body = self.command('machine.create', {'name': 'Bord', 'location': '',
                                                'enabled': False, 'workflow_id': None})
        self.assertEqual(self.post('/api/makerspace/commands', body, cookie, origin=False)[0], 403)
        headers = {'Content-Type': 'application/json', 'Cookie': cookie,
                   'Origin': 'https://evil.example'}
        self.assertEqual(self.call(self.teacher, 'POST', '/api/makerspace/commands',
                                   body, headers)[0], 403)

    def test_workflow_validation_explains_fix_without_leaking_internal_errors(self):
        cookie = self.login()
        definition = self.get('/api/makerspace/state', cookie)[2]['templates']['manual']
        definition['steps'][-1]['role'] = 'operator'
        for route, body in (
            ('/api/makerspace/workflows/validate', {'definition': definition}),
            ('/api/makerspace/commands', self.command('workflow.create', {'definition': definition})),
        ):
            with self.subTest(route=route):
                status, _, data = self.post(route, body, cookie)
                self.assertEqual(status, 400)
                self.assertEqual(data['error'], "Trinnet med handlingen 'Ryd' skal have læreren som ansvarlig.")
        self.assertEqual(self.get('/api/makerspace/state', cookie)[2]['workflows'], [])
        with patch.object(self.teacher.makerspace, 'execute', side_effect=ValueError('private/internal/path')):
            status, _, data = self.post('/api/makerspace/commands', self.command(
                'workflow.create', {'definition': definition}), cookie)
        self.assertEqual(status, 400)
        self.assertEqual(data['error'], 'Ugyldig ændring. Kontrollér felter og workflowets trin.')

    def test_bearer_and_agent_routes_are_rejected(self):
        cookie = self.login()
        old_token = 'mksp_' + 'x' * 43
        # A real historical credential row must not reactivate the removed
        # authentication path. Preserve it as data; never consult it for login.
        with self.teacher.makerspace.store.connection() as db:
            db.execute('''CREATE TABLE agents (id TEXT PRIMARY KEY, name TEXT,
                          scopes TEXT, token_hash TEXT, created_at REAL, revoked INTEGER)''')
            db.execute('INSERT INTO agents VALUES(?,?,?,?,?,?)',
                       ('a' * 32, 'Historisk', '["makerspace:read","machines:write"]',
                        hashlib.sha256(old_token.encode()).hexdigest(), 1, 0))
        self.assertEqual(self.get('/api/makerspace/state', token=old_token)[0], 403)
        self.assertEqual(self.get('/api/makerspace/state', cookie=cookie, token=old_token)[0], 403)
        command = self.command('machine.create', {'name': 'Afvises', 'location': '',
                               'enabled': False, 'workflow_id': None})
        self.assertEqual(self.post('/api/makerspace/commands', command, token=old_token)[0], 403)
        self.assertEqual(self.post('/api/makerspace/commands', command, cookie=cookie, token=old_token)[0], 403)
        self.assertEqual(self.get('/api/makerspace/state', cookie=cookie)[2]['machines'], [])
        self.assertEqual(self.get('/api/makerspace/agents', cookie)[0], 404)
        self.assertEqual(self.post('/api/makerspace/agents/issue', {}, cookie)[0], 404)
        self.assertEqual(self.post('/api/makerspace/agents/revoke', {}, cookie)[0], 404)
        self.assertEqual(self.get('/makerspace-agents.js', cookie)[0], 404)
        self.assertNotIn(b'Agentadgang', self.get('/makerspace', cookie)[2])

    def test_root_and_ui_tokens_and_pupil_hub_cannot_access(self):
        for role in ('root', 'ui'):
            self.assertEqual(self.get('/api/makerspace/state', token=self.tokens['pc1'][role])[0], 403)
        self.assertEqual(self.call(self.hub, 'GET', '/api/makerspace/state')[0], 404)

    def test_static_assets_and_malformed_body(self):
        for path in ('/makerspace', '/makerspace.js', '/makerspace-printers.js', '/makerspace.css'):
            status, _, body = self.call(self.teacher, 'GET', path)
            self.assertEqual(status, 200)
            self.assertTrue(body)
        status, _, body = self.call(self.teacher, 'POST', '/api/makerspace/commands',
                                    b'{broken', {'Content-Type': 'application/json'}, raw=True)
        self.assertEqual(status, 400)
        self.assertIsInstance(body, dict)


def uuid_hex():
    return secrets.token_hex(16)


class MakerspaceServiceTest(unittest.TestCase):
    def test_laser_configuration_and_cached_observations_remain_distinct(self):
        observation = {'id': 'flux:laser-a', 'source_id': 'laser-a', 'kind': 'laser',
                       'adapter': 'flux', 'name': 'Lokalt navn', 'model': 'fbb2',
                       'status': 'unknown', 'online': False, 'observed_at': 123}
        cached = {'status': 'ok', 'items': [observation]}
        before = deepcopy(cached)
        stations = [{'id': name, 'name': 'Centralt navn', 'model': 'fhexa1',
                     'enabled': False, 'location': '', 'version': 1, 'workflow': None}
                    for name in ('laser-a', 'laser-b')]
        with tempfile.TemporaryDirectory() as folder:
            service = MakerspaceService(Path(folder) / 'makerspace.sqlite3',
                laser_stations=SimpleNamespace(snapshot=lambda: cached),
                laser_administration=SimpleNamespace(snapshot=lambda: {
                    'status': 'ok', 'stations': stations, 'jobs': []}))
            with self.assertRaises(PermissionError):
                service.snapshot({'id': 'agent:' + 'a' * 32, 'role': 'agent',
                                  'scopes': ['makerspace:read']})
            with self.assertRaises(PermissionError):
                service.snapshot({'id': 'agent:' + 'a' * 32, 'role': 'teacher',
                                  'scopes': ['makerspace:read']})
            with self.assertRaises(PermissionError):
                service.snapshot({'id': 'teacher:' + 'a' * 32, 'role': 'agent',
                                  'scopes': ['makerspace:read']})
            snapshot = service.snapshot({'id': 'teacher:' + 'a' * 32, 'role': 'teacher',
                                         'scopes': ['makerspace:read']})
            inventory = {row['id']: row for row in snapshot['inventory']}
            self.assertEqual(set(inventory), {'flux:laser-a', 'flux:laser-b'})
            for row in inventory.values():
                self.assertEqual((row['kind'], row['adapter']), ('laser', 'flux'))
                self.assertEqual(row['name'], 'Centralt navn')
                self.assertEqual(row['model'], 'fhexa1')
                self.assertEqual(row['status'], 'unknown')
                self.assertFalse(row['online'])
            self.assertEqual(inventory['flux:laser-a']['observed_model'], 'fbb2')
            self.assertIsNone(inventory['flux:laser-b']['observed_at'])
            self.assertEqual(snapshot['computers'], [])
            self.assertEqual(cached, before)
            self.assertEqual(snapshot['observed_machines'], [observation])

    def test_malformed_action_is_a_validation_error(self):
        with tempfile.TemporaryDirectory() as folder:
            service = MakerspaceService(Path(folder) / 'makerspace.sqlite3')
            for action in (None, [], {}, 4):
                with self.subTest(action=action), self.assertRaises(ValueError):
                    service.execute({'id': 'teacher:' + '1' * 32, 'scopes': []},
                        {'action': action, 'params': {}, 'expected_version': 0,
                         'request_id': uuid_hex()})


if __name__ == '__main__':
    unittest.main()
