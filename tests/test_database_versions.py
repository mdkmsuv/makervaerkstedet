import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.core import Queue
from app.laser_server import Inbox


class DatabaseVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def version(path):
        db = sqlite3.connect(path)
        try:
            return db.execute('PRAGMA user_version').fetchone()[0]
        finally:
            db.close()

    def test_new_queue_and_inbox_finish_at_current_version(self):
        queue = Queue(self.root / 'print', [])
        queue.close()
        Inbox(self.root / 'laser', code='testcode')
        self.assertEqual(self.version(self.root / 'print' / 'queue.sqlite3'), 1)
        self.assertEqual(self.version(self.root / 'laser' / 'afleveringer.sqlite3'), 1)

    def test_legacy_queue_with_real_job_is_migrated_without_losing_row(self):
        folder = self.root / 'print'
        folder.mkdir()
        path = folder / 'queue.sqlite3'
        db = sqlite3.connect(path)
        db.execute('''CREATE TABLE jobs (
            id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, digest TEXT NOT NULL,
            filename TEXT NOT NULL, colour TEXT, meta TEXT NOT NULL, created REAL NOT NULL,
            updated REAL NOT NULL, status TEXT NOT NULL, printer_id TEXT, slot INTEGER,
            actual_colour TEXT, message TEXT NOT NULL DEFAULT '', progress INTEGER DEFAULT 0,
            remaining INTEGER, remote_name TEXT, profile TEXT)''')
        db.execute('PRAGMA user_version=0')
        db.execute('CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)')
        db.execute("INSERT INTO settings VALUES ('paused', 'true')")
        db.execute('INSERT INTO jobs(id,request_id,digest,filename,meta,created,updated,status) VALUES (?,?,?,?,?,?,?,?)',
                   ('a' * 32, 'req', 'b' * 64, 'model.svg', '{}', 1, 1, 'queued'))
        db.commit()
        db.close()
        queue = Queue(folder, [])
        self.assertTrue(queue.paused)
        queue.close()
        db = sqlite3.connect(path)
        try:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT filename,status FROM jobs').fetchone(), ('model.svg', 'queued'))
        finally:
            db.close()

    def test_legacy_inbox_with_real_job_is_migrated_without_losing_row(self):
        folder = self.root / 'laser'
        folder.mkdir()
        (folder / 'laererkode.txt').write_text('testcode', encoding='ascii')
        path = folder / 'afleveringer.sqlite3'
        db = sqlite3.connect(path)
        db.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE, fingerprint TEXT, created_at TEXT, student TEXT, classroom TEXT, project TEXT, status TEXT, svg TEXT, details TEXT)')
        db.execute('CREATE TABLE image_settings (job_id TEXT PRIMARY KEY, modes TEXT NOT NULL)')
        db.execute('PRAGMA user_version=0')
        db.execute('INSERT INTO jobs(id,request_id,fingerprint,created_at,student,classroom,project,status,svg,details) VALUES (?,?,?,?,?,?,?,?,?,?)',
                   ('c' * 32, 'req', 'd' * 64, 'now', 'elev', 'klasse', 'projekt', 'queued', '<svg/>', '{}'))
        db.commit()
        db.close()
        inbox = Inbox(folder, code='testcode')
        db = sqlite3.connect(path)
        try:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT project,status FROM jobs').fetchone(), ('projekt', 'queued'))
        finally:
            db.close()

    def test_current_version_preserves_pinned_workflow_rows(self):
        folder = self.root / 'print'
        queue = Queue(folder, [])
        queue.db.execute("INSERT INTO jobs(id,request_id,digest,filename,meta,created,updated,status,workflow,workflow_step,workflow_version) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         ('e' * 32, 'pinned', 'f' * 64, 'pinned.svg', '{}', 1, 1, 'workflow_wait', 'wf', 2, 7))
        queue.db.commit()
        queue.close()
        reopened = Queue(folder, [])
        try:
            row = reopened.db.execute('SELECT status,workflow,workflow_step,workflow_version FROM jobs WHERE id=?', ('e' * 32,)).fetchone()
            self.assertEqual(tuple(row), ('workflow_wait', 'wf', 2, 7))
            self.assertEqual(self.version(folder / 'queue.sqlite3'), 1)
        finally:
            reopened.close()

    def test_current_laser_version_preserves_handoff_row(self):
        folder = self.root / 'laser'
        Inbox(folder, code='testcode')
        path = folder / 'afleveringer.sqlite3'
        db = sqlite3.connect(path)
        db.execute("INSERT INTO jobs(id,request_id,created_at,status,svg,details,payload_available,workflow_version,handoff_claimed,physical_done) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   ('f' * 32, 'pinned', 'now', 'queued', '<svg/>', '{}', 1, 3, 1, 0))
        db.commit()
        db.close()
        reopened = Inbox(folder, code='testcode')
        db = sqlite3.connect(path)
        try:
            self.assertEqual(db.execute('SELECT workflow_version,handoff_claimed,physical_done FROM jobs WHERE id=?', ('f' * 32,)).fetchone(), (3, 1, 0))
            self.assertEqual(self.version(path), 1)
        finally:
            db.close()

    def test_unknown_queue_and_inbox_versions_are_rejected_before_db_write(self):
        for unknown in (2, 99):
            for kind, constructor in (('print', lambda p: Queue(p, [])),
                                       ('laser', lambda p: Inbox(p, code='testcode'))):
                folder = self.root / f'{kind}-{unknown}'
                folder.mkdir()
                path = folder / ('queue.sqlite3' if kind == 'print' else 'afleveringer.sqlite3')
                db = sqlite3.connect(path)
                db.execute(f'PRAGMA user_version={unknown}')
                db.execute('CREATE TABLE marker(value TEXT)')
                db.execute("INSERT INTO marker VALUES ('unchanged')")
                db.commit()
                db.close()
                before = path.read_bytes()
                with self.assertRaises(ValueError):
                    constructor(folder)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(self.version(path), unknown)

    def test_queue_closes_connection_when_later_initialization_fails(self):
        folder = self.root / 'print'
        opened = []
        original_connect = sqlite3.connect

        def connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            opened.append(connection)
            return connection

        with patch('app.core.sqlite3.connect', side_effect=connect), \
                patch('app.core.FileLibrary', side_effect=RuntimeError('injected init failure')):
            with self.assertRaisesRegex(RuntimeError, 'injected init failure'):
                Queue(folder, [])
        self.assertEqual(len(opened), 1)
        # Retain a strong reference: garbage collection must not hide a leak.
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[0].execute('SELECT 1')

    def test_unknown_inbox_version_does_not_create_teacher_secret(self):
        folder = self.root / 'laser'
        folder.mkdir()
        path = folder / 'afleveringer.sqlite3'
        db = sqlite3.connect(path)
        db.execute('PRAGMA user_version=2')
        db.commit()
        db.close()
        with self.assertRaises(ValueError):
            Inbox(folder)
        self.assertFalse((folder / 'laererkode.txt').exists())


if __name__ == '__main__':
    unittest.main()
