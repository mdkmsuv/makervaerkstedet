import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest

from makerspace_laser_admin import LaserAdministration
from makerspace_store import MakerspaceStore
from makerspace_workflows import templates


class Handler(BaseHTTPRequestHandler):
    payload = {}
    received = []
    token = None
    status = 200

    def do_GET(self):
        self.send_response(type(self).status); self.send_header('Content-Type', 'application/json'); self.end_headers()
        self.wfile.write(json.dumps(type(self).payload).encode())

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        body = json.loads(self.rfile.read(length))
        type(self).received.append(body)
        self.send_response(type(self).status); self.send_header('Content-Type', 'application/json'); self.end_headers()
        envelope = body['envelope']
        result = {'id': envelope['params']['id'], 'name': 'Station', 'location': 'Lab', 'model': 'fbb2',
                  'enabled': False, 'version': envelope['expected_version'] + 1, 'workflow': None}
        self.wfile.write(json.dumps({'ok': True, 'action': envelope['action'], 'result': result}).encode())

    def log_message(self, *args):
        pass


class LaserAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        key = 'mkbridge_' + 'a' * 43
        (self.root / 'makerspace-laser.key').write_text(key, encoding='ascii')
        Handler.received = []
        Handler.status = 200
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.admin = LaserAdministration({'backend': f'127.0.0.1:{self.server.server_port}',
                                          'key_file': 'makerspace-laser.key'}, base=self.root)

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2); self.temp.cleanup()

    def test_snapshot_and_command_whitelist_secrets(self):
        Handler.payload = {'stations': [{'id': 'laser-a', 'name': 'A', 'location': 'Lab', 'model': 'fbb2',
                         'enabled': True, 'version': 3, 'workflow': None, 'secret': 'drop'}], 'jobs': []}
        snapshot = self.admin.snapshot()
        self.assertEqual(snapshot['stations'][0]['id'], 'laser-a'); self.assertNotIn('secret', json.dumps(snapshot))
        envelope = {'action': 'laser.station.update', 'params': {'id': 'laser-a', 'name': 'A', 'location': 'Lab', 'model': 'fbb2', 'enabled': False}, 'expected_version': 3, 'request_id': 'a' * 32}
        result = self.admin.execute('teacher:' + 'b' * 32, envelope)
        self.assertEqual(result['result']['version'], 4)
        self.assertEqual(Handler.received[0]['envelope'], envelope)

    def test_403_is_a_permanent_permission_error(self):
        Handler.status = 403
        with self.assertRaises(PermissionError):
            self.admin.snapshot()
        envelope = {'action': 'laser.station.update', 'params': {'id': 'laser-a', 'name': 'A', 'location': 'Lab', 'model': 'fbb2', 'enabled': False}, 'expected_version': 3, 'request_id': 'a' * 32}
        with self.assertRaises(PermissionError):
            self.admin.execute('teacher:' + 'b' * 32, envelope)

    def test_malformed_station_and_job_payloads_are_rejected(self):
        Handler.payload = {'stations': [{'id': 7, 'name': 'A', 'location': 'Lab', 'model': 'fbb2', 'enabled': True, 'version': 1, 'workflow': None}], 'jobs': []}
        self.assertEqual(self.admin.snapshot()['status'], 'unavailable')
        Handler.payload = {'stations': [], 'jobs': [{'id': 'bad', 'project': 'p', 'status': 'ready', 'station_id': 'laser-a', 'version': 0, 'workflow_id': None, 'workflow_revision': None, 'workflow_name': None, 'step': None, 'production_authorized': False, 'physical_done': False, 'handoff_claimed': False}]}
        self.assertEqual(self.admin.snapshot()['status'], 'unavailable')

    def test_store_resolves_published_laser_revision_before_backend(self):
        store = MakerspaceStore(self.root / 'hub.sqlite3')
        actor = {'id': 'teacher:' + 'c' * 32, 'role': 'teacher', 'scopes': ['workflows:write', 'machines:write']}
        workflow = store.execute(actor, {'action': 'workflow.create', 'params': {'definition': templates()['laser']}, 'expected_version': 0, 'request_id': '1' * 32})['result']
        store.execute(actor, {'action': 'workflow.publish', 'params': {'id': workflow['id']}, 'expected_version': 1, 'request_id': '2' * 32})
        envelope = {'action': 'laser.workflow.assign', 'params': {'id': 'laser-a', 'workflow_id': workflow['id'], 'revision': 1}, 'expected_version': 1, 'request_id': '3' * 32}
        result = store.execute(actor, envelope, external=self.admin)
        self.assertEqual(result['result']['id'], 'laser-a')
        sent = Handler.received[-1]['envelope']
        self.assertEqual(sent['params']['workflow']['revision'], 1)
        self.assertEqual(sent['params']['workflow']['definition']['kind'], 'laser')


if __name__ == '__main__':
    unittest.main()
