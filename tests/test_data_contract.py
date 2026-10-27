import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

from app.core import Queue
from app.data_contract import inspect_component, require_contract
from app.laser_server import Inbox

HUB = Path(__file__).parents[1] / 'linux-elevhub' / 'server' / 'elevhub'
sys.path.insert(0, str(HUB))
from makerspace_api import MakerspaceService  # noqa: E402


class DataContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_real_queue_inbox_and_hub_are_inspected_read_only(self):
        queue = Queue(self.root / 'print', [])
        queue.close()
        inbox = Inbox(self.root / 'laser', code='testcode')
        hub_path = self.root / 'hub' / 'makerspace.sqlite3'
        MakerspaceService(hub_path)
        paths = {'print': self.root / 'print' / 'queue.sqlite3',
                 'laser': self.root / 'laser' / 'afleveringer.sqlite3',
                 'hub': self.root / 'hub' / 'makerspace.sqlite3'}
        before = {role: (path.stat().st_mtime_ns, path.read_bytes()) for role, path in paths.items()}
        reports = {role: inspect_component(path, role) for role, path in paths.items()}
        after = {role: (path.stat().st_mtime_ns, path.read_bytes()) for role, path in paths.items()}
        self.assertEqual(before, after)
        self.assertEqual({r['contract'] for r in reports.values()}, {1})
        self.assertEqual(reports['hub']['facts']['agents'], 0)
        self.assertEqual(reports['print']['facts']['pending_print_jobs'], 0)
        self.assertEqual(reports['laser']['facts']['unfinished_laser_handoffs'], 0)
        require_contract({'hub': 1, 'print': 1, 'laser': 1}, reports)

    def test_aggregate_facts_do_not_expose_job_content(self):
        queue = Queue(self.root / 'print', [])
        queue.db.execute("INSERT INTO jobs(id,request_id,digest,filename,meta,created,updated,status) VALUES(?,?,?,?,?,?,?,?)",
                         ('a' * 32, 'req', 'b' * 64, 'private.svg', '{}', 1, 1, 'workflow_wait'))
        queue.db.commit()
        queue.close()
        inbox = Inbox(self.root / 'laser', code='testcode')
        db = sqlite3.connect(self.root / 'laser' / 'afleveringer.sqlite3')
        try:
            db.execute("INSERT INTO jobs(id,request_id,created_at,status,svg,details,payload_available,handoff_claimed,physical_done) VALUES(?,?,?,?,?,?,?,?,?)",
                       ('c' * 32, 'req2', '2026-01-01', 'queued', '<svg>private</svg>', json.dumps({'student': 'private'}), 1, 1, 0))
            db.commit()
        finally:
            db.close()
        print_report = inspect_component(self.root / 'print' / 'queue.sqlite3', 'print')
        laser_report = inspect_component(self.root / 'laser' / 'afleveringer.sqlite3', 'laser')
        self.assertEqual(print_report['facts']['pending_print_jobs'], 1)
        self.assertEqual(laser_report['facts']['unfinished_laser_handoffs'], 1)
        self.assertNotIn('private.svg', json.dumps(print_report))
        self.assertNotIn('private', json.dumps(laser_report))

    def test_attention_and_finished_production_are_not_hidden_from_reconciliation(self):
        queue = Queue(self.root / 'print', [])
        self.addCleanup(queue.close)
        for marker, status in (('a', 'attention'), ('b', 'done')):
            queue.db.execute("INSERT INTO jobs(id,request_id,digest,filename,meta,created,updated,status,workflow) VALUES(?,?,?,?,?,?,?,?,?)",
                             (marker * 32, marker, marker * 64, 'model.stl', '{}', 1, 1, status, '{}'))
        queue.db.commit()
        facts = inspect_component(self.root / 'print' / 'queue.sqlite3', 'print')['facts']
        self.assertEqual(facts['jobs'], 2)
        self.assertEqual(facts['pending_print_jobs'], 1)
        self.assertEqual(facts['uncertain_print_jobs'], 1)
        self.assertEqual(facts['unfinished_print_workflows'], 2)

    def test_current_wal_is_read_without_modifying_database_or_journal(self):
        queue = Queue(self.root / 'print', [])
        self.addCleanup(queue.close)
        queue.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        queue.db.execute('PRAGMA wal_autocheckpoint=0')
        queue.db.execute("INSERT INTO jobs(id,request_id,digest,filename,meta,created,updated,status) VALUES(?,?,?,?,?,?,?,?)",
                         ('e' * 32, 'wal-request', 'f' * 64, 'only-wal.stl', '{}', 1, 1, 'dispatching'))
        queue.db.commit()
        path = self.root / 'print' / 'queue.sqlite3'
        wal = path.with_name(path.name + '-wal')
        self.assertGreater(wal.stat().st_size, 0)
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (path, wal)}
        result = inspect_component(path, 'print')
        self.assertEqual(result['facts']['jobs'], 1)
        self.assertEqual(result['facts']['uncertain_print_jobs'], 1)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before})

    def test_legacy_and_future_schema_require_explicit_migration(self):
        Queue(self.root / 'print', []).close()
        Inbox(self.root / 'laser', code='testcode')
        for role, filename in (('print', 'queue.sqlite3'), ('laser', 'afleveringer.sqlite3')):
            path = self.root / role / filename
            for version in (0, 2, 99):
                with self.subTest(role=role, version=version):
                    with closing(sqlite3.connect(path)) as db:
                        db.execute(f'PRAGMA user_version={version}')
                        db.commit()
                    before = path.read_bytes()
                    with self.assertRaises(ValueError):
                        inspect_component(path, role)
                    self.assertEqual(path.read_bytes(), before)

    def test_contract_rejects_missing_unknown_bool_and_wrong_version(self):
        actual = {'hub': {'contract': 1}}
        require_contract({'hub': 1}, actual)
        for declared, value in (
                ({'print': 1}, actual), ({'hub': True}, actual),
                ({'hub': 2}, actual), ({'hub': 1}, {'hub': {'contract': 2}}),
                ({'hub': 1, 'print': 1}, actual),
                ({'hub': 1}, {'hub': {'role': 'print', 'contract': 1}})):
            with self.assertRaises(ValueError):
                require_contract(declared, value)

    def test_unknown_sqlite_schema_and_links_are_refused(self):
        path = self.root / 'unknown.sqlite3'
        db = sqlite3.connect(path)
        try:
            db.execute('PRAGMA user_version=99')
            db.commit()
        finally:
            db.close()
        with self.assertRaises(ValueError):
            inspect_component(path, 'hub')
        link = self.root / 'link.sqlite3'
        try:
            link.symlink_to(path)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable')
        with self.assertRaises(ValueError):
            inspect_component(link, 'hub')


if __name__ == '__main__':
    unittest.main()
