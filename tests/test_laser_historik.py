"""Pupil names in laser deliveries must not be kept forever (GDPR), and the
open submission queue must not be able to fill the server disk."""
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from unittest import mock
import uuid

from app.laser_inbox_lifecycle import delete_expired_history
from app.laser_server import Inbox, HISTORY_SWEEP_SECONDS

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" data-laservaerksted="1" width="100mm" height="60mm" '
       'viewBox="0 0 100 60"><g data-operation="cut" fill="none" stroke="#000">'
       '<path d="M10 10H60V40H10Z" data-operation="cut"/></g></svg>')


def dage_siden(dage):
    return (datetime.now(timezone.utc) - timedelta(days=dage)).isoformat()


class HistorikTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.inbox = Inbox(self.temp.name, code='test')

    def aflever(self, navn, dage_gammel, status, afsluttet_dage=None):
        job = self.inbox.submit({'elevnavn': navn, 'klasse': '5A', 'projekt': 'Skilt',
                                 'requestId': str(uuid.uuid4()), 'svg': SVG})['id']
        if status != 'received':
            self.inbox.status(job, status)
        afsluttet = dage_gammel if afsluttet_dage is None else afsluttet_dage
        with self.inbox.db() as db:
            db.execute('UPDATE jobs SET created_at=?, finished_at=CASE WHEN finished_at IS NULL THEN NULL ELSE ? END '
                       'WHERE id=?', (dage_siden(dage_gammel), dage_siden(afsluttet), job))
        return job

    def elever(self):
        return sorted(j['student'] for j in self.inbox.jobs())

    def test_old_done_rows_are_deleted_but_pending_and_recent_are_kept(self):
        # Finishing a job also sweeps, so create the expired one last.
        self.aflever('Ny færdig', 5, 'done')
        self.aflever('Gammel venter', 45, 'received')
        self.aflever('Gammel klar', 45, 'ready')
        self.aflever('Gammel færdig', 45, 'done')
        with self.inbox.db() as db:
            self.assertEqual(delete_expired_history(db), 1)
        self.assertEqual(self.elever(), ['Gammel klar', 'Gammel venter', 'Ny færdig'])

    def test_retention_counts_from_completion_not_from_submission(self):
        # Waited 45 days in the queue, finished 2 days ago: kept 28 more days.
        self.aflever('Længe i køen', 45, 'done', afsluttet_dage=2)
        with self.inbox.db() as db:
            self.assertEqual(delete_expired_history(db), 0)
        self.assertEqual(self.elever(), ['Længe i køen'])

    def test_finishing_a_job_also_sweeps_expired_history(self):
        self.aflever('Gammel færdig', 45, 'done')
        nyt = self.aflever('Aktuel', 0, 'received')
        self.inbox.status(nyt, 'done')
        self.assertEqual(self.elever(), ['Aktuel'])

    def test_restart_sweeps_expired_history(self):
        self.aflever('Gammel færdig', 45, 'done')
        self.assertEqual(sorted(j['student'] for j in Inbox(self.temp.name, code='test').jobs()), [])

    def test_a_long_running_server_sweeps_when_the_teacher_list_loads(self):
        self.aflever('Gammel færdig', 45, 'done')
        self.assertEqual(self.elever(), ['Gammel færdig'])  # swept less than an hour ago
        self.inbox.last_sweep -= HISTORY_SWEEP_SECONDS
        self.assertEqual(self.elever(), [])

    def test_deleted_names_are_overwritten_and_space_is_returned(self):
        gammel = self.aflever('Zyxwvut Elevnavn', 45, 'done')
        self.aflever('Beholdes', 0, 'received')
        with self.inbox.db() as db:
            # A stored drawing spanning ~100 pages (clean_svg strips padding, so set it directly).
            db.execute('UPDATE jobs SET svg=? WHERE id=?', ('x' * 400_000, gammel))
        with self.inbox.db() as db:
            self.assertEqual(delete_expired_history(db), 1)
            self.assertEqual(db.execute('PRAGMA freelist_count').fetchone()[0], 0)
        self.assertNotIn('Zyxwvut Elevnavn'.encode(), self.inbox.db_path.read_bytes())
        self.assertEqual(self.elever(), ['Beholdes'])

    def test_queue_refuses_new_work_when_pending_svg_is_too_large(self):
        klar = self.aflever('Venter', 0, 'ready')
        with self.inbox.db() as db:
            gemt = db.execute('SELECT octet_length(svg) FROM jobs').fetchone()[0]  # cleaned, not raw, size
        with mock.patch('app.laser_server.MAX_QUEUE_BYTES', 2 * gemt):
            self.aflever('Plads endnu', 0, 'received')
            with self.assertRaisesRegex(ValueError, 'køen er fuld'):
                self.aflever('For meget', 0, 'received')
            self.inbox.status(klar, 'done')  # finished work frees its payload
            self.aflever('Plads igen', 0, 'received')


if __name__ == '__main__':
    unittest.main()
