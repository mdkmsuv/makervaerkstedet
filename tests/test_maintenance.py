from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from app.core import Queue


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = datetime(2026, 9, 15, 12).timestamp()
        self.configs = [{'id':'one','name':'E1','model':'A1 mini'}, {'id':'two','name':'E2','model':'A1 mini'}]
        self.q = Queue(self.temp.name, self.configs, clock=lambda:self.now)

    def tearDown(self):
        self.q.close()
        self.temp.cleanup()

    def test_first_month_has_no_fabricated_completion_and_remains_due_when_offline(self):
        initial = self.q.maintenance_snapshot()
        self.assertEqual(initial['due_count'], 0)
        self.assertIsNone(initial['printers'][0]['completed_on'])
        self.assertEqual(initial['printers'][0]['next_due'], '2026-10-15')
        self.now = datetime(2026, 10, 15, 0).timestamp()
        self.assertEqual(self.q.maintenance_snapshot()['due_count'], 2)
        self.now = datetime(2026, 12, 10, 12).timestamp()
        self.assertEqual(self.q.maintenance_snapshot()['due_count'], 2)

    def test_completion_only_moves_one_printer_and_does_not_release_or_resume(self):
        self.q.pause(True)
        self.now = datetime(2026, 10, 20, 12).timestamp()
        self.q.complete_maintenance('one')
        data = self.q.maintenance_snapshot()
        self.assertEqual(data['due_count'], 1)
        self.assertEqual(data['printers'][0]['completed_on'], '2026-10-20')
        self.assertEqual(data['printers'][0]['next_due'], '2026-11-20')
        self.assertTrue(self.q.paused)
        self.assertFalse(any(p['bed_clear'] for p in self.q.printers.values()))
        self.assertEqual(self.q.snapshot()['jobs'], [])

    def test_calendar_month_handles_short_month_leap_year_and_new_year(self):
        for start, due in [('2027-01-31','2027-02-28'),('2028-01-31','2028-02-29'),('2026-12-31','2027-01-31')]:
            with self.subTest(start=start):
                self.now = datetime.fromisoformat(start+'T12:00:00').timestamp()
                self.q.complete_maintenance('one')
                self.assertEqual(self.q.maintenance_snapshot()['printers'][0]['next_due'], due)

    def test_restart_and_rename_preserve_dates_by_printer_id(self):
        self.q.complete_maintenance('one')
        self.q.close()
        self.now = datetime(2026, 10, 16, 12).timestamp()
        self.configs[0]['name'] = 'Renamed printer'
        self.q = Queue(self.temp.name, self.configs, clock=lambda:self.now)
        data = self.q.maintenance_snapshot()
        self.assertEqual(data['due_count'], 2)
        self.assertEqual(data['printers'][0]['name'], 'Renamed printer')
        self.assertEqual(data['printers'][0]['completed_on'], '2026-09-15')
        self.assertEqual(data['printers'][1]['next_due'], '2026-10-15')

    def test_unknown_printer_cannot_be_marked_complete(self):
        with self.assertRaises(ValueError):
            self.q.complete_maintenance('missing')
        self.assertTrue(all(p['completed_on'] is None for p in self.q.maintenance_snapshot()['printers']))


if __name__ == '__main__':
    unittest.main()
