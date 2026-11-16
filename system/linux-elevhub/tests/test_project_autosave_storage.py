"""Real local working-copy storage; synthetic bytes, no school services."""
import http.client
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'projekter'))
from projektarkiv import Projektarkiv, Konflikt
from lokal import make_server


class AutosaveStorage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Projektarkiv(Path(self.temp.name) / 'archive')
        self.project = 'a' * 32
        self.params = dict(project=self.project, name='Synthetic project', folder='QA',
                           kind='andet', filename='fixture.txt')

    def save(self, data, source='browser', expected=None):
        if expected is None:
            try:
                expected = self.store.meta(self.project)['revision']
            except KeyError:
                expected = 0
        return self.store.save(data, **self.params, expected=expected, source=source)

    def test_unchanged_autosave_and_manual_checkpoint_do_not_extend_30_day_expiry(self):
        start, day = 2_000_000_000, 86400
        with patch('projektarkiv.time.time', return_value=start):
            first = self.save(b'unchanged local work', 'autosave')
        with patch('projektarkiv.time.time', return_value=start + 29 * day):
            self.save(b'unchanged local work', 'autosave')
            checkpoint = self.save(b'unchanged local work', 'browser')
        self.assertEqual(checkpoint['modified'], first['modified'])
        self.assertEqual(self.store.cleanup(now=start + 30 * day)['deleted'], [self.project])
        self.assertEqual(list(self.store.files.glob('*/*.bin')), [])

    def test_changed_autosave_restarts_local_30_day_expiry_and_all_versions_expire_together(self):
        start, day = 2_000_000_000, 86400
        with patch('projektarkiv.time.time', return_value=start):
            self.save(b'manual project')
        with patch('projektarkiv.time.time', return_value=start + 29 * day):
            changed = self.save(b'continued local work', 'autosave')
        self.assertEqual(changed['modified'], start + 29 * day)
        self.assertEqual(self.store.cleanup(now=start + 30 * day)['deleted'], [])
        self.assertEqual(self.store.read(self.project)[1], b'continued local work')
        self.assertEqual(self.store.cleanup(now=start + 59 * day)['deleted'], [self.project])
        self.assertEqual(list(self.store.files.glob('*/*.bin')), [])

    def test_one_hundred_autosaves_keep_latest_working_copy_and_all_manual_checkpoints(self):
        manuals = {1: b'manual start'}
        self.save(manuals[1])
        for index in range(1, 101):
            data = ('working copy %03d' % index).encode()
            meta = self.save(data, 'autosave')
            self.assertEqual(meta['revision'], index + 1, 'revision never reuses a pruned number')
            if index in (25, 75):
                checkpoint = self.save(data)
                self.assertEqual(checkpoint['revision'], meta['revision'])
                manuals[meta['revision']] = data
        latest = self.store.meta(self.project)
        self.assertEqual([(v['revision'], v['source']) for v in latest['versions']],
                         [(101, 'autosave'), (76, 'browser'), (26, 'browser'), (1, 'browser')])
        self.assertEqual({p.name for p in (self.store.files / self.project).iterdir()},
                         {'1.bin', '26.bin', '76.bin', '101.bin'})
        reopened = Projektarkiv(self.store.root)
        for revision, data in manuals.items():
            self.assertEqual(reopened.read(self.project, revision)[1], data)
        self.assertEqual(reopened.read(self.project)[1], b'working copy 100')
        with self.assertRaises(Konflikt):
            self.save(b'stale editor data', 'autosave', expected=100)
        with self.assertRaises(KeyError):
            reopened.read(self.project, 100)

    def test_manual_dedup_promotes_autosave_and_future_autosaves_preserve_it(self):
        self.save(b'first autosave', 'autosave')
        manual = self.save(b'first autosave')
        self.assertEqual(manual['revision'], 1)
        self.assertEqual(manual['versions'][0]['source'], 'browser')
        self.assertEqual(list((self.store.files / self.project).glob('*.bin')),
                         [self.store.files / self.project / '1.bin'])
        self.save(b'next autosave', 'autosave')
        self.save(b'latest autosave', 'autosave')
        self.assertEqual(self.store.read(self.project, 1)[1], b'first autosave')
        self.assertEqual([v['revision'] for v in self.store.meta(self.project)['versions']], [3, 1])

    def test_distinct_manual_save_preserves_existing_versions(self):
        self.save(b'working', 'autosave')
        self.save(b'manual changed bytes')
        self.save(b'next working', 'autosave')
        self.assertEqual([v['revision'] for v in self.store.meta(self.project)['versions']], [3, 2, 1])
        self.assertEqual(self.store.read(self.project, 1)[1], b'working')
        self.assertEqual(self.store.read(self.project, 2)[1], b'manual changed bytes')

    def test_failed_write_and_failed_commit_keep_previous_working_copy(self):
        self.save(b'previous working copy', 'autosave')
        with patch('projektarkiv.os.replace', side_effect=OSError('synthetic write interruption')):
            with self.assertRaises(OSError):
                self.save(b'write never completed', 'autosave')
        self.assertEqual(self.store.read(self.project)[1], b'previous working copy')
        real_connect = sqlite3.connect

        class FailedCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError('synthetic interrupted commit')

        with patch('projektarkiv.sqlite3.connect', side_effect=lambda *a, **kw: real_connect(*a, factory=FailedCommit, **kw)):
            with self.assertRaises(sqlite3.OperationalError):
                self.save(b'commit never completed', 'autosave', expected=1)
        self.assertEqual(self.store.meta(self.project)['revision'], 1)
        self.assertEqual(self.store.read(self.project)[1], b'previous working copy')
        self.assertTrue((self.store.files / self.project / '1.bin').is_file())
        # Retrying after a failed commit replaces only its unpublished orphan.
        self.assertEqual(self.save(b'retry completed', 'autosave')['revision'], 2)
        self.assertEqual(self.store.read(self.project)[1], b'retry completed')
        self.assertFalse((self.store.files / self.project / '1.bin').exists())

    def test_failed_first_save_does_not_publish_a_project(self):
        with patch('projektarkiv.os.replace', side_effect=OSError('synthetic first write failure')):
            with self.assertRaises(OSError):
                self.save(b'first bytes', 'autosave')
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.save(b'first bytes', 'autosave')['revision'], 1)

    def test_only_exact_previous_autosave_file_is_removed_after_commit(self):
        self.save(b'old', 'autosave')
        old = self.store.files / self.project / '1.bin'
        unknown = old.parent / 'teacher-note.bin'
        unknown.write_bytes(b'leave this file')
        other = 'b' * 32
        self.store.save(b'other project', **{**self.params, 'project':other}, expected=0, source='autosave')
        original_unlink = Path.unlink
        removed = []

        def unlink(path, *args, **kwargs):
            if path == old:
                self.assertEqual(self.store.meta(self.project)['revision'], 2, 'commit precedes removal')
                self.assertEqual(self.store.read(self.project)[1], b'new')
                self.assertTrue(old.is_file())
                removed.append(path)
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, 'unlink', unlink):
            self.save(b'new', 'autosave')
        self.assertEqual(removed, [old])
        self.assertEqual(unknown.read_bytes(), b'leave this file')
        self.assertEqual(self.store.read(other)[1], b'other project')

    def test_quota_failure_keeps_previous_working_copy(self):
        self.save(b'old', 'autosave')
        self.store.max_total = 3
        with self.assertRaises(OSError):
            self.save(b'new', 'autosave')
        self.assertEqual(self.store.read(self.project)[1], b'old')

    def test_unlink_failure_after_commit_keeps_current_copy_and_old_orphan(self):
        self.save(b'old working copy', 'autosave')
        old = self.store.files / self.project / '1.bin'
        original_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if path == old:
                raise OSError('synthetic unlink interruption')
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, 'unlink', unlink):
            with self.assertRaises(OSError):
                self.save(b'new working copy', 'autosave')
        self.assertEqual(self.store.read(self.project)[1], b'new working copy')
        self.assertEqual(old.read_bytes(), b'old working copy')
        self.assertEqual([v['revision'] for v in self.store.meta(self.project)['versions']], [2])
        self.assertEqual(self.save(b'new working copy', 'autosave', expected=1)['revision'], 2)

    def test_http_only_exact_autosave_flag_selects_working_copy(self):
        server = make_server(0, self.store)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        origin = 'http://127.0.0.1:' + str(server.server_port)

        def post(data, extra=''):
            query = urlencode({**self.params, 'id':self.project,
                               'expected': self.store.meta(self.project)['revision'] if self.store.list() else 0})
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            try:
                connection.request('POST', '/_elevhub/projekter/api/save?' + query + extra, data,
                                   {'Origin':origin, 'X-Elevhub-Project':'1', 'Content-Type':'application/octet-stream'})
                result = connection.getresponse()
                return result.status, json.loads(result.read())
            finally:
                connection.close()

        try:
            self.assertEqual(post(b'first', '&autosave=1')[0], 200)
            self.assertEqual(self.store.meta(self.project)['versions'][0]['source'], 'autosave')
            self.assertEqual(post(b'second', '&autosave=1')[0], 200)
            self.assertEqual(len(self.store.meta(self.project)['versions']), 1)
            self.assertEqual(post(b'second')[0], 200)
            self.assertEqual(self.store.meta(self.project)['versions'][0]['source'], 'browser')
            self.assertEqual(post(b'third', '&autosave=0')[0], 200)
            self.assertEqual(self.store.meta(self.project)['versions'][0]['source'], 'browser')
            for invalid in ('', 'true', '2', '-1', '1&autosave=0'):
                with self.subTest(invalid=invalid):
                    self.assertEqual(post(b'invalid save', '&autosave=' + invalid)[0], 400)
            self.assertEqual(self.store.read(self.project)[1], b'third')
        finally:
            server.shutdown(); server.server_close(); worker.join()


if __name__ == '__main__':
    unittest.main()
