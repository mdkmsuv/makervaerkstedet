"""Teacher workflow removal through real local adapters; no hardware or pupils."""
import copy
import unittest
import uuid
from tests import test_makerspace_printer_integration as printer_fixture
from tests import test_makerspace_laser_integration as laser_fixture


class WorkflowUnbindingTests(unittest.TestCase):
    def printer(self):
        fixture = printer_fixture.MakerspacePrinterIntegrationTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        return fixture

    def laser(self):
        fixture = laser_fixture.LaserMakerspaceIntegrationTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        return fixture

    def test_printer_removal_preserves_current_job_and_new_jobs_use_queue(self):
        f = self.printer()
        machine, _workflow, version = f.setup_workflow_printer()
        f.ready_printer(machine)
        job = f.app.queue.submit(printer_fixture.cube(20), 'old.stl', 'any', 'unbinding-old-job')
        self.assertIsNone(f.app.queue.reserve())
        pinned = copy.deepcopy(f.app.queue.workflow_jobs())
        params = {'id': machine, 'workflow_id': None, 'revision': None}
        removed = f.command(f.teacher_actor, 'printer.workflow.assign', params, version, 'a' * 32)
        self.assertIsNone(f.app.printer_bridge.snapshot()['printers'][0]['workflow'])
        self.assertEqual(f.app.queue.workflow_jobs(), pinned)
        self.assertEqual(f.app.queue.get_job(job['id'])['status'], 'workflow_wait')
        self.assertEqual(f.command(f.teacher_actor, 'printer.workflow.assign', params, version, 'a' * 32), removed)
        self.assertEqual(f.app.queue.workflow_jobs(), pinned)
        new = f.app.queue.submit(printer_fixture.cube(20), 'new.stl', 'any', 'unbinding-new-job')
        self.assertIsNone(f.app.queue.reserve(), 'Current job still owns the printer')
        self.assertEqual(f.app.queue.workflow_jobs(), pinned)
        for index, step in enumerate(('prepare', 'approve', 'produce'), 1):
            f.job_advance(f.teacher_actor, job, step, index, uuid.uuid4().hex)
        old = f.app.queue.reserve()
        self.assertEqual(old['id'], job['id'])
        self.assertTrue(f.app.queue.begin_dispatch(job['id']))
        f.app.queue.dispatched(job['id'])
        f.app.queue.update_report(machine, state='RUNNING', task=old['remote_name'], error=0)
        f.app.queue.update_report(machine, state='FINISH', task=old['remote_name'], error=0)
        self.assertIsNone(f.app.queue.reserve(), 'Physical bed clearance remains required')
        f.job_advance(f.teacher_actor, job, 'collect', 4, 'b' * 32)
        f.job_advance(f.teacher_actor, job, 'clear', 5, 'c' * 32)
        reserved = f.app.queue.reserve()
        self.assertEqual(reserved['id'], new['id'])
        self.assertFalse(reserved.get('workflow_id'))
        self.assertEqual(f.app.queue.workflow_jobs(), [])

    def test_printer_removal_stale_version_and_readonly_are_rejected(self):
        f = self.printer()
        machine, _workflow, version = f.setup_workflow_printer()
        params = {'id': machine, 'workflow_id': None, 'revision': None}
        before = copy.deepcopy(f.app.settings)
        with self.assertRaises(printer_fixture.Conflict):
            f.command(f.teacher_actor, 'printer.workflow.assign', params, version - 1, 'a' * 32)
        with self.assertRaises(PermissionError):
            f.command({**f.teacher_actor, 'scopes': ['makerspace:read']}, 'printer.workflow.assign', params, version, 'b' * 32)
        self.assertEqual(before, f.app.settings)

    def test_laser_removal_preserves_current_job_and_blocks_new_binding(self):
        f = self.laser()
        job = f.setup_job()
        pinned = copy.deepcopy(f.inbox.workflows.snapshot()['jobs'])
        params = {'id': 'laser-fbb2', 'workflow_id': None, 'revision': None}
        removed = f.command(laser_fixture.TEACHER, 'laser.workflow.assign', params, 3, 'a' * 32)
        self.assertIsNone(removed['result']['workflow'])
        self.assertEqual(f.inbox.workflows.snapshot()['jobs'], pinned)
        self.assertEqual(f.command(laser_fixture.TEACHER, 'laser.workflow.assign', params, 3, 'a' * 32), removed)
        self.assertEqual(f.inbox.workflows.snapshot()['jobs'], pinned)
        new = f.inbox.submit({'elevnavn': 'Elev', 'klasse': '5.A', 'projekt': 'Ny',
                             'requestId': 'new-unbinding-job', 'svg': laser_fixture.SVG})
        with self.assertRaises((ValueError, laser_fixture.Conflict)):
            f.command(laser_fixture.TEACHER, 'laser.job.bind', {'id': new['id'], 'station_id': 'laser-fbb2'}, 0, 'b' * 32)
        f.advance(job, 'prepare', 1)
        self.assertEqual(f.inbox.workflows.snapshot()['jobs'][0]['version'], 2)

    def test_laser_removal_stale_version_is_rejected(self):
        f = self.laser()
        f.setup_job()
        before = copy.deepcopy(f.inbox.workflows.snapshot())
        with self.assertRaises(laser_fixture.Conflict):
            f.command(laser_fixture.TEACHER, 'laser.workflow.assign',
                      {'id': 'laser-fbb2', 'workflow_id': None, 'revision': None}, 2, 'a' * 32)
        self.assertEqual(before, f.inbox.workflows.snapshot())


if __name__ == '__main__':
    unittest.main()
