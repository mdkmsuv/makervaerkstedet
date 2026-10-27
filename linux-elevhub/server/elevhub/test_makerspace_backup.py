"""Tests for consistent minimal and historical makerspace snapshots."""

import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from makerspace_backup import backup, restore_to_new_file, _remove_owned
from makerspace_store import MakerspaceStore
from makerspace_workflows import templates


def legacy_database(path):
    with closing(sqlite3.connect(path)) as db:
        db.executescript('''
            CREATE TABLE objects(type TEXT, id TEXT, version INTEGER, body TEXT);
            CREATE TABLE workflow_versions(id TEXT, revision INTEGER, body TEXT);
            CREATE TABLE requests(actor TEXT, request_id TEXT, digest TEXT, response TEXT);
            CREATE TABLE audit(sequence INTEGER, at REAL, actor TEXT, action TEXT, target TEXT, request_id TEXT, outcome TEXT);
            CREATE TABLE agents(id TEXT, name TEXT, scopes TEXT, token_hash TEXT, created_at REAL, revoked INTEGER);
            CREATE TABLE agent_requests(actor TEXT, request_id TEXT, action TEXT, digest TEXT, agent_id TEXT);
            PRAGMA user_version=1;
            INSERT INTO agents VALUES('legacy-id', 'historical', '["makerspace:read"]', 'token-digest', 1, 0);
        ''')


class MakerspaceBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "source.sqlite3"
        MakerspaceStore(self.source)

    @staticmethod
    def actor():
        return {
            'id': 'teacher:' + '1' * 32,
            'role': 'teacher',
            'scopes': ['machines:write', 'workflows:write', 'jobs:write'],
        }

    def command(self, store, action, params, expected, request_id):
        return store.execute(self.actor(), {
            'action': action, 'params': params, 'expected_version': expected,
            'request_id': request_id,
        })

    def test_snapshot_uses_online_backup_and_keeps_minimal_source_unchanged(self):
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute('PRAGMA journal_mode=WAL')
        stop = threading.Event()
        started = threading.Event()
        errors = []

        def writer():
            connection = None
            try:
                connection = sqlite3.connect(self.source, timeout=5)
                version = 0
                while not stop.is_set():
                    version += 1
                    for letter in ('a', 'b'):
                        connection.execute(
                            "INSERT OR REPLACE INTO objects(type,id,version,body) VALUES(?,?,?,?)",
                            ('machine', letter * 32, version, json.dumps({'id': letter * 32, 'version': version})))
                    connection.commit()
                    started.set()
            except Exception as error:
                errors.append(error)
            finally:
                if connection is not None:
                    connection.close()

        thread = threading.Thread(target=writer)
        thread.start()
        destination = Path(self.temp.name) / "snapshot.sqlite3"
        try:
            self.assertTrue(started.wait(5))
            before = self.source.read_bytes()
            result = backup(self.source, destination)
        finally:
            stop.set()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        self.assertEqual(result["integrity"], "ok")
        self.assertTrue(destination.exists())
        self.assertNotEqual(self.source.read_bytes(), before)
        with closing(sqlite3.connect(destination)) as connection:
            versions = [row[0] for row in connection.execute('SELECT version FROM objects ORDER BY id')]
            self.assertEqual(len(versions), 2)
            self.assertEqual(versions[0], versions[1])
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='agents'").fetchone())

    def test_restore_roundtrip_preserves_minimal_rows_and_idempotency(self):
        with closing(sqlite3.connect(self.source)) as db:
            db.execute("INSERT INTO objects VALUES(?,?,?,?)", ('machine', 'a' * 32, 1, '{"name":"Bord"}'))
            db.execute("INSERT INTO requests VALUES(?,?,?,?)", ('teacher:' + '1' * 32, '2' * 32, 'd' * 64, '{"ok":true}'))
            db.commit()
        snapshot = Path(self.temp.name) / 'snapshot.sqlite3'
        restored = Path(self.temp.name) / 'restored.sqlite3'
        backup(self.source, snapshot)
        restore_to_new_file(snapshot, restored)
        with closing(sqlite3.connect(restored)) as db:
            self.assertEqual(db.execute("SELECT body FROM objects WHERE id=?", ('a' * 32,)).fetchone()[0], '{"name":"Bord"}')
            self.assertEqual(db.execute("SELECT response FROM requests WHERE request_id=?", ('2' * 32,)).fetchone()[0], '{"ok":true}')
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='agents'").fetchone())

    def test_real_store_workflow_roundtrip_replays_without_new_job_or_audit(self):
        store = MakerspaceStore(self.source)
        definition = templates()['manual']
        created = self.command(store, 'workflow.create', {'definition': definition}, 0, '1' * 32)['result']
        published = self.command(store, 'workflow.publish', {'id': created['id']}, created['version'], '2' * 32)['result']
        machine = self.command(store, 'machine.create', {
            'name': 'Bord', 'location': 'Rum', 'enabled': True,
            'workflow_id': created['id'],
        }, 0, '3' * 32)['result']
        job_envelope = {
            'action': 'job.create',
            'params': {'machine_id': machine['id'], 'title': 'Testopgave'},
            'expected_version': 0, 'request_id': '4' * 32,
        }
        job = store.execute(self.actor(), job_envelope)['result']
        for index, step in enumerate(definition['steps'][1:], start=1):
            job = self.command(store, 'job.advance', {
                'id': job['id'], 'step_id': step['id'], 'confirmed': True,
            }, job['version'], f'{index + 4:032x}')['result']
        self.assertEqual(job['status'], 'done')
        with store.connection() as db:
            before_jobs = db.execute("SELECT COUNT(*) FROM objects WHERE type='job'").fetchone()[0]
            before_audit = db.execute('SELECT COUNT(*) FROM audit').fetchone()[0]

        snapshot = Path(self.temp.name) / 'workflow-snapshot.sqlite3'
        restored = Path(self.temp.name) / 'workflow-restored.sqlite3'
        backup(self.source, snapshot)
        restore_to_new_file(snapshot, restored)
        restored_store = MakerspaceStore(restored)
        replayed = restored_store.execute(self.actor(), job_envelope)
        self.assertEqual(replayed['result']['id'], job['id'])
        with restored_store.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM objects WHERE type='job'").fetchone()[0], before_jobs)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM audit').fetchone()[0], before_audit)
            persisted = json.loads(db.execute("SELECT body FROM objects WHERE type='job'").fetchone()[0])
        self.assertEqual(persisted['definition'], definition)
        self.assertEqual(persisted['workflow_revision'], published['active_revision'])

    def test_historical_agent_tables_are_preserved_in_snapshot_and_revoked_on_restore(self):
        source = Path(self.temp.name) / 'legacy.sqlite3'
        legacy_database(source)
        snapshot = Path(self.temp.name) / 'legacy-snapshot.sqlite3'
        restored = Path(self.temp.name) / 'legacy-restored.sqlite3'
        backup(source, snapshot)
        with closing(sqlite3.connect(snapshot)) as db:
            self.assertEqual(db.execute('SELECT token_hash,revoked FROM agents').fetchone(), ('token-digest', 0))
        restore_to_new_file(snapshot, restored)
        with closing(sqlite3.connect(restored)) as db:
            self.assertEqual(db.execute('SELECT token_hash,revoked FROM agents').fetchone(), ('token-digest', 1))
        with closing(sqlite3.connect(source)) as db:
            self.assertEqual(db.execute('SELECT token_hash,revoked FROM agents').fetchone(), ('token-digest', 0))

    def test_refuses_existing_or_symlink_destination_and_bad_source(self):
        destination = Path(self.temp.name) / "already.sqlite3"
        destination.write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            backup(self.source, destination)
        symlink = Path(self.temp.name) / "source-link.sqlite3"
        try:
            symlink.symlink_to(self.source)
        except (OSError, NotImplementedError):
            symlink = None
        if symlink is not None:
            with self.assertRaises(ValueError):
                backup(symlink, Path(self.temp.name) / "link-out.sqlite3")
        invalid = Path(self.temp.name) / "invalid.sqlite3"
        with closing(sqlite3.connect(invalid)) as db:
            db.execute('PRAGMA user_version=99')
        with self.assertRaises(ValueError):
            backup(invalid, Path(self.temp.name) / "invalid-out.sqlite3")
        self.assertFalse((Path(self.temp.name) / "invalid-out.sqlite3").exists())

    def test_refuses_existing_sqlite_sidecars(self):
        for suffix in ('-wal', '-shm', '-journal'):
            destination = Path(self.temp.name) / ('sidecar' + suffix)
            destination.write_bytes(b'occupied')
            with self.assertRaises(FileExistsError):
                backup(self.source, Path(self.temp.name) / 'sidecar')
            destination.unlink()

    def test_missing_required_schema_is_rejected_and_partial_destination_removed(self):
        source = Path(self.temp.name) / 'missing-audit.sqlite3'
        with closing(sqlite3.connect(source)) as db:
            db.executescript('''
                CREATE TABLE objects(type TEXT, id TEXT, version INTEGER, body TEXT);
                CREATE TABLE workflow_versions(id TEXT, revision INTEGER, body TEXT);
                CREATE TABLE requests(actor TEXT, request_id TEXT, digest TEXT, response TEXT);
                PRAGMA user_version=1;
            ''')
        destination = Path(self.temp.name) / 'missing-copy.sqlite3'
        with self.assertRaises(ValueError):
            backup(source, destination)
        self.assertFalse(destination.exists())

    def test_expired_snapshot_deadline_removes_partial_copy(self):
        destination = Path(self.temp.name) / 'expired.sqlite3'
        with patch('makerspace_backup._BACKUP_TIMEOUT', 0):
            with self.assertRaises((TimeoutError, ValueError)):
                backup(self.source, destination)
        self.assertFalse(destination.exists())

    def test_optional_legacy_columns_are_checked_without_rejecting_extra_columns(self):
        source = Path(self.temp.name) / 'legacy.sqlite3'
        legacy_database(source)
        with closing(sqlite3.connect(source)) as db:
            db.execute('ALTER TABLE agents ADD COLUMN review_note TEXT')
            db.commit()
        result = backup(source, Path(self.temp.name) / 'legacy-copy.sqlite3')
        self.assertEqual(result['integrity'], 'ok')
        malformed = Path(self.temp.name) / 'malformed.sqlite3'
        legacy_database(malformed)
        with closing(sqlite3.connect(malformed)) as db:
            db.execute('ALTER TABLE agents RENAME TO agents_old')
            db.execute('CREATE TABLE agents(id TEXT)')
            db.commit()
        with self.assertRaises(ValueError):
            backup(malformed, Path(self.temp.name) / 'malformed-copy.sqlite3')

    def test_failure_after_destination_open_cleans_owned_file(self):
        destination = Path(self.temp.name) / 'failed.sqlite3'
        with patch('makerspace_backup._validate_database', side_effect=[None, ValueError('forced backup failure')]):
            with self.assertRaises(ValueError):
                backup(self.source, destination)
        self.assertFalse(destination.exists())

    def test_cleanup_never_removes_unowned_sidecars_or_replaced_destination(self):
        destination = Path(self.temp.name) / 'owned.sqlite3'
        destination.write_bytes(b'own')
        stat = destination.lstat()
        sidecar = destination.with_name(destination.name + '-journal')
        sidecar.write_bytes(b'belongs to someone else')
        _remove_owned(destination, (stat.st_dev, stat.st_ino))
        self.assertFalse(destination.exists())
        self.assertEqual(sidecar.read_bytes(), b'belongs to someone else')
        destination.write_bytes(b'new file')
        _remove_owned(destination, (-1, -1))
        self.assertEqual(destination.read_bytes(), b'new file')


if __name__ == '__main__':
    unittest.main()
