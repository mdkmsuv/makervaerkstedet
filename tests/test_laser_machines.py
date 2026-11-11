import xml.etree.ElementTree as ET
import unittest

from app import laser_plate as plate
from app.laser_ghost import FluxGhost
from app.laser_machines import get_machine
from tests.test_laser_station_material import SVG


class FakeParserSocket:
    def __init__(self):
        self.commands = []
        self.payload = []
        self.replies = iter([{'status': 'continue'}, {'status': 'ok'},
                             {'status': 'complete', 'length': 3, 'time': 1.0, 'metadata': {}}, b'abc'])

    def send_text(self, text):
        self.commands.append(text)

    def send_binary(self, data):
        self.payload.append(data)

    def recv_json_or_bytes(self):
        return next(self.replies)

    def close(self):
        pass


class MachineProfileTests(unittest.TestCase):
    def test_profiles_keep_conservative_hexa_area_separate_from_document(self):
        hexa = get_machine('fhexa1')
        self.assertEqual((hexa['width'], hexa['height']), (730, 410))
        self.assertEqual((hexa['cameraWidth'], hexa['cameraHeight']), (730, 395))
        self.assertEqual((hexa['documentWidth'], hexa['documentHeight']), (740, 410))
        self.assertEqual((get_machine()['width'], get_machine()['height']), (600, 375))
        with self.assertRaises(ValueError):
            get_machine('fhx2rf4')  # HEXA RF is a different machine.

    def test_hexa_presets_are_dedicated_and_have_distinct_cut_settings(self):
        hexa = plate.load_presets(model='fhexa1')
        beambox = plate.load_presets()
        self.assertEqual(hexa['model'], 'fhexa1')
        self.assertIn('Beam Studio 2.5.14', hexa['source'])
        self.assertEqual(hexa['units'], {'power': '%', 'speed': 'mm/s'})
        self.assertNotEqual(hexa['presets']['wood_3mm_cutting'], beambox['presets']['wood_3mm_cutting'])
        self.assertEqual(hexa['presets']['wood_3mm_cutting']['speed'], 6)

    def test_hexa_bvg_metadata_matches_beam_studio_model(self):
        geometry = plate.job_geometry(SVG)
        entries = [dict(id='a' * 32, name='Test', geometry=geometry, x=10, y=10, rotation=0)]
        material = plate.material_by_id(plate.load_presets(model='fhexa1'), 'wood_3mm')
        root = ET.fromstring(plate.build_bvg(entries, material, model='fhexa1', bed=(730, 410)))
        self.assertEqual((root.get('width'), root.get('height'), root.get('data-workarea')),
                         ('7400', '4100', 'fhexa1'))
        self.assertEqual(root.get('viewBox'), '0 0 7400 4100')
        layer = root.find('{http://www.w3.org/2000/svg}g')
        self.assertIsNotNone(layer)
        # Beam Studio 2.5.14 layer-config-helper.ts baseConfig applies to HEXA:
        # LASER_UNIVERSAL=15 and the focus/height defaults are model-independent.
        self.assertEqual({key: layer.get('data-' + key) for key in
                          ('module', 'height', 'focus', 'focusStep', 'zstep', 'minPower')},
                         {'module': '15', 'height': '-3', 'focus': '-2',
                          'focusStep': '-2', 'zstep': '0', 'minPower': '0'})
        with self.assertRaises(ValueError):
            plate.build_bvg(entries, material, model='fhexa1', bed=(600, 375))

    def test_compiler_uses_model_specific_verified_flags_without_hardware(self):
        ghost = FluxGhost(executable=None)
        ghost._start = lambda: None
        ws = FakeParserSocket()
        ghost._connect = lambda route: ws
        task = ghost.task('<svg/>', model='fhexa1')
        self.assertEqual(task['fcode'], b'abc')
        self.assertIn('-fhexa1 -model fhexa1', ws.commands[0])
        self.assertEqual(ws.commands[1], 'go  -f -hexa -model fhexa1 -acc 7500 -vsc -vsl 1200 -min-speed 0.5')
        self.assertNotIn('fbb2', ' '.join(ws.commands))
        with self.assertRaises(ValueError):
            ghost.task('<svg/>', model='unverified')


if __name__ == '__main__':
    unittest.main()
