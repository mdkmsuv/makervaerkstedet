import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from app.laser_station import Station
from app.laser_transfer import FluxTransfer, TransferError
from tests.test_laser_station_material import FakeQueue, SVG, photograph, rectangle


JOB_ID = 'a' * 32
FCODE = b'fake compiled fcode, never a real machine job'


class FakeGhost:
    def __init__(self):
        self.documents = []
        self.models = []

    def available(self):
        return True

    def task(self, document, model='fbb2'):
        self.documents.append(document)
        self.models.append(model)
        return {'fcode': FCODE, 'seconds': 12.5, 'bytes': len(FCODE), 'metadata': {}}


class FakeTransfer:
    def __init__(self, *, available=True, error=None):
        self.available = available
        self.error = error
        self.calls = []

    def status(self):
        return {'available': self.available, 'message': 'transfer disabled', 'physicalStart': False}

    def upload(self, content, *, job_id, model='fbb2'):
        self.calls.append((content, job_id))
        if self.error:
            raise self.error
        return {'path': '/SD/fake.fc', 'bytes': len(content), 'verified': True, 'started': False}


class StationTransferTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.queue = FakeQueue()
        self.ghost = FakeGhost()
        self.transfer = FakeTransfer()
        self.opened = []
        self.station = Station(self.queue, Path(temporary.name) / 'plates', ghost=self.ghost,
                               transfer=self.transfer, opener=self.opened.append)
        self.request = {'material': 'wood_3mm', 'items': [{'id': JOB_ID, 'x': 10, 'y': 10, 'rotation': 0}],
                        'open': False}

    def hexa_station(self, transfer=None):
        return Station(self.queue, self.station.plates_dir.parent / 'hexa', ghost=self.ghost,
                       transfer=transfer if transfer is not None else self.transfer,
                       opener=self.opened.append, machine='fhexa1')

    def approved(self):
        token = self.station.approve_plate(dict(self.request, confirmed=True))['approvalToken']
        return dict(self.request, approvalToken=token)

    def prepared(self):
        approved = self.approved()
        result = self.station.prepare_transfer(approved)
        return dict(approved, transferId=result['transferId'], confirmed=True), result

    def test_prepare_requires_exact_explicit_approval_and_only_compiles_locally(self):
        with self.assertRaises(ValueError):
            self.station.prepare_transfer(self.request)
        with self.assertRaises(ValueError):
            self.station.approve_plate(self.request)
        self.assertEqual(self.ghost.documents, [])
        data, result = self.prepared()
        self.assertEqual(len(self.ghost.documents), 1)
        self.assertEqual(self.transfer.calls, [])
        self.assertEqual(self.opened, [])
        self.assertFalse(result['sent'])
        self.assertFalse(result['started'])
        saved = self.station.prepared_transfer['file']
        self.assertEqual(saved.read_bytes(), FCODE)
        record = json.loads(saved.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertTrue(record['teacherApproved'])
        self.assertEqual(record['sha256'], hashlib.sha256(FCODE).hexdigest())
        self.assertEqual(data['transferId'], record['id'])

    def test_approval_is_bound_to_placement_material_and_source(self):
        approved = self.approved()
        for changed in (dict(approved, items=[dict(self.request['items'][0], x=20)]),
                        dict(approved, material='acrylic_3mm')):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.station.prepare_transfer(changed)
        self.queue.svg = SVG.replace('H60 V30', 'H59 V30')
        with self.assertRaises(ValueError):
            self.station.prepare_transfer(approved)
        self.assertEqual(self.ghost.documents, [])

    def test_material_scene_change_invalidates_prepared_transfer(self):
        scene = self.station.material_image(photograph())['scene']
        scene = self.station.material_regions({
            'sceneId': scene['id'], 'revision': scene['revision'],
            'regions': [{'id': 'sheet', 'points': rectangle(0, 0, 300, 200), 'holes': []}],
        })['scene']
        scene = self.station.material_approve({
            'sceneId': scene['id'], 'revision': scene['revision'], 'confirmed': True,
        })['scene']
        self.request.update(sceneId=scene['id'], sceneRevision=scene['revision'])
        data, _ = self.prepared()
        self.station.material_image(photograph())
        with self.assertRaises(ValueError):
            self.station.send_transfer(data)
        self.assertEqual(self.transfer.calls, [])

    def test_send_requires_confirmation_current_job_and_untampered_file(self):
        data, _ = self.prepared()
        for changed in (dict(data, confirmed=False), dict(data, transferId='0' * 32),
                        dict(data, material='acrylic_3mm'),
                        dict(data, items=[dict(self.request['items'][0], x=20)])):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.station.send_transfer(changed)
        self.assertEqual(self.transfer.calls, [])
        file = self.station.prepared_transfer['file']
        file.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'ændret'):
            self.station.send_transfer(data)
        self.assertEqual(self.transfer.calls, [])

    def test_send_rejects_stale_source_expired_and_disabled_transfer(self):
        data, _ = self.prepared()
        self.station.prepared_transfer['expires'] = 0
        with self.assertRaises(ValueError):
            self.station.send_transfer(data)
        self.assertEqual(self.transfer.calls, [])
        self.station.prepared_transfer['expires'] = float('inf')
        self.transfer.available = False
        with self.assertRaisesRegex(ValueError, 'disabled'):
            self.station.send_transfer(data)
        self.assertEqual(self.transfer.calls, [])
        self.transfer.available = True
        self.queue.svg = SVG.replace('H60 V30', 'H59 V30')
        with self.assertRaises(ValueError):
            self.station.send_transfer(data)
        self.assertEqual(self.transfer.calls, [])

    def test_send_is_single_attempt_even_after_uncertain_failure(self):
        data, _ = self.prepared()
        self.transfer.error = TransferError('uncertain upload')
        with self.assertRaisesRegex(TransferError, 'uncertain'):
            self.station.send_transfer(data)
        self.assertEqual(self.transfer.calls, [(FCODE, data['transferId'])])
        with self.assertRaises(ValueError):
            self.station.send_transfer(data)
        self.assertEqual(len(self.transfer.calls), 1)

    def test_success_records_verified_upload_without_physical_start(self):
        data, _ = self.prepared()
        result = self.station.send_transfer(data)
        self.assertTrue(result['sent'])
        self.assertFalse(result['started'])
        self.assertEqual(self.transfer.calls, [(FCODE, data['transferId'])])
        self.assertEqual(self.opened, [])
        record = json.loads(self.station.prepared_transfer['file'].with_suffix('.json').read_text(encoding='utf-8'))
        self.assertTrue(record['sent'])
        self.assertFalse(record['started'])
        self.assertFalse(self.station.last_plate['physicalStarted'])
        with self.assertRaises(ValueError):
            self.station.send_transfer(data)

    def test_hexa_state_presets_calibration_and_placement_bounds(self):
        station = self.hexa_station()
        state = station.state()
        self.assertEqual(state['bed'], {'width': 730.0, 'height': 410.0, 'machine': 'HEXA',
                                        'model': 'fhexa1', 'cameraWidth': 730.0, 'cameraHeight': 395.0})
        self.assertEqual(station.presets['model'], 'fhexa1')
        self.assertEqual(station.presets['presets']['wood_3mm_cutting']['speed'], 6)
        scene = station.material_image(photograph())['scene']
        self.assertEqual((scene['width'], scene['height'], scene['imageWidth'], scene['imageHeight']),
                         (730.0, 410.0, 1200, 674))
        station.material_clear()
        good = dict(self.request, items=[dict(self.request['items'][0], x=650)])
        bad = dict(self.request, items=[dict(self.request['items'][0], x=680)])
        self.assertEqual(station.check(good, run_flux=False)[0]['problems'], [])
        self.assertTrue(station.check(bad, run_flux=False)[0]['problems'])
        token = station.approve_plate(dict(good, confirmed=True))['approvalToken']
        station.prepare_transfer(dict(good, approvalToken=token))
        self.assertEqual(self.ghost.models, ['fhexa1'])
        self.assertEqual(station.prepared_transfer['record']['machine'], 'fhexa1')
        self.assertIn('data-workarea="fhexa1"', self.ghost.documents[0])

    def test_hexa_send_refuses_beambox_transfer_configuration_without_socket(self):
        key = self.station.plates_dir.parent / 'trusted.pem'
        key.write_text('-----BEGIN RSA PRIVATE KEY-----\nfixture\n', encoding='ascii')
        transfer = FluxTransfer({'enabled': True, 'commissioned': True, 'port': 8282,
                                 'device_uuid': 'c' * 32, 'model': 'fbb2',
                                 'client_key_file': str(key)},
                                websocket_factory=lambda *args, **kwargs: self.fail('socket opened'))
        station = self.hexa_station(transfer=transfer)
        token = station.approve_plate(dict(self.request, confirmed=True))['approvalToken']
        data = dict(self.request, approvalToken=token)
        transfer_id = station.prepare_transfer(data)['transferId']
        with self.assertRaisesRegex(TransferError, 'model'):
            station.send_transfer(dict(data, transferId=transfer_id, confirmed=True))
        self.assertTrue(station.prepared_transfer['attempted'])

    def test_unverified_receipt_is_not_recorded_as_sent(self):
        data, _ = self.prepared()
        self.transfer.upload = lambda content, *, job_id, model: {
            'path': '/SD/fake.fc', 'bytes': len(content), 'verified': False, 'started': False,
        }
        with self.assertRaisesRegex(TransferError, 'kvittering'):
            self.station.send_transfer(data)
        self.assertFalse(self.station.prepared_transfer['record']['sent'])
        self.assertTrue(self.station.prepared_transfer['attempted'])
        with self.assertRaises(ValueError):
            self.station.send_transfer(data)


if __name__ == '__main__':
    unittest.main()
