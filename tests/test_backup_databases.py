from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from app.backup_databases import protect_restored_database, snapshot_database, validate_database
import app.backup_databases as backup_databases
from app.core import Queue
from app.laser_server import Inbox

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linux-elevhub' / 'server'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linux-elevhub' / 'server' / 'elevhub'))
from makerspace_api import MakerspaceService  # noqa: E402

SVG = '<svg xmlns="http://www.w3.org/2000/svg" data-laservaerksted="1" width="20mm" height="20mm" viewBox="0 0 20 20"><g data-operation="cut"><path data-operation="cut" d="M1 1H19V19H1Z"/></g></svg>'

class BackupDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def real_databases(self):
        hub_dir, print_dir, laser_dir = (self.root / n for n in ('hub', 'print', 'laser'))
        hub_dir.mkdir(); print_dir.mkdir(); laser_dir.mkdir()
        service = MakerspaceService(str(hub_dir / 'makerspace.sqlite3'))
        queue = Queue(print_dir, [{'id': 'p1', 'name': 'P1S', 'model': 'P1S', 'enabled': True}]); queue.pause(False)
        from scripts.smoke_slice import cube
        job = queue.submit(cube(5), 'backup.stl', 'any', 'backup-db-request-001')
        queue.db.execute("UPDATE jobs SET status='dispatching' WHERE id=?", (job['id'],)); queue.db.commit()
        inbox = Inbox(laser_dir, code='backup-code')
        laser_job = inbox.submit({'elevnavn': 'Elev', 'klasse': '5.A', 'projekt': 'Skilt', 'requestId': 'backup-db-laser-001', 'svg': SVG})
        with inbox.db() as db: db.execute("UPDATE jobs SET workflow=?, station_id='laser-fbb2', workflow_version=4, handoff_claimed=1 WHERE id=?", ('{}', laser_job['id']))
        return service, queue, inbox, job, laser_job

    def minimal_hub(self):
        source = self.root / 'hub.sqlite3'
        with closing(sqlite3.connect(source)) as db:
            db.executescript('''
                CREATE TABLE objects(type TEXT, id TEXT, version INTEGER, body TEXT);
                CREATE TABLE workflow_versions(id TEXT, revision INTEGER, body TEXT);
                CREATE TABLE requests(actor TEXT, request_id TEXT, digest TEXT, response TEXT);
                CREATE TABLE audit(sequence INTEGER, at REAL, actor TEXT, action TEXT, target TEXT, request_id TEXT, outcome TEXT);
                PRAGMA user_version=1;
            ''')
            db.commit()
        return source

    def test_real_roles_validate(self):
        _service, queue, _inbox, _job, _laser = self.real_databases()
        try:
            self.assertEqual(validate_database(self.root / 'hub' / 'makerspace.sqlite3', None)['role'], 'hub')
            self.assertEqual(validate_database(self.root / 'print' / 'queue.sqlite3', None)['role'], 'print')
            self.assertEqual(validate_database(self.root / 'laser' / 'afleveringer.sqlite3', None)['role'], 'laser')
        finally: queue.close()

    def test_snapshot_is_wal_consistent_and_source_unchanged(self):
        _service, queue, _inbox, _job, _laser = self.real_databases()
        self.addCleanup(queue.close)
        source = self.root / 'print' / 'queue.sqlite3'
        queue.db.execute('PRAGMA wal_autocheckpoint=0')
        queue.db.execute("UPDATE jobs SET filename='only-in-wal.stl'")
        queue.db.commit()
        wal = source.with_name(source.name + '-wal')
        self.assertGreater(wal.stat().st_size, 0)
        original = {p: p.read_bytes() for p in (source, wal)}
        destination = self.root / 'snapshot.sqlite3'
        result = snapshot_database(source, destination, 'print')
        self.assertEqual(result['integrity'], 'ok')
        for path, content in original.items():
            self.assertEqual(path.read_bytes(), content)
        with closing(sqlite3.connect(destination)) as db:
            self.assertEqual(db.execute('SELECT status,filename FROM jobs').fetchone(), ('dispatching', 'only-in-wal.stl'))
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
        for suffix in ('-wal', '-shm', '-journal'):
            self.assertFalse(destination.with_name(destination.name + suffix).exists())
        with self.assertRaises(FileExistsError): snapshot_database(source, destination, 'print')

    def test_failed_snapshot_removes_owned_sidecars_and_allows_retry(self):
        source = self.minimal_hub()
        destination = self.root / 'retry.sqlite3'
        calls = []

        def fail_target(db, role, deadline):
            calls.append(role)
            if len(calls) == 2:
                (self.root / 'retry.sqlite3-journal').write_bytes(b'owned by failed snapshot')
                raise RuntimeError('synthetic snapshot failure')

        with patch.object(backup_databases, '_validate_with_deadline', side_effect=fail_target):
            with self.assertRaisesRegex(RuntimeError, 'synthetic snapshot failure'):
                snapshot_database(source, destination, 'hub')
        self.assertFalse(destination.exists())
        for suffix in ('-wal', '-shm', '-journal'):
            self.assertFalse(destination.with_name(destination.name + suffix).exists())
        result = snapshot_database(source, destination, 'hub')
        self.assertEqual(result['integrity'], 'ok')

    def test_timed_out_snapshot_removes_owned_sidecars_and_allows_retry(self):
        source = self.minimal_hub()
        destination = self.root / 'timeout-retry.sqlite3'
        calls = []

        def timeout_target(db, role, deadline):
            calls.append(role)
            if len(calls) == 2:
                (self.root / 'timeout-retry.sqlite3-wal').write_bytes(b'owned by timed out snapshot')
                raise TimeoutError('synthetic snapshot timeout')

        with patch.object(backup_databases, '_validate_with_deadline', side_effect=timeout_target):
            with self.assertRaisesRegex(TimeoutError, 'synthetic snapshot timeout'):
                snapshot_database(source, destination, 'hub')
        self.assertFalse(destination.exists())
        for suffix in ('-wal', '-shm', '-journal'):
            self.assertFalse(destination.with_name(destination.name + suffix).exists())
        result = snapshot_database(source, destination, 'hub')
        self.assertEqual(result['integrity'], 'ok')

    def test_existing_destination_or_sidecar_is_never_removed_on_reservation_failure(self):
        source = self.minimal_hub()
        destination = self.root / 'reserved.sqlite3'
        destination.write_bytes(b'foreign destination')
        sidecar = destination.with_name(destination.name + '-journal')
        sidecar.write_bytes(b'foreign sidecar')
        with self.assertRaises(FileExistsError):
            snapshot_database(source, destination, 'hub')
        self.assertEqual(destination.read_bytes(), b'foreign destination')
        self.assertEqual(sidecar.read_bytes(), b'foreign sidecar')

    def test_existing_destination_link_is_rejected_without_touching_target(self):
        source = self.minimal_hub()
        outside = self.root / 'outside.sqlite3'
        outside.write_bytes(b'foreign target')
        destination = self.root / 'linked.sqlite3'
        try:
            destination.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest('symlink creation is unavailable on this host')
        with self.assertRaises(ValueError):
            snapshot_database(source, destination, 'hub')
        self.assertTrue(destination.is_symlink())
        self.assertEqual(outside.read_bytes(), b'foreign target')

    def test_unknown_schema_corruption_and_missing_column_rejected(self):
        _service, queue, _inbox, _job, _laser = self.real_databases(); queue.close(); source = self.root / 'print' / 'queue.sqlite3'
        with closing(sqlite3.connect(source)) as db: db.execute('DROP TABLE library_models'); db.commit()
        with self.assertRaises(ValueError): validate_database(source, 'print')
        source.write_bytes(b'not sqlite')
        with self.assertRaises((ValueError, sqlite3.Error)): validate_database(source, 'print')

    def test_legacy_marker_can_be_copied_without_migration_but_future_version_is_refused(self):
        _service, queue, _inbox, _job, _laser = self.real_databases()
        queue.close()
        for role, filename in (('print', 'queue.sqlite3'), ('laser', 'afleveringer.sqlite3')):
            source = self.root / role / filename
            with closing(sqlite3.connect(source)) as db:
                db.execute('PRAGMA user_version=0')
                db.commit()
            original = source.read_bytes()
            target = self.root / (role + '-legacy.sqlite3')
            self.assertEqual(validate_database(source, role)['user_version'], 0)
            self.assertEqual(snapshot_database(source, target, role)['user_version'], 0)
            self.assertEqual(validate_database(target, role)['user_version'], 0)
            self.assertEqual(source.read_bytes(), original)
            with closing(sqlite3.connect(source)) as db:
                db.execute('PRAGMA user_version=2')
                db.commit()
            with self.assertRaises(ValueError):
                validate_database(source, role)
            target = self.root / (role + '-future.sqlite3')
            with self.assertRaises(ValueError):
                snapshot_database(source, target, role)
            self.assertFalse(target.exists())

    def test_protection_changes_copies_but_preserves_source_state(self):
        _service, queue, _inbox, job, laser_job = self.real_databases(); queue.close(); copies = self.root / 'copies'; copies.mkdir()
        for role, name in (('hub', 'makerspace.sqlite3'), ('print', 'queue.sqlite3'), ('laser', 'afleveringer.sqlite3')):
            (copies / role).mkdir(); (copies / role / name).write_bytes((self.root / role / name).read_bytes())
        protect_restored_database(copies / 'hub' / 'makerspace.sqlite3', 'hub'); protect_restored_database(copies / 'print' / 'queue.sqlite3', 'print'); protect_restored_database(copies / 'laser' / 'afleveringer.sqlite3', 'laser')
        with closing(sqlite3.connect(copies / 'hub' / 'makerspace.sqlite3')) as db:
            self.assertNotIn('agents', {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")})
        with closing(sqlite3.connect(copies / 'print' / 'queue.sqlite3')) as db:
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0], 'true'); self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?', (job['id'],)).fetchone()[0], 'dispatching')
        with closing(sqlite3.connect(copies / 'laser' / 'afleveringer.sqlite3')) as db: self.assertEqual(db.execute('SELECT station_id,workflow_version,handoff_claimed FROM jobs WHERE id=?', (laser_job['id'],)).fetchone(), ('laser-fbb2', 4, 1))
        with closing(sqlite3.connect(self.root / 'hub' / 'makerspace.sqlite3')) as db:
            self.assertNotIn('agents', {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")})

    def test_legacy_agent_tables_are_preserved_and_revoked_only_in_restored_copy(self):
        path = self.root / 'legacy-hub.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            db.executescript('''
                CREATE TABLE objects(type TEXT, id TEXT, version INTEGER, body TEXT);
                CREATE TABLE workflow_versions(id TEXT, revision INTEGER, body TEXT);
                CREATE TABLE requests(actor TEXT, request_id TEXT, digest TEXT, response TEXT);
                CREATE TABLE audit(sequence INTEGER, at REAL, actor TEXT, action TEXT, target TEXT, request_id TEXT, outcome TEXT);
                CREATE TABLE agents(id TEXT, name TEXT, scopes TEXT, token_hash TEXT, created_at REAL, revoked INTEGER);
                CREATE TABLE agent_requests(actor TEXT, request_id TEXT, action TEXT, digest TEXT, agent_id TEXT);
                PRAGMA user_version=1;
                INSERT INTO agents VALUES('a', 'historical', '["makerspace:read"]', 'hash', 1, 0);
            ''')
        snapshot = self.root / 'legacy-snapshot.sqlite3'
        restored = self.root / 'legacy-restored.sqlite3'
        snapshot_database(path, snapshot, 'hub')
        with closing(sqlite3.connect(snapshot)) as db:
            self.assertEqual(db.execute('SELECT token_hash,revoked FROM agents').fetchone(), ('hash', 0))
        # Restore API performs the historical credential revocation transform.
        from app.backup_databases import snapshot_database as snapshot_copy
        snapshot_copy(path, restored, 'hub')
        protect_restored_database(restored, 'hub')
        with closing(sqlite3.connect(restored)) as db:
            self.assertEqual(db.execute('SELECT token_hash,revoked FROM agents').fetchone(), ('hash', 1))
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('SELECT token_hash,revoked FROM agents').fetchone(), ('hash', 0))

if __name__ == '__main__': unittest.main()
