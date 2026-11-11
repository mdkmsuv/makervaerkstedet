import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from app.firmware import FirmwareChecks, INTERVAL, MAX_PAGE, fetch_release, parse_release


class FirmwareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 1_800_000_000
        self.fetch = Mock(return_value={'version': '01.08.01.00', 'published_on': '2026-06-03'})
        self.checker = FirmwareChecks(self.temp.name, clock=lambda: self.now, fetch=self.fetch)

    def tearDown(self):
        self.checker.stop()
        self.temp.cleanup()

    def test_parses_only_release_headings_excluding_beta_and_inline_versions(self):
        html = '<h2>Version <b>01.08.01.00</b>（20260603）</h2><p>AMS 99.99.99.99</p><h2>Version 02.00.00.00 beta (20260901)</h2><h2>Version 01.07.00.00 (20251027)</h2>'
        self.assertEqual(parse_release(html), self.fetch.return_value)
        with self.assertRaises(ValueError):
            parse_release('<h1>Access denied</h1><p>Version 99.99.99.99</p>')

    def test_checks_immediately_then_weekly_including_after_restart(self):
        first = self.checker.check()
        self.assertTrue(first['unread'])
        self.assertEqual(first['next_check'], self.now + INTERVAL)
        self.now += INTERVAL - 1
        self.checker.check()
        restarted = FirmwareChecks(self.temp.name, clock=lambda: self.now, fetch=self.fetch)
        restarted.check()
        self.fetch.assert_called_once()
        self.now += 1
        restarted.check()
        self.assertEqual(self.fetch.call_count, 2)

    def test_error_preserves_last_success_but_never_claims_up_to_date(self):
        first = self.checker.check()
        self.fetch.side_effect = OSError('Network unavailable')
        self.now += INTERVAL
        result = self.checker.check()
        self.assertEqual(result['latest'], first['latest'])
        self.assertEqual(result['last_success'], first['last_success'])
        self.assertTrue(result['error'])
        self.assertFalse(result['installed_version_known'])
        self.assertEqual(result['next_check'], self.now + INTERVAL)
        self.assertFalse(result['checking'])

    def test_acknowledged_release_stays_hidden_until_version_changes(self):
        self.checker.check()
        self.assertFalse(self.checker.acknowledge()['unread'])
        restarted = FirmwareChecks(self.temp.name, clock=lambda: self.now, fetch=self.fetch)
        self.assertFalse(restarted.snapshot()['unread'])
        self.now += INTERVAL
        self.assertFalse(self.checker.check()['unread'])
        self.fetch.return_value = {'version':'01.09.00.00','published_on':None}
        self.now += INTERVAL
        self.assertTrue(self.checker.check()['unread'])

    def test_manual_checks_throttled_and_disabled_instances_never_fetch(self):
        self.checker.check()
        self.checker.check(force=True)
        self.fetch.assert_called_once()
        self.now += 60
        self.checker.check(force=True)
        self.assertEqual(self.fetch.call_count, 2)
        disabled = FirmwareChecks(self.temp.name, enabled=False, fetch=self.fetch)
        disabled.start()
        disabled.check(force=True)
        self.assertIsNone(disabled.thread)
        self.assertEqual(self.fetch.call_count, 2)

    def test_overlapping_checks_do_not_duplicate_network_requests(self):
        started, finish = threading.Event(), threading.Event()
        def waiting_fetch():
            started.set()
            finish.wait(2)
            return self.fetch.return_value
        self.checker.fetch = Mock(side_effect=waiting_fetch)
        thread = threading.Thread(target=self.checker.check)
        thread.start()
        try:
            self.assertTrue(started.wait(2))
            self.now += 120
            self.assertTrue(self.checker.check(force=True)['checking'])
            self.checker.fetch.assert_called_once()
        finally:
            finish.set()
            thread.join()

    def test_recovers_unreadable_saved_state_and_does_not_create_queue_files(self):
        (Path(self.temp.name)/'firmware-check.json').write_text('{broken')
        checker = FirmwareChecks(self.temp.name, clock=lambda:self.now, fetch=self.fetch)
        self.assertTrue(checker.snapshot()['error'])
        self.assertFalse(checker.check()['error'])
        self.assertEqual([p.name for p in Path(self.temp.name).iterdir()], ['firmware-check.json'])

    def test_network_fetch_is_bounded_and_source_checked(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.url = 'https://wiki.bambulab.com/en/a1-mini/manual/a1-mini-firmware-release-history'
        response.read.return_value = b'<h2>Version 01.08.01.00 (20260603)</h2>'
        with patch('app.firmware.urlopen',return_value=response) as network:
            self.assertEqual(fetch_release(), self.fetch.return_value)
            self.assertEqual(network.call_args.kwargs['timeout'], 10)
            response.read.assert_called_once_with(MAX_PAGE + 1)
            response.url = 'https://unrelated.invalid/page'
            with self.assertRaises(ValueError): fetch_release()
            response.url = 'https://wiki.bambulab.com/example'
            response.read.return_value = b'x' * (MAX_PAGE + 1)
            with self.assertRaises(ValueError): fetch_release()


if __name__ == '__main__':
    unittest.main()
