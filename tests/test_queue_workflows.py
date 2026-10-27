import json
import hashlib
import sqlite3
from pathlib import Path
import tempfile
import unittest

from app.core import Queue
from app.hardware_workflows import validate_binding
from app.printer_config import ConfigConflict
from scripts.smoke_slice import cube


TEACHER = 'teacher:' + 'a' * 32
AGENT = 'agent:' + 'b' * 32
DEFINITION = {
    'name': 'Printforløb', 'kind': '3d_print',
    'steps': [
        {'id': 'submit', 'label': 'Indsend', 'role': 'operator', 'action': 'submit'},
        {'id': 'prepare', 'label': 'Forbered', 'role': 'teacher', 'action': 'prepare'},
        {'id': 'approve', 'label': 'Godkend', 'role': 'teacher', 'action': 'approve'},
        {'id': 'produce', 'label': 'Producer', 'role': 'teacher', 'action': 'produce'},
        {'id': 'collect', 'label': 'Afhent', 'role': 'operator', 'action': 'collect'},
        {'id': 'clear', 'label': 'Ryd', 'role': 'teacher', 'action': 'clear'},
    ],
}
BINDING = {'id': 'c' * 32, 'revision': 4, 'definition': DEFINITION}


class QueueWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 1000.0
        self.config = {'id': 'p1s', 'name': 'P1S', 'model': 'P1S', 'enabled': True,
                       'workflow': json.loads(json.dumps(BINDING))}
        self.q = Queue(self.temp.name, [self.config], clock=lambda: self.now)
        self.q.update_report('p1s', state='IDLE', task='', error=0,
                             trays=[{'slot': 0, 'colour': '#3678DC', 'material': 'PLA',
                                     'profile': 'Bambu PLA Basic'}])
        self.q.release_bed('p1s')
        self.count = 0

    def tearDown(self):
        self.q.close()
        self.temp.cleanup()

    def job(self):
        self.count += 1
        return self.q.submit(cube(20), 'cube.stl', '#3678DC', f'workflow-request-{self.count:05d}', 'standard')

    def advance(self, job, step, expected, actor=TEACHER, request=None):
        request = request or hashlib.md5(f"{job['id']}:{step}:{expected}".encode()).hexdigest()
        return self.q.advance_workflow(actor, {'action': 'printer.job.advance',
            'params': {'id': job['id'], 'step_id': step, 'confirmed': True},
            'expected_version': expected, 'request_id': request})

    def authorize(self, job):
        self.assertIsNone(self.q.reserve())
        self.assertEqual(self.q.get_job(job['id'])['status'], 'workflow_wait')
        self.advance(job, 'prepare', 1)
        self.advance(job, 'approve', 2)
        produced_request = job['id']
        produced = self.advance(job, 'produce', 3, request=produced_request)
        self.assertEqual(self.advance(job, 'produce', 3, request=produced_request), produced)
        self.assertTrue(produced['result']['production_authorized'])
        self.assertEqual(self.q.get_job(job['id'])['workflow_version'], 4)

    def test_reserve_binds_and_waits_without_dispatch_then_teacher_steps_are_idempotent(self):
        job = self.job()
        self.authorize(job)
        prepared = self.q.reserve()
        self.assertEqual(prepared['status'], 'preparing')
        self.assertEqual(prepared['printer_id'], 'p1s')
        with self.assertRaises(ConfigConflict):
            self.advance(job, 'produce', 3, request='f' * 32)
        self.q._change(job['id'], status='done', message='test completion')
        first = self.advance(job, 'collect', 4, actor=TEACHER, request='e' * 32)
        self.assertEqual(first['result']['step']['id'], 'clear')
        self.assertEqual(self.advance(job, 'collect', 4, actor=TEACHER, request='e' * 32), first)
        self.advance(job, 'clear', 5, request='0' * 32)

    def test_produce_only_authorizes_and_reserve_keeps_machine_and_bed_guards(self):
        job = self.job(); self.authorize(job)
        self.q.printers['p1s']['bed_clear'] = False
        self.assertIsNone(self.q.reserve())
        self.assertEqual(self.q.get_job(job['id'])['status'], 'workflow_wait')
        self.q.printers['p1s']['bed_clear'] = True
        prepared = self.q.reserve()
        self.assertEqual(prepared['status'], 'preparing')
        self.q.printers['p1s']['state'] = 'RUNNING'
        self.assertFalse(self.q.begin_dispatch(job['id']))
        self.assertEqual(self.q.get_job(job['id'])['status'], 'workflow_wait')

    def test_done_collect_clear_blocks_legacy_release_until_workflow_clear(self):
        job = self.job(); self.authorize(job); prepared = self.q.reserve(); self.assertIsNotNone(prepared)
        self.assertTrue(self.q.begin_dispatch(job['id'])); self.q.dispatched(job['id'])
        self.q.update_report('p1s', state='RUNNING', task=prepared['remote_name'], progress=50)
        self.q.update_report('p1s', state='FINISH', task=prepared['remote_name'], progress=100)
        self.assertEqual(self.q.get_job(job['id'])['status'], 'done')
        with self.assertRaises(ValueError): self.q.release_bed('p1s')
        self.advance(job, 'collect', 4, actor=TEACHER, request='f' * 32)
        with self.assertRaises(ValueError): self.q.release_bed('p1s')
        self.assertFalse(hasattr(self.q, 'update_camera_check'))
        self.assertFalse(self.q.printers['p1s']['bed_clear'])
        self.advance(job, 'clear', 5, request='0' * 32)
        self.assertTrue(self.q.printers['p1s']['bed_clear'])

    def test_failed_and_cancelled_jobs_are_forced_to_clear_step(self):
        for result, request in (('failed', '1' * 32), ('cancelled', '2' * 32)):
            job = self.job(); self.authorize(job); self.assertIsNotNone(self.q.reserve())
            self.q._change(job['id'], status=result, message='test')
            current = self.q.get_job(job['id'])
            self.assertEqual(current['workflow_step'], 5)
            self.assertFalse(current['workflow_authorized'])
            self.advance(job, 'clear', current['workflow_version'], request=request)
            self.assertTrue(self.q.get_job(job['id'])['workflow_complete'])

    def test_repeated_clear_receipt_does_not_clear_plate_after_later_activity(self):
        job = self.job(); self.authorize(job); prepared = self.q.reserve()
        self.assertTrue(self.q.begin_dispatch(job['id']))
        self.q.dispatched(job['id'])
        self.q.update_report('p1s', state='RUNNING', task=prepared['remote_name'], error=0)
        self.q.update_report('p1s', state='FINISH', task=prepared['remote_name'], error=0)
        self.advance(job, 'collect', 4, actor=TEACHER)
        receipt = self.advance(job, 'clear', 5, request='d' * 32)
        self.assertTrue(self.q.printers['p1s']['bed_clear'])
        self.q.update_report('p1s', state='RUNNING', task='another-physical-job', error=0)
        self.assertEqual(self.advance(job, 'clear', 5, request='d' * 32), receipt)
        self.assertFalse(self.q.printers['p1s']['bed_clear'])

    def test_restart_and_pre_dispatch_retry_keep_pinned_workflow_and_printer(self):
        job = self.job(); self.authorize(job); preparing = self.q.reserve()
        pinned = self.q.get_job(job['id'])['workflow']
        self.q.retry_preparation(job['id'])
        current = self.q.get_job(job['id'])
        self.assertEqual(current['status'], 'workflow_wait')
        self.assertEqual(current['printer_id'], preparing['printer_id'])
        self.assertEqual(current['workflow'], pinned)
        self.q.close()
        reopened = Queue(self.temp.name, [self.config], clock=lambda: self.now)
        try:
            restored = reopened.get_job(job['id'])
            self.assertEqual(restored['status'], 'workflow_wait')
            self.assertEqual(restored['printer_id'], 'p1s')
            self.assertEqual(restored['workflow'], pinned)
        finally:
            reopened.close()

    def test_agent_cannot_confirm_teacher_steps_and_state_stays_unchanged(self):
        job = self.job(); self.assertIsNone(self.q.reserve())
        before = self.q.get_job(job['id'])
        for index, step in enumerate(('prepare', 'approve', 'produce'), start=1):
            with self.assertRaises(PermissionError):
                self.advance(job, step, index, actor=AGENT,
                              request=hashlib.md5(('agent-' + step).encode()).hexdigest())
            after = self.q.get_job(job['id'])
            self.assertEqual((after['workflow_step'], after['workflow_version'], after['workflow_authorized']),
                             (before['workflow_step'], before['workflow_version'], before['workflow_authorized']))
            self.advance(job, step, index)
            before = self.q.get_job(job['id'])
        self.q._change(job['id'], status='done', message='test completion')
        # A legacy agent actor is rejected even for the former operator step.
        before_collect = self.q.get_job(job['id'])
        with self.assertRaises(PermissionError):
            self.advance(job, 'collect', 4, actor=AGENT, request='a' * 32)
        self.assertEqual(self.q.get_job(job['id'])['workflow_step'], before_collect['workflow_step'])
        self.advance(job, 'collect', 4, actor=TEACHER, request='b' * 32)
        before_clear = self.q.get_job(job['id'])
        with self.assertRaises(PermissionError):
            self.advance(job, 'clear', before_clear['workflow_version'], actor=AGENT, request='c' * 32)
        self.assertEqual(self.q.get_job(job['id'])['workflow_step'], before_clear['workflow_step'])
        self.advance(job, 'clear', before_clear['workflow_version'], actor=TEACHER, request='d' * 32)

    def test_out_of_order_and_changed_request_id_do_not_mutate_workflow(self):
        job = self.job(); self.assertIsNone(self.q.reserve())
        with self.assertRaises(ConfigConflict):
            self.advance(job, 'approve', 1, request='d' * 32)
        self.assertEqual(self.q.get_job(job['id'])['workflow_step'], 1)
        self.advance(job, 'prepare', 1, request='d' * 32)
        with self.assertRaises(ConfigConflict):
            self.advance(job, 'approve', 2, request='d' * 32)
        current = self.q.get_job(job['id'])
        self.assertEqual((current['workflow_step'], current['workflow_version']), (2, 2))

    def test_workflow_step_receipt_and_mutation_rollback_together_on_audit_failure(self):
        job = self.job(); self.assertIsNone(self.q.reserve())
        with self.q.lock:
            self.q.db.execute("""CREATE TRIGGER fail_workflow_audit
                BEFORE INSERT ON workflow_audit BEGIN SELECT RAISE(ABORT, 'test rollback'); END""")
            self.q.db.commit()
        request = 'e' * 32
        with self.assertRaises(sqlite3.Error):
            self.advance(job, 'prepare', 1, request=request)
        current = self.q.get_job(job['id'])
        self.assertEqual((current['workflow_step'], current['workflow_version']), (1, 1))
        with self.q.lock:
            self.q.db.execute('DROP TRIGGER fail_workflow_audit')
            self.q.db.commit()
        result = self.advance(job, 'prepare', 1, request=request)
        self.assertEqual(result['result']['step']['id'], 'approve')

    def test_restart_from_preparing_restores_workflow_wait_without_losing_authorization(self):
        job = self.job(); self.authorize(job)
        prepared = self.q.reserve()
        self.assertEqual(prepared['status'], 'preparing')
        self.q.close()
        reopened = Queue(self.temp.name, [self.config], clock=lambda: self.now)
        try:
            restored = reopened.get_job(job['id'])
            self.assertEqual(restored['status'], 'workflow_wait')
            self.assertEqual(restored['printer_id'], 'p1s')
            self.assertEqual(restored['workflow_authorized'], 1)
            self.assertEqual(json.loads(restored['workflow'])['revision'], 4)
        finally:
            reopened.close()

    def test_restart_after_dispatch_enters_recovery_and_never_redispatches(self):
        job = self.job(); self.authorize(job); prepared = self.q.reserve()
        self.assertTrue(self.q.begin_dispatch(job['id']))
        self.assertEqual(self.q.get_job(job['id'])['status'], 'dispatching')
        self.q.close()
        reopened = Queue(self.temp.name, [self.config], clock=lambda: self.now)
        try:
            restored = reopened.get_job(job['id'])
            self.assertEqual(restored['status'], 'recovering')
            self.assertEqual(restored['printer_id'], prepared['printer_id'])
            self.assertIsNone(reopened.reserve())
            self.assertEqual(reopened.get_job(job['id'])['status'], 'recovering')
        finally:
            reopened.close()

    def test_changed_printer_config_workflow_does_not_change_active_pinned_revision(self):
        job = self.job(); self.assertIsNone(self.q.reserve())
        pinned = self.q.get_job(job['id'])['workflow']
        changed = json.loads(json.dumps(BINDING)); changed['revision'] = 5; changed['definition']['name'] = 'Ny revision'
        self.config['workflow'] = validate_binding(changed)
        self.assertEqual(json.loads(self.q.get_job(job['id'])['workflow']), json.loads(pinned))
        self.assertEqual(json.loads(self.q.get_job(job['id'])['workflow'])['revision'], 4)


if __name__ == '__main__':
    unittest.main()
