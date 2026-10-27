import sqlite3
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.database_migrations import migrate_database
from app.database_migrations import verify_restored_database
from app.backup_databases import protect_restored_database
from app.core import Queue
from app.laser_server import Inbox
import sys
HUB = Path(__file__).parents[1] / 'linux-elevhub' / 'server' / 'elevhub'
sys.path.insert(0, str(HUB))
from makerspace_api import MakerspaceService  # noqa: E402


class DatabaseMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def make_db(self, role, version=0):
        folder = self.root / role
        path = folder / ({'print': 'queue.sqlite3', 'laser': 'afleveringer.sqlite3'}.get(role, 'makerspace.sqlite3'))
        if role == 'print':
            queue = Queue(path.parent, [{'id': 'p1', 'name': 'P1S', 'model': 'P1S', 'enabled': True}])
            try:
                from scripts.smoke_slice import cube
                queue.submit(cube(5), 'migration.stl', 'any', 'migration-print-request')
            finally:
                queue.close()
        elif role == 'laser':
            from tests.test_backup_databases import SVG
            inbox = Inbox(path.parent, code='testcode')
            inbox.submit({'elevnavn': 'Elev', 'klasse': '5.A', 'projekt': 'Skilt',
                          'requestId': 'migration-laser-request', 'svg': SVG})
        else:
            service = MakerspaceService(path)
        db = sqlite3.connect(path)
        db.execute(f'PRAGMA user_version={version}')
        db.commit()
        db.close()
        return path

    def snapshot(self, path):
        db = sqlite3.connect(path)
        try:
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            return {table: (tuple(db.execute(f'PRAGMA table_info("{table}")').fetchall()),
                            tuple(db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall())) for table in tables}
        finally:
            db.close()

    def test_print_and_laser_legacy_marker_only_migration(self):
        for role in ('print', 'laser'):
            path = self.make_db(role, 0)
            before = self.snapshot(path)
            result = migrate_database(path, role)
            self.assertEqual(result['to_version'], 1)
            self.assertEqual(self.snapshot(path), before)
            db = sqlite3.connect(path)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            db.close()

    def test_current_and_hub_are_noop(self):
        for role in ('hub', 'print', 'laser'):
            path = self.make_db(role, 1)
            before = path.read_bytes()
            result = migrate_database(path, role)
            self.assertFalse(result['changed'])
            self.assertEqual(path.read_bytes(), before)

    def test_unknown_schema_version_is_untouched(self):
        path = self.make_db('print', 2)
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            migrate_database(path, 'print')
        self.assertEqual(path.read_bytes(), before)

    def test_commit_failure_rolls_back_marker_and_rows(self):
        path = self.make_db('laser', 0)
        before = path.read_bytes()
        with patch('app.database_migrations._validate_with_deadline', side_effect=[None, OSError('injected migration failure')]):
            with self.assertRaisesRegex(OSError, 'injected migration failure'):
                migrate_database(path, 'laser')
        self.assertEqual(path.read_bytes(), before)

    def test_links_are_rejected(self):
        path = self.make_db('print', 0)
        link = self.root / 'link.sqlite3'
        try:
            link.symlink_to(path)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable')
        with self.assertRaises(ValueError):
            migrate_database(link, 'print')

    def test_restored_real_databases_allow_only_documented_transforms(self):
        for role in ('hub', 'print', 'laser'):
            source = self.make_db(role, 0 if role != 'hub' else 1)
            destination = self.root / ('copy-' + role + '.sqlite3')
            shutil.copy2(source, destination)
            protect_restored_database(destination, role)
            if role != 'hub':
                db = sqlite3.connect(destination)
                db.execute('PRAGMA user_version=1')
                db.commit()
                db.close()
            result = verify_restored_database(source, destination, role)
            self.assertTrue(result['verified'])

    def test_restored_changed_job_or_schema_is_rejected(self):
        source = self.make_db('print', 1)
        destination = self.root / 'changed.sqlite3'
        shutil.copy2(source, destination)
        db = sqlite3.connect(destination)
        db.execute("INSERT INTO jobs(id,request_id,digest,filename,meta,created,updated,status,printer_id,workflow,workflow_step,workflow_version,workflow_authorized,workflow_complete) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   ('x', 'changed', 'd', 'changed.svg', '{}', 1, 1, 'workflow_wait', None, 'changed', 1, 1, 0, 0))
        db.commit()
        db.close()
        with self.assertRaises(ValueError):
            verify_restored_database(source, destination, 'print')

    def test_restore_transform_must_be_present_on_candidate(self):
        # A fresh hub has no credential table and therefore has no restore
        # transform.  Exercise the compatibility transform with a historical
        # schema fixture instead of constructing the removed token service.
        hub_source = self.root / 'historical-hub.sqlite3'
        db = sqlite3.connect(hub_source)
        db.executescript('''
            CREATE TABLE objects(type TEXT, id TEXT, version INTEGER, body TEXT);
            CREATE TABLE workflow_versions(id TEXT, revision INTEGER, body TEXT);
            CREATE TABLE requests(actor TEXT, request_id TEXT, digest TEXT, response TEXT);
            CREATE TABLE audit(sequence INTEGER, at REAL, actor TEXT, action TEXT, target TEXT, request_id TEXT, outcome TEXT);
            CREATE TABLE agents(id TEXT, name TEXT, scopes TEXT, token_hash TEXT, created_at REAL, revoked INTEGER);
            CREATE TABLE agent_requests(actor TEXT, request_id TEXT, action TEXT, digest TEXT, agent_id TEXT);
            PRAGMA user_version=1;
            INSERT INTO agents VALUES('legacy', 'historical', '[]', 'digest', 1, 0);
        ''')
        db.commit(); db.close()
        hub_destination = self.root / 'hub-copy.sqlite3'
        shutil.copy2(hub_source, hub_destination)
        with self.assertRaises(ValueError):
            verify_restored_database(hub_source, hub_destination, 'hub')

        print_source = self.make_db('print', 1)
        print_destination = self.root / 'print-copy.sqlite3'
        shutil.copy2(print_source, print_destination)
        protect_restored_database(print_destination, 'print')
        db = sqlite3.connect(print_destination)
        db.execute("UPDATE settings SET value='false' WHERE key='paused'")
        db.commit()
        db.close()
        with self.assertRaises(ValueError):
            verify_restored_database(print_source, print_destination, 'print')

    def test_source_current_and_candidate_legacy_version_mismatch_is_rejected(self):
        source = self.make_db('print', 1)
        destination = self.root / 'legacy-copy.sqlite3'
        shutil.copy2(source, destination)
        db = sqlite3.connect(destination)
        db.execute('PRAGMA user_version=0')
        db.commit()
        db.close()
        with self.assertRaises(ValueError):
            verify_restored_database(source, destination, 'print')

        destination.unlink()
        shutil.copy2(source, destination)
        db = sqlite3.connect(destination)
        db.execute('CREATE TABLE unexpected(value TEXT)')
        db.commit()
        db.close()
        with self.assertRaises(ValueError):
            verify_restored_database(source, destination, 'print')


    def test_print_rowids_used_for_queue_order_and_watermarks_must_be_preserved(self):
        source = self.make_db('print', 1)
        destination = self.root / 'changed-rowids.sqlite3'
        shutil.copy2(source, destination)
        protect_restored_database(destination, 'print')
        db = sqlite3.connect(destination)
        try:
            visible = db.execute('SELECT * FROM jobs').fetchall()
            db.execute('UPDATE jobs SET rowid=rowid+1000')
            db.commit()
            self.assertEqual(db.execute('SELECT * FROM jobs').fetchall(), visible)
        finally:
            db.close()
        with self.assertRaisesRegex(ValueError, 'data matcher ikke kilden'):
            verify_restored_database(source, destination, 'print')


if __name__ == '__main__':
    unittest.main()
