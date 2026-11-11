from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from app.generation_relocation import relocate_school_files


class GenerationRelocationTests(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.gettempdir()).resolve() / 'school-relocation-fixture'
        self.old = root / 'data-old'
        self.new = root / 'data-new'
        self.external = root / 'machine'

    def test_rebases_owner_laser_and_internal_private_paths_without_mutation(self):
        files = {
            'installation-owner.json': {
                'schemaVersion': 1, 'product': 'ElevhubServer', 'startupProtocol': 'generation-v1',
                'ownerId': 'keep-me', 'dataRoot': str(self.old),
            },
            'laserstation/laser-installation.json': {
                'plates': str(self.old / 'Laserplader'), 'port': 8790,
                'ca_file': str(self.old / 'security' / 'ca.crt'),
                'vision': {'ca_file': str(self.old / 'security' / 'vision-ca.crt')},
                'direct_transfer': {'client_key_file': str(self.old / 'laser.key')},
                'beam_studio': r'C:\Program Files\Beam Studio\Beam Studio.exe',
                'flux_api': r'C:\Program Files\Beam Studio\flux_api.exe',
                'beam_studio_store': r'C:\Users\Teacher\AppData\Roaming\Beam Studio\config.json',
                'server': 'https://school.example', 'unknown': {'keep': True},
            },
        }
        before = deepcopy(files)
        result = relocate_school_files(self.old, self.new, files)
        self.assertEqual(files, before)
        self.assertEqual(result['installation-owner.json']['dataRoot'], str(self.new))
        laser = result['laserstation/laser-installation.json']
        self.assertEqual(laser['plates'], str(self.new / 'Laserplader'))
        self.assertEqual(laser['ca_file'], str(self.new / 'security' / 'ca.crt'))
        self.assertEqual(laser['vision']['ca_file'], str(self.new / 'security' / 'vision-ca.crt'))
        self.assertEqual(laser['direct_transfer']['client_key_file'], str(self.new / 'laser.key'))
        self.assertEqual(laser['beam_studio'], before['laserstation/laser-installation.json']['beam_studio'])
        self.assertEqual(laser['flux_api'], before['laserstation/laser-installation.json']['flux_api'])
        self.assertEqual(laser['beam_studio_store'], before['laserstation/laser-installation.json']['beam_studio_store'])
        self.assertEqual(laser['unknown'], {'keep': True})

    def test_external_private_paths_are_preserved(self):
        files = {'laserstation/laser-installation.json': {
            'plates': str(self.old / 'Laserplader'),
            'ca_file': str(self.external / 'ca.crt'),
            'vision': {'ca_file': str(self.external / 'vision.crt')},
            'direct_transfer': {'client_key_file': str(self.external / 'flux.key')},
        }}
        result = relocate_school_files(self.old, self.new, files)
        self.assertEqual(result, files | {'laserstation/laser-installation.json': {
            **files['laserstation/laser-installation.json'], 'plates': str(self.new / 'Laserplader')}})

    def test_malformed_owner_laser_and_paths_are_rejected(self):
        cases = [
            ({'installation-owner.json': {'schemaVersion': 1, 'product': 'ElevhubServer',
                                          'startupProtocol': 'generation-v1', 'dataRoot': str(self.old / 'other')}}, 'owner'),
            ({'installation-owner.json': {'schemaVersion': 1, 'product': 'ElevhubServer',
                                          'startupProtocol': 'legacy', 'dataRoot': str(self.old)}}, 'protocol'),
            ({'laserstation/laser-installation.json': {'plates': str(self.old / 'Other')}}, 'plates'),
            ({'laserstation/laser-installation.json': {'plates': str(self.old / 'Laserplader'), 'port': True}}, 'port'),
            ({'laserstation/laser-installation.json': {'plates': str(self.old / 'Laserplader'),
                                                        'vision': 'bad'}}, 'nested'),
            ({'laserstation/laser-installation.json': {'plates': 'Laserplader'}}, 'relative'),
        ]
        for files, label in cases:
            with self.subTest(label=label), self.assertRaises(ValueError):
                relocate_school_files(self.old, self.new, files)

    def test_unknown_files_and_fields_are_deep_copied(self):
        files = {'school.json': {'nested': {'value': ['x']}}}
        result = relocate_school_files(self.old, self.new, files)
        self.assertEqual(result, files)
        self.assertIsNot(result, files)
        self.assertIsNot(result['school.json']['nested'], files['school.json']['nested'])

    def test_invalid_file_keys_and_same_root_are_rejected(self):
        with self.assertRaises(ValueError):
            relocate_school_files(self.old, self.new, {'../escape.json': {}})
        with self.assertRaises(ValueError):
            relocate_school_files(self.old, self.old, {})

    def test_roots_must_be_disjoint_and_lexically_unambiguous(self):
        with self.assertRaises(ValueError):
            relocate_school_files(self.old, self.old / 'child', {})
        with self.assertRaises(ValueError):
            relocate_school_files(self.old / 'child', self.old, {})
        for old, new in ((r'C:\School\data-old\..\old', r'C:\School\data-new'),
                         (r'\\server\share\old', r'C:\School\data-new'),
                         (r'C:relative-old', r'C:\School\data-new'),
                         (r'\relative-old', r'C:\School\data-new')):
            with self.subTest(old=old, new=new), self.assertRaises(ValueError):
                relocate_school_files(old, new, {})

    def test_interpreted_paths_reject_controls_dot_segments_and_drive_relative(self):
        cases = [
            {'installation-owner.json': {
                'schemaVersion': 1, 'product': 'ElevhubServer', 'startupProtocol': 'generation-v1',
                'dataRoot': str(self.old / 'part..' / '..' / 'data-old')}},
            {'laserstation/laser-installation.json': {
                'plates': str(self.old / 'Laserplader' / '..' / 'Laserplader')}},
            {'laserstation/laser-installation.json': {
                'plates': str(self.old / 'Laserplader'), 'ca_file': 'C:relative-ca.crt'}},
            {'laserstation/laser-installation.json': {
                'plates': str(self.old / 'Laserplader'), 'vision': {'ca_file': str(self.old / 'ca\n.crt')}}},
        ]
        for files in cases:
            with self.subTest(files=files), self.assertRaises(ValueError):
                relocate_school_files(self.old, self.new, files)
        for key in ('bad:name.json', 'bad\\name.json', 'bad\nname.json'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                relocate_school_files(self.old, self.new, {key: {}})


if __name__ == '__main__':
    unittest.main()
