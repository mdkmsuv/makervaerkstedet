import base64
import io
import json
import lzma
from pathlib import Path
import sys
import tempfile
import struct
from types import SimpleNamespace
import unittest
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'projekter'))
from filtyper import association, archive_listing
from makecode_import import project_url, editable_project
from project_open import plan, native_request, native_status, workshop_urls, DEFAULT_WORKSHOPS
from projekt_http import opening
from projektarkiv import Projektarkiv
from scanner import Scanner


def zipped(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return stream.getvalue()


class ProjectOpening(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.projects = Path(self.temp.name) / 'Projekter'
        self.store = Projektarkiv(self.projects / '.elevhub')
        self.server = SimpleNamespace(projekter=self.store, native_projects=True,
            workshop_urls=DEFAULT_WORKSHOPS, projekt_apps=[{'id':'scratch','port':8792}, {'id':'kredsloeb','port':8794}])

    def tearDown(self):
        self.temp.cleanup()

    def save(self, filename, data, kind='andet'):
        return self.store.save(data, project=uuid.uuid4().hex, name='Åbneprøve',
            folder='QA', kind=kind, filename=filename, expected=0)

    def test_windows_and_linux_workshop_config_encodings(self):
        config = Path(self.temp.name) / 'workshops.json'
        expected = {'3dprint': 'https://198.51.100.106:8770'}
        for encoding in ('utf-8', 'utf-8-sig'):
            with self.subTest(encoding=encoding):
                config.write_text(json.dumps(expected), encoding=encoding)
                self.assertEqual(workshop_urls(config), expected)

    def test_all_supported_extensions_have_a_program(self):
        expected = {'a.sb3':'scratch', 'a.HEX':'microbit', 'a.stl':'3dprint',
            'a.klosterskitse':'cadoodle', 'a.svg':'laser', 'a.projekt.svg':'laser',
            'a.png':'image', 'a.wav':'audio', 'a.mp3':'audio', 'a.csv':'table',
            'a.txt':'text', 'a.json':'json', 'a.zip':'archive', 'a.piskel':'piskel'}
        for name, program in expected.items():
            with self.subTest(name=name):
                self.assertEqual(association(name, 'scratch')['program'], program)

    def test_content_beats_old_workshop_label(self):
        self.assertEqual(association('a.svg', 'laser', b'<svg data-workshop="vinyl"/>')['program'], 'folie')
        self.assertEqual(association('a.txt', 'microbit', b'$ 1 0.000005 10\nr 1 2')['program'], 'kredsloeb')
        self.assertEqual(association('a.txt', 'andet', b'<cir f="1" ts="0.000005"></cir>')['program'], 'kredsloeb')
        self.assertEqual(association('a.json', 'andet', b'{"targets":[],"meta":{}}')['program'], 'scratch')
        self.assertEqual(association('a.json', 'andet', b'{"marks":[],"height":3}')['program'], 'cadoodle')
        self.assertEqual(association('a.zip', 'andet', zipped({'project.json':'{}'}))['program'], 'scratch')
        self.assertEqual(association('a.zip', 'andet', zipped({'a.doodle':'{}','part.stl':'solid'}))['program'], 'cadoodle')

    def test_real_makecode_lzma_js_export(self):
        data = (Path(__file__).parent/'fixtures/makecode-source.hex').read_bytes()
        url = project_url(data)
        self.assertTrue(url.startswith('https://makecode.microbit.org/?lang=da#project:'))
        imported = json.loads(lzma.decompress(base64.b64decode(url.split('#project:')[1])))
        files = json.loads(imported['source'])
        self.assertEqual(imported['meta']['name'], 'Elevhub-pilot')
        self.assertIn('main.blocks', files)
        self.assertEqual(files['main.ts'], 'basic.forever(function () {\n\t\n})\n')

    def test_hex_missing_source_or_corrupt_is_actionable(self):
        with self.assertRaisesRegex(ValueError, 'ikke et MakeCode-projekt'):
            project_url(b':00000001FF\n')
        data = (Path(__file__).parent/'fixtures/makecode-source.hex').read_bytes()
        with self.assertRaises(ValueError):
            project_url(data.replace(b'41140E2F', b'41140E2E', 1))
        with self.assertRaises(ValueError):
            project_url(data[:90])

    def test_native_selected_revision_and_idempotent_retry(self):
        m = self.save('figur.klosterskitse', b'{"marks":[],"height":3}')
        self.store.save(b'{"marks":[],"height":8}',project=m['id'],name=m['name'],
            folder=m['folder'],kind=m['kind'],filename=m['filename'],expected=1)
        meta, old = self.store.read(m['id'],1)
        token = uuid.uuid4().hex
        first = native_request(self.server, meta, old, token)
        self.assertEqual(native_request(self.server, meta, old, token), first)
        root = self.store.root/'open-requests'
        self.assertEqual((root/(token+'.klosterskitse')).read_bytes(),old)
        self.assertEqual(len(list(root.glob('*.json'))),1)
        self.assertEqual(native_status(self.server,token)['state'],'pending')
        with self.assertRaises(ValueError):
            native_request(self.server,meta,old,'../escape')

    def test_local_svg_uses_guarded_project_entry_and_stl_native_or_preview(self):
        m = self.save('a.svg',b'<svg data-workshop="vinyl"/>')
        result = plan(self.server,m,self.store.read(m['id'])[1])
        self.assertEqual(result['mode'], 'navigate')
        self.assertEqual(result['url'],DEFAULT_WORKSHOPS['folie']+'?elevhub_project='+m['id'])
        model = b'\0'*80 + struct.pack('<I12fH',1,0,0,1,0,0,0,10,0,0,0,10,0,0)
        m = self.save('a.stl',model)
        self.assertEqual(plan(self.server,m,model)['mode'],'native')
        self.server.native_projects=False
        self.assertEqual(plan(self.server,m,model)['mode'],'frame')
        for broken in (b'stl',model[:-1],model[:96]+struct.pack('<f',float('nan'))+model[100:]):
            with self.subTest(data=broken[:6]), self.assertRaises(ValueError):
                plan(self.server,m,broken)

    def test_old_hex_opens_in_local_guarded_controller_with_editable_blocks(self):
        data = (Path(__file__).parent/'fixtures/makecode-source.hex').read_bytes()
        m = self.save('tidligere-projekt.hex', data, 'microbit')
        self.server.projekt_apps.append({'id':'microbit', 'port':8797})
        result = plan(self.server, m, data)
        self.assertEqual((result['mode'], result['port']), ('local',8797))
        self.assertNotIn('url', result)
        project = result['editable']['project']
        self.assertEqual(project['header']['name'], 'Elevhub-pilot')
        self.assertIn('main.blocks', project['text'])
        self.assertIn('basic.forever', project['text']['main.ts'])

    def test_saved_data_and_microbit_json_reopen_in_their_workshops(self):
        for kind, format_, contents, port in [
            ('microbit', 'elevhub-microbit-project', {'project':{'header':{'name':'test'},'text':{'main.ts':'basic.showNumber(5)'}}},8797),
            ('datatraener', 'elevhub-datatraener-project', {'gestureData':[]},8793)]:
            data = json.dumps({'format':format_, 'version':1, 'type':kind, **contents}).encode()
            self.server.projekt_apps.append({'id':kind, 'port':port})
            m = self.save(kind+'.json',data,kind)
            result = plan(self.server,m,data)
            self.assertEqual((result['program'],result['mode'],result['port']), (kind,'local',port))

    def test_archive_paths_are_checked_before_native_extract(self):
        for path in ('../outside','/outside','C:/outside','..\\outside'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                archive_listing(zipped({'project.doodle':'{}',path:'bad'}))
        with self.assertRaises(ValueError):
            archive_listing(b'not a zip')

    def test_list_label_uses_file_content_and_changes_with_revision(self):
        m=self.save('a.svg',b'<svg data-workshop="vinyl"/>','laser')
        self.assertEqual(opening(self.server,m)['program'],'folie')
        m=self.store.save(b'<svg data-workshop="laser"/>',project=m['id'],name=m['name'],
            folder=m['folder'],kind=m['kind'],filename=m['filename'],expected=1)
        self.assertEqual(opening(self.server,m)['program'],'laser')

    def test_native_project_snapshot_includes_dependencies_and_versions(self):
        folder=self.projects/'3D'/'Min model';folder.mkdir(parents=True)
        (folder/'Min model.doodle').write_text('{}')
        (folder/'figur.stl').write_bytes(b'first model')
        scan=Scanner(self.projects,self.store)
        scan.scan();self.assertEqual(self.store.list(),[])
        scan.scan();items=self.store.list();self.assertEqual(len(items),1)
        m, data=self.store.read(items[0]['id'])
        self.assertEqual(association(m['filename'],m['kind'],data)['program'],'cadoodle')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(archive.read('figur.stl'),b'first model')
        (folder/'figur.stl').write_bytes(b'changed model')
        scan.scan();scan.scan()
        self.assertEqual(self.store.meta(m['id'])['revision'],2)
        self.assertEqual(self.store.read(m['id'],1)[1],data)


if __name__ == '__main__':
    unittest.main()
