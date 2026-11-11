import tempfile
import unittest
from unittest.mock import Mock

from app.core import Queue
from app.worker import Worker
from scripts.smoke_slice import cube


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 1000
        self.configs = [{'id': 'one', 'name': 'Simulated', 'model': 'A1 mini', 'enabled': True}]
        self.trays = [dict(slot=254, extruder=0, colour='#3678DC', material='PLA', profile='Bambu PLA Basic')]
        self.q = Queue(self.temp.name, self.configs, clock=lambda: self.now)
        self.report()
        self.q.release_bed('one')
        self.job = self.q.submit(cube(10), 'test.stl', 'any', 'restart-test-request-001')

    def tearDown(self):
        self.q.close()
        self.temp.cleanup()

    def report(self, state='IDLE', task='', **kwargs):
        self.q.update_report('one', state=state, task=task, error=0, trays=self.trays, **kwargs)

    def restart(self):
        self.q.close()
        self.now += 5
        self.q = Queue(self.temp.name, self.configs, clock=lambda: self.now)

    def dispatch(self, status='dispatching'):
        self.job = self.q.reserve()
        self.assertTrue(self.q.begin_dispatch(self.job['id']))
        if status in ('starting', 'printing'):
            self.q.dispatched(self.job['id'])
        if status == 'printing':
            self.report('RUNNING', self.job['remote_name'], progress=15)

    def test_queued_waits_for_network_and_new_bed_confirmation(self):
        self.restart()
        self.assertIsNone(self.q.reserve())
        self.now += 3600
        self.assertIsNone(self.q.reserve())
        self.report()
        self.assertIsNone(self.q.reserve())
        self.q.release_bed('one')
        self.assertEqual(self.q.reserve()['id'], self.job['id'])

    def test_partial_startup_report_never_makes_printer_ready(self):
        self.restart()
        self.q.update_report('one', state='IDLE', trays=self.trays)
        self.assertFalse(self.q.online(self.q.printers['one']))
        with self.assertRaises(ValueError):
            self.q.release_bed('one')
        self.assertIsNone(self.q.reserve())

    def test_paused_queue_stays_paused_after_restart_and_new_clear_bed(self):
        self.q.pause(True)
        self.restart()
        self.report()
        self.q.release_bed('one')
        self.assertTrue(self.q.paused)
        self.assertIsNone(self.q.reserve())

    def test_preparing_can_retry_because_no_start_was_committed(self):
        self.q.reserve()
        self.restart()
        self.assertEqual(self.q.get_job(self.job['id'])['status'], 'queued')
        self.report()
        self.q.release_bed('one')
        self.assertEqual(self.q.reserve()['id'], self.job['id'])

    def test_every_uncertain_phase_reconciles_without_reserving_again(self):
        for phase in ('dispatching', 'starting', 'printing'):
            with self.subTest(phase=phase):
                self.dispatch(phase)
                self.restart()
                self.assertEqual(self.q.get_job(self.job['id'])['status'], 'recovering')
                self.assertIsNone(self.q.reserve())
                self.report('RUNNING', self.job['remote_name'], progress=52)
                self.assertEqual(self.q.get_job(self.job['id'])['status'], 'printing')
                self.assertIsNone(self.q.reserve())
                self.report('FINISH', self.job['remote_name'], progress=100)
                self.assertEqual(self.q.get_job(self.job['id'])['status'], 'done')
                self.assertFalse(self.q.bed_is_clear(self.q.printers['one']))
                self.q.release_bed('one')
                self.job = self.q.submit(cube(10), 'next.stl', 'any', 'restart-next-job-' + phase, source='recovery-fixture-' + phase)

    def test_finished_while_computer_off_is_completed_without_resend(self):
        self.dispatch()
        self.restart()
        self.report('FINISH', self.job['remote_name'], progress=100)
        self.assertEqual(self.q.get_job(self.job['id'])['status'], 'done')

    def test_printer_pause_is_recognized_but_never_resumed_by_a_command(self):
        self.dispatch()
        self.restart()
        self.report('PAUSE', self.job['remote_name'])
        self.assertEqual(self.q.get_job(self.job['id'])['status'], 'printing')
        self.assertIsNone(self.q.reserve())

    def test_other_job_idle_missing_identity_and_errors_need_teacher(self):
        self.dispatch()
        for state, task, error in [('RUNNING', 'other', 0), ('IDLE', '', 0),
                                   ('FINISH', '', 0), ('RUNNING', self.job['remote_name'], 1),
                                   ('FINISH', 'prefix_' + self.job['remote_name'], 0)]:
            with self.subTest(state=state, task=task, error=error):
                self.q._change(self.job['id'], status='starting')
                self.restart()
                self.q.update_report('one', state=state, task=task, error=error)
                self.assertEqual(self.q.get_job(self.job['id'])['status'], 'attention')
                self.assertFalse(self.q.begin_dispatch(self.job['id']))
                self.assertEqual(self.q.get_job(self.job['id'])['status'], 'attention')
                self.assertIsNone(self.q.reserve())

    def test_partial_reports_cannot_complete_from_cached_job_identity(self):
        self.dispatch()
        self.restart()
        self.q.update_report('one', task=self.job['remote_name'])
        self.q.update_report('one', state='FINISH', error=0)
        self.assertEqual(self.q.get_job(self.job['id'])['status'], 'recovering')
        self.now += 91
        self.q.reserve()
        self.assertEqual(self.q.get_job(self.job['id'])['status'], 'attention')

    def test_second_crash_and_offline_period_never_replay(self):
        self.dispatch()
        self.restart()
        self.restart()
        self.now += 5000
        self.q.reserve()
        self.assertEqual(self.q.get_job(self.job['id'])['status'], 'recovering')
        self.report('RUNNING', self.job['remote_name'])
        self.assertEqual(self.q.get_job(self.job['id'])['status'], 'printing')

    def test_reconnect_discards_old_job_identity(self):
        self.dispatch('printing')
        self.q.offline('one')
        self.q.update_report('one', state='FINISH')
        self.assertEqual(self.q.get_job(self.job['id'])['status'], 'recovering')
        self.report('RUNNING', 'another-job')
        self.assertEqual(self.q.get_job(self.job['id'])['status'], 'attention')

    def test_network_failure_before_start_retries_after_delay(self):
        job = self.q.reserve()
        driver = Mock()
        driver.upload.side_effect = ConnectionError('simulated offline')
        worker = Worker(self.q, Mock(), {'one': driver})
        worker.process(job)
        driver.send.assert_not_called()
        self.assertEqual(self.q.get_job(job['id'])['status'], 'queued')
        self.report()
        self.q.release_bed('one')
        self.assertIsNone(self.q.reserve())
        self.now += 31
        self.report()
        self.assertIsNotNone(self.q.reserve())

    def test_network_failure_during_start_never_retries(self):
        job = self.q.reserve()
        driver = Mock()
        driver.send.side_effect = ConnectionError('reply lost')
        worker = Worker(self.q, Mock(), {'one': driver})
        worker.process(job)
        driver.send.assert_called_once()
        self.assertEqual(self.q.get_job(job['id'])['status'], 'attention')
        self.restart()
        self.report('RUNNING', job['remote_name'])
        self.assertEqual(self.q.get_job(job['id'])['status'], 'attention')


if __name__ == '__main__':
    unittest.main()
