"""The shelf's return route serves the installed home, never arbitrary local files."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request,urlopen
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'projekter'))
from lokal import make_server
from projektarkiv import Projektarkiv

class StudentHomeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.start=root/'site/start.html';self.start.parent.mkdir();self.start.write_text('<!doctype html><title>Synthetic kiosk</title>')
        (self.start.parent/'assets').mkdir();(self.start.parent/'assets/test.css').write_text('body{color:black}')
        self.config=root/'kiosk.json';self.config.write_text(json.dumps({'hub':'https://school.example.test:8773','theme':'green','ui_token':'synthetic-private-ui'}))
        self.server=make_server(0,Projektarkiv(root/'projects'));self.server.kiosk_start_file=self.start;self.server.kiosk_config=self.config
        threading.Thread(target=self.server.serve_forever,daemon=True).start();self.addCleanup(self.stop)
        self.base=f'http://127.0.0.1:{self.server.server_port}/_elevhub/projekter'
    def stop(self):self.server.shutdown();self.server.server_close()
    def get(self,path,headers=None):return urlopen(Request(self.base+path,headers=headers or {}),timeout=3)
    def test_home_mirror_and_assets_preserve_navigation_without_file_scheme(self):
        with self.get('/home') as response:
            self.assertEqual(response.read(),self.start.read_bytes())
            self.assertIn('https://school.example.test:8773',response.headers['Content-Security-Policy'])
            self.assertIn('http://127.0.0.1:8790',response.headers['Content-Security-Policy'])
        with self.get('/assets/test.css') as response:self.assertEqual(response.read(),b'body{color:black}')
        with self.get('/api/config') as response:
            data=json.load(response);self.assertEqual(data['home_url'],'http://127.0.0.1:8791/_elevhub/projekter/home');self.assertNotIn('ui_token',json.dumps(data))
    def test_cross_site_reads_traversal_and_symlink_assets_are_rejected(self):
        for path,headers in [('/home',{'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'cors'}),('/assets/%2e%2e%2fkiosk.json',{}),('/assets/secret.py',{})]:
            with self.assertRaises(HTTPError):self.get(path,headers)
        (self.start.parent/'assets/linked.css').symlink_to(self.config)
        with self.assertRaises(HTTPError):self.get('/assets/linked.css')
    def test_font_is_served_from_the_same_prefix_used_by_embedded_pickers(self):
        with self.get('/Manrope.ttf') as response:
            self.assertGreater(len(response.read()),100000)

if __name__=='__main__':unittest.main()
