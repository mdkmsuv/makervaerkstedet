"""Bounded queue inventory and eventual crash cleanup without live data."""
from contextlib import nullcontext
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from app.core import Queue, CLEANUP_RETRY_BATCH
from scripts.smoke_slice import cube


class QueueStorageBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.queue = Queue(self.folder, [], demo=True)

    def tearDown(self):
        self.queue.close()
        self.temp.cleanup()

    def test_terminal_history_is_reconciled_in_pages_and_worker_finishes(self):
        job = self.queue.submit(cube(10), 'fixture.stl', 'any', 'fixture-request-long')
        self.queue.resolve(job['id'], 'cancelled')
        ids = [f'{index:032x}' for index in range(1, CLEANUP_RETRY_BATCH * 3 + 2)]
        for job_id in ids:
            self.queue.db.execute('''INSERT INTO jobs
                (id,request_id,digest,filename,meta,created,updated,status)
                VALUES(?,?,?,?,?,?,?,?)''',
                (job_id, job_id, job['digest'], 'old.stl', '{}', 1, 1, 'done'))
        self.queue.db.commit()
        late_upload = self.folder / 'uploads' / (ids[-1] + '.stl')
        late_upload.write_bytes(b'old owned transient upload')
        self.queue._terminal_cleanup_cursor = ''
        self.queue._terminal_cleanup_complete = False
        result = self.queue.cleanup_terminal_jobs()
        self.assertLessEqual(len(result), CLEANUP_RETRY_BATCH)
        self.assertTrue(late_upload.exists())
        for _ in range(6):
            self.queue.retry_pending_cleanup()
        self.assertFalse(late_upload.exists())
        self.assertTrue(self.queue._terminal_cleanup_complete)
        self.assertEqual(self.queue.db.execute('SELECT count(*) FROM jobs').fetchone()[0], len(ids) + 1)

    def test_upload_link_metadata_is_read_without_following_target(self):
        entry = Mock(name='entry')
        entry.name = 'external.stl'
        entry.stat.return_value = types.SimpleNamespace(st_mode=stat.S_IFLNK, st_size=999999)
        with patch('app.core.os.scandir', return_value=nullcontext(iter([entry]))):
            with self.assertRaisesRegex(ValueError, 'omdirigeret'):
                self.queue._upload_bytes()
        entry.stat.assert_called_once_with(follow_symlinks=False)

    def test_inventory_budget_stops_before_unbounded_directory_walk(self):
        visited = []
        def entries():
            for index in range(100):
                visited.append(index)
                yield types.SimpleNamespace(name='unknown-' + str(index))
        with patch('app.core.UPLOAD_INVENTORY_LIMIT', 3), \
             patch('app.core.os.scandir', return_value=nullcontext(entries())):
            with self.assertRaisesRegex(ValueError, 'oprydning'):
                self.queue.submit(cube(10), 'new.stl', 'any', 'bounded-inventory')
        self.assertEqual(visited, [0, 1, 2, 3])
        self.assertEqual(self.queue.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)

    def test_upload_directory_disguised_as_model_rejects_before_job_commit(self):
        (self.folder / 'uploads' / 'directory.stl').mkdir()
        with self.assertRaisesRegex(ValueError, 'ugyldig'):
            self.queue.submit(cube(10), 'new.stl', 'any', 'invalid-upload-path')
        self.assertEqual(self.queue.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
