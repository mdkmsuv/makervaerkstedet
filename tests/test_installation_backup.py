import json
import hashlib
import gc
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from app.core import Queue
from app.installation_backup import restore, snapshot, verify
from app.laser_server import Inbox
from app.instance import InstanceLock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linux-elevhub' / 'server'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linux-elevhub' / 'server' / 'elevhub'))
from makerspace_api import MakerspaceService  # noqa: E402


class InstallationBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.server = root / 'server'; self.server.mkdir()
        self.hub = self.server / 'hub'; self.print = self.server / 'printvaerksted'; self.laser = self.server / 'designvaerksted'
        for folder, role in ((self.server, 'server'), (self.hub, 'hub'), (self.print, 'print'), (self.laser, 'laser')):
            folder.mkdir(exist_ok=True); (folder / 'backup-protocol.json').write_text(json.dumps({'protocol': 1, 'role': role}), encoding='utf-8')
        self.service = MakerspaceService(str(self.hub / 'makerspace.sqlite3'))
        actor = {'id': 'teacher:' + '1' * 32, 'role': 'teacher', 'scopes': ['makerspace:read', 'machines:write', 'workflows:write', 'jobs:write', 'audit:read']}
        self.service.execute(actor, {'action': 'machine.create', 'params': {'name': 'Bord', 'location': 'Rum', 'enabled': False, 'workflow_id': None}, 'expected_version': 0, 'request_id': '1' * 32})
        self.queue = Queue(self.print, [{'id': 'p1', 'name': 'P1S', 'model': 'P1S', 'enabled': True}])
        self.queue.pause(False)
        # Queue database with a real job and durable file library entry.
        from scripts.smoke_slice import cube
        self.job = self.queue.submit(cube(5), 'backup.stl', 'any', 'backup-job-000001')
        self.queue.db.execute("UPDATE jobs SET status='dispatching' WHERE id=?", (self.job['id'],)); self.queue.db.commit()
        self.inbox = Inbox(self.laser, code='backup-code')
        self.laser_job = self.inbox.submit({'elevnavn': 'Elev', 'klasse': '5.A', 'projekt': 'Skilt', 'requestId': 'backup-laser-0001', 'svg': '<svg xmlns="http://www.w3.org/2000/svg" data-laservaerksted="1" width="20mm" height="20mm" viewBox="0 0 20 20"><g data-operation="cut"><path data-operation="cut" d="M1 1H19V19H1Z"/></g></svg>'})
        definition = {'name': 'Laser backup', 'kind': 'laser', 'steps': [{'id': a, 'label': a, 'role': r, 'action': a} for a, r in [('submit', 'operator'), ('prepare', 'teacher'), ('approve', 'teacher'), ('produce', 'teacher'), ('collect', 'operator'), ('clear', 'teacher')]]}
        teacher = 'teacher:' + '1' * 32
        env = lambda action, params, expected, req: {'action': action, 'params': params, 'expected_version': expected, 'request_id': req}
        wf = self.inbox.workflows
        wf.execute(teacher, env('laser.station.create', {'id': 'laser-fbb2', 'name': 'Backup laser', 'location': 'Rum', 'model': 'fbb2', 'enabled': False}, 0, '3' * 32))
        wf.execute(teacher, env('laser.station.update', {'id': 'laser-fbb2', 'name': 'Backup laser', 'location': 'Rum', 'model': 'fbb2', 'enabled': True}, 1, '4' * 32))
        wf.execute(teacher, env('laser.workflow.assign', {'id': 'laser-fbb2', 'workflow': {'id': 'a' * 32, 'revision': 1, 'definition': definition}}, 2, '5' * 32))
        wf.execute(teacher, env('laser.job.bind', {'id': self.laser_job['id'], 'station_id': 'laser-fbb2'}, 0, '6' * 32))
        wf.execute(teacher, env('laser.job.advance', {'id': self.laser_job['id'], 'step_id': 'prepare', 'confirmed': True}, 1, '7' * 32))
        wf.execute(teacher, env('laser.job.advance', {'id': self.laser_job['id'], 'step_id': 'approve', 'confirmed': True}, 2, '8' * 32))
        wf.execute(teacher, env('laser.job.advance', {'id': self.laser_job['id'], 'step_id': 'produce', 'confirmed': True}, 3, '9' * 32))
        wf.check_export('laser-fbb2', 'fbb2', [self.laser_job['id']])
        security = self.server / 'security'
        security.mkdir()
        for filename in ('server.crt', 'server.key', 'ca.crt'):
            (security / filename).write_bytes(b'isolated-test-fixture-' + filename.encode())
        (security / 'enheder.json').write_text('{"test-device": {"enabled": true}}', encoding='utf-8')
        (self.print / 'settings.json').write_text(json.dumps({'studio_path': 'C:\\Slicer\\bambu-studio.exe',
            'printers': [{'id': 'p1', 'host': '192.168.5.10', 'access_code': 'fixture-private-code'}]}), encoding='utf-8')
        (self.print / 'makerspace-bridge.key').write_text('mkbridge_' + 'A' * 43, encoding='ascii')
        (self.laser / 'makerspace-laser.key').write_text('mkbridge_' + 'B' * 43, encoding='ascii')
        (self.server / 'konfiguration.json').write_text(json.dumps({
            'tls': {'certifikat': 'security/server.crt', 'noegle': 'security/server.key', 'ca': 'security/ca.crt'},
            'enhedsregister': 'security/enheder.json',
            'makerspace_printers': {'key_file': 'printvaerksted/makerspace-bridge.key'},
            'makerspace_laser_queue': {'key_file': 'designvaerksted/makerspace-laser.key'}}), encoding='utf-8')
        self.profile = {'format': 1, 'roots': {'server': {'path': str(self.server), 'role': 'server'}},
            'components': {'hub': {'root': 'server', 'path': 'hub'}, 'print': {'root': 'server', 'path': 'printvaerksted'}, 'laser': {'root': 'server', 'path': 'designvaerksted'}},
            'config': {'root': 'server', 'path': 'konfiguration.json'}}

    def tearDown(self):
        if self.queue is not None:
            self.queue.close()
        gc.collect()
        self.temp.cleanup()

    def test_configured_laser_plate_writer_is_locked_and_uncovered_data_rejected(self):
        plates = self.server / 'Laserplader'
        plates.mkdir()
        (plates / 'backup-protocol.json').write_text(
            json.dumps({'protocol': 1, 'role': 'laser_station'}), encoding='utf-8')
        config = self.server / 'laserstation/laser-installation.json'
        config.parent.mkdir()
        config.write_text(json.dumps({'plates': str(plates)}), encoding='utf-8')
        destination = Path(self.temp.name) / 'station-backup'
        with InstanceLock(plates):
            with self.assertRaises(RuntimeError):
                snapshot(self.profile, destination)
        self.assertFalse(destination.exists())
        (plates / 'pupil-work.svg').write_text('<svg/>', encoding='utf-8')
        self.assertTrue(snapshot(self.profile, destination)['ok'])
        self.assertEqual((destination / 'roots/server/Laserplader/pupil-work.svg').read_text(encoding='utf-8'), '<svg/>')
        outside = Path(self.temp.name) / 'outside-plates'
        outside.mkdir()
        config.write_text(json.dumps({'plates': str(outside)}), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'uden for backupens'):
            snapshot(self.profile, Path(self.temp.name) / 'outside-backup')

    def test_snapshot_verify_restore_preserves_databases_keys_and_isolation(self):
        root = Path(self.temp.name); backup = root / 'backup'; destination = root / 'restored'
        source_hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.print / 'queue.sqlite3', self.laser / 'afleveringer.sqlite3', self.print / 'makerspace-bridge.key', self.laser / 'makerspace-laser.key')}
        result = snapshot(self.profile, backup)
        self.assertTrue(result['ok']); self.assertEqual(verify(backup)['ok'], True)
        before = (backup / 'backup-manifest.json').read_bytes()
        report = restore(backup, destination)
        self.assertTrue(report['isolated']); self.assertTrue(report['agents_revoked']); self.assertTrue(report['print_paused'])
        self.assertTrue((destination / '.restore-hold.json').exists())
        self.assertTrue((destination / 'roots' / 'server' / 'printvaerksted' / 'makerspace-bridge.key').exists())
        self.assertTrue((destination / 'roots' / 'server' / 'designvaerksted' / 'makerspace-laser.key').exists())
        self.assertEqual((destination / 'roots' / 'server' / 'printvaerksted' / 'makerspace-bridge.key').read_bytes(), (self.print / 'makerspace-bridge.key').read_bytes())
        self.assertEqual((destination / 'roots' / 'server' / 'designvaerksted' / 'makerspace-laser.key').read_bytes(), (self.laser / 'makerspace-laser.key').read_bytes())
        self.assertEqual(before, (backup / 'backup-manifest.json').read_bytes())
        restored_config = json.loads((destination / 'roots' / 'server' / 'konfiguration.json').read_text(encoding='utf-8'))
        self.assertTrue(restored_config['makerspace_printers']['key_file'].replace('\\', '/').endswith('printvaerksted/makerspace-bridge.key'))
        for section, field, relative in [('tls', 'noegle', 'security/server.key'),
                                         ('tls', 'certifikat', 'security/server.crt'), ('tls', 'ca', 'security/ca.crt')]:
            self.assertEqual(Path(restored_config[section][field]), destination / 'roots' / 'server' / relative)
            self.assertEqual(Path(restored_config[section][field]).read_bytes(), (self.server / relative).read_bytes())
        self.assertEqual(Path(restored_config['enhedsregister']).read_bytes(), (self.server / 'security/enheder.json').read_bytes())
        self.assertEqual((destination / 'roots/server/printvaerksted/settings.json').read_bytes(),
                         (self.print / 'settings.json').read_bytes())
        for path, digest in source_hashes.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        import sqlite3
        db = sqlite3.connect(destination / 'roots' / 'server' / 'printvaerksted' / 'queue.sqlite3')
        try:
            self.assertEqual(db.execute("SELECT status FROM jobs WHERE id=?", (self.job['id'],)).fetchone()[0], 'dispatching')
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0], 'true')
        finally:
            db.close()
        self.assertEqual(self.queue.db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0], 'false')
        db = sqlite3.connect(destination / 'roots' / 'server' / 'designvaerksted' / 'afleveringer.sqlite3')
        try:
            row = db.execute("SELECT station_id,workflow_version,handoff_claimed FROM jobs WHERE id=?", (self.laser_job['id'],)).fetchone()
            self.assertEqual(row, ('laser-fbb2', 4, 1))
            with self.inbox.db() as original:
                for table in ('laser_stations', 'laser_workflow_commands', 'laser_workflow_audit'):
                    rows = [tuple(row) for row in original.execute(f'SELECT * FROM {table}')]
                    self.assertTrue(rows)
                    self.assertEqual(db.execute(f'SELECT * FROM {table}').fetchall(), rows)
                self.assertEqual(db.execute('SELECT workflow FROM jobs').fetchone()[0],
                                 original.execute('SELECT workflow FROM jobs').fetchone()[0])
        finally:
            db.close()
        db = sqlite3.connect(destination / 'roots' / 'server' / 'hub' / 'makerspace.sqlite3')
        try:
            self.assertNotIn('agents', {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")})
        finally:
            db.close()
        db = sqlite3.connect(self.hub / 'makerspace.sqlite3')
        try:
            self.assertNotIn('agents', {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")})
        finally:
            db.close()

    def test_verify_rejects_truncation_extra_and_path_traversal(self):
        root = Path(self.temp.name); backup = root / 'backup'; snapshot(self.profile, backup)
        manifest = json.loads((backup / 'backup-manifest.json').read_text(encoding='utf-8'))
        row = next(row for row in manifest['files'] if row['path'].endswith('konfiguration.json'))
        (backup / row['path']).write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError): verify(backup)
        snapshot(self.profile, root / 'backup2')
        (root / 'backup2' / 'extra.txt').write_text('x')
        with self.assertRaises(ValueError): verify(root / 'backup2')
        snapshot(self.profile, root / 'backup3')
        manifest = json.loads((root / 'backup3' / 'backup-manifest.json').read_text(encoding='utf-8'))
        manifest['files'][0]['path'] = 'roots/../escape.txt'
        (root / 'backup3' / 'backup-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaises(ValueError): verify(root / 'backup3')

    def test_source_change_during_copy_fails_without_success_report(self):
        from app import installation_backup
        destination = Path(self.temp.name) / 'changed'; original = installation_backup.shutil.copyfileobj; changed = {'done': False}
        def copy_then_change(reader, writer, length=0):
            result = original(reader, writer, length=length)
            if not changed['done']:
                changed['done'] = True
                (self.server / 'konfiguration.json').write_text(json.dumps({'usikker_test': True, 'changed': True}), encoding='utf-8')
            return result
        with patch.object(installation_backup.shutil, 'copyfileobj', side_effect=copy_then_change):
            with self.assertRaises(OSError): installation_backup.snapshot(self.profile, destination)
        self.assertFalse((destination / 'restore-report.json').exists())
        self.assertFalse((destination / 'backup-manifest.json').exists())

    def test_wal_reader_index_mutation_is_ignored_but_committed_write_is_not(self):
        from app import installation_backup
        database = self.print / 'queue.sqlite3'
        # The normal Queue keeps a connection open for its worker.  Close it
        # so this test exercises SQLite's real WAL reader index independently,
        # and can deterministically clean up on Windows.
        self.queue.close()
        self.queue = None
        fresh = self.server / 'fresh-print.sqlite3'
        shutil.copyfile(self.print / 'queue.sqlite3', fresh)
        database = fresh
        with closing(sqlite3.connect(database)) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower(), 'wal')
            db.execute("UPDATE settings SET value='false' WHERE key='paused'")
            db.commit()
        shm = database.with_name(database.name + '-shm')
        self.assertTrue(snapshot(self.profile, Path(self.temp.name) / 'wal-created'))

        # Also cover a reader index that is created after the initial source
        # inventory, without relying on unlinking an open WAL sidecar on
        # Windows.
        late_database = self.server / 'fresh-print-late.sqlite3'
        shutil.copyfile(database, late_database)
        late_shm = late_database.with_name(late_database.name + '-shm')
        late_shm.unlink(missing_ok=True)
        original = installation_backup.snapshot_database
        created = {'done': False}

        def snapshot_then_create_shm(source, destination, role=None, timeout=30):
            result = original(source, destination, role=role, timeout=timeout)
            if role == 'print' and not created['done']:
                created['done'] = True
                late_shm.write_bytes(b'')
            return result

        with patch.object(installation_backup, 'snapshot_database', side_effect=snapshot_then_create_shm):
            self.assertTrue(snapshot(self.profile, Path(self.temp.name) / 'wal-late-shm'))
        self.assertTrue(created['done'])

        reader = sqlite3.connect(database)
        reader.execute('SELECT COUNT(*) FROM jobs').fetchone()
        self.assertTrue(shm.exists())
        self.assertTrue(snapshot(self.profile, Path(self.temp.name) / 'wal-reader'))
        reader.close()
        del reader
        gc.collect()

        changed = {'done': False}
        unrelated = self.server / 'unrelated.shm'
        unrelated.write_bytes(b'unchanged')

        def snapshot_then_unrelated_write(source, destination, role=None, timeout=30):
            result = original(source, destination, role=role, timeout=timeout)
            if role == 'print' and not changed['done']:
                changed['done'] = True
                unrelated.write_bytes(b'changed')
            return result

        with patch.object(installation_backup, 'snapshot_database', side_effect=snapshot_then_unrelated_write):
            with self.assertRaises(OSError):
                snapshot(self.profile, Path(self.temp.name) / 'unrelated-shm-write')
        self.assertTrue(changed['done'])

        unrelated.write_bytes(b'unchanged')
        changed['done'] = False

        def snapshot_then_write(source, destination, role=None, timeout=30):
            result = original(source, destination, role=role, timeout=timeout)
            if role == 'print' and not changed['done']:
                changed['done'] = True
                with closing(sqlite3.connect(database)) as db:
                    db.execute("UPDATE settings SET value='true' WHERE key='paused'")
                    db.commit()
            return result

        with patch.object(installation_backup, 'snapshot_database', side_effect=snapshot_then_write):
            with self.assertRaises(OSError):
                snapshot(self.profile, Path(self.temp.name) / 'wal-write')
        self.assertTrue(changed['done'])

    def test_stable_sqlite_scan_keeps_nonempty_wal(self):
        from app import installation_backup
        signature = (1, 2, 0, 3)
        scan = ({'queue.sqlite3': signature, 'queue.sqlite3-shm': signature,
                 'queue.sqlite3-wal': signature}, set())
        stable = installation_backup._stable_sqlite_scan(scan, {'queue.sqlite3'})
        self.assertNotIn('queue.sqlite3-shm', stable[0])
        self.assertNotIn('queue.sqlite3-wal', stable[0])
        grown = dict(scan[0], **{'queue.sqlite3-wal': (1, 2, 1, 3)})
        stable = installation_backup._stable_sqlite_scan((grown, set()), {'queue.sqlite3'})
        self.assertIn('queue.sqlite3-wal', stable[0])

    def test_profile_rejects_bool_format_unknown_and_duplicate_roots(self):
        root = Path(self.temp.name)
        with self.assertRaises(ValueError): snapshot({**self.profile, 'format': True}, root / 'bool')
        with self.assertRaises(ValueError): snapshot({**self.profile, 'roots': {**self.profile['roots'], 'unknown!': self.profile['roots']['server']}}, root / 'unknown')
        duplicate = dict(self.profile['roots']); duplicate['hub2'] = dict(duplicate['server'])
        with self.assertRaises(ValueError): snapshot({**self.profile, 'roots': duplicate}, root / 'duplicate')

    def test_overlapping_component_reference_is_rejected(self):
        profile = dict(self.profile)
        profile['components'] = dict(self.profile['components'], laser={'root': 'server', 'path': 'hub/nested'})
        with self.assertRaises(ValueError): snapshot(profile, Path(self.temp.name) / 'overlap-component')

    def test_corrupt_nested_backup_protocol_is_rejected_even_with_updated_hash(self):
        backup = Path(self.temp.name) / 'backup'; snapshot(self.profile, backup)
        target = backup / 'roots' / 'server' / 'printvaerksted' / 'backup-protocol.json'
        target.write_text('{"protocol":1,"role":"laser"}', encoding='utf-8')
        manifest = json.loads((backup / 'backup-manifest.json').read_text(encoding='utf-8'))
        row = next(item for item in manifest['files'] if item['path'].endswith('printvaerksted/backup-protocol.json'))
        row['sha256'] = hashlib.sha256(target.read_bytes()).hexdigest(); row['size'] = target.stat().st_size
        (backup / 'backup-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaises(ValueError): verify(backup)

    def test_verify_rejects_unlisted_empty_and_duplicate_directories(self):
        backup = Path(self.temp.name) / 'backup'
        snapshot(self.profile, backup)
        extra = backup / 'roots/server/unlisted'
        extra.mkdir()
        with self.assertRaisesRegex(ValueError, 'ekstra'):
            verify(backup)
        extra.rmdir()
        manifest_path = backup / 'backup-manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['directories'].append(manifest['directories'][0])
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Ugyldig datamappe'):
            verify(backup)

    def test_backup_survives_original_source_removal(self):
        backup = Path(self.temp.name) / 'backup'; destination = Path(self.temp.name) / 'restored'
        snapshot(self.profile, backup)
        self.queue.close()
        shutil.rmtree(self.server)
        self.assertTrue(verify(backup)['ok'])
        self.assertTrue(restore(backup, destination)['isolated'])

    def test_failed_restore_copy_keeps_hold_without_report(self):
        from app import installation_backup
        backup = Path(self.temp.name) / 'backup'; destination = Path(self.temp.name) / 'failed-restore'; snapshot(self.profile, backup)
        original = installation_backup.shutil.copyfileobj; corrupted = {'done': False}
        def copy_then_corrupt(reader, writer, length=0):
            result = original(reader, writer, length=length)
            if not corrupted['done']:
                corrupted['done'] = True; writer.seek(0, 2); writer.write(b'corrupt'); writer.flush()
            return result
        with patch.object(installation_backup.shutil, 'copyfileobj', side_effect=copy_then_corrupt):
            with self.assertRaises(OSError): restore(backup, destination)
        self.assertTrue((destination / '.restore-hold.json').exists())
        self.assertFalse((destination / 'restore-report.json').exists())

    def test_live_component_lock_rejects_snapshot_before_destination(self):
        with InstanceLock(self.print):
            with self.assertRaises(RuntimeError):
                snapshot(self.profile, Path(self.temp.name) / 'locked')
        self.assertFalse((Path(self.temp.name) / 'locked').exists())

    def test_profile_rejects_overlap_existing_destination_and_bad_key_reference(self):
        root = Path(self.temp.name)
        (self.server / 'nested').mkdir()
        with self.assertRaises(ValueError): snapshot({**self.profile, 'roots': {**self.profile['roots'], 'copy': {'path': str(self.server / 'nested'), 'role': 'server'}}}, root / 'overlap')
        backup = root / 'backup'; snapshot(self.profile, backup)
        with self.assertRaises(FileExistsError): restore(backup, root)
        bad = dict(self.profile); bad['config'] = {'root': 'server', 'path': 'konfiguration.json'}
        self.server.joinpath('konfiguration.json').write_text(json.dumps({'usikker_test': True, 'makerspace_printers': {'key_file': '../outside.key'}}))
        with self.assertRaises((ValueError, OSError, FileNotFoundError)): snapshot(bad, root / 'bad')


if __name__ == '__main__':
    unittest.main()
