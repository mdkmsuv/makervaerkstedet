import os
from contextlib import closing
import gc
import hashlib
import json
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[2]),
                str(Path(__file__).resolve().parents[1] / 'projekter')]
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

    def legacy_cadoodle_record(self, folder, names):
        """An archive record written by an earlier version (kind and source type cadoodle)."""
        folder.mkdir(parents=True)
        entries = []
        for name, data in names.items():
            path = folder / name
            path.write_bytes(data)
            stat = path.stat()
            entries.append({'relative': path.relative_to(self.projects).as_posix(),
                            'stat': [stat.st_mtime_ns, stat.st_size], 'digest': hashlib.sha256(data).hexdigest()})
        project = uuid.uuid4().hex
        self.store.save(b'PK old snapshot', project=project, name='Model', folder='3D', kind='cadoodle',
                        filename='model.zip', expected=0, source='Projekter-mappen')
        scanner = Scanner(self.projects, self.store)
        first = next(iter(names))
        scanner.saved[(folder / first).relative_to(self.projects).as_posix()] = {
            'stat': 'legacy', 'id': project, 'revision': 1,
            'source': {'type': 'cadoodle', 'relative': entries[0]['relative'],
                       'directory': folder.relative_to(self.projects).as_posix(), 'files': entries}}
        with self.store.connect() as db:
            db.execute('UPDATE projects SET modified=? WHERE id=?', (time.time() - RETENTION_SECONDS, project))
        return scanner, project

    def test_old_cadoodle_record_expires_after_retention_with_unchanged_originals(self):
        folder = self.projects / '3D-print' / 'model'
        scanner, project = self.legacy_cadoodle_record(folder, {'model.doodle': b'{}', 'base.stl': b'base'})
        self.assertEqual(self.store.list()[0]['kind'], 'cadoodle')
        self.assertEqual(scanner.cleanup(now=time.time())['deleted'], [project])
        self.assertFalse((folder / 'model.doodle').exists())
        self.assertFalse((folder / 'base.stl').exists())
        self.assertEqual(self.store.list(), [])

    def test_old_cadoodle_record_with_removed_originals_still_expires(self):
        folder = self.projects / '3D-print' / 'model'
        scanner, project = self.legacy_cadoodle_record(folder, {'model.doodle': b'{}'})
        (folder / 'model.doodle').unlink()
        self.assertEqual(scanner.cleanup(now=time.time())['deleted'], [project])

    def test_old_cadoodle_new_dependency_blocks_cleanup(self):
        folder = self.projects / '3D-print' / 'model'
        scanner, project = self.legacy_cadoodle_record(folder, {'model.doodle': b'{}', 'base.stl': b'base'})
        (folder / 'new.stl').write_bytes(b'new')
        self.assertEqual(scanner.cleanup(now=time.time())['deleted'], [])
        self.assertTrue((folder / 'new.stl').exists())
        self.assertEqual(self.store.meta(project)['kind'], 'cadoodle')

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

    def test_partial_old_cadoodle_deletion_retries_after_restart(self):
        folder = self.projects / '3D' / 'model'
        scanner, project = self.legacy_cadoodle_record(folder, {'model.doodle': b'{}', 'base.stl': b'base'})
        now = time.time()
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


class ExistingArchiveAdoption(unittest.TestCase):
    """Synthetic schema observed on the unbundled generation; no school data."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        with closing(sqlite3.connect(self.root / 'projekter.sqlite3')) as db, db:
            db.execute('''CREATE TABLE projects (
                id TEXT PRIMARY KEY,name TEXT NOT NULL,folder TEXT NOT NULL,
                kind TEXT NOT NULL,filename TEXT NOT NULL,revision INTEGER NOT NULL,
                modified REAL NOT NULL,archived INTEGER NOT NULL DEFAULT 0,
                protected INTEGER NOT NULL DEFAULT 0,deleted_at REAL)''')
            db.execute('CREATE TABLE settings (key TEXT PRIMARY KEY,value INTEGER NOT NULL)')
            db.execute("INSERT INTO settings VALUES ('retention_days',90)")
        self.params = dict(project='a'*32,name='Synthetic',folder='QA',kind='andet',
                           filename='note.txt',expected=0)

    def db_change(self, sql, values=()):
        with closing(sqlite3.connect(self.root / 'projekter.sqlite3')) as db, db:
            db.execute(sql, values)

    def test_save_new_and_existing_projects_keeps_extra_columns_and_policy(self):
        store = Projektarkiv(self.root)
        store.save(b'old', **self.params)
        self.db_change('UPDATE projects SET protected=1')
        saved = store.save(b'new', **{**self.params,'expected':1})
        self.assertTrue(saved['protected'])
        self.assertIsNone(saved['expires_at'])
        self.assertEqual(saved['retention_days'],90)
        self.assertEqual(store.read(self.params['project'],1)[1],b'old')
        self.assertEqual(store.read(self.params['project'])[1],b'new')

    def test_persisted_policy_and_protection_control_cleanup(self):
        store = Projektarkiv(self.root)
        store.save(b'old', **self.params)
        now = time.time()
        self.db_change('UPDATE projects SET modified=?',(now-60*86400,))
        self.assertEqual(store.cleanup(now=now)['deleted'],[])
        self.db_change('UPDATE projects SET protected=1,modified=?',(now-100*86400,))
        self.assertEqual(store.cleanup(now=now)['deleted'],[])
        self.db_change('UPDATE projects SET protected=0')
        self.assertEqual(store.cleanup(now=now)['deleted'],[self.params['project']])

    def test_deleted_project_stays_hidden_and_cannot_be_saved_back_into_list(self):
        store = Projektarkiv(self.root)
        store.save(b'old', **self.params)
        self.db_change('UPDATE projects SET deleted_at=123')
        self.assertEqual(store.list(),[])
        with self.assertRaises(ValueError):
            store.save(b'old', **{**self.params,'expected':1})
        self.assertEqual(store.meta(self.params['project'])['deleted_at'],123)
        self.assertEqual(store.read(self.params['project'])[1],b'old')

    def test_existing_policy_is_reread_and_invalid_policy_does_not_delete(self):
        store = Projektarkiv(self.root)
        store.save(b'old', **self.params)
        now = time.time()
        self.db_change('UPDATE projects SET modified=?',(now-60*86400,))
        self.db_change("UPDATE settings SET value=120 WHERE key='retention_days'")
        self.assertEqual(store.meta(self.params['project'])['retention_days'],120)
        self.db_change("UPDATE settings SET value=0 WHERE key='retention_days'")
        with self.assertRaises(ValueError):store.cleanup(now=now)
        with closing(sqlite3.connect(self.root/'projekter.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM projects').fetchone()[0],1)

    def test_missing_existing_policy_has_no_silent_30_day_fallback(self):
        self.db_change('DROP TABLE settings')
        with self.assertRaises(ValueError):Projektarkiv(self.root)


if __name__ == '__main__':
    unittest.main()
