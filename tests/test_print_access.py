import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path

from app.core import Queue, SubmissionBlocked
from scripts.smoke_slice import cube


class PrintAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 10_000
        self.queue = Queue(self.temp.name, [], demo=True, clock=lambda: self.now)
        self.number = 0

    def tearDown(self):
        self.queue.close()
        self.temp.cleanup()

    def restart(self):
        self.queue.close()
        self.queue = Queue(self.temp.name, [], demo=True, clock=lambda: self.now)

    def submit(self, source='ip:203.0.113.11', request_id=None):
        self.number += 1
        return self.queue.submit(cube(), 'model.stl', 'any',
                                 request_id or f'print-access-{self.number:05d}', source=source)

    def block(self):
        for _ in range(9):
            self.submit()
        with self.assertRaises(SubmissionBlocked):
            self.submit()
        return self.queue.print_access_overview()['blocked'][0]

    def test_100_simultaneous_requests_block_one_computer_without_a_two_job_cap(self):
        def send(number):
            try:
                return self.queue.submit(cube(), 'model.stl', 'any', f'concurrent-access-{number:05d}', source='computer-a')
            except SubmissionBlocked:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            jobs = list(pool.map(send, range(100)))
        self.assertEqual(sum(job is not None for job in jobs), 9)
        self.assertEqual(len(list((Path(self.temp.name) / 'uploads').glob('*.stl'))), 9)
        self.assertEqual(self.submit(source='computer-b')['status'], 'queued')
        self.assertEqual(len(self.queue.print_access_overview()['blocked']), 1)
        self.assertEqual(self.queue.print_access('computer-a')['retry_after'], 3600)

    def test_retries_and_restart_do_not_extend_block_and_expiry_reopens_automatically(self):
        block = self.block()
        self.restart()
        self.now += 1000
        with self.assertRaises(SubmissionBlocked) as blocked:
            self.submit()
        self.assertEqual(blocked.exception.retry_after, 2600)
        self.assertEqual(self.queue.print_access_overview()['blocked'][0]['blocked_until'], block['blocked_until'])
        self.now += 2599
        self.restart()
        self.assertFalse(self.queue.print_access('ip:203.0.113.11')['allowed'])
        self.now += 1
        self.assertEqual(self.queue.print_access_overview()['blocked'], [])
        self.assertEqual(self.submit()['status'], 'queued')

    def test_teacher_unlock_resets_count_at_same_timestamp_and_old_id_cannot_unlock_new_block(self):
        first = self.block()
        self.queue.unblock_print_access(first['id'])
        self.restart()
        self.assertTrue(self.queue.print_access('ip:203.0.113.11')['allowed'])
        second = self.block()
        self.queue.unblock_print_access(first['id'])
        self.assertFalse(self.queue.print_access('ip:203.0.113.11')['allowed'])
        self.queue.unblock_print_access(second['id'])
        self.assertEqual(self.submit()['status'], 'queued')

    def test_receipt_retry_is_free_even_when_blocked_and_cannot_change_source_or_file(self):
        first = self.submit(request_id='accepted-request-12345')
        for _ in range(8): self.submit()
        with self.assertRaises(SubmissionBlocked): self.submit()
        self.assertEqual(self.submit(request_id='accepted-request-12345')['id'], first['id'])
        with self.assertRaises(ValueError):
            self.submit(source='another-computer', request_id='accepted-request-12345')
        with self.assertRaises(ValueError):
            self.queue.submit(cube(30), 'model.stl', 'any', 'accepted-request-12345', source='ip:203.0.113.11')
        self.assertEqual(len(self.queue.snapshot()['jobs']), 9)

    def test_cancelled_failed_done_all_count_but_rolling_window_expires(self):
        for i in range(9):
            job = self.submit()
            self.queue._change(job['id'], status=('done', 'failed', 'cancelled')[i % 3])
        with self.assertRaises(SubmissionBlocked): self.submit()
        # A separate source at the exact rolling-window boundary is eligible.
        for _ in range(9): self.submit(source='computer-b')
        self.now += 3600
        self.assertEqual(self.submit(source='computer-b')['status'], 'queued')

    def test_settings_persist_and_never_shorten_or_extend_existing_block(self):
        block = self.block()
        self.queue.set_print_guard(30)
        self.restart()
        self.assertEqual(self.queue.print_guard_settings()['trigger_count'], 30)
        self.assertEqual(self.queue.print_access_overview()['blocked'][0]['blocked_until'], block['blocked_until'])
        for value in (True, 0, 1, 101, 3.5, '10', None):
            with self.assertRaises(ValueError): self.queue.set_print_guard(value)

    def test_old_quota_is_removed_on_upgrade(self):
        self.queue.db.execute("INSERT INTO settings VALUES('submission_limits',?)", (json.dumps({'per_hour':3,'outstanding':2}),))
        self.queue.db.commit()
        self.restart()
        self.assertIsNone(self.queue.db.execute("SELECT value FROM settings WHERE key='submission_limits'").fetchone())
        for _ in range(9): self.submit()

    def test_invalid_files_do_not_trigger_block_and_other_sources_are_private(self):
        for _ in range(9): self.submit()
        with self.assertRaises(ValueError):
            self.queue.submit(b'not-stl', 'bad.stl', 'any', 'bad-upload-request-1', source='ip:203.0.113.11')
        self.assertTrue(self.queue.print_access('ip:203.0.113.11')['allowed'])
        self.assertNotIn('source', self.queue.snapshot()['jobs'][0])
        self.assertNotIn('203.0.113.11', json.dumps(self.queue.print_access('computer-b')))


if __name__ == '__main__':
    unittest.main()
