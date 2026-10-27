import os
import gc
import json
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'projekter'))
from projektarkiv import Projektarkiv, RETENTION_SECONDS  # noqa: E402
from scanner import Scanner  # noqa: E402


class ProjectRetention(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.projects = Path(self.temp.name) / 'Projekter'
        self.projects.mkdir()
        self.store = Projektarkiv(self.projects / '.elevhub')
        self.project = uuid.uuid4().hex
        self.params = dict(project=self.project, name='Model', folder='5A', kind='laser',
                           filename='model.svg', expected=0)

    def tearDown(self):
        gc.collect()
        self.temp.cleanup()

    def expire(self, age, now):
        self.store.save(b'model', **self.params)
        db = sqlite3.connect(self.store.root / 'projekter.sqlite3')
        try:
            db.execute('UPDATE projects SET modified=? WHERE id=?', (now - age, self.project))
            db.commit()
        finally:
            db.close()

    def test_29_days_survives_and_30_and_31_days_expire(self):
        now = 2_000_000_000
        self.expire(29 * 86400, now)
        self.assertEqual(self.store.cleanup(now=now)['deleted'], [])
        self.assertEqual(self.store.meta(self.project)['retention_days'], 30)

        self.store = Projektarkiv(Path(self.temp.name) / 'other' / '.elevhub')
        self.project = uuid.uuid4().hex
        self.params['project'] = self.project
        for age in (30 * 86400, 31 * 86400):
            self.store.save(b'model', **self.params)
            db = sqlite3.connect(self.store.root / 'projekter.sqlite3')
            try:
                db.execute('UPDATE projects SET modified=? WHERE id=?', (now - age, self.project))
                db.commit()
            finally:
                db.close()
            self.assertEqual(len(self.store.cleanup(now=now)['deleted']), 1)
            self.project = uuid.uuid4().hex
            self.params['project'] = self.project

    def test_new_save_activity_refreshes_expiry(self):
        now = time.time() + 1
        self.store.save(b'old', **self.params)
        db = sqlite3.connect(self.store.root / 'projekter.sqlite3')
        try:
            db.execute('UPDATE projects SET modified=? WHERE id=?', (now - RETENTION_SECONDS, self.project))
            db.commit()
        finally:
            db.close()
        self.params['expected'] = 1
        self.store.save(b'new', **self.params)
        self.assertEqual(self.store.cleanup(now=now)['deleted'], [])
        self.assertGreater(self.store.meta(self.project)['expires_at'], now)

    def test_scanner_removes_verified_original_and_does_not_reimport_it(self):
        original = self.projects / 'Laser' / 'model.svg'
        original.parent.mkdir()
        original.write_bytes(b'<svg/>')
        scanner = Scanner(self.projects, self.store)
        scanner.scan()
        scanner.scan()
        scanner.scan()
        project = self.store.list()[0]['id']
        now = time.time()
        db = sqlite3.connect(self.store.root / 'projekter.sqlite3')
        try:
            db.execute('UPDATE projects SET modified=? WHERE id=?', (now - RETENTION_SECONDS, project))
            db.commit()
        finally:
            db.close()
        result = scanner.cleanup(now=now)
        self.assertEqual(result['deleted'], [project])
        self.assertFalse(original.exists())
        self.assertNotIn(project, [record['id'] for record in scanner.saved.values()])
        scanner.scan()
        scanner.scan()
        self.assertEqual(scanner.store.list(), [])

    def test_user_deleted_original_is_safe_to_expire_after_complete_scan(self):
        original = self.projects / 'Laser' / 'model.svg'
        original.parent.mkdir()
        original.write_bytes(b'<svg/>')
        scanner = Scanner(self.projects, self.store)
        scanner.scan()
        scanner.scan()
        scanner.scan()
        project = self.store.list()[0]['id']
        original.unlink()
        scanner.scan()
        now = time.time()
        db = sqlite3.connect(self.store.root / 'projekter.sqlite3')
        try:
            db.execute('UPDATE projects SET modified=? WHERE id=?', (now - RETENTION_SECONDS, project))
            db.commit()
        finally:
            db.close()
        self.assertEqual(scanner.cleanup(now=now)['deleted'], [project])

    def test_changed_original_keeps_archive_until_saved_or_removed(self):
        original = self.projects / 'Laser' / 'model.svg'
        original.parent.mkdir()
        original.write_bytes(b'<svg/>')
        scanner = Scanner(self.projects, self.store)
        scanner.scan()
        scanner.scan()
        scanner.scan()
        project = self.store.list()[0]['id']
        original.write_bytes(b'<svg>changed</svg>')
        scanner.scan()
        now = time.time()
        db = sqlite3.connect(self.store.root / 'projekter.sqlite3')
        try:
            db.execute('UPDATE projects SET modified=? WHERE id=?', (now - RETENTION_SECONDS, project))
            db.commit()
        finally:
            db.close()
        self.assertEqual(scanner.cleanup(now=now)['deleted'], [])
        self.assertEqual(self.store.meta(project)['revision'], 1)

    def test_old_index_entry_migrates_source_after_stable_scan(self):
        original = self.projects / 'Laser' / 'model.svg'
        original.parent.mkdir()
        original.write_bytes(b'<svg/>')
        scanner = Scanner(self.projects, self.store)
        scanner.scan()
        scanner.scan()
        scanner.scan()
        index = json.loads(scanner.index.read_text(encoding='utf-8'))
        for entry in index.values():
            entry.pop('source', None)
        scanner.index.write_text(json.dumps(index), encoding='utf-8')
        restarted = Scanner(self.projects, self.store)
        restarted.scan()
        restarted.scan()
        migrated = json.loads(restarted.index.read_text(encoding='utf-8'))
        self.assertTrue(next(iter(migrated.values())).get('source'))

    def test_pending_cleanup_refuses_changed_original(self):
        original = self.projects / 'Laser' / 'model.svg'
        original.parent.mkdir()
        original.write_bytes(b'<svg/>')
        scanner = Scanner(self.projects, self.store)
        scanner.scan()
        scanner.scan()
        scanner.scan()
        project = self.store.list()[0]['id']
        now = time.time()
        db = sqlite3.connect(self.store.root / 'projekter.sqlite3')
        try:
            db.execute('UPDATE projects SET modified=? WHERE id=?', (now - RETENTION_SECONDS, project))
            db.commit()
        finally:
            db.close()
        with patch.object(Path, 'unlink', side_effect=OSError('busy')):
            self.assertEqual(scanner.cleanup(now=now)['deleted'], [])
        original.write_bytes(b'<svg>changed</svg>')
        self.assertEqual(scanner.cleanup(now=now)['deleted'], [])
        self.assertEqual(self.store.meta(project)['revision'], 1)

    def test_cadoodle_new_dependency_blocks_cleanup(self):
        folder = self.projects / '3D-print' / 'model'
        folder.mkdir(parents=True)
        (folder / 'model.doodle').write_text('{"projectName":"Model"}', encoding='utf-8')
        (folder / 'base.stl').write_bytes(b'base')
        scanner = Scanner(self.projects, self.store)
        scanner.scan()
        scanner.scan()
        scanner.scan()
        project = self.store.list()[0]['id']
        source = scanner.saved[next(key for key, value in scanner.saved.items() if value['id'] == project)]['source']
        (folder / 'new.stl').write_bytes(b'new')
        now = time.time()
        db = sqlite3.connect(self.store.root / 'projekter.sqlite3')
        try:
            db.execute('UPDATE projects SET modified=? WHERE id=?', (now - RETENTION_SECONDS, project))
            db.commit()
        finally:
            db.close()
        result = self.store.cleanup({project: source}, projects_root=self.projects, now=now)
        self.assertEqual(result['deleted'], [])
        self.assertTrue((folder / 'new.stl').exists())

    @unittest.skipUnless(os.name == 'posix', 'symlinks are required for this safety test')
    def test_symlink_original_and_path_escape_are_never_deleted(self):
        outside = Path(self.temp.name) / 'outside.svg'
        outside.write_bytes(b'keep')
        original = self.projects / 'Laser' / 'model.svg'
        original.parent.mkdir()
        original.symlink_to(outside)
        self.store.save(b'model', **self.params)
        now = time.time()
        db = sqlite3.connect(self.store.root / 'projekter.sqlite3')
        try:
            db.execute('UPDATE projects SET modified=? WHERE id=?', (now - RETENTION_SECONDS, self.project))
            db.commit()
        finally:
            db.close()
        record = {'type': 'file', 'relative': 'Laser/model.svg', 'stat': [0, 5],
                  'digest': 'not-used'}
        result = self.store.cleanup({self.project: record}, projects_root=self.projects, now=now)
        self.assertEqual(result['deleted'], [self.project])
        self.assertTrue(original.is_symlink())
        self.assertEqual(outside.read_bytes(), b'keep')

        self.project = uuid.uuid4().hex
        self.params['project'] = self.project
        self.store.save(b'escape', **self.params)
        db = sqlite3.connect(self.store.root / 'projekter.sqlite3')
        try:
            db.execute('UPDATE projects SET modified=? WHERE id=?', (now - RETENTION_SECONDS, self.project))
            db.commit()
        finally:
            db.close()
        escaped = {'type': 'file', 'relative': '../outside.svg', 'stat': [outside.stat().st_mtime_ns, 4],
                   'digest': 'not-used'}
        self.store.cleanup({self.project: escaped}, projects_root=self.projects, now=now)
        self.assertEqual(outside.read_bytes(), b'keep')

    def test_failed_deletion_is_reported_and_retried(self):
        now = time.time()
        self.store.save(b'model', **self.params)
        db = sqlite3.connect(self.store.root / 'projekter.sqlite3')
        try:
            db.execute('UPDATE projects SET modified=? WHERE id=?', (now - RETENTION_SECONDS, self.project))
            db.commit()
        finally:
            db.close()
        blob = self.store.files / self.project / '1.bin'
        with patch.object(Path, 'unlink', side_effect=OSError('busy')):
            result = self.store.cleanup(now=now)
        self.assertEqual(result['deleted'], [])
        self.assertEqual(self.store.meta(self.project)['revision'], 1)
        self.assertTrue(blob.exists())
        self.assertEqual(self.store.cleanup(now=now)['deleted'], [self.project])

    def test_failure_after_original_unlink_is_retried_from_pending_record(self):
        original = self.projects / 'Laser' / 'model.svg'
        original.parent.mkdir()
        original.write_bytes(b'<svg/>')
        scanner = Scanner(self.projects, self.store)
        scanner.scan()
        scanner.scan()
        scanner.scan()
        project = self.store.list()[0]['id']
        now = time.time()
        db = sqlite3.connect(self.store.root / 'projekter.sqlite3')
        try:
            db.execute('UPDATE projects SET modified=? WHERE id=?', (now - RETENTION_SECONDS, project))
            db.commit()
        finally:
            db.close()
        original_unlink = Path.unlink
        calls = {'count': 0}

        def fail_on_second(path):
            calls['count'] += 1
            if calls['count'] == 2:
                raise OSError('busy')
            return original_unlink(path)

        with patch.object(Path, 'unlink', fail_on_second):
            result = scanner.cleanup(now=now)
        self.assertEqual(result['deleted'], [])
        self.assertFalse(original.exists())
        self.assertEqual(self.store.cleanup(projects_root=self.projects, now=now)['deleted'], [project])

    def test_partial_cadoodle_deletion_retries_after_restart(self):
        folder = self.projects / '3D' / 'model'
        folder.mkdir(parents=True)
        (folder/'model.doodle').write_text('{"projectName":"Model"}', encoding='utf-8')
        (folder/'base.stl').write_bytes(b'base')
        scanner = Scanner(self.projects, self.store)
        scanner.scan()
        scanner.scan()
        project = self.store.list()[0]['id']
        now = time.time()
        with self.store.connect() as db:
            db.execute('UPDATE projects SET modified=? WHERE id=?', (now-RETENTION_SECONDS, project))
        real_unlink = Path.unlink
        calls = 0
        def fail_second(path):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('busy')
            real_unlink(path)
        with patch.object(Path, 'unlink', fail_second):
            self.assertEqual(scanner.cleanup(now=now)['deleted'], [])
        restarted = Projektarkiv(self.store.root)
        result = restarted.cleanup(projects_root=self.projects, now=now)
        self.assertEqual(result['deleted'], [project])
        self.assertFalse((folder/'model.doodle').exists())
        self.assertFalse((folder/'base.stl').exists())

    def test_saved_edit_clears_interrupted_expiry(self):
        self.store.save(b'old', **self.params)
        with self.store.connect() as db:
            db.execute('INSERT INTO retention_pending VALUES (?, ?)', (self.project, '{}'))
        self.store.save(b'new', **{**self.params, 'expected': 1})
        with self.store.connect() as db:
            self.assertIsNone(db.execute('SELECT * FROM retention_pending WHERE project=?', (self.project,)).fetchone())

    def test_new_import_of_old_file_gets_full_retention_period(self):
        original = self.projects/'old.svg'
        original.write_bytes(b'<svg/>')
        os.utime(original, (time.time()-100*86400, time.time()-100*86400))
        scanner = Scanner(self.projects, self.store)
        scanner.scan()
        scanner.scan()
        self.assertEqual(scanner.cleanup()['deleted'], [])
        self.assertGreater(self.store.list()[0]['expires_at'], time.time()+29*86400)


if __name__ == '__main__':
    unittest.main()
