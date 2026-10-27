import hashlib
import json
from pathlib import Path
import unittest

from app import installation_update
from app.generation_selection import bind_arguments
from app.installation_backup import restore, snapshot, verify

class GenerationSchoolRestoreTests(unittest.TestCase):
    def setUp(self):
        from tests.test_installation_backup import InstallationBackupTests
        self.data = InstallationBackupTests()
        self.data.setUp()
        self.addCleanup(self.data.tearDown)
        self.root = Path(self.data.temp.name)
        self._add_generation_files()

    def _add_generation_files(self):
        server = self.data.server
        laserstation = server / 'laserstation'
        laserstation.mkdir()
        (server / 'Laserplader').mkdir()
        (server / 'Laserplader/backup-protocol.json').write_text(
            json.dumps({'protocol': 1, 'role': 'laser_station'}), encoding='utf-8')
        (server / 'security' / 'vision-ca.crt').write_bytes(b'vision-ca')
        (server / 'security' / 'laser-client.key').write_bytes(b'laser-client-key')
        external = self.root / 'external-beam-studio-sentinel.exe'
        external.write_bytes(b'beam-studio-sentinel')
        (server / 'installation-owner.json').write_text(json.dumps({
            'schemaVersion': 1,
            'product': 'ElevhubServer',
            'startupProtocol': 'generation-v1',
            'ownerId': '0123456789abcdef0123456789abcdef',
            'dataRoot': str(server),
            'taskName': 'ElevhubServer',
        }), encoding='utf-8')
        (laserstation / 'laser-installation.json').write_text(json.dumps({
            'plates': str(server / 'Laserplader'),
            'port': 8790,
            'ca_file': str(server / 'security' / 'ca.crt'),
            'vision': {'ca_file': str(server / 'security' / 'vision-ca.crt')},
            'direct_transfer': {'client_key_file': str(server / 'security' / 'laser-client.key')},
            'beam_studio': str(external),
            'flux_api': str(external),
            'beam_studio_store': str(external),
            'server': 'https://school.example',
            'custom': {'preserve': True},
        }), encoding='utf-8')
        self.station_path = laserstation / 'laser-installation.json'
        self.owner_path = server / 'installation-owner.json'
        self.external = external

    def _assert_rebased_files(self, restored_root):
        owner = json.loads((restored_root / 'installation-owner.json').read_text(encoding='utf-8'))
        station = json.loads((restored_root / 'laserstation/laser-installation.json').read_text(encoding='utf-8'))
        self.assertEqual(owner['dataRoot'], str(restored_root))
        self.assertEqual(owner['ownerId'], '0123456789abcdef0123456789abcdef')
        self.assertEqual(station['plates'], str(restored_root / 'Laserplader'))
        self.assertEqual(station['ca_file'], str(restored_root / 'security/ca.crt'))
        self.assertEqual(station['vision']['ca_file'], str(restored_root / 'security/vision-ca.crt'))
        self.assertEqual(station['direct_transfer']['client_key_file'], str(restored_root / 'security/laser-client.key'))
        self.assertEqual(station['beam_studio'], str(self.external))
        self.assertEqual(station['flux_api'], str(self.external))
        self.assertEqual(station['beam_studio_store'], str(self.external))
        self.assertEqual(station['custom'], {'preserve': True})
        bound = bind_arguments({'data_root': str(restored_root)}, 'laser-station', [])
        self.assertEqual(bound, ['--config', str(restored_root / 'laserstation/laser-installation.json')])

    def test_backup_restore_rebases_generation_files_and_preserves_source(self):
        backup = self.root / 'generation-backup'
        destination = self.root / 'generation-restored'
        watched = (self.owner_path, self.station_path, self.external,
                   self.data.server / 'security/ca.crt', self.data.server / 'security/laser-client.key')
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in watched}
        self.assertTrue(snapshot(self.data.profile, backup)['ok'])
        self.assertTrue(verify(backup)['ok'])
        report = restore(backup, destination)
        self.assertTrue(report['isolated'])
        restored_root = destination / 'roots/server'
        self._assert_rebased_files(restored_root)
        self.assertTrue((destination / '.restore-hold.json').exists())
        for path in (restored_root, restored_root / 'hub', restored_root / 'printvaerksted', restored_root / 'designvaerksted'):
            self.assertTrue((path / '.restore-hold.json').exists(), path)
        self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in watched})

    def test_prepare_verify_rebases_files_and_changed_station_is_rejected(self):
        from tests.test_release_compatibility import ReleaseCompatibilityTests
        source_builder = ReleaseCompatibilityTests()
        source_builder.setUp()
        self.addCleanup(source_builder.tearDown)
        program = source_builder._source_cohort()
        destination = self.root / 'prepared-generation'
        watched = (self.owner_path, self.station_path, self.data.server / 'security/ca.crt')
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in watched}
        result = installation_update.prepare(self.data.profile, program, destination)
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(installation_update.verify_prepared(destination), result)
        candidate = destination / 'candidate-data' / 'roots/server'
        self._assert_rebased_files(candidate)
        self.assertTrue((destination / '.restore-hold.json').exists())
        self.assertTrue((destination / 'candidate-data/.restore-hold.json').exists())
        for path in (candidate, candidate / 'hub', candidate / 'printvaerksted', candidate / 'designvaerksted'):
            self.assertTrue((path / '.restore-hold.json').exists(), path)
        self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in watched})
        changed = json.loads((candidate / 'laserstation/laser-installation.json').read_text(encoding='utf-8'))
        changed['custom']['preserve'] = False
        (candidate / 'laserstation/laser-installation.json').write_text(json.dumps(changed), encoding='utf-8')
        with self.assertRaises(ValueError):
            installation_update.verify_prepared(destination)

    def test_legacy_owner_is_preserved_but_unknown_protocol_is_refused(self):
        owner = json.loads(self.owner_path.read_text(encoding='utf-8'))
        del owner['startupProtocol']
        self.owner_path.write_text(json.dumps(owner), encoding='utf-8')
        backup = self.root / 'legacy-backup'
        snapshot(self.data.profile, backup)
        destination = self.root / 'legacy-restored'
        restore(backup, destination)
        self.assertEqual((destination / 'roots/server/installation-owner.json').read_bytes(),
                         self.owner_path.read_bytes())
        owner['startupProtocol'] = 'typo-generation'
        self.owner_path.write_text(json.dumps(owner), encoding='utf-8')
        invalid = self.root / 'invalid-protocol-backup'
        snapshot(self.data.profile, invalid)
        with self.assertRaisesRegex(ValueError, 'ukendt opstartsprotokol'):
            restore(invalid, self.root / 'invalid-restored')


if __name__ == '__main__':
    unittest.main()
