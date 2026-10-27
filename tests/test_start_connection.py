import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from app.bambu import BambuPrinter, print_command


class StartConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queue = Mock(folder=Path(self.temp.name))
        import threading
        self.queue.lock = threading.RLock()
        self.queue.active.return_value = {'id': 'job1', 'status': 'starting'}
        self.driver = BambuPrinter({'id': 'printer1', 'serial': 'SERIAL', 'access_code': 'test', 'model': 'A1 mini'}, self.queue, Mock())
        self.driver.pending_start = {'job_id': 'job1', 'sequence_id': '21001'}

    def reply(self, update):
        self.driver._message(None, None, Mock(payload=json.dumps({'print': update}).encode()))

    def test_commandless_failure_is_correlated(self):
        self.reply({'sequence_id': '21001', 'result': 'fail', 'reason': 'error'})
        self.queue.fail.assert_called_once()
        self.assertEqual(self.queue.fail.call_args.args[0], 'job1')
        self.assertTrue(self.queue.fail.call_args.kwargs['uncertain'])
        self.queue.update_report.assert_not_called()
        self.assertEqual(self.driver.report, {})

    def test_real_h1_authorization_rejection_without_result(self):
        # Exact reply shape from H1, 2026-09-14. It has neither result nor reason.
        self.reply({'command': 'project_file', 'sequence_id': '21001', 'err_code': 84033543, 'subtask_name': 'pv_job1'})
        self.queue.fail.assert_called_once()
        self.assertIn('Bambu-godkendelse', self.queue.fail.call_args.args[1])
        self.queue.update_report.assert_not_called()
        self.assertEqual(self.driver.report, {})

    def test_other_clients_authorization_rejection_is_ignored(self):
        self.reply({'command': 'project_file', 'sequence_id': '999', 'err_code': 84033543})
        self.queue.fail.assert_not_called()
        self.queue.update_report.assert_not_called()

    def test_other_client_failure_cannot_fail_our_job(self):
        self.reply({'sequence_id': '12', 'command': 'project_file', 'result': 'fail'})
        self.queue.fail.assert_not_called()
        self.queue.update_report.assert_not_called()

    def test_success_ack_is_not_printing(self):
        self.reply({'sequence_id': '21001', 'result': 'success'})
        self.queue.update_report.assert_not_called()
        self.queue.fail.assert_not_called()

    def test_old_ack_cannot_affect_new_job(self):
        self.queue.active.return_value = {'id': 'job2', 'status': 'starting'}
        self.reply({'sequence_id': '21001', 'result': 'fail'})
        self.queue.fail.assert_not_called()

    def test_telemetry_without_command_still_works(self):
        self.reply({'gcode_state': 'RUNNING', 'subtask_name': 'pv_job1'})
        self.queue.update_report.assert_called_once()
        self.assertEqual(self.queue.update_report.call_args.kwargs['state'], 'RUNNING')

    def test_current_subtask_is_not_mixed_with_old_filename(self):
        self.reply({'gcode_state': 'RUNNING', 'gcode_file': 'pv_old.gcode.3mf', 'subtask_name': 'external-new', 'print_error': 0})
        self.assertEqual(self.queue.update_report.call_args.kwargs['task'], 'external-new')

    def test_cached_identity_cannot_confirm_a_new_status_packet(self):
        self.reply({'gcode_state': 'RUNNING', 'subtask_name': 'pv_old', 'print_error': 0})
        self.queue.reset_mock()
        self.reply({'gcode_state': 'FINISH', 'print_error': 0})
        self.assertNotIn('task', self.queue.update_report.call_args.kwargs)

    def test_no_publish_until_subscription_acknowledged(self):
        self.driver.client = Mock()
        with self.assertRaises(ConnectionError):
            self.driver.send({'id': 'job1', 'slot': 254, 'remote_name': 'pv_job1.gcode.3mf'})
        self.driver.client.publish.assert_not_called()

    def test_external_spool_does_not_request_ams_mapping(self):
        command = print_command({'slot': 254, 'remote_name': 'test.gcode.3mf'}, {}, sequence_id='21001')['print']
        self.assertFalse(command['use_ams'])
        self.assertEqual(command['ams_mapping'], [])
        self.assertEqual(command['ams_mapping2'], [])
        self.assertEqual(command['md5'], '')
        self.assertEqual(command['url'], 'ftp:///test.gcode.3mf')

    def test_ams_slot_still_maps_to_its_physical_spool(self):
        command = print_command({'slot': 6, 'remote_name': 'test.gcode.3mf'}, {})['print']
        self.assertTrue(command['use_ams'])
        self.assertEqual(command['ams_mapping'], [6])
        self.assertEqual(command['ams_mapping2'], [{'ams_id': 1, 'slot_id': 2}])


if __name__ == '__main__':
    unittest.main()
