import hashlib
import json
from pathlib import Path
import shutil
import unittest
import uuid
from unittest.mock import patch

from app import generation_materialization, installation_update


class GenerationMaterializationTests(unittest.TestCase):
    def setUp(self):
        from tests.test_installation_update import InstallationUpdateTests
        self.update = InstallationUpdateTests()
        self.update.setUp()
        self.addCleanup(self.update.doCleanups)
        self.root = Path(self.update.data.temp.name)
        self.install_root = self.root / 'installed-runtime'
        self.install_root.mkdir()
        owner_path = self.update.data.server / 'installation-owner.json'
        owner = {
            'schemaVersion': 1, 'product': 'ElevhubServer', 'startupProtocol': 'generation-v1',
            'state': 'installed', 'taskName': 'ElevhubServer',
            'ownerId': str(uuid.UUID('0123456789abcdef0123456789abcdef')),
            'installRoot': str(self.install_root), 'dataRoot': str(self.update.data.server),
        }
        owner_path.write_text(json.dumps(owner), encoding='utf-8')
        laserstation = self.update.data.server / 'laserstation'
        laserstation.mkdir()
        (self.update.data.server / 'Laserplader').mkdir()
        (self.update.data.server / 'Laserplader/backup-protocol.json').write_text(
            json.dumps({'protocol': 1, 'role': 'laser_station'}), encoding='utf-8')
        (self.update.data.server / 'security' / 'ca.crt').write_bytes(b'laser-ca')
        (self.update.data.server / 'security' / 'vision-ca.crt').write_bytes(b'vision-ca')
        (self.update.data.server / 'security' / 'laser-client.key').write_bytes(b'laser-key')
        (laserstation / 'laser-installation.json').write_text(json.dumps({
            'plates': str(self.update.data.server / 'Laserplader'), 'port': 8790,
            'ca_file': str(self.update.data.server / 'security' / 'ca.crt'),
            'vision': {'ca_file': str(self.update.data.server / 'security' / 'vision-ca.crt')},
            'direct_transfer': {'client_key_file': str(self.update.data.server / 'security' / 'laser-client.key')},
        }), encoding='utf-8')
        self.destination = self.root / 'materialized'
        self.prepared = self.root / 'prepared-materialization'
        self.result = installation_update.prepare(self.update.data.profile, self.update.program,
                                                  self.prepared)
        self.operation_id = json.loads((self.prepared / installation_update.JOURNAL).read_text(
            encoding='utf-8'))['operation_id']

    @staticmethod
    def _tree_hash(path):
        values = {}
        for item in sorted(path.rglob('*')):
            if item.is_file():
                values[item.relative_to(path).as_posix()] = hashlib.sha256(item.read_bytes()).hexdigest()
        return values

    def test_materialize_rebases_and_keeps_prepared_source_held(self):
        before = self._tree_hash(self.prepared)
        result = generation_materialization.materialize(
            self.prepared, self.destination, expected_operation_id=self.operation_id)
        self.assertTrue(result['materialized'])
        self.assertFalse(result['activation_ready'])
        self.assertEqual(result['operation_id'], self.operation_id)
        self.assertEqual(generation_materialization.verify_materialized(
            self.destination, expected_operation_id=self.operation_id), result)
        # Activation's target profile may already hold data/service/enrollment
        # locks after its first verification; the retry must ignore only those
        # declared lock paths, never arbitrary instance.lock files.
        with installation_update.backup._locked_profile(result['profile'], allow_restored=True):
            self.assertEqual(generation_materialization.verify_materialized(
                self.destination, expected_operation_id=self.operation_id), result)
        data = self.destination / 'data'
        owner = json.loads((data / 'installation-owner.json').read_text(encoding='utf-8'))
        station = json.loads((data / 'laserstation/laser-installation.json').read_text(encoding='utf-8'))
        self.assertEqual(owner['dataRoot'], str(data))
        self.assertEqual(station['plates'], str(data / 'Laserplader'))
        self.assertEqual(station['ca_file'], str(data / 'security/ca.crt'))
        for hold in (self.destination / '.restore-hold.json', data / '.restore-hold.json',
                     data / 'hub/.restore-hold.json', data / 'printvaerksted/.restore-hold.json',
                     data / 'designvaerksted/.restore-hold.json'):
            self.assertTrue(hold.exists(), hold)
        self.assertEqual(before, self._tree_hash(self.prepared))

    def test_tampered_materialization_is_rejected(self):
        generation_materialization.materialize(
            self.prepared, self.destination, expected_operation_id=self.operation_id)
        target = self.destination / 'data' / 'installation-owner.json'
        target.write_bytes(target.read_bytes() + b' tampered')
        with self.assertRaises(ValueError):
            generation_materialization.verify_materialized(
                self.destination, expected_operation_id=self.operation_id)

    def test_wrong_final_profile_is_rejected(self):
        generation_materialization.materialize(
            self.prepared, self.destination, expected_operation_id=self.operation_id)
        metadata_path = self.destination / generation_materialization.MANIFEST
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        metadata['profile']['roots']['server']['path'] = str(self.update.data.server)
        generation_materialization._write_json(metadata_path, metadata)
        with self.assertRaises(ValueError):
            generation_materialization.verify_materialized(
                self.destination, expected_operation_id=self.operation_id)

    def test_resealed_different_program_is_rejected_against_prepared_release(self):
        generation_materialization.materialize(
            self.prepared, self.destination, expected_operation_id=self.operation_id)
        other = self.root / 'other-program'
        shutil.copytree(self.update.program, other)
        manifest_path = other / 'makerspace-release.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['release'] = 'different-release'
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        shutil.rmtree(self.destination / 'program')
        shutil.copytree(other, self.destination / 'program')
        metadata_path = self.destination / generation_materialization.MANIFEST
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        metadata['manifest_sha256'] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        metadata['files'], metadata['directories'] = generation_materialization._inventory(self.destination)
        generation_materialization._write_json(metadata_path, metadata)
        with self.assertRaises(ValueError):
            generation_materialization.verify_materialized(
                self.destination, expected_operation_id=self.operation_id)

    def test_prepared_lock_is_held_during_copy(self):
        seen = []
        original = generation_materialization._copy_tree

        def guarded(source, target):
            try:
                from app.instance import InstanceLock
                InstanceLock(self.prepared)
            except RuntimeError:
                seen.append(True)
            else:
                raise AssertionError('prepared lock was not held')
            return original(source, target)

        with patch.object(generation_materialization, '_copy_tree', side_effect=guarded):
            generation_materialization.materialize(
                self.prepared, self.destination, expected_operation_id=self.operation_id)
        self.assertEqual(seen, [True])

    def test_partial_destination_is_never_reused(self):
        self.destination.mkdir()
        (self.destination / 'partial').write_text('unfinished', encoding='utf-8')
        with self.assertRaises((FileExistsError, ValueError)):
            generation_materialization.materialize(
                self.prepared, self.destination, expected_operation_id=self.operation_id)
        self.assertEqual((self.destination / 'partial').read_text(encoding='utf-8'), 'unfinished')


if __name__ == '__main__':
    unittest.main()
