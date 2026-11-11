import hashlib
import http.client
import json
import threading
import unittest
from pathlib import Path

from app.server import Handler, Server
from app.teacher_auth import TeacherAuth


class FakeQueue:
    def __init__(self):
        self.payload = b'solid model\nendsolid model\n'
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.calls = []

    def library_list(self):
        return [{'digest': self.digest, 'filename': 'model.stl', 'colour': None,
                 'quality': 'standard', 'meta': {}, 'created': 1, 'size': len(self.payload)}]

    def library_download(self, digest):
        if digest != self.digest:
            raise ValueError('Modellen blev ikke fundet.')
        return self.payload

    def library_page(self, page=1, page_size=25, query=''):
        return {'files': self.library_list(), 'page': page, 'page_size': page_size,
                'has_more': False, 'total': 1, 'storage': {}, 'policy': {}}

    def library_download_entry(self, digest):
        return self.library_download(digest), 'model.stl'

    def library_reprint(self, digest, request_id, *, source='teacher', colour=None, quality=None):
        self.calls.append((digest, request_id, source, colour, quality))
        return {'id': 'a' * 32, 'status': 'queued'}


class FakeApp:
    token = 'secret'
    auth = TeacherAuth(token)
    folder = Path('.')
    queue = FakeQueue()


def request(method, path, body=None, token=None):
    conn = http.client.HTTPConnection('127.0.0.1', FakeApp.server.server_address[1])
    headers = {}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    if body is not None:
        raw = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
        headers['Content-Length'] = str(len(raw))
    else:
        raw = None
    conn.request(method, path, raw, headers)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, response.getheaders(), data


class LibraryHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FakeApp.server = Server(('127.0.0.1', 0), Handler)
        FakeApp.server.app = FakeApp()
        threading.Thread(target=FakeApp.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        FakeApp.server.shutdown()
        FakeApp.server.server_close()

    def test_library_requires_teacher_auth(self):
        status, _, _ = request('GET', '/api/library')
        self.assertEqual(status, 401)

    def test_library_list_and_download_are_teacher_only(self):
        status, _, body = request('GET', '/api/library', token='secret')
        self.assertEqual(status, 200)
        digest = json.loads(body)['files'][0]['digest']
        status, headers, body = request('GET', f'/api/library/{digest}/download', token='secret')
        self.assertEqual(status, 200)
        self.assertEqual(body, FakeApp.queue.payload)
        self.assertIn("filename*=UTF-8''model.stl", dict(headers)['Content-Disposition'])

    def test_library_rejects_traversal_and_preserves_request_id(self):
        status, _, _ = request('GET', '/api/library/../download', token='secret')
        self.assertIn(status, (400, 404))
        digest = FakeApp.queue.digest
        payload = {'request_id': 'request-id-123456', 'quality': 'beautiful', 'colour': '#E84142'}
        status, _, body = request('POST', f'/api/library/{digest}/reprint', payload, token='secret')
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(body)['request_id'], payload['request_id'])
        self.assertEqual(FakeApp.queue.calls[-1][1], payload['request_id'])
