import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from app.bambu import BAMBU_CANCEL_PRINT_ERROR, BambuPrinter


class BambuCancellationCodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.queue = Mock(folder=Path(self.temp.name))
        self.queue.lock = threading.RLock()
        self.queue.clock.return_value = 1000
        self.driver = BambuPrinter(
            {'id': 'e1', 'serial': 'SERIAL', 'access_code': 'test', 'model': 'A1 mini'},
            self.queue, Mock())

    def tearDown(self):
        self.temp.cleanup()

    def push(self, update):
        payload = Mock(payload=json.dumps({'print': update}).encode())
        self.driver._message(None, None, payload)
        return self.queue.update_report.call_args.kwargs

    def test_failed_cancel_code_with_explicit_empty_hms_is_normalized(self):
        kwargs = self.push({'gcode_state': 'FAILED', 'print_error': BAMBU_CANCEL_PRINT_ERROR, 'hms': []})
        self.assertEqual(kwargs['state'], 'FAILED')
        self.assertEqual(kwargs['error'], 0)

    def test_cancel_notification_in_idle_and_finish_with_no_hms_is_not_a_fault(self):
        for state in ('IDLE', 'FINISH'):
            with self.subTest(state=state):
                kwargs = self.push({'gcode_state': state, 'print_error': BAMBU_CANCEL_PRINT_ERROR, 'hms': []})
                self.assertEqual(kwargs['error'], 0)

    def test_idle_sensor_fault_is_not_hidden_by_cancel_notification(self):
        kwargs = self.push({'gcode_state': 'IDLE', 'print_error': BAMBU_CANCEL_PRINT_ERROR,
                            'hms': [{'attr': 50337792, 'code': 65539}]})
        self.assertEqual(kwargs['error'], BAMBU_CANCEL_PRINT_ERROR)

    def test_cleared_hms_in_idle_removes_only_cancellation_error(self):
        self.push({'gcode_state': 'IDLE', 'print_error': BAMBU_CANCEL_PRINT_ERROR,
                   'hms': [{'attr': 50337792, 'code': 65539}]})
        kwargs = self.push({'hms': []})
        self.assertEqual(kwargs, {'error': 0})

    def test_missing_hms_does_not_normalize_cancel_code(self):
        kwargs = self.push({'gcode_state': 'FAILED', 'print_error': BAMBU_CANCEL_PRINT_ERROR})
        self.assertEqual(kwargs['error'], BAMBU_CANCEL_PRINT_ERROR)

    def test_active_hms_does_not_normalize_cancel_code(self):
        kwargs = self.push({'gcode_state': 'FAILED', 'print_error': BAMBU_CANCEL_PRINT_ERROR,
                            'hms': [{'attr': 50337792, 'code': 65539}]})
        self.assertEqual(kwargs['error'], BAMBU_CANCEL_PRINT_ERROR)

    def test_unknown_error_with_empty_hms_stays_an_error(self):
        kwargs = self.push({'gcode_state': 'FAILED', 'print_error': 123456, 'hms': []})
        self.assertEqual(kwargs['error'], 123456)

    def test_fresh_hms_can_reclassify_cached_cancellation_without_refreshing_state(self):
        self.push({'gcode_state': 'FAILED', 'print_error': BAMBU_CANCEL_PRINT_ERROR, 'hms': []})
        self.queue.reset_mock()
        kwargs = self.push({'hms': [{'attr': 50337792, 'code': 65539}]})
        self.assertEqual(kwargs, {'error': BAMBU_CANCEL_PRINT_ERROR})

    def test_state_only_packet_revokes_stale_cancellation_classification(self):
        self.push({'gcode_state': 'FAILED', 'print_error': BAMBU_CANCEL_PRINT_ERROR, 'hms': []})
        self.queue.reset_mock()
        kwargs = self.push({'gcode_state': 'RUNNING'})
        self.assertEqual(kwargs['state'], 'RUNNING')
        self.assertEqual(kwargs['error'], BAMBU_CANCEL_PRINT_ERROR)


if __name__ == '__main__':
    unittest.main()
