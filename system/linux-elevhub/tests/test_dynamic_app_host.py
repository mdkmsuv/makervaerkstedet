"""A new installed app works without editing a fixed core app/format list."""
from pathlib import Path as _LayoutPath
import sys as _layout_sys
_LAYOUT_ROOT = next(p for p in _LayoutPath(__file__).resolve().parents if (p / 'REPOSITORY-LAYOUT.json').is_file())
_layout_sys.path[:0] = [str(_LAYOUT_ROOT), str(_LAYOUT_ROOT / 'system')]
from repository import source_path, repo_root
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.request
import uuid
import subprocess
import signal
import time
import os
import struct
from urllib.parse import urlencode

ROOT=repo_root(__file__)
sys.path[:0]=[str(ROOT),str(source_path('linux-elevhub/projekter', ROOT))]
from tests.test_app_catalog import descriptor
from projektarkiv import Projektarkiv
from workshop_sessions import WorkshopSessions
from project_open import plan
host=__import__('app_host') if importlib.util.find_spec('app_host') else None


class DynamicAppTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(host,'Manifest-driven app hosting is missing')
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
    def install(self,app=None):
        app=app or descriptor()
        folder=self.root/'apps'/app['id'];www=folder/'www';www.mkdir(parents=True)
        (folder/'app.json').write_text(json.dumps(app))
        (www/'index.html').write_text('<html><head></head><body>Noter</body></html>')
        return app
    def test_empty_base_has_project_shelf_without_app_ports(self):
        self.assertEqual(host.discover_apps(self.root/'apps'),[])
        self.assertEqual(host.runtime_endpoints([]),[(8791,'/_elevhub/health','elevhub-lokal','projekter')])

    def test_suffix_overlapping_formats_are_isolated_and_never_misclassify_a_file(self):
        alpha=descriptor('alpha',8890);alpha['project']['extension']='.mkv'
        beta=descriptor('beta',8891);beta['project']['extension']='.beta.mkv'
        self.install(alpha);self.install(beta)
        with self.assertRaisesRegex(ValueError,'duplicate_app_project_format'):
            host.discover_apps(self.root/'apps')
        self.assertEqual(host.discover_apps(self.root/'apps',strict=False),[])
        from filtyper import association,project_kind
        self.assertEqual(association('example.beta.mkv','beta',b'opaque',[alpha,beta])['program'],'andet')
        self.assertEqual(project_kind('example.beta.mkv','beta',b'opaque',[alpha,beta]),'andet')
        with self.assertRaisesRegex(ValueError,'project_format_conflict'):
            Projektarkiv(self.root/'ambiguous-store',apps=[alpha,beta])

    def test_new_app_is_discovered_and_project_format_saves_reopens_and_finishes(self):
        self.install();apps=host.discover_apps(self.root/'apps')
        self.assertEqual(apps[0]['id'],'noter')
        store=Projektarkiv(self.root/'state',apps=apps)
        key=uuid.uuid4().hex;raw=b'{"notes":"elevens tekst"}'
        meta=store.save(raw,project=key,name='Note',folder='Test',kind='noter',filename='note.noter.json',expected=0)
        server=type('Server',(),{'projekt_apps':apps,'workshop_urls':{}})()
        result=plan(server,meta,raw)
        self.assertEqual((result['mode'],result['port']),('local',8799))
        sessions=WorkshopSessions(kinds={'noter'})
        client=uuid.uuid4().hex;sessions.poll({'client':client,'kind':'noter','dirty':True})
        req=sessions.request();sessions.acknowledge({'client':client,'id':req['id'],'state':'completed'})
        self.assertEqual(sessions.status(req['id'])['state'],'completed')
        retained=Projektarkiv(self.root/'state',apps=[])
        self.assertEqual(retained.read(key)[1],raw)

    def test_stateless_html_has_no_project_bridge_or_adapter_wait(self):
        self.install(descriptor('noter',8799,project=False))
        apps=host.discover_apps(self.root/'apps')
        value=host.asset(apps[0],'/index.html')
        self.assertIsNotNone(value)
        self.assertIn(b'/_elevhub/app.js',value[0])
        self.assertNotIn(b'projekt-bridge',value[0])

    def test_generic_app_can_read_same_public_school_profile(self):
        from lokal import make_server
        self.install(descriptor('noter',8799,project=False));app=host.discover_apps(self.root/'apps')[0]
        profile=self.root/'profile/local-appearance';profile.mkdir(parents=True)
        (profile/'manifest.json').write_text(json.dumps({'schemaVersion':1,'school_name':'Fælles profil','theme':'yellow','style':{}}))
        store=Projektarkiv(self.root/'state')
        server=make_server(0,store,app='noter',assets=app['assets'],descriptor=app,data_root=self.root/'profile')
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api/school-appearance') as response:value=json.load(response)
        self.assertEqual((value['school_name'],value['theme']),('Fælles profil','yellow'))

    def test_shared_assets_come_from_this_runtime_not_another_installed_checkout(self):
        from lokal import make_server
        self.install();app=host.discover_apps(self.root/'apps')[0]
        shared=self.root/'shared';shared.mkdir();(shared/'school-theme.js').write_bytes(b'/* isolated shared helper */')
        server=make_server(0,Projektarkiv(self.root/'state'),app='noter',assets=app['assets'],descriptor=app,shared_root=shared)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/school-theme.js') as response:
            self.assertEqual(response.read(),b'/* isolated shared helper */')

    def test_shelf_configuration_uses_its_own_school_profile(self):
        from lokal import make_server
        profile=self.root/'profile/local-appearance';profile.mkdir(parents=True)
        (profile/'manifest.json').write_text(json.dumps({'schemaVersion':1,'school_name':'Den lokale skole',
            'theme':'yellow','font':'metrophobic','style':{}}))
        server=make_server(0,Projektarkiv(self.root/'state'),data_root=profile.parent)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/_elevhub/projekter/api/config') as response:
            appearance=json.load(response)['appearance']
        self.assertEqual((appearance['school_name'],appearance['font']),('Den lokale skole','metrophobic'))

    def test_traversal_symlinks_duplicate_ports_and_bad_app_do_not_claim_basis(self):
        self.install();apps=host.discover_apps(self.root/'apps')
        self.assertIsNone(host.asset(apps[0],'/../app.json'))
        other=descriptor('quiz',8799,project=False);self.install(other)
        with self.assertRaisesRegex(ValueError,'duplicate_app_port'):host.discover_apps(self.root/'apps')

    def test_generic_project_size_and_extension_are_enforced(self):
        self.install();apps=host.discover_apps(self.root/'apps');store=Projektarkiv(self.root/'state',apps=apps)
        for filename,raw in [('other.sb3',b'{}'),('note.noter.json',b'x'*1_000_001)]:
            with self.assertRaises(ValueError):store.save(raw,project=uuid.uuid4().hex,name='Note',folder='',kind='noter',filename=filename,expected=0)

    def test_broken_app_does_not_stop_other_apps_or_the_empty_base(self):
        self.install();folder=self.root/'apps'/'broken';folder.mkdir();(folder/'app.json').write_text('{broken')
        self.assertEqual([a['id'] for a in host.discover_apps(self.root/'apps',strict=False)],['noter'])

    def test_incomplete_app_does_not_reserve_port_and_ambiguous_apps_are_isolated(self):
        self.install(descriptor('alpha',8799,project=False));self.install(descriptor('zebra',8799,project=False))
        (self.root/'apps/alpha/www/index.html').unlink();(self.root/'apps/alpha/www').rmdir()
        self.assertEqual([a['id'] for a in host.discover_apps(self.root/'apps',strict=False)],['zebra'])
        (self.root/'apps/alpha/www').mkdir();(self.root/'apps/alpha/www/index.html').write_text('<html></html>')
        self.install(descriptor('third',8801,project=False))
        self.assertEqual([a['id'] for a in host.discover_apps(self.root/'apps',strict=False)],['third'])

    def test_actual_five_descriptors_keep_builtin_imports_over_real_http(self):
        from lokal import make_server
        from filtyper import project_kind
        apps=[json.loads(path.read_text(encoding='utf-8')) for path in (ROOT/'apps').glob('*/app.json')]
        store=Projektarkiv(self.root/'state',apps=apps)
        server=make_server(0,store,apps=apps,workshops={})
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        origin=f'http://127.0.0.1:{server.server_port}'
        stl=b'\0'*80+struct.pack('<I12fH',1,0,0,1,0,0,0,10,0,0,0,10,0,0)
        micro=json.dumps({'format':'elevhub-microbit-project','version':1,'project':{'header':{},'text':{}}}).encode()
        for filename,data,want in [('model.stl',stl,'stl'),('kode.hex',b':00000001FF\n','microbit'),
                                  ('tegning.svg',b'<svg xmlns="http://www.w3.org/2000/svg"/>','laser'),
                                  ('gammelt-navn.json',micro,'microbit')]:
            with self.subTest(filename=filename):
                self.assertEqual(project_kind(filename,'andet',data,apps),want)
                query=urlencode({'id':uuid.uuid4().hex,'name':'Import','folder':'QA','kind':'andet','filename':filename,'expected':0})
                request=urllib.request.Request(origin+'/_elevhub/projekter/api/save?'+query,data=data,
                    headers={'Origin':origin,'Content-Type':'application/octet-stream','X-Elevhub-Project':'1'})
                with urllib.request.urlopen(request) as response:meta=json.load(response)
                self.assertEqual(meta['kind'],want)
                self.assertEqual(store.read(meta['id'])[1],data)

    def test_actual_folie_descriptor_preserves_library_theme_and_has_no_queue_access(self):
        from app import laser_local
        self.assertTrue(callable(getattr(laser_local,'create_installed_servers',None)),'Actual installed gateway factory is missing')
        class WebsiteLibrary:
            def status(self):return {'enabled':True,'version':3,'source_url':'https://xn--skolevrkstedet-5ib.dk/svg-bibliotek/','cache_state':'stale','last_success':None}
            def catalogue_response(self):return {**self.status(),'items':[]}
        # Deliberately no school server. A library request must use its own
        # fixed-origin local cache service, even when the queue is disconnected.
        app=json.loads((ROOT/'apps/folie/app.json').read_text(encoding='utf-8'))
        (self.root/'www').mkdir();(self.root/'www/laser.html').write_text('<html><head></head><body>Folie</body></html>')
        (self.root/'school/local-appearance').mkdir(parents=True);(self.root/'school/local-appearance/manifest.json').write_text(json.dumps({'schemaVersion':1,'school_name':'Syntetisk skole','theme':'green','style':{}}))
        app.update(port=0,assets=str(self.root/'www'))
        inbox=laser_local.ForwardingInbox('http://127.0.0.1:1')
        servers=laser_local.create_installed_servers([app],inbox,inbox,data_root=self.root/'school',runtime={},library_client=WebsiteLibrary())
        self.assertEqual(len(servers),1)
        server=servers[0];threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        origin=f'http://127.0.0.1:{server.server_port}'
        with urllib.request.urlopen(origin+'/api/folie/library/status') as response:self.assertTrue(json.load(response)['enabled'])
        with urllib.request.urlopen(origin+'/api/folie/library') as response:self.assertEqual(json.load(response)['cache_state'],'stale')
        with urllib.request.urlopen(origin+'/api/school-appearance') as response:appearance=json.load(response)
        self.assertEqual((appearance['theme'],appearance['school_name']),('green','Syntetisk skole'))
        for path in ('/api/jobs','/api/laser/submit'):
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(origin+path,data=b'',headers={'Origin':origin}))
            self.assertEqual(error.exception.code,404)

    def test_pending_queue_is_delivered_after_the_last_app_is_removed(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        from app.laser_local import Outbox
        from tests.test_laser_outbox import payload
        received=[]
        class SchoolFixture(BaseHTTPRequestHandler):
            def do_POST(h):
                received.append(json.loads(h.rfile.read(int(h.headers['Content-Length']))))
                data=json.dumps({'id':'a'*32,'duplicate':False}).encode()
                h.send_response(200);h.send_header('Content-Length',str(len(data)));h.end_headers();h.wfile.write(data)
            def log_message(*args):pass
        school=ThreadingHTTPServer(('127.0.0.1',0),SchoolFixture)
        threading.Thread(target=school.serve_forever,daemon=True).start()
        self.addCleanup(school.server_close);self.addCleanup(school.shutdown)
        outbox=Outbox(self.root/'outbox');job=payload();outbox.put(job)
        process=subprocess.Popen([sys.executable,'-B','-m','app.laser_local','--server-url',
            f'http://127.0.0.1:{school.server_port}','--manifest-root',str(self.root/'empty-apps'),
            '--outbox-dir',str(self.root/'outbox')],cwd=ROOT / "system",stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+8
            while outbox.waiting() and process.poll() is None and time.monotonic()<deadline:time.sleep(.05)
            if process.poll() is not None:
                output,error=process.communicate()
                self.fail('Outbox worker exited: '+error.decode(errors='replace'))
            self.assertEqual(len(outbox.waiting()),0)
            self.assertEqual(received,[job])
        finally:
            process.send_signal(signal.SIGINT)
            process.communicate(timeout=5)

if __name__=='__main__':unittest.main()
