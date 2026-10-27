"""A teacher may clear a stopped old print, never an active or new failed job."""
import tempfile
import unittest
from app.core import Queue
from scripts.smoke_slice import cube


class AcknowledgedStopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 1000
        self.config = [{'id': 'mini', 'model': 'A1 mini', 'name': 'E4', 'enabled': True}]
        self.q = Queue(self.temp.name, self.config, clock=lambda: self.now)
        self.trays = [dict(slot=254, colour='#0ACC38', material='PLA', profile='Bambu PLA Basic')]
        self.report()

    def tearDown(self):
        self.q.close()
        self.temp.cleanup()

    def report(self, **changes):
        self.q.update_report('mini', **dict(state='FAILED', task='old-cancelled-print', error=0, trays=self.trays, **changes))

    def test_teacher_release_allows_one_new_job_despite_old_failed_telemetry(self):
        job = self.q.submit(cube(10), 'Test.stl', 'any', 'acknowledged-stop-test-1', 'fast')
        self.assertIsNone(self.q.reserve())
        self.assertTrue(self.q.snapshot()['printers'][0]['can_release'])
        self.q.release_bed('mini')
        self.report()
        self.assertEqual(self.q.snapshot()['printers'][0]['status'], 'ready')
        reserved = self.q.reserve()
        self.assertEqual(reserved['id'], job['id'])
        self.report()
        self.assertEqual(self.q.get_job(job['id'])['status'], 'preparing')
        self.assertTrue(self.q.begin_dispatch(job['id']))
        self.q.dispatched(job['id'])
        self.report()
        self.assertEqual(self.q.get_job(job['id'])['status'], 'starting')
        self.q.update_report('mini', state='RUNNING', task=reserved['remote_name'], error=0)
        self.assertEqual(self.q.get_job(job['id'])['status'], 'printing')
        self.assertFalse(self.q.printers['mini']['bed_clear'])
        self.q.update_report('mini', state='FAILED', task=reserved['remote_name'], error=123)
        self.assertEqual(self.q.get_job(job['id'])['status'], 'attention')
        with self.assertRaises(ValueError):
            self.q.release_bed('mini')

    def test_actual_error_cannot_be_acknowledged(self):
        self.q.update_report('mini', error=123)
        self.assertFalse(self.q.snapshot()['printers'][0]['can_release'])
        with self.assertRaises(ValueError):
            self.q.release_bed('mini')

    def test_ack_is_revoked_by_error_even_if_error_later_clears(self):
        self.q.release_bed('mini')
        self.q.update_report('mini', error=123)
        self.report()
        self.assertFalse(self.q.ready(self.q.printers['mini']))
        self.assertFalse(self.q.acknowledged_stop(self.q.printers['mini']))

    def test_task_change_or_activity_requires_new_teacher_clearance(self):
        for changes in ({'task': 'different-print'}, {'state': 'RUNNING'}, {'state': 'IDLE'}):
            with self.subTest(changes=changes):
                self.report()
                self.q.release_bed('mini')
                self.q.update_report('mini', **changes)
                self.assertFalse(self.q.acknowledged_stop(self.q.printers['mini']))
                self.report()
                self.assertFalse(self.q.ready(self.q.printers['mini']))

    def test_disconnect_and_freshness_gap_revoke_ack(self):
        self.q.release_bed('mini')
        self.q.offline('mini')
        self.report()
        self.assertFalse(self.q.ready(self.q.printers['mini']))
        self.q.release_bed('mini')
        self.now += 45
        self.report()
        self.assertFalse(self.q.ready(self.q.printers['mini']))

    def test_ack_does_not_survive_restart(self):
        self.q.release_bed('mini')
        self.q.close()
        self.q = Queue(self.temp.name, self.config, clock=lambda: self.now)
        self.report()
        self.assertFalse(self.q.ready(self.q.printers['mini']))

    def test_advisory_camera_cannot_acknowledge_failed_print(self):
        self.now += 1
        self.assertFalse(hasattr(self.q, 'update_camera_check'))
        self.assertFalse(self.q.ready(self.q.printers['mini']))
        self.assertFalse(self.q.acknowledged_stop(self.q.printers['mini']))

    def test_known_aborted_print_finishes_without_teacher_and_is_never_replayed(self):
        self.q.release_bed('mini')
        job = self.q.submit(cube(10), 'Test.stl', 'any', 'known-aborted-print-1')
        reserved = self.q.reserve()
        self.q.begin_dispatch(job['id'])
        self.q.dispatched(job['id'])
        self.q.update_report('mini', state='RUNNING', task=reserved['remote_name'], error=0)
        self.q.update_report('mini', state='FAILED')
        self.assertEqual(self.q.get_job(job['id'])['status'], 'printing')
        self.q.update_report('mini', state='FAILED', task=reserved['remote_name'], error=0)
        self.assertEqual(self.q.get_job(job['id'])['status'], 'cancelled')
        self.assertEqual(self.q.snapshot()['printers'][0]['status'], 'bed')
        self.assertIsNone(self.q.reserve())
        self.assertFalse(self.q.bed_is_clear(self.q.printers['mini']))

    def test_uncertain_start_failure_is_not_automatically_dismissed(self):
        self.q.release_bed('mini')
        job = self.q.submit(cube(10), 'Test.stl', 'any', 'uncertain-start-test-1')
        reserved = self.q.reserve()
        self.q.begin_dispatch(job['id'])
        self.q.dispatched(job['id'])
        self.q.update_report('mini', state='FAILED', task=reserved['remote_name'], error=0)
        self.assertEqual(self.q.get_job(job['id'])['status'], 'attention')
        self.assertFalse(self.q.camera_idle(self.q.printers['mini']))

    def attention_job(self):
        self.q.release_bed('mini')
        job = self.q.submit(cube(10), 'Test.stl', 'any', 'teacher-resolves-abort')
        reserved = self.q.reserve()
        self.q.begin_dispatch(job['id'])
        self.q.dispatched(job['id'])
        self.q.update_report('mini', state='FAILED', task=reserved['remote_name'], error=0)
        self.assertEqual(self.q.get_job(job['id'])['status'], 'attention')
        return job

    def test_teacher_can_resolve_failed_machine_then_release_plate_for_next_job(self):
        job = self.attention_job()
        self.q.resolve(job['id'], 'cancelled')
        self.assertEqual(self.q.get_job(job['id'])['status'], 'cancelled')
        p = self.q.snapshot()['printers'][0]
        self.assertEqual(p['status'], 'bed')
        self.assertTrue(p['can_release'])
        self.assertFalse(p['bed_clear'])
        next_job = self.q.submit(cube(10), 'Next.stl', 'any', 'after-teacher-resolves-abort')
        self.assertIsNone(self.q.reserve())
        self.q.release_bed('mini')
        self.assertEqual(self.q.reserve()['id'], next_job['id'])
        self.assertTrue(self.q.begin_dispatch(next_job['id']))
        self.assertEqual(self.q.get_job(job['id'])['status'], 'cancelled')

    def test_old_stopped_button_also_accepts_failed_machine(self):
        job = self.attention_job()
        self.q.resolve(job['id'], 'failed')
        self.assertTrue(self.q.can_release(self.q.printers['mini']))
        self.assertFalse(self.q.bed_is_clear(self.q.printers['mini']))

    def test_legacy_camera_clearance_cannot_release_after_resolved_abort(self):
        job = self.attention_job()
        self.q.resolve(job['id'], 'cancelled')
        self.now += 1
        self.q.printers['mini'].update(bed_clear=True, bed_source='camera', bed_checked=self.now, bed_ai_trial=True)
        self.assertFalse(self.q.ready(self.q.printers['mini']))
        self.q.release_bed('mini')
        self.assertTrue(self.q.ready(self.q.printers['mini']))

    def test_resolution_is_blocked_for_busy_offline_and_stale_printers(self):
        job = self.attention_job()
        for reason in ('RUNNING','PREPARE','PAUSE','OFFLINE','stale'):
            with self.subTest(reason=reason):
                self.q.update_report('mini', state='FAILED', task='stopped', error=0)
                if reason=='stale': self.now += 45
                elif reason=='OFFLINE': self.q.offline('mini')
                else: self.q.update_report('mini', state=reason)
                # Offline isolation does not acknowledge a physical stop.
                expected = ['isolated'] if reason in ('OFFLINE', 'stale') else []
                self.assertEqual(self.q.resolution_options(self.q.get_job(job['id'])), expected)
                with self.assertRaises(ValueError): self.q.resolve(job['id'], 'cancelled')
                self.assertEqual(self.q.get_job(job['id'])['status'], 'attention')
        self.q.update_report('mini', state='FAILED', task='stopped', error=0)
        self.assertEqual(self.q.resolution_options(self.q.get_job(job['id'])), ['cancelled','failed'])
        with self.assertRaises(ValueError): self.q.resolve(job['id'], 'done')

    def test_teacher_can_close_stopped_faulted_job_without_clearing_fault_or_bed(self):
        job = self.attention_job()
        self.q.update_report('mini', state='FAILED', task='stopped', error=0x0300400C)
        self.assertEqual(self.q.resolution_options(self.q.get_job(job['id'])), ['cancelled', 'failed'])
        with self.assertRaises(ValueError):
            self.q.resolve(job['id'], 'done')
        self.q.resolve(job['id'], 'cancelled')
        p = self.q.printers['mini']
        self.assertEqual(p['error'], 0x0300400C)
        self.assertFalse(self.q.can_release(p))
        self.assertFalse(self.q.ready(p))
        with self.assertRaises(ValueError):
            self.q.release_bed('mini')
        snapshot = self.q.snapshot()['printers'][0]
        self.assertEqual(snapshot['status'], 'attention')
        self.assertIn('printerens skærm', snapshot['message'])
        self.q.update_report('mini', state='FAILED', task='stopped', error=0)
        self.assertTrue(self.q.can_release(p))
        self.assertFalse(self.q.ready(p))
        self.q.release_bed('mini')
        self.assertTrue(self.q.ready(p))

    def test_existing_attention_after_restart_has_visible_resolution_options(self):
        job = self.attention_job()
        self.q.close()
        self.q = Queue(self.temp.name, self.config, clock=lambda: self.now)
        self.report()
        self.assertEqual(self.q.snapshot()['jobs'][0]['resolution_options'], ['cancelled','failed'])
        self.q.resolve(job['id'], 'cancelled')
        self.assertEqual(self.q.snapshot()['jobs'][0]['resolution_options'], [])

    def test_filament_confirmation_pause_resumes_tracking_same_print(self):
        self.q.release_bed('mini')
        job = self.q.submit(cube(10), 'Test.stl', 'any', 'filament-confirmation-test-1')
        reserved = self.q.reserve()
        self.q.begin_dispatch(job['id'])
        self.q.dispatched(job['id'])
        self.q.update_report('mini', state='PAUSE', task=reserved['remote_name'], error=0x07FF8007)
        self.assertEqual(self.q.get_job(job['id'])['status'], 'printing')
        self.assertIn('filament', self.q.get_job(job['id'])['message'])
        with self.assertRaises(ValueError):
            self.q.release_bed('mini')
        self.q.update_report('mini', state='RUNNING', task=reserved['remote_name'], error=0)
        self.assertEqual(self.q.get_job(job['id'])['status'], 'printing')
        self.q.update_report('mini', state='FINISH', task=reserved['remote_name'], error=0)
        self.assertEqual(self.q.get_job(job['id'])['status'], 'done')

    def test_known_filament_prompt_recovers_after_restart_without_resend(self):
        self.q.release_bed('mini')
        job = self.q.submit(cube(10), 'Test.stl', 'any', 'filament-confirmation-restart-1')
        reserved = self.q.reserve()
        self.q.begin_dispatch(job['id'])
        self.q.dispatched(job['id'])
        self.q.close()
        self.q = Queue(self.temp.name, self.config, clock=lambda: self.now)
        self.q.update_report('mini', state='PAUSE', task=reserved['remote_name'], error=0x07FF8007, trays=self.trays)
        self.assertEqual(self.q.get_job(job['id'])['status'], 'printing')
        self.assertIsNone(self.q.reserve())


if __name__ == '__main__':
    unittest.main()
