import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.installation import configure, read_printer_transfer, verify_files, redirect_legacy


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pid = 'a' * 32
        self.transfer = self.root / 'transfer.json'
        self.data = {'format': 'printvaerksted-printers-v1', 'printers': [
            {'id': self.pid, 'model': 'A1 mini', 'name': 'School printer', 'host': '192.168.1.55',
             'serial': 'ABC123', 'access_code': 'test-only', 'bed_type': 'Textured PEI Plate', 'enabled': True}],
            'certificates': {self.pid + '-mqtt.sha256': 'b' * 64}}
        self.transfer.write_text(json.dumps(self.data))

    def tearDown(self):
        self.temp.cleanup()

    def test_migration_is_paused_disabled_and_has_no_prints_or_camera_approval(self):
        result = configure(self.root, self.transfer)
        self.assertTrue(result['imported'])
        folder = self.root / 'data' / 'school'
        settings = json.loads((folder / 'settings.json').read_text())
        self.assertFalse(settings['printers'][0]['enabled'])
        self.assertEqual(settings['studio_path'], str(self.root / 'tools' / 'bambu' / 'bambu-studio.exe'))
        with closing(sqlite3.connect(folder / 'queue.sqlite3')) as db, db:
            self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0], 'true')
        self.assertFalse((folder / 'cameras').exists())
        self.assertEqual((folder / 'certificates' / (self.pid + '-mqtt.sha256')).read_text(), 'b' * 64)

    def test_upgrade_preserves_existing_connections_and_pause_choice(self):
        configure(self.root, self.transfer)
        folder = self.root / 'data' / 'school'
        path = folder / 'settings.json'
        settings = json.loads(path.read_text())
        settings['printers'][0]['name'] = 'Teacher edited'
        settings['printers'][0]['enabled'] = True
        path.write_text(json.dumps(settings))
        with closing(sqlite3.connect(folder / 'queue.sqlite3')) as db, db:
            db.execute("UPDATE settings SET value='false' WHERE key='paused'")
        result = configure(self.root, self.transfer)
        self.assertFalse(result['imported'])
        self.assertEqual(json.loads(path.read_text())['printers'][0]['name'], 'Teacher edited')
        self.assertTrue(json.loads(path.read_text())['printers'][0]['enabled'])
        with closing(sqlite3.connect(folder / 'queue.sqlite3')) as db, db:
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0], 'false')

    def test_commissioned_school_package_enables_connections_but_starts_empty_and_paused(self):
        (self.root / 'printvaerksted-package.json').write_text(json.dumps({
            'distribution': 'klostermarksskolen', 'activate_imported_printers': True}))
        configure(self.root, self.transfer)
        folder = self.root / 'data' / 'school'
        path = folder / 'settings.json'
        settings = json.loads(path.read_text())
        self.assertTrue(settings['printers'][0]['enabled'])
        with closing(sqlite3.connect(folder / 'queue.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0], 'true')
        settings['printers'][0]['enabled'] = False
        path.write_text(json.dumps(settings))
        configure(self.root, self.transfer)
        self.assertFalse(json.loads(path.read_text())['printers'][0]['enabled'])

    def test_activation_requires_explicit_school_package_marker(self):
        (self.root / 'printvaerksted-package.json').write_text(json.dumps({'activate_imported_printers': True}))
        configure(self.root, self.transfer)
        settings = json.loads((self.root / 'data' / 'school' / 'settings.json').read_text())
        self.assertFalse(settings['printers'][0]['enabled'])

    def test_invalid_certificate_path_cannot_escape_import_folder(self):
        self.data['certificates'] = {'../../escaped': 'b' * 64}
        self.transfer.write_text(json.dumps(self.data))
        with self.assertRaises(ValueError):
            configure(self.root, self.transfer)
        self.assertFalse((self.root / 'data' / 'school' / 'settings.json').exists())

    def test_import_rejects_public_network_and_duplicate_printers(self):
        for change in ('public', 'duplicate'):
            with self.subTest(change=change):
                data = json.loads(self.transfer.read_text())
                if change == 'public':
                    data['printers'][0]['host'] = '8.8.8.8'
                else:
                    data['printers'] *= 2
                self.transfer.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    read_printer_transfer(self.transfer)
                self.transfer.write_text(json.dumps(self.data))

    def test_missing_or_corrupt_package_files_are_rejected(self):
        file = self.root / 'model.bin'
        file.write_bytes(b'model')
        (self.root / 'integrity.json').write_text(json.dumps({'model.bin': hashlib.sha256(b'model').hexdigest()}))
        self.assertEqual(verify_files(self.root), 1)
        file.write_bytes(b'bad')
        with self.assertRaises(ValueError):
            verify_files(self.root)
        file.unlink()
        with self.assertRaises(ValueError):
            verify_files(self.root)


    def test_machine_data_path_and_history_survive_configure(self):
        data = self.root / 'shared-data'
        (self.root / 'installation.json').write_text(json.dumps({'data_root': str(data)}), encoding='utf-8-sig')
        configure(self.root, self.transfer)
        self.assertTrue((data / 'school' / 'queue.sqlite3').exists())
        self.assertFalse((self.root / 'data' / 'school').exists())
        from app.core import Queue
        from scripts.smoke_slice import cube
        q = Queue(data / 'school', self.data['printers'])
        job = q.submit(cube(), 'history.stl', 'any', 'install-history-request-1234')
        q.pause(False)
        q.close()
        camera = data / 'school' / 'cameras' / 'saved-settings.json'
        camera.parent.mkdir()
        camera.write_text('{"automatic":false,"calibration":"retained"}')
        configure(self.root)
        q = Queue(data / 'school', self.data['printers'])
        try:
            self.assertEqual(q.get_job(job['id'])['status'], 'queued')
            self.assertFalse(q.paused)
            self.assertIn('retained', camera.read_text())
        finally:
            q.close()

    def test_legacy_shortcuts_forward_and_original_launchers_are_backed_up(self):
        old = self.root / 'old'
        target = self.root / 'new'
        for directory in (old, target):
            (directory / 'app').mkdir(parents=True)
            (directory / 'printvaerksted-package.json').write_text('{"app":"printvaerksted"}')
        for name in ('desktop', 'server'):
            (old / 'app' / (name + '.py')).write_text('# old launcher')
        redirect_legacy(old, target)
        redirect_legacy(old, target)
        for name in ('desktop', 'server'):
            code = (old / 'app' / (name + '.py')).read_text()
            compile(code, name + '.py', 'exec')
            self.assertIn('app.desktop', code)
            self.assertEqual((old / 'app' / (name + '.py.before-autostart')).read_text(), '# old launcher')




if __name__ == '__main__':
    unittest.main()
