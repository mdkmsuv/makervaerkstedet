"""Exercise the operator facade on real temporary school databases."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import unittest
from unittest.mock import patch

from app import school_backup
from app.instance import InstanceLock
from app.generation_runtime import build_active_command
from tests import test_installation_backup as data_fixture
from tests import test_school_admin as embedded


class SchoolBackupTests(unittest.TestCase):
    def setUp(self):
        self.fx = data_fixture.InstallationBackupTests()
        self.fx.setUp()
        self.addCleanup(self.fx.tearDown)
        self.root = Path(self.fx.temp.name)
        self.runtime = self.root / 'installed'; self.runtime.mkdir()
        self.owner_path = self.fx.server / 'installation-owner.json'
        self.owner = {'schemaVersion': 1, 'product': 'ElevhubServer', 'state': 'installed',
                      'ownerId': '01234567-89ab-cdef-0123-456789abcdef', 'taskName': 'ElevhubServer',
                      'dataRoot': str(self.fx.server), 'installRoot': str(self.runtime),
                      'externalBackends': []}
        self.save_owner()
        self.private = patch('app.installation_backup._private_directory')
        self.private.start(); self.addCleanup(self.private.stop)

    def save_owner(self):
        self.owner_path.write_text(json.dumps(self.owner), encoding='utf-8')

    def create(self, destination):
        return school_backup.execute('create', data_root=self.fx.server,
                                     runtime_root=self.runtime, destination=destination)

    def test_operator_create_verify_restore_requires_no_manual_json_profile(self):
        backup = self.root / 'Backup med æøå'
        result = self.create(backup)
        self.assertTrue(result['verified'])
        self.assertEqual(set(result['result']['components']), {'hub', 'print', 'laser'})
        self.assertTrue(school_backup.execute('verify', source=backup)['verified'])
        restored = self.root / 'Gendannet skole'
        result = school_backup.execute('restore', source=backup, destination=restored)
        self.assertTrue(result['isolated'])
        self.assertFalse(result['activation_ready'])
        self.assertTrue((restored / '.restore-hold.json').is_file())
        self.assertEqual((restored / 'roots/server/security/server.key').read_bytes(),
                         (self.fx.server / 'security/server.key').read_bytes())

    def test_live_service_refuses_before_backup_destination(self):
        target = self.root / 'backup'
        with InstanceLock(self.fx.server):
            with self.assertRaises(RuntimeError):
                self.create(target)
        self.assertFalse(target.exists())

    def test_owner_external_backend_or_partial_generation_refuse_before_destination(self):
        for changes in ({'state': 'rolledBack'}, {'ownerId': 'missing'},
                        {'dataRoot': str(self.runtime)}, {'externalBackends': ['print']},
                        {'startupProtocol': 'generation-v1'}):
            with self.subTest(changes=changes):
                original = dict(self.owner); self.owner.update(changes); self.save_owner()
                target = self.root / 'rejected'
                with self.assertRaises(ValueError):
                    self.create(target)
                self.assertFalse(target.exists())
                self.owner = original; self.save_owner()
        (self.runtime / 'control').mkdir()
        with self.assertRaises(ValueError):
            self.create(self.root / 'partial-generation')

    def test_missing_queue_cannot_silently_be_excluded(self):
        self.fx.queue.close(); self.fx.queue = None
        (self.fx.print / 'queue.sqlite3').unlink()
        destination = self.root / 'incomplete'
        with self.assertRaises(OSError):
            self.create(destination)
        self.assertFalse(destination.exists())

    def test_exact_operation_arguments_and_offline_verify_restore(self):
        backup = self.root / 'backup'; self.create(backup)
        with self.assertRaises(ValueError):
            school_backup.execute('verify', source=backup, destination=self.root / 'unused')
        with self.assertRaises(ValueError):
            school_backup.execute('create', data_root=self.fx.server, runtime_root=self.runtime,
                                 source=backup, destination=self.root / 'unused')
        self.owner_path.unlink()
        self.assertTrue(school_backup.execute('verify', source=backup)['verified'])
        self.assertTrue(school_backup.execute('restore', source=backup,
                        destination=self.root / 'recovered')['isolated'])


@unittest.skipUnless(os.name == 'nt' and embedded.embedded.ARCHIVE.is_file(),
                     'requires the cached embedded Windows runtime')
class SchoolBackupActiveTests(unittest.TestCase):
    def test_real_selected_runtime_creates_and_verifies_backup_under_shared_selection(self):
        code = embedded.SchoolAdminActiveTests(); code.setUp()
        self.addCleanup(code.doCleanups)
        for name in ('school_backup.py', 'installation_backup.py', 'backup_guard.py',
                     'backup_databases.py', 'data_contract.py', 'instance.py', 'generation_relocation.py'):
            shutil.copy2(embedded.embedded.ROOT / 'app' / name, code.package / 'app' / name)
        code._copy_admin_and_reseal()
        code.record['manifest_sha256'] = code.fixture.manifest_sha256
        data = data_fixture.InstallationBackupTests(); data.setUp()
        self.addCleanup(data.tearDown)
        code.data = data.server
        code.record['data_root'] = str(data.server)
        code._write_selection()
        owner = {'schemaVersion': 1, 'product': 'ElevhubServer', 'state': 'installed',
                 'ownerId': 'a' * 32, 'taskName': 'ElevhubServer', 'startupProtocol': 'generation-v1',
                 'dataRoot': str(data.server), 'installRoot': str(code.stable), 'externalBackends': []}
        (data.server / 'installation-owner.json').write_text(json.dumps(owner), encoding='utf-8')
        destination = code.root / 'backup'
        for action, arguments in (('backup-create', ['--destination', str(destination)]),
                                  ('backup-verify', ['--source', str(destination)])):
            command = build_active_command(code.stable, 'school-admin', [action, *arguments])
            result = subprocess.run(command, cwd=code.root, capture_output=True,
                                    text=True, timeout=40)
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertTrue(value['ok'])
            self.assertTrue(value['verified'])
        self.assertEqual(json.loads((code.control / 'active-generation.json').read_text()), code.record)


if __name__ == '__main__':
    unittest.main()
