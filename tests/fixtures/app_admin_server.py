"""Actual teacher HTTP routes with synthetic publisher/device reports only."""
from pathlib import Path as _LayoutPath
import sys as _layout_sys
_LAYOUT_ROOT = next(p for p in _LayoutPath(__file__).resolve().parents if (p / 'REPOSITORY-LAYOUT.json').is_file())
_layout_sys.path[:0] = [str(_LAYOUT_ROOT), str(_LAYOUT_ROOT / 'system')]
from repository import source_path, repo_root
import hashlib
import json
from pathlib import Path
import signal
import sys
import threading
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(source_path('linux-elevhub/server/elevhub', ROOT))]
import elevhub
import test_app_admin as domain
from app import app_catalog
from tilstand import Tilstand,Laererkode

fixture=domain.AppAdminTests('test_defaults_do_not_silently_change_existing_devices')
fixture.setUp()
stop=threading.Event();token=uuid.uuid4().hex
try:
    registry=fixture.root/'registry.json';value=json.loads(registry.read_bytes())
    value['enheder']['pc2']={'token_sha256':hashlib.sha256(b'v'*43).hexdigest(),'ui_token_sha256':hashlib.sha256(b'w'*43).hexdigest()}
    registry.write_text(json.dumps(value));fixture.config['device_bindings']['pc2']={'role':'student','platform':'debian-amd64'}
    first=json.loads((fixture.root/'public-apt-cache/manifests'/(fixture.catalog_hash+'.json')).read_bytes())
    other=domain.descriptor('quiz',8802,project=False)
    second=domain.envelope(other)['apps'][0];payload=b'synthetic app quiz'
    second['artifact'].update(size=len(payload),sha256=hashlib.sha256(payload).hexdigest())
    first['apps'].append(second);first['sequence']=2
    raw=json.dumps(first).encode();digest=hashlib.sha256(raw).hexdigest();folder=fixture.root/'public-apt-cache/manifests'
    (folder/(digest+'.json')).write_bytes(raw);(folder/(digest+'.minisig')).write_bytes(b'synthetic signature')
    (fixture.root/'public-apt-cache/pool'/second['artifact']['name']).write_bytes(payload)
    fixture.config['catalog']=[r for r in fixture.config['catalog'] if r['component']!='app-catalog']+[{'manifest':'public-apt-cache/manifests/'+digest+'.json','signature':'public-apt-cache/manifests/'+digest+'.minisig','role':'student','platform':'debian-amd64','component':'app-catalog'}]
    reports={}
    def report(device,installed,result=None):
        reports[device]=installed
        fixture.service.poll(device,{'schema':1,'installed':[{'component':'studentcore','version':'2.1.0~20261114.1'},{'component':'updater','version':'1.1.0~20261114.1'}],'result':None})
        return fixture.admin.poll(device,{'schema':1,'capabilities':['apps-v1'],'updater_version':'1.1.0~20261114.1','core_version':'2.1.0~20261114.1','installed':installed,'issues':[],'result':result})
    report('pc1',fixture.inventory());report('pc2',[])
    profile=fixture.root/'local-appearance';profile.mkdir()
    (profile/'manifest.json').write_text(json.dumps({'schemaVersion':1,'school_name':'Syntetisk skole','theme':'green','style':{}}))
    state=Tilstand(fixture.root,[])
    fixture.admin.catalog_observer=state.include_app_descriptors
    class Handler(elevhub.Laerer):
        def do_GET(self):
            if self.path=='/api/makerspace/state':return self.svar(200,{'sources':{'printers':'ok'},'inventory':[]})
            return super().do_GET()
        def do_POST(self):
            if self.path=='/_qa/'+token+'/stop':
                self.rfile.read(int(self.headers.get('Content-Length',0)));self.svar(200,{'stopped':True});stop.set();return
            if self.path=='/_qa/'+token+'/report':
                self.rfile.read(int(self.headers.get('Content-Length',0)))
                for device in list(reports):
                    for job in report(device,reports[device])['jobs']:
                        entry=next(e for e in first['apps'] if e['descriptor']['id']==job['app_id'])['descriptor']
                        installed=[i for i in reports[device] if i['id']!=entry['id']]+[{'id':entry['id'],'package':entry['package'],'version':entry['version'] if job['action']!='remove' else None,'state':'installed' if job['action']!='remove' else 'absent'}]
                        report(device,installed,{'job_id':job['job_id'],'revision':job['revision'],'state':'succeeded','code':None})
                return self.svar(200,{'reports':'synthetic'})
            return super().do_POST()
        def log_message(self,*args):pass
    server=elevhub.Server(('127.0.0.1',0),Handler)
    server.simple_updates=fixture.service;server.enheder=fixture.registry;server.navn='synthetic-admin-qa'
    server.tilstand=state;server.apps=[];server.kode=Laererkode(fixture.root);server.kode.vaelg('synthetic-only-code')
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *args:stop.set())
    origin=f'http://127.0.0.1:{server.server_port}'
    from test_school_profile_admin import png
    print(json.dumps({'origin':origin,'cleanup':origin+'/_qa/'+token+'/stop','report':origin+'/_qa/'+token+'/report','logo_png':png()}),flush=True)
    try:stop.wait()
    finally:server.shutdown();server.server_close();thread.join()
finally:
    fixture.doCleanups()
