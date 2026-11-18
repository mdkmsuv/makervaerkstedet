"""Physical plate confirmation on synthetic telemetry, without a printer driver."""
import tempfile
import threading
import unittest
from types import SimpleNamespace
from uuid import uuid4

from app.core import FRESH_SECONDS, Queue
from app.printer_config import ConfigConflict
from app.server import Handler, Server
from tests import test_queue_workflows as workflows
from tests.test_library_retention_integration import _HTTP


class PrintPlateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1000
        self.config = {'id': 'p1s', 'name': 'Testprinter', 'model': 'P1S', 'enabled': True}
        self.queue = self.open_queue()
        self.addCleanup(lambda: self.queue.close())

    def open_queue(self):
        return Queue(self.temp.name, [self.config], clock=lambda: self.now)

    def report(self, state='IDLE', task='', error=0):
        self.queue.update_report('p1s', state=state, task=task, error=error)

    def plate(self, status):
        plate, = self.queue.plates()['plates']
        self.assertEqual(set(plate), {'id', 'name', 'status', 'token'})
        self.assertEqual(plate['status'], status)
        if status == 'waiting':
            self.assertRegex(plate['token'], r'^[a-f0-9]{32}$')
        else:
            self.assertEqual(plate['token'], '')
        return plate['token']

    def test_tokens_follow_machine_cycles_and_restart_even_at_identical_clock_times(self):
        self.plate('offline')
        self.report()
        before = self.plate('waiting')
        self.report()
        self.assertEqual(self.plate('waiting'), before)
        self.report('RUNNING', 'unobserved')
        self.report()
        with self.assertRaises(ConfigConflict):
            self.queue.confirm_plate('p1s', before)
        before = self.plate('waiting')
        self.report('RUNNING', 'external')
        self.plate('printing')
        self.report()
        with self.assertRaises(ConfigConflict):
            self.queue.confirm_plate('p1s', before)
        current = self.plate('waiting')
        self.assertNotEqual(current, before)
        self.report(error=2)
        self.plate('blocked')
        self.report()
        with self.assertRaises(ConfigConflict):
            self.queue.confirm_plate('p1s', current)
        current = self.plate('waiting')
        self.now += FRESH_SECONDS
        self.plate('offline')
        self.report()
        with self.assertRaises(ConfigConflict):
            self.queue.confirm_plate('p1s', current)
        current = self.plate('waiting')
        self.queue.close()
        self.queue = self.open_queue()
        self.report()
        with self.assertRaises(ConfigConflict):
            self.queue.confirm_plate('p1s', current)

    def test_physical_clear_on_failed_job_survives_task_cleanup_and_is_audited_once(self):
        self.report('FAILED', 'stopped-job')
        token = self.plate('waiting')
        self.assertEqual(self.queue.confirm_plate('p1s', token), {'ok': True})
        self.plate('clear')
        self.report('FAILED')
        self.assertTrue(self.queue.ready(self.queue.printers['p1s']))
        self.assertFalse(self.queue.teacher_cleared(self.queue.printers['p1s']))
        with self.assertRaises(ConfigConflict):
            self.queue.confirm_plate('p1s', token)
        row, = self.queue.db.execute('SELECT actor,job_id,step_id FROM workflow_audit')
        self.assertEqual(tuple(row), ('station:plate', 'printer:p1s', 'clear'))

    def test_bridge_requires_service_auth_and_rejects_extra_commands_and_replays(self):
        self.report()
        server = Server(('127.0.0.1', 0), Handler)
        token = uuid4().hex
        server.app = SimpleNamespace(queue=self.queue, printer_bridge=SimpleNamespace(token=token))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        http = _HTTP(server)
        path = '/api/makerspace/printers/plates'
        self.assertEqual(http.json('GET', path)[0], 401)
        self.assertEqual(http.json('GET', path, cookie='test=1', authorization=token)[0], 401)
        status, _, result = http.json('GET', path, authorization=token)
        self.assertEqual(status, 200)
        plate, = result['plates']
        confirm = {'id': plate['id'], 'token': plate['token']}
        self.assertEqual(http.json('POST', path, confirm)[0], 401)
        self.assertEqual(http.json('POST', path, dict(confirm, action='start'), authorization=token)[0], 400)
        status, _, result = http.json('POST', path, confirm, authorization=token)
        self.assertEqual((status, result), (200, {'ok': True}))
        self.assertEqual(http.json('POST', path, confirm, authorization=token)[0], 409)


class PrintPlateWorkflowTests(unittest.TestCase):
    def test_only_terminal_clear_advances_on_the_station(self):
        fixture = workflows.QueueWorkflowTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        queue = fixture.q
        job = fixture.job()
        fixture.authorize(job)
        # The authorized job waits for a fresh physical clearance after a disconnect.
        queue.offline('p1s')
        queue.update_report('p1s', state='IDLE', task='', error=0, trays=[{
            'slot': 0, 'colour': '#3678DC', 'material': 'PLA', 'profile': 'Bambu PLA Basic'}])
        self.assertIsNone(queue.reserve())
        plate, = queue.plates()['plates']
        self.assertEqual(plate['status'], 'waiting')
        queue.confirm_plate('p1s', plate['token'])
        self.assertEqual(queue.get_job(job['id'])['workflow_version'], 4)
        prepared = queue.reserve()
        self.assertTrue(queue.begin_dispatch(job['id']))
        queue.dispatched(job['id'])
        queue.update_report('p1s', state='RUNNING', task=prepared['remote_name'], error=0)
        queue.update_report('p1s', state='FINISH', task=prepared['remote_name'], error=0)
        blocked, = queue.plates()['plates']
        self.assertEqual((blocked['status'], blocked['token']), ('blocked', ''))
        with self.assertRaises(ConfigConflict):
            queue.confirm_plate('p1s', '')
        fixture.advance(job, 'collect', 4)
        waiting, = queue.plates()['plates']
        self.assertEqual(waiting['status'], 'waiting')
        queue.confirm_plate('p1s', waiting['token'])
        self.assertTrue(queue.get_job(job['id'])['workflow_complete'])
        self.assertTrue(queue.get_job(job['id'])['physical_resolved'])
        actor, = queue.db.execute('SELECT actor FROM workflow_audit WHERE job_id=? AND step_id=?',
                                  (job['id'], 'clear')).fetchone()
        self.assertEqual(actor, 'station:plate')
        self.assertEqual(queue.plates()['plates'][0]['status'], 'clear')
        with self.assertRaises(PermissionError):
            fixture.advance(job, 'clear', 5, actor='station:plate')
