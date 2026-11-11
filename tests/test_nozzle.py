"""Exercise nozzle routing from actual report shapes through reservation and dispatch."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from app.bambu import BambuPrinter, read_trays
from app.core import Queue
from app.slicer import Slicer
from app.worker import Worker
from scripts.smoke_slice import cube


class Profiles:
    def find_filament(self, model, filament_id):
        return 'Bambu PLA Basic' if filament_id == 'GFA00' else None


def report(right='00000003', left='00000103'):
    return {'gcode_state': 'IDLE', 'subtask_name': '', 'print_error': 0, 'ams': {'tray_exist_bits': '11', 'ams': [
        {'id': '0', 'info': right, 'tray': [{'id': '0', 'tray_color': 'FF0000FF', 'tray_type': 'PLA', 'tray_info_idx': 'GFA00'}]},
        {'id': '1', 'info': left, 'tray': [{'id': '0', 'tray_color': '0000FFFF', 'tray_type': 'PLA', 'tray_info_idx': 'GFA00'}]},
    ]}}


class NozzleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 1000.0
        self.config = {'id': 'h2d', 'name': 'H2D', 'model': 'H2D', 'enabled': True, 'access_code': 'test-only', 'serial': 'TEST'}
        self.q = Queue(self.temp.name, [self.config], clock=lambda: self.now)
        # Creates a client but never starts it or connects to a network.
        self.driver = BambuPrinter(self.config, self.q, Profiles())
        self.push(report())
        self.q.release_bed('h2d')

    def tearDown(self):
        self.q.close()
        self.temp.cleanup()

    def push(self, data):
        self.driver._message(None, None, SimpleNamespace(payload=json.dumps({'print': data}).encode()))

    def job(self, colour):
        return self.q.submit(cube(), 'cube.stl', colour, 'nozzle-routing-request')

    def test_colour_determines_both_ams_slot_and_nozzle(self):
        self.job('#0000FF')
        job = self.q.reserve()
        self.assertEqual((job['slot'], job['extruder']), (4, 0))
        self.assertTrue(self.q.begin_dispatch(job['id']))

    def test_any_colour_uses_known_connection(self):
        self.job('any')
        job = self.q.reserve()
        self.assertEqual((job['slot'], job['extruder']), (0, 1))

    def test_invalid_or_shared_routing_is_never_guessed(self):
        for info in (None, '', 'bad-value', '00000E03', '00000F03'):
            with self.subTest(info=info):
                self.assertEqual(read_trays(report(info, info), self.config, Profiles()), [])
        missing = report()
        for ams in missing['ams']['ams']:
            del ams['info']
        self.assertEqual(read_trays(missing, self.config, Profiles()), [])

    def test_reconnecting_ams_to_other_nozzle_requires_reslicing(self):
        self.job('#FF0000')
        job = self.q.reserve()
        self.assertEqual(job['extruder'], 1)
        self.push({'ams': {'ams': [{'id': '0', 'info': '00000103'}]}})
        self.assertFalse(self.q.begin_dispatch(job['id']))
        self.assertEqual(self.q.get_job(job['id'])['status'], 'queued')
        replacement = self.q.reserve()
        self.assertEqual((replacement['slot'], replacement['extruder']), (0, 0))

    def test_fresh_spool_data_does_not_refresh_old_connection(self):
        self.job('#FF0000')
        self.now += 25
        self.push({'gcode_state': 'IDLE'})
        self.now += 20
        self.push({'gcode_state': 'IDLE', 'ams': {'ams': [{'id': '0', 'tray': [{'id': '0', 'remain': 70}]}]}})
        self.assertTrue(self.q.online(self.q.printers['h2d']))
        self.assertTrue(self.q.printers['h2d']['bed_clear'])
        self.assertIsNone(self.q.reserve())
        self.push({'ams': {'ams': [{'id': '0', 'info': '00000003'}]}})
        self.assertIsNotNone(self.q.reserve())

    def test_worker_slices_with_automatically_selected_left_nozzle(self):
        self.job('#0000FF')
        job = self.q.reserve()
        slicer, driver = Mock(), Mock()
        slicer.slice.return_value = Path(self.temp.name) / 'output.gcode.3mf'
        Worker(self.q, slicer, {'h2d': driver}).process(job)
        self.assertEqual(slicer.slice.call_args.args[1]['extruder'], 0)
        driver.send.assert_called_once()

    def test_connection_change_during_upload_never_starts_old_slice(self):
        self.job('#0000FF')
        job = self.q.reserve()
        slicer, driver = Mock(), Mock()
        driver.upload.side_effect = lambda *_: self.push({'ams': {'ams': [{'id': '1', 'info': '00000003'}]}})
        Worker(self.q, slicer, {'h2d': driver}).process(job)
        driver.send.assert_not_called()
        self.assertEqual(self.q.get_job(job['id'])['status'], 'queued')

    def test_slicer_requires_explicit_resolved_connection(self):
        slicer = Slicer('unused', Path(self.temp.name) / 'profiles', self.temp.name)
        with self.assertRaises(ValueError):
            slicer.slice({}, {'model': 'H2D'}, 'unused.stl')


if __name__ == '__main__':
    unittest.main()
