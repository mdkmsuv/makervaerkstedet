"""Offline queue recovery and FTPS failures, with no hardware or live data."""
import copy
import ftplib
import json
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock

from app.core import Queue
from app.printer_bridge import PrinterBridge
from app.printer_config import ConfigConflict
from app.worker import Worker
from scripts.smoke_slice import cube
from tests.test_queue_workflows import BINDING, TEACHER


class PrinterRecoveryTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.now = 1000
        self.config = {'id': 'one', 'name': 'Printer', 'location': '', 'model': 'A1 mini',
                       'host': '192.168.1.40', 'serial': 'SERIAL1', 'access_code': 'secret',
                       'enabled': True, 'version': 1, 'bed_type': 'Textured PEI Plate'}
        self.q = Queue(self.folder, [self.config], clock=lambda: self.now)
        self.trays = [dict(slot=254, extruder=0, colour='#3678DC', material='PLA',
                           profile='Bambu PLA Basic')]
        self.report()
        self.q.release_bed('one')
        self.job = self.q.submit(cube(10), 'model.stl', 'any', 'transfer-recovery-test')
        self.driver = Mock()
        self.slicer = Mock()
        self.slicer.slice.side_effect = self.slice
        self.worker = Worker(self.q, self.slicer, {'one': self.driver})

    def tearDown(self):
        self.q.close()
        self.temp.cleanup()

    def report(self, state='IDLE', task=''):
        self.q.update_report('one', state=state, task=task, error=0, trays=self.trays)

    def slice(self, job, config, source):
        output = self.folder / 'sliced' / job['id'] / job['remote_name']
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b'validated sliced fixture')
        return output

    def attempt(self):
        job = self.q.reserve()
        self.assertIsNotNone(job)
        with self.assertLogs('app.worker', level='ERROR') if self.driver.upload.side_effect else _NoLogs():
            self.worker.process(job)
        return self.q.get_job(self.job['id'])

    def due(self):
        self.now += 31
        self.report()

    def restart(self):
        self.q.close()
        self.now += 5
        self.q = Queue(self.folder, [self.config], clock=lambda: self.now)
        self.worker = Worker(self.q, self.slicer, {'one': self.driver})

    def recovering(self, workflow=False):
        job = self.q.reserve()
        if workflow:
            # Pin an already authorized workflow without changing unrelated tests.
            self.q._change(job['id'], workflow=json.dumps(BINDING), workflow_version=4,
                           workflow_step=3, workflow_authorized=1)
        self.assertTrue(self.q.begin_dispatch(job['id']))
        self.q.offline('one')
        return self.q.get_job(job['id'])

    def test_offline_recovering_can_be_isolated_without_claiming_done_or_empty_bed(self):
        job = self.recovering()
        self.assertEqual(self.q.resolution_options(job), ['isolated'])
        for invalid in ('done', 'failed', 'cancelled'):
            with self.assertRaises(ValueError):
                self.q.resolve(job['id'], invalid)
        self.q.resolve(job['id'], 'isolated')
        current = self.q.get_job(job['id'])
        self.assertEqual(current['status'], 'cancelled')
        self.assertTrue(current['isolated'])
        self.assertIsNone(self.q.active('one'))
        self.assertFalse(self.q.printers['one']['bed_clear'])
        with self.assertRaises(ValueError):
            self.q.release_bed('one')
        self.restart()
        self.assertEqual(self.q.get_job(job['id'])['status'], 'cancelled')
        self.assertIsNone(self.q.reserve())

    def test_stale_offline_option_is_rejected_after_coherent_reconnect(self):
        job = self.recovering()
        self.assertIn('isolated', self.q.resolution_options(job))
        self.report('RUNNING', job['remote_name'])
        with self.assertRaises(ValueError):
            self.q.resolve(job['id'], 'isolated')
        self.assertEqual(self.q.get_job(job['id'])['status'], 'printing')

    def test_isolation_retry_does_not_revoke_later_physical_clear(self):
        job = self.recovering()
        self.q.resolve(job['id'], 'isolated')
        model = self.q.library_page()['files'][0]
        self.assertTrue(model['in_use'])
        self.report()
        self.q.release_bed('one')
        self.q.resolve(job['id'], 'isolated')
        self.assertTrue(self.q.printers['one']['bed_clear'])
        current = self.q.get_job(job['id'])
        self.assertTrue(current['isolated'])
        self.assertTrue(current['physical_resolved'])
        self.assertFalse(self.q.library_page()['files'][0]['in_use'])
        self.restart()
        self.assertTrue(self.q.get_job(job['id'])['physical_resolved'])
        self.assertTrue(self.q.library_delete(model['digest'], 'a' * 32,
                                            model['generation'], True)['deleted'])

    def test_stale_telemetry_offers_isolation_but_running_reconnect_blocks_clear(self):
        job = self.q.reserve()
        self.assertTrue(self.q.begin_dispatch(job['id']))
        self.q._change(job['id'], status='attention')
        self.now += 41
        self.assertEqual(self.q.resolution_options(self.q.get_job(job['id'])), ['isolated'])
        self.q.resolve(job['id'], 'isolated')
        self.report('RUNNING', job['remote_name'])
        self.assertFalse(self.q.printers['one']['bed_clear'])
        with self.assertRaises(ValueError):
            self.q.release_bed('one')

    def test_isolated_workflow_allows_connection_repair_but_requires_physical_clear(self):
        job = self.recovering(workflow=True)
        self.q.resolve(job['id'], 'isolated')
        current = self.q.get_job(job['id'])
        self.assertEqual(current['workflow_step'], 5)
        self.assertFalse(current['workflow_authorized'])
        self.assertTrue(self.q.workflow_blocks_bed('one'))
        app = types.SimpleNamespace(folder=self.folder, settings_path=self.folder / 'settings.json',
            queue=self.q, settings={'printers': [self.config]}, settings_lock=threading.RLock(),
            slicer=self.slicer, drivers={'one': self.driver}, demo=False)
        factory = Mock(return_value=Mock())
        bridge = PrinterBridge(app, factory)
        data = {**self.config, 'host': '192.168.1.41', 'access_code': ''}
        data.pop('version')
        command = {'action': 'printer.update', 'params': data, 'expected_version': 1,
                   'request_id': 'd' * 32}
        response = bridge.execute(TEACHER, command)
        self.assertEqual(bridge.execute(TEACHER, command), response)
        factory.assert_called_once()
        self.assertFalse(self.q.printers['one']['bed_clear'])
        self.assertTrue(self.q.workflow_blocks_bed('one'))
        changed = copy.deepcopy(command)
        changed.update(expected_version=2, request_id='e' * 32)
        changed['params']['serial'] = 'OTHER'
        with self.assertRaises(ConfigConflict):
            bridge.execute(TEACHER, changed)
        self.report()
        with self.assertRaises(ValueError):
            self.q.release_bed('one')
        clear = {'action': 'printer.job.advance', 'params': {'id': job['id'], 'step_id': 'clear', 'confirmed': True},
                 'expected_version': current['workflow_version'], 'request_id': 'f' * 32}
        self.q.advance_workflow(TEACHER, clear)
        self.assertTrue(self.q.printers['one']['bed_clear'])
        self.assertTrue(self.q.get_job(job['id'])['physical_resolved'])
        self.assertFalse(self.q.library_page()['files'][0]['in_use'])

    def test_transfer_failure_preserves_mqtt_and_bed_and_stops_after_three_attempts(self):
        self.driver.upload.side_effect = TimeoutError('contains secret: do not expose')
        for count in range(1, 4):
            job = self.attempt()
            self.assertEqual(job['transfer_attempts'], count)
            self.assertEqual(job['status'], 'attention' if count == 3 else 'queued')
            self.assertTrue(self.q.online(self.q.printers['one']))
            self.assertEqual(self.q.printers['one']['state'], 'IDLE')
            self.assertTrue(self.q.printers['one']['bed_clear'])
            self.assertNotIn('secret', job['message'])
            self.assertIn('tidsfristen', self.q.snapshot()['printers'][0]['message'])
            self.due()
        self.assertIsNone(self.q.reserve())
        self.slicer.slice.assert_called_once()
        self.assertEqual(self.driver.upload.call_count, 3)
        self.driver.send.assert_not_called()

    def test_ftp_permanent_error_is_bounded_and_does_not_mark_mqtt_offline(self):
        self.driver.upload.side_effect = ftplib.error_perm('550 storage refused')
        self.assertEqual(self.attempt()['status'], 'queued')
        self.assertTrue(self.q.online(self.q.printers['one']))
        self.assertIn('printeren afviste filen', self.q.get_job(self.job['id'])['message'])

    def test_successful_retry_reuses_slice_and_still_crosses_dispatch_gate(self):
        self.driver.upload.side_effect = TimeoutError()
        self.attempt(); self.due()
        self.driver.upload.side_effect = None
        self.assertEqual(self.attempt()['status'], 'starting')
        self.slicer.slice.assert_called_once()
        self.driver.send.assert_called_once()
        self.assertFalse(self.q.printers['one']['bed_clear'])
        self.assertEqual(self.q.get_job(self.job['id'])['transfer_attempts'], 0)

    def test_changed_config_or_payload_requires_new_slicing(self):
        self.driver.upload.side_effect = TimeoutError()
        self.attempt(); self.due()
        self.q.configs['one']['bed_type'] = 'High Temp Plate'
        self.attempt(); self.due()
        path = self.worker.prepared[self.job['id']][1]
        path.write_bytes(b'changed sliced file')
        self.attempt()
        self.assertEqual(self.slicer.slice.call_count, 3)

    def test_restart_preserves_attempt_count_and_reslices_conservatively(self):
        self.driver.upload.side_effect = TimeoutError()
        self.attempt()
        self.restart(); self.due(); self.q.release_bed('one')
        self.assertEqual(self.attempt()['transfer_attempts'], 2)
        self.due()
        self.assertEqual(self.attempt()['status'], 'attention')
        self.assertEqual(self.slicer.slice.call_count, 2)
        self.restart(); self.report()
        self.assertIsNone(self.q.reserve())

    def test_crash_during_last_transfer_cannot_get_an_extra_attempt(self):
        job = self.q.reserve()
        self.q._change(job['id'], transfer_attempts=2)
        self.assertTrue(self.q.begin_transfer(job['id']))
        self.restart(); self.report(); self.q.release_bed('one')
        self.assertEqual(self.attempt()['status'], 'attention')
        self.slicer.slice.assert_not_called()
        self.driver.upload.assert_not_called()

    def test_bed_revocation_during_upload_prevents_dispatch_even_on_success(self):
        self.driver.upload.side_effect = lambda *args: self.q.offline('one')
        job = self.q.reserve()
        self.worker.process(job)
        self.driver.send.assert_not_called()
        self.assertEqual(self.q.get_job(job['id'])['status'], 'queued')
        self.assertFalse(self.q.printers['one']['bed_clear'])


class _NoLogs:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


if __name__ == '__main__':
    unittest.main()
