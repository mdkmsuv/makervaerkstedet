import sqlite3
import tempfile
import unittest

from app.laser_inbox_lifecycle import (cleanup_terminal_payload, cleanup_terminal_payloads,
                                       enable_incremental_autovacuum, ensure_payload_column,
                                       payload_available)


class LaserInboxLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = sqlite3.connect(self.temp.name + '/inbox.sqlite3')
        self.db.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT, svg TEXT, details TEXT)')
        self.db.execute('CREATE TABLE image_settings (job_id TEXT PRIMARY KEY, modes TEXT NOT NULL)')
        self.db.execute("INSERT INTO jobs VALUES ('done-id','done','<svg>payload</svg>','{\"width\":100}')")
        self.db.execute("INSERT INTO jobs VALUES ('ready-id','ready','<svg>keep</svg>','{}')")
        self.db.execute("INSERT INTO image_settings VALUES ('done-id','{}')")
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_done_drops_svg_and_image_settings_but_keeps_history(self):
        self.assertTrue(cleanup_terminal_payload(self.db, 'done-id'))
        row = self.db.execute('SELECT status, svg, details, payload_available FROM jobs WHERE id=?', ('done-id',)).fetchone()
        self.assertEqual(row, ('done', None, '{"width":100}', 0))
        self.assertIsNone(self.db.execute('SELECT * FROM image_settings WHERE job_id=?', ('done-id',)).fetchone())
        self.assertFalse(payload_available({'status': 'done', 'payload_available': 0}))

    def test_open_job_retains_payload_and_restart_retry_is_idempotent(self):
        self.assertFalse(cleanup_terminal_payload(self.db, 'ready-id'))
        self.assertEqual(self.db.execute('SELECT svg FROM jobs WHERE id=?', ('ready-id',)).fetchone()[0], '<svg>keep</svg>')
        self.assertEqual(cleanup_terminal_payloads(self.db), 1)
        self.assertEqual(cleanup_terminal_payloads(self.db), 0)
        ensure_payload_column(self.db)

    def test_missing_job_does_not_change_database(self):
        self.assertFalse(cleanup_terminal_payload(self.db, 'missing'))
        self.assertEqual(self.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 2)

    def test_legacy_database_can_enable_incremental_reclaim(self):
        self.assertEqual(self.db.execute('PRAGMA auto_vacuum').fetchone()[0], 0)
        self.assertTrue(enable_incremental_autovacuum(self.db))
        self.assertEqual(self.db.execute('PRAGMA auto_vacuum').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
