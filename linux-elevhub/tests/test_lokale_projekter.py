import http.client
import json
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'projekter'))
from lokal import make_server, Navigation
from projektarkiv import Projektarkiv, Konflikt
from scanner import Scanner


class LocalProjects(unittest.TestCase):
    def test_deployed_package_contains_runtime_modules_and_project_ui(self):
        package = Path(__file__).resolve().parents[1] / 'projekter'
        for name in ('lokal.py', 'projekt_http.py', 'projektarkiv.py', 'scanner.py',
                     'web/projekter.html', 'web/projekter.js', 'web/projekt-bridge.js'):
            with self.subTest(name=name):
                self.assertTrue((package / name).is_file())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.projects = Path(self.temp.name)/'Projekter'
        self.store = Projektarkiv(self.projects/'.elevhub')
        self.project = uuid.uuid4().hex
        self.params = dict(project=self.project, name='Lampe', folder='5A · gruppe 3',
                           kind='laser', filename='Lampe.projekt.svg', expected=0)

    def tearDown(self):
        self.temp.cleanup()

    def test_history_survives_restart_and_stale_writer(self):
        self.store.save(b'first editable file', **self.params)
        self.params['expected'] = 1
        self.store.save(b'second editable file', **self.params)
        with self.assertRaises(Konflikt):
            self.store.save(b'stale tab', **self.params)
        reopened = Projektarkiv(self.store.root)
        self.assertEqual(reopened.read(self.project, 1)[1], b'first editable file')
        self.assertEqual(reopened.read(self.project)[1], b'second editable file')
        self.assertEqual(reopened.list('gruppe 3')[0]['revision'], 2)
        # Retrying an uncertain completed save must not produce duplicate versions.
        self.assertEqual(reopened.save(b'second editable file', **self.params)['revision'], 2)

    def test_failed_save_keeps_old_version(self):
        self.store.save(b'first', **self.params)
        self.store.max_total = 5
        self.params['expected'] = 1
        with self.assertRaises(OSError):
            self.store.save(b'second', **self.params)
        self.assertEqual(self.store.read(self.project)[1], b'first')

    def test_scanner_waits_for_stable_file_and_keeps_deleted_original_history(self):
        folder = self.projects/'Scratch'/'5A'
        folder.mkdir(parents=True)
        original = folder/'Spil.sb3'
        original.write_bytes(b'v1')
        scan = Scanner(self.projects, self.store)
        scan.scan()
        self.assertEqual(self.store.list(), [])
        scan.scan()
        found = self.store.list()[0]
        self.assertEqual(found['kind'], 'scratch')
        original.write_bytes(b'new larger version')
        scan.scan()
        self.assertEqual(self.store.meta(found['id'])['revision'], 1)
        scan.scan()
        original.unlink()
        scan.scan()
        self.assertEqual(self.store.read(found['id'])[1], b'new larger version')
        self.assertEqual(self.store.read(found['id'], 1)[1], b'v1')
        restarted = Scanner(self.projects, self.store)
        restarted.scan()
        restarted.scan()
        self.assertEqual(len(self.store.list()), 1)

    def test_file_types_paths_and_checksum(self):
        for filename in ('../secret.svg', 'c:\\test.svg', 'run.html'):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                self.store.save(b'file', **{**self.params, 'filename':filename})
        self.store.save(b'file', **self.params)
        (self.store.files/self.project/'1.bin').write_bytes(b'corrupt')
        with self.assertRaises(OSError):
            self.store.read(self.project)

    def test_local_http_origin_and_host_boundary(self):
        server = make_server(0, self.store)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        query = urlencode({**self.params, 'id':self.project})
        def request(method, path, body=None, headers=None):
            con = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            try:
                con.request(method, path, body, headers or {})
                response = con.getresponse()
                return response.status, response.read(), dict(response.getheaders())
            finally:
                con.close()
        try:
            save = '/_elevhub/projekter/api/save?'+query
            headers = {'Origin':origin,'X-Elevhub-Project':'1','Content-Type':'application/octet-stream'}
            self.assertEqual(request('POST',save,b'hello',headers)[0], 200)
            self.assertEqual(request('POST',save,b'foreign',{**headers,'Origin':'https://foreign.example'})[0],403)
            self.assertEqual(request('GET','/_elevhub/projekter/api/list',headers={'Host':'foreign.example'})[0],403)
            self.assertEqual(request('GET','/_elevhub/projekter/api/list',headers={'Sec-Fetch-Site':'cross-site'})[0],403)
            self.assertEqual(request('GET','/_elevhub/projekter/',headers={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate'})[0],200)
            status, body, response_headers = request('GET','/_elevhub/projekter/api/file?id='+self.project)
            self.assertEqual((status,body),(200,b'hello'))
            self.assertIn('attachment',response_headers['Content-Disposition'])
            self.assertNotIn('Access-Control-Allow-Origin',response_headers)
            self.assertEqual(server.server_address[0], '127.0.0.1')
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_local_navigation_tracks_project_open_but_not_embedded_picker(self):
        assets = Path(self.temp.name)/'scratch'
        assets.mkdir()
        (assets/'index.html').write_text('<html><head></head><body>Scratch</body></html>')
        navigation = Navigation()
        server = make_server(0, self.store, app='scratch', assets=assets, navigation=navigation,
                             apps=[{'id':'scratch', 'port':8792}])
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        def get(path, headers=None):
            con = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            try:
                con.request('GET',path,headers=headers or {})
                r=con.getresponse()
                return r.status,r.read()
            finally:
                con.close()
        try:
            self.assertIsNone(json.loads(get('/_elevhub/health')[1])['navigation'])
            get('/',{'Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'})
            state = json.loads(get('/_elevhub/health')[1])
            self.assertEqual(state['navigation']['id'],'scratch')
            self.assertEqual(set(state['navigation']),{'id','version'})
            self.assertEqual(state['apps'],[{'id':'scratch','port':8792}])
            get('/_elevhub/projekter/',{'Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'iframe'})
            self.assertEqual(navigation.read(),state['navigation'])
            get('/_elevhub/projekter/',{'Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'})
            self.assertEqual(navigation.read()['id'],'start')
            self.assertNotEqual(navigation.read()['version'],state['navigation']['version'])
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == '__main__':
    unittest.main()
