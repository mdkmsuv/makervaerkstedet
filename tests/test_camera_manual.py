"""Manual camera and plate-release regressions, isolated from physical printers."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from app.camera import Cameras
from app.camera_transfer import read_camera_transfer, import_cameras
from app.core import Queue
from app.server import Application, Handler, Server
from app.teacher_auth import TeacherAuth
from tests.test_library_retention_integration import _HTTP


class ManualCameraTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.config = {'id': 'a' * 32, 'name': 'Printer', 'model': 'A1 mini',
                       'serial': 'SERIAL1', 'host': '192.0.2.1', 'enabled': True}
        self.pid = self.config['id']
        self.queue = Queue(self.folder, [self.config], clock=lambda: 1000.)
        self.queue.update_report(self.pid, state='IDLE', task='', error=0)
        output = io.BytesIO()
        Image.new('RGB', (640, 480), '#808080').save(output, 'JPEG')
        self.jpeg = output.getvalue()
        self.capture = Mock(return_value=self.jpeg)
        self.camera = Cameras(self.folder, self.queue, capture=self.capture, clock=lambda: 1000.)

    def tearDown(self):
        self.queue.close()
        self.temp.cleanup()

    def test_capture_preserves_archives_and_cannot_release_legacy_automatic_configuration(self):
        directory, path = self.camera.paths(self.pid)
        archive = directory / 'reference-archive.jpg'
        archive.write_bytes(self.jpeg)
        settings = {'serial': self.config['serial'], 'automatic': True,
                    'release_mode': 'ai_trial', 'ai_validation': {'passed': True},
                    'references': [{'file': archive.name}], 'trial_approval': {'approved': 1}}
        path.write_text(json.dumps(settings), encoding='utf-8')
        self.assertEqual(self.capture.call_count, 0)
        latest = self.camera.capture(self.pid)
        self.assertEqual(self.camera.image(self.pid), self.jpeg)
        self.assertEqual(self.camera.status(self.pid)['latest'], latest)
        self.assertFalse(self.queue.bed_is_clear(self.queue.printers[self.pid]))
        self.assertFalse(hasattr(self.camera, 'ai'))
        self.assertFalse(hasattr(self.camera, 'thread'))
        self.assertFalse(hasattr(self.camera, 'set_automatic'))
        persisted = json.loads(path.read_text(encoding='utf-8'))
        for key, value in settings.items():
            self.assertEqual(persisted[key], value)
        self.assertEqual(archive.read_bytes(), self.jpeg)
        self.queue.release_bed(self.pid)
        self.assertTrue(self.queue.teacher_cleared(self.queue.printers[self.pid]))
        self.camera.capture(self.pid)
        self.assertTrue(self.queue.teacher_cleared(self.queue.printers[self.pid]))

    def test_old_clearance_never_passes_reservation_or_dispatch_guard(self):
        printer = self.queue.printers[self.pid]
        printer.update(bed_clear=True, bed_source='camera', bed_checked=1000., bed_ai_trial=True)
        self.assertFalse(self.queue.bed_is_clear(printer))
        self.assertFalse(self.queue.bed_is_clear(printer, for_dispatch=True))
        self.queue.release_bed(self.pid)
        self.assertTrue(self.queue.bed_is_clear(printer, for_dispatch=True))
        self.queue.update_report(self.pid, state='RUNNING')
        self.assertFalse(self.queue.bed_is_clear(printer))

    def test_bad_identity_cannot_capture_or_replace_saved_photo(self):
        directory, path = self.camera.paths(self.pid)
        path.write_text(json.dumps({'serial': 'OTHER'}), encoding='utf-8')
        with self.assertRaises(ValueError):
            self.camera.capture(self.pid)
        self.capture.assert_not_called()
        self.assertFalse((directory / 'latest.jpg').exists())
        with self.assertRaises(ValueError):
            self.camera.image('../other')

    def test_camera_transfer_keeps_photographs_without_reenabling_automatic_release(self):
        name = 'reference-' + 'b' * 32 + '.jpg'
        settings = {'references': [{'file': name, 'captured': 900.}],
                    'polygon': [[.1,.1],[.9,.1],[.9,.9],[.1,.9]],
                    'view_approved': True, 'validated': True, 'automatic': True,
                    'release_mode': 'ai_trial', 'ai_validation': {'passed': True}}
        photo = {'jpeg': base64.b64encode(self.jpeg).decode(),
                 'sha256': hashlib.sha256(self.jpeg).hexdigest()}
        entry = {key: self.config[key] for key in ('serial','host','model')}
        entry.update(settings=settings, images={name: photo, 'occupied-validation.jpg': photo})
        entries = read_camera_transfer({'cameras': {self.pid: entry}}, [self.config])
        self.assertFalse(entries[self.pid]['settings']['automatic'])
        self.assertNotIn('ai_validation', entries[self.pid]['settings'])
        target = self.folder / 'imported'
        self.assertEqual(import_cameras(target, entries), 1)
        self.assertEqual((target / 'cameras' / self.pid / name).read_bytes(), self.jpeg)
        saved = json.loads((target / 'cameras' / self.pid / 'camera.json').read_text(encoding='utf-8'))
        self.assertFalse(saved['automatic'])

    def test_http_keeps_authenticated_viewing_and_manual_release_but_retires_automation(self):
        app = types.SimpleNamespace(folder=self.folder, queue=self.queue, cameras=self.camera,
                                    auth=TeacherAuth('secret-code'), demo=False)
        server = Server(('127.0.0.1', 0), Handler)
        server.app = app
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        http = _HTTP(server)
        try:
            self.assertEqual(http.request('POST','/api/camera/capture',{'id':self.pid})[0],401)
            _, headers, _ = http.request('POST','/api/auth/login',{'code':'secret-code'})
            cookie = headers['Set-Cookie'].split(';',1)[0]
            self.assertEqual(http.request('POST','/api/camera/capture',{'id':self.pid},cookie=cookie)[0],200)
            status, _, jpeg = http.request('GET',f'/api/camera/image/{self.pid}',cookie=cookie)
            self.assertEqual((status,jpeg),(200,self.jpeg))
            for endpoint in ('automatic','vision','validate-ai','check','reference','capture-all','reference-all'):
                with self.subTest(endpoint=endpoint):
                    self.assertEqual(http.request('POST',f'/api/camera/{endpoint}',{'id':self.pid,'enabled':True},cookie=cookie)[0],404)
                    self.assertEqual(http.request('POST',f'/api/camera/{endpoint}',{},cookie=cookie)[0],404)
            self.assertFalse(self.queue.bed_is_clear(self.queue.printers[self.pid]))
            self.assertEqual(http.request('POST','/api/printers/release',{'id':self.pid},cookie=cookie)[0],200)
            self.assertTrue(self.queue.teacher_cleared(self.queue.printers[self.pid]))
            self.assertEqual(self.capture.call_count,1)
        finally:
            server.shutdown();server.server_close();thread.join(5)

    def test_legacy_remote_and_capture_settings_do_not_start_background_camera_work(self):
        target = self.folder / 'startup'
        target.mkdir()
        settings = {'studio_path': 'missing-studio.exe', 'printers': [],
                    'collect_completion_samples': True,
                    'plate_camera': {'url':'http://127.0.0.1:1','token':'synthetic-old-token'}}
        (target / 'settings.json').write_text(json.dumps(settings),encoding='utf-8')
        # Isolate only unrelated printer/firmware scheduling; use real camera and queue.
        with patch('app.server.Worker'), patch('app.server.FirmwareChecks'):
            app = Application(target, demo=False)
            try:
                self.assertFalse(hasattr(app.cameras,'thread'))
                self.assertFalse(hasattr(app.cameras,'external_camera'))
                self.assertFalse(hasattr(app.queue,'collect_completion_samples'))
                self.assertEqual(json.loads((target/'settings.json').read_text(encoding='utf-8')),settings)
            finally:
                app.shutdown()
