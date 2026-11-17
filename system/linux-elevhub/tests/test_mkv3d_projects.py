import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import uuid

sys.path[:0] = [str(Path(__file__).resolve().parents[2]),
                str(Path(__file__).resolve().parents[1] / 'projekter')]
from filtyper import association, project_kind
from project_open import DEFAULT_WORKSHOPS, plan
from projektarkiv import Projektarkiv
from workshop_sessions import WorkshopSessions


def project():
    return json.dumps({'format':'makervaerkstedet-3d-project', 'version':1,
        'scene':{'format':'makervaerkstedet-3d', 'version':2, 'units':'mm',
                 'plate':{'w':180,'d':180},'objects':[],'roots':[]}}).encode()

class ModelProjects(unittest.TestCase):
    def test_save_versions_and_open_native_3d(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Projektarkiv(Path(temp)); ident=uuid.uuid4().hex
            meta=store.save(project(), project=ident, name='Navnebrik', folder='5A · Test', kind='3dprint', filename='brik.mkv3d', expected=0)
            self.assertEqual(meta['revision'],1)
            self.assertEqual(project_kind('brik.mkv3d'), '3dprint')
            self.assertEqual(association('brik.mkv3d')['program'],'3dprint')
            meta, data=store.read(ident,1)
            server=SimpleNamespace(projekt_apps=[],workshop_urls=DEFAULT_WORKSHOPS)
            route=plan(server, meta, data, revision=1)
            self.assertEqual(route['mode'],'navigate')
            self.assertEqual(route['url'],f'http://127.0.0.1:8798/model3d.html?elevhub_project={ident}&elevhub_revision=1')
            self.assertEqual(data,project())

    def test_invalid_native_format_is_rejected(self):
        server=SimpleNamespace(projekt_apps=[],workshop_urls=DEFAULT_WORKSHOPS)
        meta={'id':'a'*32,'filename':'a.mkv3d','kind':'3dprint'}
        for data in (b'{}',b'invalid',b'{"format":"makervaerkstedet-3d-project","version":true,"scene":{}}'):
            with self.subTest(data=data), self.assertRaises(ValueError): plan(server,meta,data)

    def test_3d_workshop_has_handoff_session(self):
        sessions=WorkshopSessions(lambda:100)
        sessions.poll({'client':'a'*32,'kind':'3dprint','dirty':True})
        self.assertEqual(sessions.request()['state'],'pending')

if __name__=='__main__': unittest.main()
