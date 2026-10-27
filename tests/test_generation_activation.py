"""Real prepared program/data pairs; only the read-only host boundary is faked."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

from app import generation_activation as activation
from app import generation_materialization as materialization
from app import generation_transaction as preparation
from app import installation_backup as backup
from app import installation_update as update
from app.generation_lock import GenerationLock
from app.generation_selection import ACTIVE, LOCK, TRANSITION, SelectionLease
from app.instance import InstanceLock
from tests import test_generation_transaction as fixture


class OfflineHost:
    def __init__(self, case):
        self.case = case
        self.calls = 0
        self.denied = False

    def check(self, runtime, source, target):
        self.calls += 1
        if self.denied:
            raise ValueError('host denied')
        # Check the contract at the boundary rather than merely mocking locks.
        with self.case.assertRaises(RuntimeError):
            with GenerationLock(Path(runtime) / 'control' / LOCK, exclusive=False):
                pass
        for record in (source, target):
            for relative in ('', 'hub', 'printvaerksted', 'designvaerksted', 'Laserplader'):
                with self.case.assertRaises(RuntimeError):
                    with InstanceLock(Path(record['data_root']) / relative):
                        pass


@unittest.skipUnless(sys.platform == 'win32' and fixture.install_fixture.ARCHIVE.is_file(),
                     'requires the cached Windows embedded runtime')
class GenerationActivationTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixture.GenerationTransactionTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        plates = self.fx.data.server / 'Laserplader'
        plates.mkdir()
        (plates / 'backup-protocol.json').write_text(
            json.dumps({'protocol': 1, 'role': 'laser_station'}), encoding='utf-8')
        station = self.fx.data.server / 'laserstation/laser-installation.json'
        station.parent.mkdir()
        station.write_text(json.dumps({'plates': str(plates), 'port': 8790}), encoding='utf-8')
        self.prepared = self.fx.prepare()
        self.final = self.fx.install.root / 'final-generation'
        self.material = materialization.materialize(
            self.fx.target / preparation.PREPARED, self.final,
            expected_operation_id=self.prepared['operation_id'])
        self.host = OfflineHost(self)
        self.selector = self.fx.control / ACTIVE
        self.barrier = self.fx.control / TRANSITION

    def activate(self):
        return activation.activate(self.fx.runtime, self.fx.target, self.final, host=self.host)

    def state(self):
        return preparation._json(self.fx.target / activation.STATE)

    def selected(self):
        return json.loads(self.selector.read_bytes())

    def hold_crash(self, phase):
        original = activation._save_state

        def fail(folder, plan, next_phase, recovery_selection=None):
            if next_phase == phase:
                raise RuntimeError('power loss')
            return original(folder, plan, next_phase, recovery_selection)

        with patch.object(activation, '_save_state', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'power loss'):
                self.activate()

    def test_commit_preserves_original_pair_and_allows_live_idempotent_retry(self):
        with backup.offline_profile(self.fx.data.profile) as locked:
            source_before = preparation._fingerprint(locked)
        seal = backup._hash(self.fx.target / preparation.PREPARED / update.SEAL)
        result = self.activate()
        self.assertEqual(result['state'], 'active')
        self.assertFalse(result['services_started'])
        self.assertFalse(self.barrier.exists())
        selected = self.selected()
        self.assertEqual(selected['revision'], json.loads(self.fx.original)['revision'] + 1)
        self.assertEqual(selected['data_root'], str(self.final / 'data'))
        self.assertEqual(selected['program_root'], str(self.final / 'program'))
        self.assertEqual(list(self.final.rglob(backup.HOLD_FILE)), [])
        self.assertTrue((self.fx.target / preparation.PREPARED / backup.HOLD_FILE).exists())
        self.assertEqual(seal, backup._hash(self.fx.target / preparation.PREPARED / update.SEAL))
        with backup.offline_profile(self.fx.data.profile) as locked:
            self.assertEqual(source_before, preparation._fingerprint(locked))
        with closing(sqlite3.connect(self.final / 'data/printvaerksted/queue.sqlite3')) as db:
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0], 'true')
        actual = self.fx.inspect()
        self.assertEqual(actual.returncode, 0, actual.stderr)
        self.assertEqual(json.loads(actual.stdout)['data_root'], str(self.final / 'data'))
        newer = self.final / 'data/new-project.txt'
        newer.write_text('Work after opening', encoding='utf-8')
        with SelectionLease(self.fx.runtime), InstanceLock(self.final / 'data/hub'):
            self.assertEqual(self.activate(), result)
        self.assertEqual(newer.read_text(encoding='utf-8'), 'Work after opening')
        with self.assertRaisesRegex(ValueError, 'anden aktivering'):
            activation.activate(self.fx.runtime, self.fx.target, self.fx.next, host=self.host)
        with self.assertRaisesRegex(ValueError, 'ny datakopi'):
            activation.abort_unpublished(self.fx.runtime, self.fx.target, host=self.host)

    def test_denied_host_preserves_prepare_barrier_and_can_retry(self):
        before = self.barrier.read_bytes()
        self.host.denied = True
        with self.assertRaisesRegex(ValueError, 'host denied'):
            self.activate()
        self.assertEqual(self.selector.read_bytes(), self.fx.original)
        self.assertEqual(self.barrier.read_bytes(), before)
        self.assertTrue((self.final / backup.HOLD_FILE).exists())
        self.host.denied = False
        self.assertEqual(self.activate()['state'], 'active')

    def test_crash_after_pointer_publication_resumes_exact_target(self):
        self.hold_crash('releasing')
        self.assertEqual(self.state()['phase'], 'publishing')
        self.assertEqual(self.selected()['data_root'], str(self.final / 'data'))
        self.assertTrue(self.barrier.exists())
        self.assertNotEqual(self.fx.inspect().returncode, 0)
        self.assertEqual(activation.activate(self.fx.runtime, self.fx.target, host=self.host)['state'], 'active')

    def test_partial_hold_release_and_finished_phase_resume(self):
        self.hold_crash('releasing')
        original = Path.unlink
        count = 0

        def fail(path, *args, **kwargs):
            nonlocal count
            if path.name == backup.HOLD_FILE:
                count += 1
                if count == 2:
                    raise OSError('power loss during hold release')
            return original(path, *args, **kwargs)

        with patch.object(Path, 'unlink', fail):
            with self.assertRaisesRegex(OSError, 'power loss during hold release'):
                self.activate()
        self.assertEqual(self.state()['phase'], 'releasing')
        self.assertTrue(self.barrier.exists())

        def fail_barrier(path, *args, **kwargs):
            if path == self.barrier:
                raise OSError('power loss before opening')
            return original(path, *args, **kwargs)

        with patch.object(Path, 'unlink', fail_barrier):
            with self.assertRaisesRegex(OSError, 'power loss before opening'):
                self.activate()
        self.assertEqual(self.state()['phase'], 'active')
        self.assertTrue(self.barrier.exists())
        self.assertEqual(self.activate()['state'], 'active')

    def test_abort_before_publication_keeps_original_revision(self):
        self.hold_crash('publishing')
        self.assertEqual(self.state()['phase'], 'staged')
        result = activation.abort_unpublished(self.fx.runtime, self.fx.target, host=self.host)
        self.assertEqual(result['state'], 'aborted')
        self.assertEqual(self.selector.read_bytes(), self.fx.original)
        self.assertTrue((self.final / backup.HOLD_FILE).exists())
        self.assertEqual(result, activation.abort_unpublished(self.fx.runtime, self.fx.target, host=self.host))

    def test_abort_after_publication_restores_original_roots_with_new_revision(self):
        self.hold_crash('active')
        self.assertEqual(list(self.final.rglob(backup.HOLD_FILE)), [])
        original_save = activation._save_state

        def fail(folder, plan, phase, recovery_selection=None):
            if phase == 'aborted':
                raise RuntimeError('cancel journal saved before crash')
            return original_save(folder, plan, phase, recovery_selection)

        with patch.object(activation, '_save_state', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'cancel journal saved before crash'):
                activation.abort_unpublished(self.fx.runtime, self.fx.target, host=self.host)
        self.assertTrue(self.barrier.exists())
        result = activation.abort_unpublished(self.fx.runtime, self.fx.target, host=self.host)
        original = json.loads(self.fx.original)
        self.assertEqual(self.selected(), dict(original, revision=original['revision'] + 2))
        self.assertEqual(result['state'], 'aborted')
        self.assertFalse(self.barrier.exists())
        self.assertTrue((self.final / backup.HOLD_FILE).exists())
        self.assertTrue((self.final / 'data/hub' / backup.HOLD_FILE).exists())
        self.assertEqual(self.fx.inspect().returncode, 0)
        self.assertEqual(result, activation.abort_unpublished(self.fx.runtime, self.fx.target, host=self.host))

    def test_plan_and_receipt_creation_interruptions_can_resume(self):
        self.hold_crash('staged')
        self.assertTrue((self.fx.target / activation.PLAN).exists())
        self.assertFalse((self.fx.target / activation.STATE).exists())
        atomic = preparation._atomic

        def fail(path, value):
            if path == self.barrier and value.get('operation') == 'activate_update':
                raise RuntimeError('receipt saved before crash')
            return atomic(path, value)

        with patch.object(preparation, '_atomic', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'receipt saved before crash'):
                self.activate()
        self.assertEqual(self.selector.read_bytes(), self.fx.original)
        self.assertEqual(preparation._json(self.barrier)['operation'], 'prepare_update')
        self.assertEqual(self.activate()['state'], 'active')

    def test_tampered_plan_and_target_refuse_resume_and_abort(self):
        self.hold_crash('releasing')
        selected = self.selector.read_bytes()
        plan_path = self.fx.target / activation.PLAN
        original = plan_path.read_bytes()
        plan_path.write_bytes(original + b' ')
        with self.assertRaisesRegex(ValueError, 'kvittering'):
            self.activate()
        plan_path.write_bytes(original)
        changed = self.final / 'data/unexpected-project.txt'
        changed.write_text('new data must never be lost', encoding='utf-8')
        for action in (self.activate, lambda: activation.abort_unpublished(self.fx.runtime, self.fx.target, host=self.host)):
            with self.assertRaisesRegex(ValueError, 'datakopi er ændret'):
                action()
        self.assertEqual(self.selector.read_bytes(), selected)
        self.assertTrue(self.barrier.exists())
        self.assertTrue(changed.exists())

    def test_foreign_pointer_or_committed_source_wal_keeps_barrier(self):
        self.hold_crash('releasing')
        own_pointer = self.selector.read_bytes()
        foreign = self.selected()
        foreign['revision'] += 100
        self.selector.write_bytes(activation._bytes(foreign))
        with self.assertRaisesRegex(ValueError, 'anden operation'):
            self.activate()
        self.selector.write_bytes(own_pointer)
        db_path = self.fx.data.print / 'queue.sqlite3'
        before = db_path.read_bytes()
        self.fx.data.queue.db.execute("UPDATE settings SET value='true' WHERE key='paused'")
        self.fx.data.queue.db.commit()
        self.assertEqual(before, db_path.read_bytes())
        with self.assertRaisesRegex(ValueError, 'oprindelige data er ændret'):
            self.activate()
        # An editable maintenance journal must not authorize a newer source
        # fingerprint: the protected activation receipt binds the old one.
        with backup.offline_profile(self.fx.data.profile) as locked:
            forged = preparation._fingerprint(locked)
        preparation._atomic(self.fx.target / preparation.FINGERPRINT, forged)
        journal_path = self.fx.target / preparation.JOURNAL
        journal = preparation._json(journal_path)
        journal['source_data_sha256'] = backup._hash(self.fx.target / preparation.FINGERPRINT)
        preparation._atomic(journal_path, journal)
        with self.assertRaisesRegex(ValueError, 'anden klargøring'):
            self.activate()
        self.assertTrue(self.barrier.exists())

    def test_live_generation_or_data_lease_blocks_before_pointer_change(self):
        for factory in (lambda: GenerationLock(self.fx.control / LOCK, exclusive=False),
                        lambda: InstanceLock(self.final / 'data/hub')):
            with factory():
                with self.assertRaises(RuntimeError):
                    self.activate()
            self.assertEqual(self.selector.read_bytes(), self.fx.original)
            self.assertTrue(self.barrier.exists())
        self.assertEqual(self.activate()['state'], 'active')


if __name__ == '__main__':
    unittest.main()
