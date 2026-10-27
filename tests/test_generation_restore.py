"""A deliberate historical restore preserves the still-current data generation."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from app import generation_activation as activation
from app import generation_materialization as materialization
from app import generation_transaction as transaction
from app import installation_backup as backup
from app import installation_update as update
from app.generation_lock import GenerationLock
from app.generation_selection import ACTIVE, LOCK, TRANSITION, SelectionLease
from app.instance import InstanceLock
from tests import test_generation_transaction as fixture


class OfflineHost:
    def __init__(self, case):
        self.case = case

    def check(self, runtime, source, target):
        with self.case.assertRaises(RuntimeError), GenerationLock(Path(runtime) / 'control' / LOCK):
            pass
        for record in (source, target):
            for name in ('', 'hub', 'printvaerksted', 'designvaerksted'):
                with self.case.assertRaises(RuntimeError), InstanceLock(Path(record['data_root']) / name):
                    pass


@unittest.skipUnless(sys.platform == 'win32' and fixture.install_fixture.ARCHIVE.is_file(),
                     'requires cached embedded Windows runtime')
class GenerationRestoreTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixture.GenerationTransactionTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.root = self.fx.install.root
        self.saved = self.root / 'historical-backup'
        self.old_file = self.fx.data.server / 'old-project.txt'
        self.old_file.write_text('Saved before accidental removal', encoding='utf-8')
        backup.snapshot(self.fx.data.profile, self.saved)
        self.old_file.unlink()
        self.new_file = self.fx.data.server / 'new-project.txt'
        self.new_file.write_text('New work retained in previous generation', encoding='utf-8')
        self.final = self.root / 'restored-generation'
        self.host = OfflineHost(self)

    def prepare(self):
        return transaction.restore(self.fx.runtime, self.saved, self.fx.next,
                                   self.fx.target, self.fx.install.installation_id)

    def materialize(self, operation):
        return materialization.materialize(self.fx.target / transaction.PREPARED, self.final,
                                          expected_operation_id=operation['operation_id'])

    def activate(self):
        return activation.activate(self.fx.runtime, self.fx.target, self.final, host=self.host)

    def test_restore_activates_old_data_preserves_newer_original_and_keys(self):
        saved_hash = backup._hash(self.saved / backup.MANIFEST)
        operation = self.prepare()
        self.assertEqual(operation['restore_of'], saved_hash)
        self.assertEqual((self.fx.control / ACTIVE).read_bytes(), self.fx.original)
        self.materialize(operation)
        result = self.activate()
        self.assertEqual(result['state'], 'active')
        self.assertEqual(result['restore_of'], saved_hash)
        target = self.final / 'data'
        self.assertEqual((target / self.old_file.name).read_text(encoding='utf-8'),
                         'Saved before accidental removal')
        self.assertFalse((target / self.new_file.name).exists())
        self.assertTrue(self.new_file.is_file())
        self.assertFalse(self.old_file.exists())
        for name in ('security/server.key', 'security/ca.crt', 'printvaerksted/makerspace-bridge.key'):
            self.assertEqual((target / name).read_bytes(), (self.fx.data.server / name).read_bytes())
        self.assertFalse(list(self.final.rglob(backup.HOLD_FILE)))
        self.assertEqual(saved_hash, backup._hash(self.saved / backup.MANIFEST))
        # Idempotent retry must preserve work created after reopening.
        (target / 'after.txt').write_text('After activation', encoding='utf-8')
        with SelectionLease(self.fx.runtime), InstanceLock(target / 'hub'):
            self.assertEqual(self.activate(), result)
        self.assertEqual((target / 'after.txt').read_text(encoding='utf-8'), 'After activation')

    def test_foreign_school_or_ca_refused_before_destination(self):
        owner_path = self.fx.data.server / 'installation-owner.json'
        original = owner_path.read_bytes()
        owner = json.loads(original)
        owner['ownerId'] = 'b' * 32
        owner_path.write_text(json.dumps(owner), encoding='utf-8')
        foreign = self.root / 'foreign'
        backup.snapshot(self.fx.data.profile, foreign)
        owner_path.write_bytes(original)
        with self.assertRaisesRegex(ValueError, 'anden skole'):
            transaction.restore(self.fx.runtime, foreign, self.fx.next,
                                self.fx.target, self.fx.install.installation_id)
        self.assertFalse(self.fx.target.exists())
        ca = self.fx.data.server / 'security/ca.crt'
        ca.write_bytes(b'new-identity')
        with self.assertRaisesRegex(ValueError, 'CA-identitet'):
            self.prepare()
        self.assertFalse(self.fx.target.exists())
        self.assertFalse((self.fx.control / TRANSITION).exists())

    def test_live_writer_refused_before_destination(self):
        with InstanceLock(self.fx.data.hub), self.assertRaises(RuntimeError):
            self.prepare()
        self.assertFalse(self.fx.target.exists())

    def test_interruption_resumes_using_preserved_backup_without_original_medium(self):
        original = update._phase
        def fail(target, journal, phase):
            if phase == 'migrate':
                raise RuntimeError('interrupted')
            return original(target, journal, phase)
        with patch.object(update, '_phase', side_effect=fail), self.assertRaisesRegex(RuntimeError, 'interrupted'):
            self.prepare()
        self.saved.rename(self.root / 'detached-medium')
        operation = transaction.resume(self.fx.runtime, self.fx.target)
        self.materialize(operation)
        self.assertEqual(self.activate()['state'], 'active')

    def test_new_source_data_refuses_resume_but_cancel_keeps_it(self):
        self.prepare()
        newer = self.fx.data.server / 'unexpected-new-work.txt'
        newer.write_text('Preserve', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Kildedata er ændret'):
            transaction.resume(self.fx.runtime, self.fx.target)
        transaction.cancel(self.fx.runtime, self.fx.target)
        self.assertEqual(newer.read_text(encoding='utf-8'), 'Preserve')
        self.assertEqual((self.fx.control / ACTIVE).read_bytes(), self.fx.original)

    def test_interruption_after_selector_publication_resumes_same_restore(self):
        operation = self.prepare()
        self.materialize(operation)
        original = activation._save_state
        def fail(folder, plan, phase, recovery_selection=None):
            if phase == 'releasing':
                raise RuntimeError('interrupted after publication')
            return original(folder, plan, phase, recovery_selection)
        with patch.object(activation, '_save_state', side_effect=fail), self.assertRaisesRegex(RuntimeError, 'publication'):
            self.activate()
        self.assertTrue((self.fx.control / TRANSITION).exists())
        with self.assertRaises(RuntimeError), SelectionLease(self.fx.runtime):
            pass
        self.assertEqual(self.activate()['state'], 'active')
        self.assertTrue(self.new_file.is_file())
        self.assertTrue((self.final / 'data/old-project.txt').is_file())


if __name__ == '__main__':
    unittest.main()
