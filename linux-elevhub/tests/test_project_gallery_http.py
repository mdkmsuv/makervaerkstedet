"""Exercise the thumbnail endpoint through the actual loopback HTTP boundary."""
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'projekter'))
from lokal import make_server
from projektarkiv import Projektarkiv


class GalleryHTTP(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Projektarkiv(Path(self.temp.name) / '.elevhub')
        self.project = '1' * 32
        self.params = dict(project=self.project, name='Skilt', folder='5A', kind='laser',
                           filename='skilt.svg', expected=0)
        self.svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30 30"><rect width="20" height="20"/></svg>'
        self.store.save(self.svg, **self.params)
        self.server = make_server(0, self.store)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()
        self.temp.cleanup()

    def request(self, path, headers=None, method='GET'):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, '/_elevhub/projekter/api/' + path, headers=headers or {})
            result = connection.getresponse()
            return result.status, result.read(), dict(result.getheaders())
        finally:
            connection.close()

    def test_preview_security_and_exact_revision(self):
        query = 'preview?id=' + self.project + '&revision=1'
        status, data, headers = self.request(query)
        self.assertEqual(status, 200)
        self.assertIn('image/svg+xml', headers['Content-Type'])
        self.assertIn('sandbox', headers['Content-Security-Policy'])
        self.assertIn("default-src 'none'", headers['Content-Security-Policy'])
        self.assertIn(b'20', data)
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertEqual(self.request(query, {'Host': 'foreign.example'})[0], 403)
        self.assertEqual(self.request(query, {'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.request(query, method='HEAD')[1], b'')
        self.store.save(self.svg.replace(b'20', b'25'), **{**self.params, 'expected': 1})
        self.assertEqual(self.request(query)[1], data)
        self.assertNotEqual(self.request(query.replace('revision=1', 'revision=2'))[1], data)
        self.assertEqual(self.request(query.replace('revision=1', 'revision=0'))[0], 400)
        self.assertEqual(self.request(query.replace('revision=1', 'revision=8'))[0], 404)
        self.assertEqual(self.request('preview?id=../../x')[0], 400)

    def test_unsupported_preview_and_deleted_cache(self):
        self.assertEqual(self.request('preview?id=' + self.project)[0], 200)
        with self.store.connect() as db:
            db.execute('DELETE FROM versions WHERE project=?', (self.project,))
            db.execute('DELETE FROM projects WHERE id=?', (self.project,))
        self.assertEqual(self.request('preview?id=' + self.project)[0], 404)
        # Reimporting a path may reuse its id and start a fresh version sequence.
        self.store.save(self.svg.replace(b'20', b'17'), **self.params)
        self.assertIn(b'17', self.request('preview?id=' + self.project)[1])
        other = '2' * 32
        self.store.save(b'Mine noter', **{**self.params, 'project': other, 'kind': 'andet', 'filename': 'noter.txt'})
        self.assertEqual(self.request('preview?id=' + other)[0], 204)

    def test_workshop_lists_follow_actual_format_and_keep_main_overview(self):
        foil = '3' * 32
        # Old projects may be labelled Laser while their actual SVG is Folie.
        self.store.save(self.svg.replace(b'<svg ', b'<svg data-workshop="vinyl" '),
                        **{**self.params, 'project': foil, 'name': 'Mærke'})
        cube = '4' * 32
        self.store.save(b'solid fixture\nendsolid fixture',
                        **{**self.params, 'project': cube, 'filename': 'cube.stl', 'kind': 'cadoodle'})
        for workshop, expected in [('laser', [self.project]), ('folie', [foil]), ('cadoodle', [cube]),
                                   ('scratch', []), ('microbit', []), ('datatraener', []), ('kredsloeb', [])]:
            with self.subTest(workshop=workshop):
                status, data, _ = self.request('list?workshop=' + workshop)
                self.assertEqual(status, 200)
                self.assertEqual([p['id'] for p in json.loads(data)['projects']], expected)
        self.assertEqual(len(json.loads(self.request('list')[1])['projects']), 3)
        self.assertEqual(json.loads(self.request('list?workshop=laser&q=M%C3%A6rke')[1])['projects'], [])
        self.assertEqual(self.request('list?workshop=unknown')[0], 400)
        self.assertEqual(self.request('list?workshop=laser&workshop=folie')[0], 400)


if __name__ == '__main__':
    unittest.main()
