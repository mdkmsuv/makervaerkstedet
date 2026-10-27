import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from app import installation_update
from app.instance import InstanceLock


class InstallationUpdateTests(unittest.TestCase):
    @staticmethod
    def _version(path):
        import sqlite3
        db = sqlite3.connect(path)
        try:
            return db.execute('PRAGMA user_version').fetchone()[0]
        finally:
            db.close()

    def setUp(self):
        from tests.test_installation_backup import InstallationBackupTests
        from tests.test_release_compatibility import ReleaseCompatibilityTests
        self.data = InstallationBackupTests()
        self.data.setUp()
        self.addCleanup(self.data.tearDown)
        self.source_builder = ReleaseCompatibilityTests()
        self.source_builder.setUp()
        self.addCleanup(self.source_builder.tearDown)
        self.program = self.source_builder._source_cohort()
        self.destination = Path(self.source_builder.temp.name) / 'prepared'

    def test_prepare_verify_ready_and_preserve_originals(self):
        watched = [self.data.hub / 'makerspace.sqlite3', self.data.print / 'queue.sqlite3',
                   self.data.laser / 'afleveringer.sqlite3', self.data.server / 'konfiguration.json',
                   self.data.print / 'makerspace-bridge.key']
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in watched}
        result = installation_update.prepare(self.data.profile, self.program, self.destination)
        self.assertEqual(result['format'], 1)
        self.assertEqual(result['state'], 'ready')
        self.assertFalse(result['activation_ready'])
        self.assertEqual(result['credits'], installation_update.CREDIT)
        self.assertTrue((self.destination / '.restore-hold.json').exists())
        self.assertTrue((self.destination / 'candidate-data' / '.restore-hold.json').exists())
        self.assertEqual(result, installation_update.verify_prepared(self.destination))
        self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in watched})
        InstanceLock(self.destination).close()

    def test_copy_program_preserves_empty_embedded_runtime_directories(self):
        # Embedded Python uses this directory through its ._pth file even
        # when no third-party package is installed yet.  It is deliberately
        # absent from the file manifest, so the copy routine must preserve the
        # verified directory inventory separately from manifest files.
        empty = self.program / 'runtime' / 'Lib' / 'site-packages'
        empty.mkdir(parents=True)
        release = installation_update.inspect_program(self.program)
        copied = self.destination / 'program-copy'
        self.destination.mkdir()
        installation_update._copy_program(
            self.program, copied, release['manifest_sha256'])
        self.assertTrue((copied / 'runtime' / 'Lib' / 'site-packages').is_dir())

    def test_verify_rejects_tampered_backup_program_candidate_or_report(self):
        installation_update.prepare(self.data.profile, self.program, self.destination)
        cases = [self.destination / 'backup' / 'backup-manifest.json',
                 self.destination / 'program' / 'makerspace-release.json',
                 self.destination / 'candidate-data' / 'restore-report.json',
                 self.destination / 'prepared-manifest.json']
        for target in cases:
            original = target.read_bytes()
            try:
                target.write_bytes(original + b' tampered')
                with self.assertRaises(ValueError, msg=str(target)):
                    installation_update.verify_prepared(self.destination)
            finally:
                target.write_bytes(original)

    def test_existing_destination_live_lock_and_role_mismatch_are_refused(self):
        self.destination.mkdir()
        with self.assertRaises((ValueError, FileExistsError)):
            installation_update.prepare(self.data.profile, self.program, self.destination)
        self.destination.rmdir()
        lock = InstanceLock(self.data.server)
        try:
            with self.assertRaises(RuntimeError):
                installation_update.prepare(self.data.profile, self.program, self.destination)
        finally:
            lock.close()
        bad = dict(self.data.profile)
        bad['components'] = dict(self.data.profile['components'], laser=None)
        with self.assertRaises(ValueError):
            installation_update.prepare(bad, self.program, self.destination)

    def test_migration_failure_leaves_failed_hold_and_resume_uses_completed_copy(self):
        calls = {'count': 0}
        original = installation_update.migrate_database

        def fail_once(path, role):
            calls['count'] += 1
            if calls['count'] == 1:
                raise RuntimeError('migration fault')
            return original(path, role)

        with patch.object(installation_update, 'migrate_database', side_effect=fail_once):
            with self.assertRaisesRegex(RuntimeError, 'migration fault'):
                installation_update.prepare(self.data.profile, self.program, self.destination)
        journal = json.loads((self.destination / installation_update.JOURNAL).read_text(encoding='utf-8'))
        self.assertEqual(journal['state'], 'failed')
        self.assertTrue((self.destination / '.restore-hold.json').exists())
        self.assertFalse((self.destination / 'prepared-manifest.json').exists())
        self.assertTrue((self.destination / 'backup').exists())
        # Resume must use the completed isolated inputs, even if the live
        # source description is no longer usable.
        self.data.queue.close()
        removed_source = self.data.server.with_name('removed-live-source')
        temp_root = Path(self.data.temp.name).resolve()
        self.assertTrue(self.data.server.resolve().is_relative_to(temp_root))
        self.assertTrue(removed_source.resolve().is_relative_to(temp_root))
        self.data.server.rename(removed_source)
        self.assertFalse(self.data.server.exists())
        result = installation_update.resume_preparation(self.destination)
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(result, installation_update.resume_preparation(self.destination))

    def test_prepare_accepts_legacy_zero_print_and_laser_schema_versions(self):
        self.data.queue.db.execute('PRAGMA user_version=0')
        self.data.queue.db.commit()
        with self.data.inbox.db() as db:
            db.execute('PRAGMA user_version=0')
        result = installation_update.prepare(self.data.profile, self.program, self.destination)
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(result['migrations']['print'], {'role': 'print', 'from_version': 0,
                                                         'to_version': 1, 'changed': True})
        self.assertEqual(result['migrations']['laser'], {'role': 'laser', 'from_version': 0,
                                                         'to_version': 1, 'changed': True})
        for relative in ('printvaerksted/queue.sqlite3', 'designvaerksted/afleveringer.sqlite3'):
            self.assertEqual(self._version(self.data.server / relative), 0)
            self.assertEqual(self._version(self.destination / 'backup' / 'roots' / 'server' / relative), 0)
            self.assertEqual(self._version(self.destination / 'candidate-data' / 'roots' / 'server' / relative), 1)

    def test_resume_preserves_partial_legacy_migration_outcome(self):
        self.data.queue.db.execute('PRAGMA user_version=0')
        self.data.queue.db.commit()
        with self.data.inbox.db() as db:
            db.execute('PRAGMA user_version=0')
        original = installation_update.migrate_database
        changed = {'seen': False}
        def fail_after_first_legacy(path, role):
            if changed['seen'] and role in ('print', 'laser'):
                raise RuntimeError('second legacy migration fault')
            result = original(path, role)
            if result['changed'] and not changed['seen']:
                changed['seen'] = True
                return result
            return result
        with patch.object(installation_update, 'migrate_database', side_effect=fail_after_first_legacy):
            with self.assertRaisesRegex(RuntimeError, 'second legacy migration fault'):
                installation_update.prepare(self.data.profile, self.program, self.destination)
        versions = {role: self._version(self.destination / 'candidate-data' / 'roots' / 'server' / relative)
                    for role, relative in (('print', 'printvaerksted/queue.sqlite3'),
                                           ('laser', 'designvaerksted/afleveringer.sqlite3'))}
        self.assertEqual(sorted(versions.values()), [0, 1])
        result = installation_update.resume_preparation(self.destination)
        self.assertTrue(all(result['migrations'][role]['to_version'] == 1 for role in ('print', 'laser')))
        self.assertEqual([result['migrations'][role]['from_version'] for role in ('print', 'laser')], [0, 0])
        self.assertTrue(all(result['migrations'][role]['changed'] for role in ('print', 'laser')))

    def test_incomplete_restore_is_never_resumed_or_marked_ready(self):
        marker = self.destination / 'candidate-data' / 'partial-restore.marker'
        def partial_restore(_source, destination):
            destination.mkdir(parents=True, exist_ok=True)
            marker.write_text('partial', encoding='utf-8')
            raise RuntimeError('restore fault')
        with patch.object(installation_update.backup, 'restore', side_effect=partial_restore):
            with self.assertRaises(RuntimeError):
                installation_update.prepare(self.data.profile, self.program, self.destination)
        self.assertEqual(marker.read_text(encoding='utf-8'), 'partial')
        with self.assertRaises((OSError, ValueError)):
            installation_update.resume_preparation(self.destination)
        self.assertEqual(marker.read_text(encoding='utf-8'), 'partial')
        journal = json.loads((self.destination / installation_update.JOURNAL).read_text(encoding='utf-8'))
        self.assertNotEqual(journal['state'], 'ready')
        self.assertFalse((self.destination / 'prepared-manifest.json').exists())

    def test_resume_rejects_tampered_restored_file_before_seal(self):
        with patch.object(installation_update, 'migrate_database', side_effect=RuntimeError('migration fault')):
            with self.assertRaises(RuntimeError):
                installation_update.prepare(self.data.profile, self.program, self.destination)
        target = self.destination / 'candidate-data' / 'roots' / 'server' / 'security' / 'server.crt'
        target.write_bytes(target.read_bytes() + b' tampered')
        with self.assertRaises((OSError, ValueError)):
            installation_update.resume_preparation(self.destination)
        self.assertFalse((self.destination / 'prepared-manifest.json').exists())

    def test_resume_rejects_tampered_restored_job_database_before_seal(self):
        import sqlite3
        with patch.object(installation_update, 'migrate_database', side_effect=RuntimeError('migration fault')):
            with self.assertRaises(RuntimeError):
                installation_update.prepare(self.data.profile, self.program, self.destination)
        database = self.destination / 'candidate-data' / 'roots' / 'server' / 'printvaerksted' / 'queue.sqlite3'
        connection = sqlite3.connect(database)
        try:
            connection.execute("UPDATE jobs SET status='tampered' WHERE id=?", (self.data.job['id'],))
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises((OSError, ValueError)):
            installation_update.resume_preparation(self.destination)
        self.assertFalse((self.destination / 'prepared-manifest.json').exists())


if __name__ == '__main__':
    unittest.main()
