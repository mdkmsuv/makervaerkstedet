"""Offline generation preparation against real data and an embedded runtime."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

from app import generation_transaction as transaction, installation_update as update
from app.generation_lock import GenerationLock
from app.generation_selection import ACTIVE, LOCK, TRANSITION, SelectionLease
from app.instance import InstanceLock
from app.release_compatibility import inspect_program
from tests import test_generation_install as install_fixture
from tests import test_installation_backup as data_fixture


@unittest.skipUnless(sys.platform == 'win32' and install_fixture.ARCHIVE.is_file(),
                     'requires the cached Windows embedded runtime')
class GenerationTransactionTests(unittest.TestCase):
    def setUp(self):
        self.install = install_fixture.GenerationInstallTests()
        self.install.setUp()
        self.addCleanup(self.install.doCleanups)
        self.data = data_fixture.InstallationBackupTests()
        self.data.setUp()
        self.addCleanup(self.data.tearDown)
        self.install.data = self.data.server
        (self.data.server / 'instance.lock').write_bytes(b'1')
        self.install._write_owner()
        self.install._initialize()
        self.install._write_owner(state='installed')
        self.runtime = self.install.stable
        self.control = self.runtime / 'control'
        self.target = self.install.root / 'maintenance'
        self.next = self.install.root / 'next-program'
        shutil.copytree(self.install.program, self.next)
        manifest = self.next / 'makerspace-release.json'
        value = json.loads(manifest.read_text(encoding='utf-8'))
        value['release'] = 'next-fixture'
        manifest.write_text(json.dumps(value, ensure_ascii=True, sort_keys=True), encoding='utf-8')
        self.original = (self.control / ACTIVE).read_bytes()

    def prepare(self):
        return transaction.prepare(self.runtime, self.next, self.target, self.install.installation_id)

    def inspect(self):
        return subprocess.run([str(self.runtime / 'runtime/python.exe'), '-B', '-I',
            str(self.runtime / 'app/generation_runtime.py'), '--runtime-root', str(self.runtime),
            '--active', '--expected-installation-id', self.install.installation_id,
            '--entry', 'school-admin', '--', 'inspect'], cwd=self.runtime,
            capture_output=True, text=True, timeout=35)

    def test_prepare_preserves_pair_blocks_start_and_cancel_reopens_original(self):
        watched = (self.data.server / 'installation-owner.json', self.data.server / 'konfiguration.json',
                   self.data.print / 'queue.sqlite3', self.data.laser / 'afleveringer.sqlite3',
                   self.data.print / 'makerspace-bridge.key')
        before = {path: path.read_bytes() for path in watched}
        result = self.prepare()
        self.assertEqual(result['state'], 'prepared')
        self.assertTrue(result['maintenance'])
        self.assertFalse(result['activation_ready'])
        self.assertEqual(result, transaction.resume(self.runtime, self.target))
        self.assertEqual((self.control / ACTIVE).read_bytes(), self.original)
        self.assertEqual((self.target / transaction.SOURCE).read_bytes(), self.original)
        old = inspect_program(self.target / transaction.PREVIOUS)
        self.assertEqual(old['manifest_sha256'], self.install.manifest_sha256)
        self.assertEqual(before, {path: path.read_bytes() for path in watched})
        candidate = self.target / transaction.PREPARED / update.CANDIDATE / 'roots/server'
        self.assertTrue((candidate / '.restore-hold.json').is_file())
        owner = json.loads((candidate / 'installation-owner.json').read_text(encoding='utf-8'))
        self.assertEqual(Path(owner['dataRoot']), candidate)
        blocked = self.inspect()
        self.assertNotEqual(blocked.returncode, 0)
        cancelled = transaction.cancel(self.runtime, self.target)
        self.assertFalse(cancelled['maintenance'])
        self.assertEqual(cancelled['operation_id'], result['operation_id'])
        self.assertEqual(cancelled, transaction.cancel(self.runtime, self.target))
        self.assertFalse((self.control / TRANSITION).exists())
        self.assertTrue((candidate / '.restore-hold.json').exists())
        self.assertEqual((self.control / ACTIVE).read_bytes(), self.original)
        reopened = self.inspect()
        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        self.assertEqual(Path(json.loads(reopened.stdout)['data_root']), self.data.server)

    def test_source_changes_refuse_resume_but_cancel_preserves_newer_data(self):
        with patch.object(update, '_prepare_locked', side_effect=RuntimeError('interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                self.prepare()
        newer = self.data.server / 'new-pupil-file.txt'
        newer.write_text('Saved after maintenance preparation', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Kildedata er ændret'):
            transaction.resume(self.runtime, self.target)
        self.assertTrue((self.control / TRANSITION).exists())
        transaction.cancel(self.runtime, self.target)
        self.assertEqual(newer.read_text(encoding='utf-8'), 'Saved after maintenance preparation')
        self.assertEqual((self.control / ACTIVE).read_bytes(), self.original)

    def test_committed_wal_change_is_detected_without_main_database_change(self):
        with patch.object(update, '_prepare_locked', side_effect=RuntimeError('interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                self.prepare()
        db = self.data.print / 'queue.sqlite3'
        before = db.read_bytes()
        self.data.queue.db.execute("UPDATE settings SET value='true' WHERE key='paused'")
        self.data.queue.db.commit()
        self.assertEqual(db.read_bytes(), before, 'fixture must write to WAL, not checkpoint')
        with self.assertRaisesRegex(ValueError, 'Kildedata er ændret'):
            transaction.resume(self.runtime, self.target)
        transaction.cancel(self.runtime, self.target)
        self.assertEqual(self.data.queue.db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0], 'true')

    def test_changed_selection_cannot_cancel_someone_elses_operation(self):
        with patch.object(update, '_prepare_locked', side_effect=RuntimeError('interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                self.prepare()
        selected = json.loads(self.original)
        selected['revision'] += 1
        (self.control / ACTIVE).write_text(json.dumps(selected), encoding='utf-8')
        marker = (self.control / TRANSITION).read_bytes()
        for action in (transaction.resume, transaction.cancel):
            with self.assertRaisesRegex(ValueError, 'generationsvalg er ændret'):
                action(self.runtime, self.target)
        self.assertEqual((self.control / TRANSITION).read_bytes(), marker)

    def test_migration_interruption_resumes_same_snapshot_under_source_locks(self):
        original_copy = update._copy_program
        checked = []
        def checked_copy(*args):
            with self.assertRaises(RuntimeError):
                GenerationLock(self.control / LOCK)
            with self.assertRaises(RuntimeError):
                InstanceLock(self.data.server)
            checked.append(args[1])
            return original_copy(*args)
        with patch.object(update, '_copy_program', side_effect=checked_copy), \
             patch.object(update, 'migrate_database', side_effect=RuntimeError('migration interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'migration interrupted'):
                self.prepare()
        self.assertEqual(len(checked), 2)
        journal = json.loads((self.target / transaction.JOURNAL).read_text(encoding='utf-8'))
        backup = self.target / transaction.PREPARED / update.BACKUP / 'backup-manifest.json'
        preserved = backup.read_bytes()
        nested_path = self.target / transaction.PREPARED / update.JOURNAL
        nested = json.loads(nested_path.read_text(encoding='utf-8'))
        self.assertEqual(nested['operation_id'], journal['operation_id'])
        nested_path.write_text(json.dumps(dict(nested, operation_id='f' * 32)), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'anden operation'):
            transaction.resume(self.runtime, self.target)
        nested_path.write_text(json.dumps(nested), encoding='utf-8')
        result = transaction.resume(self.runtime, self.target)
        self.assertEqual(result['state'], 'prepared')
        self.assertEqual(result['operation_id'], journal['operation_id'])
        self.assertEqual(backup.read_bytes(), preserved)
        self.assertEqual((self.control / ACTIVE).read_bytes(), self.original)
        # A crash after recording checksums must not let resume bless different
        # inputs by overwriting the already-recorded expected checksum.
        journal_path = self.target / transaction.JOURNAL
        verified = json.loads(journal_path.read_text(encoding='utf-8'))
        verified.update(state='preparing', phase='verify', backup_manifest_sha256='0' * 64)
        journal_path.write_text(json.dumps(verified), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'tidligere verificerede'):
            transaction.resume(self.runtime, self.target)
        self.assertEqual(json.loads(journal_path.read_text(encoding='utf-8'))['backup_manifest_sha256'], '0' * 64)

    def test_live_generation_or_service_refuses_before_destination_or_barrier(self):
        for lease in (lambda: SelectionLease(self.runtime), lambda: InstanceLock(self.data.server)):
            with lease():
                with self.assertRaises(RuntimeError):
                    self.prepare()
            self.assertFalse(self.target.exists())
            self.assertFalse((self.control / TRANSITION).exists())
        self.install._write_owner(state='installing')
        with self.assertRaisesRegex(ValueError, 'færdig'):
            self.prepare()
        self.assertFalse(self.target.exists())
        self.install._write_owner(state='installed', ownerId=None)
        with self.assertRaisesRegex(ValueError, 'færdig'):
            self.prepare()
        self.install._write_owner(state='installed')
        selected = json.loads(self.original)
        selected.update(program_root=str(self.next), manifest_sha256=inspect_program(self.next)['manifest_sha256'])
        (self.control / ACTIVE).write_text(json.dumps(selected), encoding='utf-8')
        nested_destination = self.next / 'nested-operation'
        with self.assertRaisesRegex(ValueError, 'overlappe'):
            transaction.prepare(self.runtime, self.install.program, nested_destination, self.install.installation_id)
        self.assertFalse(nested_destination.exists())

    def test_partial_program_is_preserved_and_cancellation_is_available(self):
        def partial_copy(source, destination, expected):
            destination.mkdir()
            (destination / 'partial.txt').write_text('incomplete', encoding='ascii')
            raise RuntimeError('copy interrupted')
        with patch.object(update, '_copy_program', side_effect=partial_copy):
            with self.assertRaisesRegex(RuntimeError, 'copy interrupted'):
                self.prepare()
        with self.assertRaises((ValueError, OSError)):
            transaction.resume(self.runtime, self.target)
        transaction.cancel(self.runtime, self.target)
        self.assertEqual((self.target / transaction.PREVIOUS / 'partial.txt').read_text(), 'incomplete')
        self.assertEqual((self.control / ACTIVE).read_bytes(), self.original)

    def test_unpublished_barrier_can_be_resumed_from_untouched_journal(self):
        atomic = transaction._atomic
        def interrupt_barrier(path, value):
            if path == self.control / TRANSITION:
                raise RuntimeError('before barrier publish')
            return atomic(path, value)
        with patch.object(transaction, '_atomic', side_effect=interrupt_barrier):
            with self.assertRaisesRegex(RuntimeError, 'before barrier publish'):
                self.prepare()
        self.assertFalse((self.control / TRANSITION).exists())
        with SelectionLease(self.runtime):
            pass
        result = transaction.resume(self.runtime, self.target)
        self.assertEqual(result['state'], 'prepared')
        self.assertTrue((self.control / TRANSITION).exists())

    def test_cancel_recovers_interruption_after_cancelled_journal_write(self):
        with patch.object(update, '_prepare_locked', side_effect=RuntimeError('interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                self.prepare()
        unlink = Path.unlink
        def interrupted_unlink(path, *args, **kwargs):
            if path == self.control / TRANSITION:
                raise OSError('before barrier removal')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', interrupted_unlink):
            with self.assertRaisesRegex(OSError, 'before barrier removal'):
                transaction.cancel(self.runtime, self.target)
        self.assertTrue((self.control / TRANSITION).exists())
        result = transaction.cancel(self.runtime, self.target)
        self.assertEqual(result['state'], 'cancelled')
        self.assertFalse((self.control / TRANSITION).exists())

    def test_malformed_journal_and_foreign_barrier_are_not_repaired(self):
        with patch.object(update, '_prepare_locked', side_effect=RuntimeError('interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                self.prepare()
        journal_path = self.target / transaction.JOURNAL
        journal = json.loads(journal_path.read_text(encoding='utf-8'))
        cases = ({'state': 'prepared'}, {'next_program': None}, {'source_data_sha256': '0' * 64},
                 {'next_program': str(self.install.program),
                  'next_manifest_sha256': self.install.manifest_sha256})
        for changed in cases:
            with self.subTest(changed=changed):
                journal_path.write_text(json.dumps(dict(journal, **changed)), encoding='utf-8')
                with self.assertRaises(ValueError):
                    transaction.resume(self.runtime, self.target)
        journal_path.write_text(json.dumps(journal), encoding='utf-8')
        (self.control / TRANSITION).write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'anden operation'):
            transaction.cancel(self.runtime, self.target)
        self.assertEqual((self.control / TRANSITION).read_text(), '{}')


if __name__ == '__main__':
    unittest.main()
