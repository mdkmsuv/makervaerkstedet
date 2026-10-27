"""Crash-ledger and startup failure tests, without touching any X display."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / 'ansible' / 'roles' / 'kiosk' / 'files'))
from pause_safety import PauseSafety, safety_clock


@unittest.skipUnless(sys.platform.startswith('linux'), 'watchdog requires Linux pidfds')
class PauseSafetyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pause-safety-test-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'ledger.json'
        self.safety = self.make_safety()
        self.key = [42, 2000000000]
        self.deadline = safety_clock() + 30

    def make_safety(self):
        value = PauseSafety(self.path)
        self.addCleanup(value.stop)
        return value

    def test_unwritable_ledger_refuses_to_lock(self):
        with mock.patch('pause_safety._write', side_effect=OSError('disk full')):
            self.assertIsNone(self.safety.arm(self.key, self.deadline))
        self.assertIsNone(self.safety.process)

    def test_start_failure_is_not_retried_until_new_pause(self):
        with mock.patch('pause_safety.subprocess.Popen', side_effect=OSError('cannot start')):
            self.assertIsNone(self.safety.arm(self.key, self.deadline))
        replacement = self.make_safety()
        self.assertIsNone(replacement.arm(self.key, self.deadline))
        self.assertEqual(replacement.arm([43, self.key[1]], self.deadline), self.deadline)

    def test_missing_pidfd_permission_refuses_to_lock(self):
        with mock.patch('pause_safety.os.pidfd_open', side_effect=PermissionError('denied')):
            self.assertIsNone(self.safety.arm(self.key, self.deadline))
        self.assertIsNone(self.safety.process)

    def test_disk_full_at_failure_cannot_relock_after_restart(self):
        self.assertEqual(self.safety.arm(self.key, self.deadline), self.deadline)
        with mock.patch('pause_safety._write', side_effect=OSError('disk now full')):
            self.safety.block()
            self.safety.stop()
        replacement = self.make_safety()
        self.assertIsNone(replacement.arm(self.key, self.deadline))

    def test_normal_release_and_restart_cannot_extend_original_deadline(self):
        self.assertEqual(self.safety.arm(self.key, self.deadline), self.deadline)
        self.safety.stop()  # Overlay has released X11 before this call.
        self.assertFalse(json.loads(self.path.read_text())['blocked'])
        replacement = self.make_safety()
        self.assertEqual(replacement.arm(self.key, self.deadline + 3600), self.deadline)

    def test_expired_persisted_pause_is_not_restarted(self):
        self.assertEqual(self.safety.arm(self.key, self.deadline), self.deadline)
        self.safety.stop()
        record = json.loads(self.path.read_text())
        record['deadline'] = safety_clock() - 1
        self.path.write_text(json.dumps(record))
        self.assertIsNone(self.make_safety().arm(self.key, self.deadline))


if __name__ == '__main__':
    unittest.main()
