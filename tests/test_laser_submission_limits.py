"""Classroom fairness quotas and exportable metadata, with isolated storage."""
from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from unittest.mock import patch
import uuid

from app.laser_server import Inbox, clean_svg


SVG = '<svg xmlns="http://www.w3.org/2000/svg" data-laservaerksted="1" width="100mm" height="60mm" viewBox="0 0 100 60"><g data-operation="cut"><path d="M10 10H60V40H10Z" data-operation="cut"/></g></svg>'


class LaserSubmissionLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='laser-quota-test-')
        self.addCleanup(self.temp.cleanup)
        self.inbox = Inbox(self.temp.name, code='test-quota-only')

    def payload(self, **changes):
        return {'elevnavn': 'Åse', 'klasse': '3.A', 'projekt': 'Navneskilt',
                'requestId': str(uuid.uuid4()), 'svg': SVG, **changes}

    def test_full_sender_still_resolves_lost_response(self):
        with patch('app.laser_server.MAX_SENDER_JOBS', 2):
            original = self.payload()
            first = self.inbox.submit(original)
            self.inbox.submit(self.payload())
            with self.assertRaisesRegex(ValueError, 'venter på læreren'):
                self.inbox.submit(self.payload())
            self.assertEqual(self.inbox.submit(original), {'id': first['id'], 'duplicate': True})
            self.assertEqual(len(self.inbox.jobs()), 2)

    def test_other_pupil_and_class_have_independent_capacity(self):
        with patch('app.laser_server.MAX_SENDER_JOBS', 1):
            self.inbox.submit(self.payload())
            self.inbox.submit(self.payload(elevnavn='Maja'))
            self.inbox.submit(self.payload(klasse='4.A'))
            with self.assertRaises(ValueError):
                self.inbox.submit(self.payload())
        self.assertEqual(len(self.inbox.jobs()), 3)

    def test_unicode_case_and_space_variants_share_capacity(self):
        with patch('app.laser_server.MAX_SENDER_JOBS', 1):
            self.inbox.submit(self.payload(elevnavn='Åse  Ørn'))
            with self.assertRaisesRegex(ValueError, 'venter på læreren'):
                self.inbox.submit(self.payload(elevnavn=' a\u030ase  ørn ', klasse='3.a'))

    def test_pending_svg_bytes_are_bounded_per_sender(self):
        svg_bytes = len(clean_svg(SVG)[0].encode('utf-8'))
        with patch('app.laser_server.MAX_SENDER_BYTES', svg_bytes):
            self.inbox.submit(self.payload())
            with self.assertRaisesRegex(ValueError, 'venter på læreren'):
                self.inbox.submit(self.payload())
            self.inbox.submit(self.payload(elevnavn='Maja'))

    def test_terminal_history_frees_capacity_without_losing_receipt(self):
        with patch('app.laser_server.MAX_SENDER_JOBS', 1):
            original = self.payload()
            first = self.inbox.submit(original)
            self.inbox.status(first['id'], 'done')
            self.assertFalse(self.inbox.submit(self.payload())['duplicate'])
            self.assertTrue(self.inbox.submit(original)['duplicate'])

    def test_quota_applies_after_reopening_existing_inbox(self):
        with patch('app.laser_server.MAX_SENDER_JOBS', 1):
            self.inbox.submit(self.payload())
            reopened = Inbox(self.temp.name, code='test-quota-only')
            with self.assertRaisesRegex(ValueError, 'venter på læreren'):
                reopened.submit(self.payload())

    def test_concurrent_submissions_cannot_exceed_quota(self):
        def submit(_):
            try:
                self.inbox.submit(self.payload())
                return True
            except ValueError:
                return False
        with patch('app.laser_server.MAX_SENDER_JOBS', 2):
            with ThreadPoolExecutor(max_workers=8) as pool:
                accepted = list(pool.map(submit, range(8)))
        self.assertEqual(sum(accepted), 2)
        self.assertEqual(len(self.inbox.jobs()), 2)

    def test_control_and_surrogate_metadata_never_reach_storage(self):
        for key in ('elevnavn', 'klasse', 'projekt'):
            for bad in ('test\x00x', 'test\nx', 'test\tx', 'test\x7fx', 'test\u0085x', 'test\ud800x'):
                with self.subTest(key=key, value=repr(bad)):
                    with self.assertRaisesRegex(ValueError, 'kontroltegn'):
                        self.inbox.submit(self.payload(**{key: bad}))
        self.assertEqual(self.inbox.jobs(), [])

    def test_global_capacity_remains_separate_from_name_quota(self):
        with patch('app.laser_server.MAX_QUEUE_JOBS', 1):
            self.inbox.submit(self.payload())
            with self.assertRaisesRegex(ValueError, 'Afleveringskøen er fuld'):
                self.inbox.submit(self.payload(elevnavn='Maja'))


if __name__ == '__main__':
    unittest.main()
