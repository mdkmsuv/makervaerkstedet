import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.core import Queue
from app.bambu import merge_report, print_command, read_trays
from app.instance import InstanceLock
from app.stl import inspect_stl
from app.queue_storage import cleanup_job_artifacts
from scripts.smoke_slice import cube


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 1000.0
        self.configs = [{'id': 'mini', 'name': 'Mini', 'model': 'A1 mini', 'enabled': True}, {'id': 'p1s', 'name': 'P1S', 'model': 'P1S', 'enabled': True}]
        self.q = Queue(self.temp.name, self.configs, clock=lambda: self.now)
        self.trays = {'mini': [dict(slot=254, colour='#3678DC', material='PLA', profile='Bambu PLA Basic')], 'p1s': [dict(slot=0, colour='#E84142', material='PLA', profile='Bambu PLA Basic')]}
        for pid in self.trays:
            self.q.update_report(pid, state='IDLE', trays=self.trays[pid], task='', error=0)
            self.q.release_bed(pid)
        self.count = 0

    def tearDown(self):
        self.q.close()
        self.temp.cleanup()

    def job(self, colour='any', size=20, quality='standard', request_id=None):
        self.count += 1
        return self.q.submit(cube(size), 'Min figur.stl', colour, request_id or f'request-number-{self.count:05d}', quality)

    def test_oldest_eligible_job_does_not_block_other_colour(self):
        self.q.printers['p1s']['bed_clear'] = False
        red = self.job('#E84142')
        blue = self.job('#3678DC')
        self.assertEqual(self.q.reserve()['id'], blue['id'])
        self.assertEqual(self.q.get_job(red['id'])['status'], 'queued')

    def test_any_colour_uses_first_compatible_printer(self):
        self.job(size=200)
        job = self.q.reserve()
        self.assertEqual(job['printer_id'], 'p1s')
        self.assertEqual(job['actual_colour'], '#E84142')

    def test_pla_only_and_quality_whitelist(self):
        self.q.update_report('mini', trays=[dict(slot=254, colour='#3678DC', material='PETG', profile='PETG')])
        self.assertNotIn('#3678DC', [c['hex'] for c in self.q.snapshot()['colours']])
        with self.assertRaises(ValueError): self.job(quality='abs')
        job = self.job(quality='beautiful')
        self.assertEqual(job['quality'], 'beautiful')

    def test_same_request_cannot_print_twice(self):
        first = self.job(request_id='same-request-id-12345')
        second = self.job(request_id='same-request-id-12345')
        self.assertEqual(first['id'], second['id'])
        with self.assertRaises(ValueError): self.job(quality='fast', request_id='same-request-id-12345')

    def test_no_two_jobs_reserved_for_same_printer(self):
        self.q.printers['p1s']['enabled'] = False
        self.job(); self.job()
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.q.reserve())) for _ in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sum(r is not None for r in results), 1)

    def test_spool_changed_after_slicing_does_not_dispatch(self):
        self.job('#3678DC')
        job = self.q.reserve()
        self.q.update_report('mini', trays=[dict(slot=254, colour='#E84142', material='PLA', profile='Bambu PLA Basic')])
        self.assertFalse(self.q.begin_dispatch(job['id']))
        self.assertEqual(self.q.get_job(job['id'])['status'], 'queued')

    def test_busy_external_job_cannot_be_overwritten(self):
        self.job(); job = self.q.reserve()
        self.q.update_report('mini', state='RUNNING', task='other_job.gcode.3mf')
        self.assertFalse(self.q.begin_dispatch(job['id']))

    def test_finished_job_still_requires_clear_bed(self):
        self.job('#3678DC'); job = self.q.reserve()
        self.assertTrue(self.q.begin_dispatch(job['id']))
        self.q.dispatched(job['id'])
        self.q.update_report('mini', state='RUNNING', task=job['remote_name'], progress=50)
        self.q.update_report('mini', state='FINISH', task=job['remote_name'], progress=100)
        self.assertEqual(self.q.get_job(job['id'])['status'], 'done')
        self.job('#3678DC')
        self.assertIsNone(self.q.reserve())
        self.q.release_bed('mini')
        self.assertIsNotNone(self.q.reserve())

    def test_wrong_job_finish_cannot_complete_current_job(self):
        self.job(); job = self.q.reserve(); self.q.begin_dispatch(job['id']); self.q.dispatched(job['id'])
        self.q.update_report('mini', state='FINISH', task='other.gcode.3mf')
        self.assertEqual(self.q.get_job(job['id'])['status'], 'starting')
        self.now += 91
        self.q.reserve()
        self.assertEqual(self.q.get_job(job['id'])['status'], 'attention')

    def test_telemetry_gap_invalidates_bed_confirmation(self):
        self.now += 45
        self.q.update_report('mini', state='IDLE', trays=self.trays['mini'])
        self.assertFalse(self.q.printers['mini']['bed_clear'])
        self.assertEqual(self.q.snapshot()['ready'], 0)

    def test_fresh_trays_do_not_refresh_stale_state(self):
        self.now += 45
        self.q.update_report('mini', trays=self.trays['mini'])
        self.assertFalse(self.q.online(self.q.printers['mini']))

    def test_external_task_change_invalidates_bed_without_running_report(self):
        self.q.update_report('mini', state='FINISH', task='different-file')
        self.assertFalse(self.q.printers['mini']['bed_clear'])

    def test_teacher_clear_survives_old_task_identity_being_cleared(self):
        self.q.printers['mini']['task'] = 'finished-job.gcode.3mf'
        self.q.release_bed('mini')
        self.q.update_report('mini', state='IDLE', task='', error=0)
        self.assertTrue(self.q.printers['mini']['bed_clear'])
        self.assertTrue(self.q.teacher_cleared(self.q.printers['mini']))

    def test_failed_teacher_ack_survives_old_task_identity_being_cleared(self):
        self.q.update_report('mini', state='FAILED', task='stopped-job.gcode.3mf', error=0)
        self.q.release_bed('mini')
        self.q.update_report('mini', state='FAILED', task='', error=0)
        self.assertTrue(self.q.printers['mini']['bed_clear'])
        self.assertTrue(self.q.acknowledged_stop(self.q.printers['mini']))

    def test_failed_teacher_ack_after_freshness_gap_is_revoked(self):
        self.q.update_report('mini', state='FAILED', task='stopped-job.gcode.3mf', error=0)
        self.q.release_bed('mini')
        self.now += 45
        self.q.update_report('mini', state='FAILED', task='', error=0)
        self.assertFalse(self.q.printers['mini']['bed_clear'])
        self.assertFalse(self.q.acknowledged_stop(self.q.printers['mini']))

    def test_task_cleanup_leaving_failed_does_not_acknowledge_a_later_failure(self):
        for state in ('IDLE', 'FINISH'):
            with self.subTest(state=state):
                self.q.update_report('mini', state='FAILED', task='stopped-job.gcode.3mf', error=0)
                self.q.release_bed('mini')
                self.q.update_report('mini', state=state, task='', error=0)
                self.assertTrue(self.q.teacher_cleared(self.q.printers['mini']))
                self.assertNotIn('acknowledged_stop_task', self.q.printers['mini'])
                self.q.update_report('mini', state='FAILED', task='', error=0)
                self.assertFalse(self.q.printers['mini']['bed_clear'])
                self.assertFalse(self.q.acknowledged_stop(self.q.printers['mini']))

    def test_new_task_identity_invalidates_teacher_clear_when_old_task_is_not_only_cleared(self):
        self.q.printers['mini']['task'] = 'finished-job.gcode.3mf'
        self.q.release_bed('mini')
        self.q.update_report('mini', state='IDLE', task='new-job.gcode.3mf', error=0)
        self.assertFalse(self.q.printers['mini']['bed_clear'])

    def test_busy_report_invalidates_teacher_clear_during_task_cleanup(self):
        self.q.printers['mini']['task'] = 'finished-job.gcode.3mf'
        self.q.release_bed('mini')
        self.q.update_report('mini', state='RUNNING', task='', error=0)
        self.assertFalse(self.q.printers['mini']['bed_clear'])

    def test_error_report_invalidates_teacher_clear_during_task_cleanup(self):
        self.q.printers['mini']['task'] = 'finished-job.gcode.3mf'
        self.q.release_bed('mini')
        self.q.update_report('mini', state='IDLE', task='', error=123)
        self.assertFalse(self.q.printers['mini']['bed_clear'])

    def test_pause_prevents_dispatch_after_preparation(self):
        self.job(); job = self.q.reserve(); self.q.pause(True)
        self.assertFalse(self.q.begin_dispatch(job['id']))
        self.assertEqual(self.q.get_job(job['id'])['status'], 'queued')

    def test_crash_recovery_does_not_resend_print(self):
        queued = self.job(); active = self.q.reserve(); self.q.begin_dispatch(active['id'])
        self.q.close()
        self.q = Queue(self.temp.name, self.configs, clock=lambda: self.now)
        self.assertEqual(self.q.get_job(queued['id'])['status'], 'recovering')
        self.assertTrue(all(not p['bed_clear'] for p in self.q.printers.values()))

    def test_attention_cannot_be_resolved_while_printing(self):
        self.job(); job = self.q.reserve(); self.q.fail(job['id'], 'uncertain', True)
        self.q.update_report('mini', state='RUNNING')
        with self.assertRaises(ValueError): self.q.resolve(job['id'], 'done')

    def test_invalid_geometry_is_rejected(self):
        self.assertEqual(inspect_stl(cube())['size'], [20,20,20])
        for payload in (b'garbage', cube()[:100], cube(float('nan')), cube(0), cube(1000)):
            with self.assertRaises(ValueError): inspect_stl(payload)

    def test_terminal_job_removes_payload_and_slicer_work_but_keeps_history(self):
        job = self.job()
        upload = Path(self.temp.name) / 'uploads' / f"{job['id']}.stl"
        work = Path(self.temp.name) / 'sliced' / job['id']
        work.mkdir(parents=True)
        (work / job['remote_name'] if job.get('remote_name') else work / 'temporary.3mf').write_bytes(b'work')
        certificate = Path(self.temp.name) / 'certificates' / 'printer-mqtt.sha256'
        certificate.parent.mkdir()
        certificate.write_text('keep', encoding='ascii')
        self.assertTrue(upload.exists())
        self.q._change(job['id'], status='failed', message='Klargøring fejlede.')
        self.assertFalse(upload.exists())
        self.assertFalse(work.exists())
        self.assertTrue(certificate.exists())
        self.assertEqual(self.q.get_job(job['id'])['status'], 'failed')

    def test_active_and_retryable_jobs_keep_payload(self):
        queued = self.job()
        queued_upload = Path(self.temp.name) / 'uploads' / f"{queued['id']}.stl"
        self.q._change(queued['id'], status='queued', retry_after=self.now + 30)
        self.assertTrue(queued_upload.exists())
        active = self.job()
        active_upload = Path(self.temp.name) / 'uploads' / f"{active['id']}.stl"
        self.q._change(active['id'], status='printing', printer_id='mini')
        self.assertTrue(active_upload.exists())

    def test_terminal_cleanup_retries_after_restart_and_rejects_arbitrary_job_id(self):
        job = self.job()
        upload = Path(self.temp.name) / 'uploads' / f"{job['id']}.stl"
        work = Path(self.temp.name) / 'sliced' / job['id']
        work.mkdir(parents=True)
        (work / 'slicer.log').write_text('temporary', encoding='utf-8')
        # Simulate a process dying after the durable terminal commit but
        # before the filesystem call.
        self.q.db.execute("UPDATE jobs SET status='done' WHERE id=?", (job['id'],))
        self.q.db.commit()
        self.q.close()
        self.q = Queue(self.temp.name, self.configs, clock=lambda: self.now)
        self.assertFalse(upload.exists())
        self.assertFalse(work.exists())
        with self.assertRaises(ValueError):
            self.q.cleanup_job('../outside')

    def test_locked_terminal_cleanup_retries_after_delay_without_touching_active_job(self):
        terminal = self.job()
        terminal_upload = Path(self.temp.name) / 'uploads' / f"{terminal['id']}.stl"
        active = self.job()
        active_upload = Path(self.temp.name) / 'uploads' / f"{active['id']}.stl"
        locked = True

        def cleanup(folder, job_id):
            if locked:
                raise OSError('simulated Windows sharing violation')
            return cleanup_job_artifacts(folder, job_id)

        with patch('app.core.cleanup_job_artifacts', side_effect=cleanup):
            self.q._change(terminal['id'], status='failed', message='Låst fil')
            self.assertIn(terminal['id'], self.q.pending_cleanup_jobs)
            self.q.retry_pending_cleanup()
            self.assertTrue(terminal_upload.exists())
            self.assertTrue(active_upload.exists())

            self.now += 60
            self.q.retry_pending_cleanup()
            self.assertTrue(terminal_upload.exists())
            self.assertTrue(active_upload.exists())

            locked = False
            self.now += 60
            self.q.retry_pending_cleanup()
            self.assertFalse(terminal_upload.exists())
            self.assertTrue(active_upload.exists())
            self.assertNotIn(terminal['id'], self.q.pending_cleanup_jobs)


class ProtocolTests(unittest.TestCase):
    def test_delta_preserves_other_slots_and_explicit_empty_clears_slot(self):
        before = {'ams': {'ams': [{'id': '0', 'tray': [{'id': '0','tray_color':'FF0000FF'}, {'id':'1','tray_color':'00FF00FF'}]}]}}
        after = merge_report(before, {'ams': {'ams': [{'id': '0','tray':[{'id':'0','tray_color':''}]}]}})
        self.assertEqual(after['ams']['ams'][0]['tray'][0]['tray_color'], '')
        self.assertEqual(after['ams']['ams'][0]['tray'][1]['tray_color'], '00FF00FF')

    def test_physical_ams_mapping_and_external_spool(self):
        command = print_command({'slot':5,'remote_name':'pv_123.gcode.3mf'}, {})['print']
        self.assertEqual(command['ams_mapping'], [5])
        self.assertEqual(command['ams_mapping2'], [{'ams_id':1,'slot_id':1}])
        self.assertTrue(command['bed_leveling'])
        external = print_command({'slot':254,'remote_name':'pv_123.gcode.3mf'}, {})['print']
        self.assertFalse(external['use_ams'])
        self.assertEqual(external['ams_mapping2'], [])

    def test_profiles_identify_pla_and_ams_presence(self):
        class Profiles:
            def find_filament(self, model, filament_id):
                return 'Bambu PLA Basic' if filament_id == 'GFA00' else None
        report={'ams': {'tray_exist_bits':'1', 'ams':[{'id':'0','tray':[{'id':'0','tray_color':'F5D328FF','tray_type':'PLA','tray_info_idx':'GFA00'}, {'id':'1','tray_color':'E84142FF','tray_type':'PETG','tray_info_idx':'GFG00'}]}]}}
        trays=read_trays(report, {'model':'P1S'}, Profiles())
        self.assertEqual(len(trays),1)
        self.assertEqual(trays[0]['colour'],'#F5D328')
        self.assertEqual(read_trays(report, {'model':'H2D'}, Profiles()),[])

    def test_single_owner_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            first=InstanceLock(folder)
            try:
                with self.assertRaises(RuntimeError): InstanceLock(folder)
            finally:
                first.close()
            second=InstanceLock(folder);second.close()


if __name__ == '__main__':
    unittest.main()
