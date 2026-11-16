"""Generated apps obey canonical package ownership and the fixed project bridge."""
import hashlib
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'system')]
from sdk.new_app import create_app
from sdk.check_app import check_app
from sdk.preview import preview_server
from app.app_catalog import compatibility, validate_app_descriptor


class SDKTemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mkv-sdk-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'apps').mkdir()

    def test_stateless_and_project_are_independent_canonical_app_packages(self):
        sentinel = self.root / 'system-sentinel'
        sentinel.write_bytes(b'unchanged core')
        before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
        for app_id, port, project in [('quiz-demo', 8890, False), ('notes-demo', 8891, True)]:
            folder = create_app(app_id, 'Mit værksted', port, project=project, root=self.root)
            descriptor = validate_app_descriptor(json.loads((folder / 'app.json').read_text(encoding='utf-8')))
            self.assertTrue(compatibility(descriptor, core_version='2.1.0')['compatible'])
            spec = check_app(self.root, app_id, self.root / 'stages' / app_id)
            prefix = f'usr/share/makervaerkstedet/apps/{app_id}/'
            self.assertTrue(all(item['path'].startswith(prefix) for item in spec['files']))
            self.assertEqual(descriptor['project'] is not None, project)
            if project:
                self.assertEqual(descriptor['project']['kind'], app_id)
                self.assertTrue((folder / 'web/notes-adapter.mjs').is_file())
            else:
                self.assertNotIn('ElevhubProjectAdapter', (folder / 'web/app.js').read_text())
            self.assertNotIn('__APP_', (folder / 'web/index.html').read_text(encoding='utf-8'))
        self.assertEqual(before, hashlib.sha256(sentinel.read_bytes()).hexdigest())
        self.assertEqual({p.name for p in self.root.iterdir()}, {'apps', 'stages', 'system-sentinel'})

    def test_refuses_overwrite_invalid_ids_reserved_ports_and_port_collision(self):
        folder = create_app('quiz-demo', 'Quiz', 8890, root=self.root)
        marker = folder / 'keep'; marker.write_text('another author')
        with self.assertRaisesRegex(ValueError, 'already_exists'):
            create_app('quiz-demo', 'Replacement', 8891, root=self.root)
        self.assertEqual(marker.read_text(), 'another author')
        with self.assertRaisesRegex(ValueError, 'port_already_used'):
            create_app('notes-demo', 'Noter', 8890, root=self.root)
        for app_id, port in [('../escape', 8892), ('UPPER', 8892), ('start', 8892), ('valid', 8791)]:
            with self.subTest(app_id=app_id), self.assertRaises(ValueError):
                create_app(app_id, 'Quiz', port, root=self.root)
        self.assertEqual([p.name for p in (self.root / 'apps').iterdir()], ['quiz-demo'])

    def test_cli_creates_only_new_app_and_keeps_canonical_system_source_bytes(self):
        # Execute the real CLI from a temporary public-source-shaped repository.
        for relative in ['repository.py', 'REPOSITORY-LAYOUT.json', 'sdk/new_app.py',
                         'system/app/__init__.py', 'system/app/app_catalog.py', 'system/app/simple_release.py']:
            target = self.root / relative; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        shutil.copytree(ROOT / 'sdk/templates', self.root / 'sdk/templates')
        def snapshot():
            return {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in self.root.rglob('*') if p.is_file()}
        before = snapshot()
        command = [sys.executable, '-B', str(self.root / 'sdk/new_app.py'), '--id', 'cli-notes',
                   '--name', 'CLI noter', '--port', '8894', '--project']
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        after = snapshot()
        self.assertTrue(all(after[name] == digest for name, digest in before.items()))
        self.assertTrue(all(name.startswith('apps/cli-notes/') for name in after.keys() - before.keys()))
        check_app(self.root, 'cli-notes', self.root / 'stage')
        repeated = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(repeated.returncode, 2)
        self.assertIn('already_exists', repeated.stderr)

    def test_escapes_html_name_and_rejects_cross_app_file_ownership(self):
        folder = create_app('safe', '<b>Navn & quiz</b>', 8890, root=self.root)
        self.assertIn('&lt;b&gt;Navn &amp; quiz&lt;/b&gt;', (folder / 'web/index.html').read_text(encoding='utf-8'))
        manifest = json.loads((folder / 'files.json').read_text())
        manifest['files'][0]['source'] = 'system/web'
        (folder / 'files.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'sdk_source_outside_app'):
            check_app(self.root, 'safe', self.root / 'stage')

    def test_refuses_linked_app_parent(self):
        elsewhere = self.root / 'elsewhere'; elsewhere.mkdir()
        (self.root / 'apps').rmdir()
        try:
            (self.root / 'apps').symlink_to(elsewhere, target_is_directory=True)
        except OSError:
            if os.name != 'nt':
                raise
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(self.root / 'apps'), str(elsewhere)], check=True, capture_output=True)
        try:
            with self.assertRaisesRegex(ValueError, 'linked_sdk_path'):
                create_app('quiz', 'Quiz', 8890, root=self.root)
            self.assertEqual(list(elsewhere.iterdir()), [])
        finally:
            # Remove this exact link/junction only; never recurse into its target.
            if (self.root / 'apps').is_symlink():
                (self.root / 'apps').unlink()
            else:
                os.rmdir(self.root / 'apps')

    def test_notes_adapter_roundtrip_validation_privacy_and_stable_signature(self):
        folder = create_app('notes-demo', 'Noter', 8891, project=True, root=self.root)
        adapter = (folder / 'web/notes-adapter.mjs').as_uri()
        script = '''import assert from 'node:assert/strict';
import {createNotesAdapter} from ADAPTER;
let text = 'Mine idéer 🌱';
const a = createNotesAdapter({id:'notes-demo',extension:'.notesdemo.mkv',getText:()=>text,setText:v=>{text=v}});
a.setName('Testnoter'); const signature=a.signature(); const blob=a.export();
assert.equal(a.signature(),signature); assert.equal(a.type,'notes-demo');
a.reset(); assert.equal(text,''); assert.equal(a.name(),''); assert.equal(a.hasContent(),false);
await a.import(blob,'Åbnede noter'); assert.equal(text,'Mine idéer 🌱');assert.equal(a.name(),'Åbnede noter');
for(const value of [{schema:2,app:'notes-demo',text:'new'}, {schema:1,app:'foreign',text:'new'},
 {schema:1,app:'notes-demo',text:3}, {schema:1,app:'notes-demo',text:'x',extra:true},
 {schema:1,app:'notes-demo',text:'x'.repeat(100001)}]) {
 await assert.rejects(a.import(new Blob([JSON.stringify(value)]),'bad'));
 assert.equal(text,'Mine idéer 🌱'); assert.equal(a.name(),'Åbnede noter');
}
await assert.rejects(a.validateImport(new Blob(['broken json'])));
await assert.rejects(a.import(new Blob(['x'.repeat(1048577)])));
assert.equal(text,'Mine idéer 🌱'); console.log('adapter roundtrip and import preservation passed');
'''.replace('ADAPTER', json.dumps(adapter))
        result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_host_injects_theme_and_only_project_apps_get_save_bridge(self):
        for project in (False, True):
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
            app_id = 'notes' if project else 'quiz'
            create_app(app_id, 'SDK prøve', port, project=project, root=self.root)
            with preview_server(self.root, app_id) as server:
                worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
                try:
                    origin = f'http://127.0.0.1:{port}'
                    page = urlopen(origin + '/').read().decode()
                    self.assertIn('/_elevhub/app.js', page)
                    self.assertIn('/school-theme.js', page)
                    self.assertEqual('/_elevhub/projekter/bridge.js' in page, project)
                    font = urlopen(origin + '/assets/metrophobic-latin.ttf').read()
                    self.assertGreater(len(font), 1000)
                    if project:
                        self.assertIn('ElevhubProjectSession', urlopen(origin + '/_elevhub/projekter/session.js').read().decode())
                        content = b'{"schema":1,"app":"notes","text":"synthetic"}'
                        request = Request(origin + '/_elevhub/projekter/api/save?id=' + 'a'*32 + '&name=Test&folder=SDK&kind=notes&filename=notes.notes.mkv&expected=0',
                                          data=content, headers={'Origin': origin, 'Content-Type': 'application/octet-stream', 'X-Elevhub-Project':'1'})
                        saved = json.loads(urlopen(request).read())
                        self.assertEqual(saved['kind'], 'notes')
                        self.assertEqual(server.projekter.read(saved['id'])[1], content)
                finally:
                    server.shutdown(); worker.join(timeout=5)

    def test_queue_app_preview_uses_real_gateway_and_never_leaves_the_temporary_outbox(self):
        from urllib.error import HTTPError
        from tests.test_laser_outbox import payload
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
        folder = create_app('koe-demo', 'Køprøve', port, root=self.root)
        descriptor = json.loads((folder / 'app.json').read_text(encoding='utf-8'))
        descriptor['capabilities']['optional'] += ['laser-submit-v1', 'svg-library-v1']
        descriptor['aflevering'] = 'laser-submit'
        (folder / 'app.json').write_text(json.dumps(descriptor, ensure_ascii=False), encoding='utf-8')
        with preview_server(self.root, 'koe-demo') as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
            try:
                origin = f'http://127.0.0.1:{port}'
                page = urlopen(origin + '/').read().decode()
                self.assertIn('/_elevhub/app.js', page)
                self.assertIn('/school-theme.js', page)
                job = payload()
                request = Request(origin + '/api/laser/submit', data=json.dumps(job).encode(),
                                  headers={'Origin': origin, 'Content-Type': 'application/json'})
                with urlopen(request) as response:
                    self.assertEqual(response.status, 202)
                    self.assertEqual(json.loads(response.read()), {'queued': True, 'requestId': job['requestId']})
                self.assertEqual([p.name for p in server.outbox.glob('*.json')], [job['requestId'] + '.json'])
                for path, status in (('/api/laser/health', 503), ('/api/laser/library/status', 503)):
                    with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                        urlopen(origin + path)
                    self.assertEqual(error.exception.code, status); error.exception.close()
                # The app declared no print capability, so the print route stays closed.
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(origin + '/api/jobs', data=b'solid', headers={'Origin': origin}))
                self.assertEqual(error.exception.code, 404); error.exception.close()
            finally:
                server.shutdown(); worker.join(timeout=5)
        self.assertFalse(server.outbox.exists())

    def test_queue_example_has_fixed_routes_and_honest_receipts(self):
        script = '''import assert from 'node:assert/strict';
import {submitLaser,submitPrint} from QUEUE;
globalThis.ElevhubApp={capabilities:{required:[],optional:['laser-submit-v1','print-job-v1']}};
let calls=[];let status=202;let body={queued:true};
globalThis.fetch=async(url,options)=>{calls.push([url,options]);return new Response(JSON.stringify(body),{status})};
const input={elevnavn:'Test',klasse:'SDK',projekt:'Test',svg:'<svg/>',requestId:'12345678-1234-1234-1234-123456789abc'};
assert.equal((await submitLaser(input)).state,'waiting');assert.equal(calls[0][0],'/api/laser/submit');
status=201;body={id:'a'.repeat(32),duplicate:false};
assert.equal((await submitPrint({stl:new Blob(['test']),requestId:input.requestId})).state,'received');
assert.equal(calls[1][0],'/api/jobs');assert.equal(calls[1][1].headers['X-Request-ID'],input.requestId);
status=423;body={};await assert.rejects(submitLaser(input),/lukket/);
status=200;body={ok:true};await assert.rejects(submitLaser(input),/ukendt/);
globalThis.fetch=async()=>{throw TypeError('network failure')};await assert.rejects(submitLaser(input),/Kvitteringen er ukendt/);
globalThis.ElevhubApp.capabilities.optional=[];const count=calls.length;
await assert.rejects(submitLaser(input),/adgang/);assert.equal(calls.length,count);
'''.replace('QUEUE', json.dumps((ROOT / 'sdk/examples/queue.mjs').as_uri()))
        result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
