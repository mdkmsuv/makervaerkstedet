"""Rollback is a new program/data generation built from the CURRENT data.

Only the Windows host boundary is injected; migrations, SQLite, copying,
selection leases and the final embedded runtime inspection are real.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import unittest

from app import generation_activation as activation
from app import generation_materialization as materialization
from app import generation_rollback as rollback
from app import generation_transaction as preparation
from app import installation_backup as backup
from app import installation_update as update
from app.core import Queue
from app.generation_selection import ACTIVE, SelectionLease
from app.instance import InstanceLock
from app.laser_server import Inbox
from app.release_compatibility import inspect_program
from scripts.smoke_slice import cube
from tests import test_generation_activation as fixture


@unittest.skipUnless(sys.platform == 'win32' and fixture.fixture.install_fixture.ARCHIVE.is_file(),
                     'requires the cached Windows embedded runtime')
class GenerationRollbackTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixture.GenerationActivationTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.fx.activate()
        self.runtime = self.fx.fx.runtime
        self.completed = self.fx.fx.target
        self.current = self.fx.selected()
        self.data = Path(self.current['data_root'])
        self.destination = self.fx.fx.install.root / 'rollback-maintenance'
        self.final = self.fx.fx.install.root / 'rollback-final'
        self.original_manifest = json.loads(self.fx.fx.original)['manifest_sha256']

    def prepare(self):
        return rollback.prepare(self.runtime, self.completed, self.destination)

    def db_rows(self, data):
        result = {}
        for component, filename in (('hub', 'makerspace.sqlite3'), ('printvaerksted', 'queue.sqlite3'),
                                    ('designvaerksted', 'afleveringer.sqlite3')):
            with closing(sqlite3.connect(data / component / filename)) as db:
                tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                          if not row[0].startswith('sqlite_') and row[0] != 'settings']
                result[component] = {table: db.execute('SELECT * FROM "' + table + '" ORDER BY rowid').fetchall()
                                     for table in tables}
        return result

    def fingerprint(self, record=None):
        record = self.current if record is None else record
        contract = inspect_program(Path(record['program_root']))['data_contract']
        with backup.offline_profile(preparation._profile(record, contract)) as locked:
            return preparation._fingerprint(locked)

    def fresh_work(self):
        actor = {'id': 'teacher:' + '1' * 32, 'role': 'teacher',
                 'scopes': ['makerspace:read', 'machines:write', 'workflows:write', 'jobs:write', 'audit:read']}
        service = fixture.fixture.data_fixture.MakerspaceService(str(self.data / 'hub/makerspace.sqlite3'))
        service.execute(actor, {'action': 'machine.create', 'params': {'name': 'New desk after upgrade',
            'location': 'New room', 'enabled': False, 'workflow_id': None}, 'expected_version': 0,
            'request_id': 'c' * 32})
        queue = Queue(self.data / 'printvaerksted', [{'id': 'p1', 'name': 'P1S', 'model': 'P1S', 'enabled': True}])
        try:
            self.new_print = queue.submit(cube(8), 'fresh-after-upgrade.stl', 'any', 'rollback-new-print-001')
            queue.pause(False)
        finally:
            queue.close()
        inbox = Inbox(self.data / 'designvaerksted', code='backup-code')
        self.new_laser = inbox.submit({'elevnavn': 'New pupil', 'klasse': 'New group', 'projekt': 'New work',
            'requestId': 'rollback-new-laser-001', 'svg': '<svg xmlns="http://www.w3.org/2000/svg" data-laservaerksted="1" width="20mm" height="20mm" viewBox="0 0 20 20"><g data-operation="cut"><path data-operation="cut" d="M1 1H19V19H1Z"/></g></svg>'})
        teacher = actor['id']
        definition = {'name': 'Fresh rollback laser', 'kind': 'laser', 'steps': [
            {'id': step, 'label': step, 'role': role, 'action': step} for step, role in
            [('submit', 'operator'), ('prepare', 'teacher'), ('approve', 'teacher'),
             ('produce', 'teacher'), ('collect', 'operator'), ('clear', 'teacher')]]}
        for index, (action, params, expected) in enumerate((
            ('laser.station.create', {'id': 'rollback-laser', 'name': 'New station', 'location': 'New room', 'model': 'fbb2', 'enabled': False}, 0),
            ('laser.station.update', {'id': 'rollback-laser', 'name': 'New station', 'location': 'New room', 'model': 'fbb2', 'enabled': True}, 1),
            ('laser.workflow.assign', {'id': 'rollback-laser', 'workflow': {'id': 'b' * 32, 'revision': 1, 'definition': definition}}, 2))):
            inbox.workflows.execute(teacher, {'action': action, 'params': params, 'expected_version': expected,
                                             'request_id': str(index + 20) * 16})
        for index, (action, params, expected) in enumerate((
            ('laser.job.bind', {'id': self.new_laser['id'], 'station_id': 'rollback-laser'}, 0),
            ('laser.job.advance', {'id': self.new_laser['id'], 'step_id': 'prepare', 'confirmed': True}, 1),
            ('laser.job.advance', {'id': self.new_laser['id'], 'step_id': 'approve', 'confirmed': True}, 2),
            ('laser.job.advance', {'id': self.new_laser['id'], 'step_id': 'produce', 'confirmed': True}, 3))):
            inbox.workflows.execute(teacher, {'action': action, 'params': params, 'expected_version': expected,
                                             'request_id': str(index + 10) * 16})
        inbox.workflows.check_export('rollback-laser', 'fbb2', [self.new_laser['id']])
        self.changed = {
            'new-project.txt': b'Pupil work created after upgrading',
            'Laserplader/fresh-plate.svg': b'<svg>new station plate</svg>',
            'printvaerksted/settings.json': json.dumps({'studio_path': 'C:\\Slicer\\bambu-studio.exe',
                'printers': [{'id': 'p1', 'host': '192.168.5.11', 'access_code': 'new-private-code'}]}).encode(),
            'printvaerksted/makerspace-bridge.key': b'mkbridge_' + b'C' * 43,
            'designvaerksted/makerspace-laser.key': b'mkbridge_' + b'D' * 43,
        }
        for relative, content in self.changed.items():
            (self.data / relative).write_bytes(content)
        self.rows = self.db_rows(self.data)
        with closing(sqlite3.connect(self.data / 'designvaerksted/afleveringer.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT station_id,workflow_version,handoff_claimed FROM jobs WHERE id=?',
                                        (self.new_laser['id'],)).fetchone(), ('rollback-laser', 4, 1))
        self.before = self.fingerprint()

    def assert_fresh_work(self, data):
        self.assertEqual(self.rows, self.db_rows(data))
        for relative, content in self.changed.items():
            self.assertEqual((data / relative).read_bytes(), content, relative)

    def test_new_generation_returns_previous_program_with_current_work(self):
        self.fresh_work()
        previous_seal = backup._hash(self.completed / preparation.PREPARED / update.SEAL)
        result = self.prepare()
        self.assertEqual(result['state'], 'prepared')
        self.assertTrue(result['maintenance'])
        self.assertEqual(result, preparation.resume(self.runtime, self.destination))
        self.assertEqual(self.before, self.fingerprint())
        self.assertEqual(self.current, json.loads((self.fx.fx.control / ACTIVE).read_bytes()))
        candidate = self.destination / preparation.PREPARED / update.CANDIDATE / 'roots/server'
        self.assert_fresh_work(candidate)
        with closing(sqlite3.connect(candidate / 'printvaerksted/queue.sqlite3')) as db:
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0], 'true')
        materialization.materialize(self.destination / preparation.PREPARED, self.final,
                                    expected_operation_id=result['operation_id'])
        report = activation.activate(self.runtime, self.destination, self.final, host=self.fx.host)
        self.assertEqual(report['state'], 'active')
        self.assertFalse(report['services_started'])
        selected = json.loads((self.fx.fx.control / ACTIVE).read_bytes())
        self.assertEqual(selected['revision'], self.current['revision'] + 1)
        self.assertEqual(selected['manifest_sha256'], self.original_manifest)
        self.assertEqual(Path(selected['program_root']), self.final / 'program')
        self.assertEqual(Path(selected['data_root']), self.final / 'data')
        self.assert_fresh_work(self.final / 'data')
        self.assertEqual(self.before, self.fingerprint(self.current))
        self.assertEqual(previous_seal, backup._hash(self.completed / preparation.PREPARED / update.SEAL))
        self.assertTrue(Path(self.current['program_root']).exists())
        self.assertTrue(self.completed.exists())
        self.assertFalse((self.fx.fx.data.server / 'new-project.txt').exists())
        inspected = self.fx.fx.inspect()
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        self.assertEqual(Path(json.loads(inspected.stdout)['program_root']), self.final / 'program')
        self.assertEqual(Path(json.loads(inspected.stdout)['data_root']), self.final / 'data')

    def test_cancel_reopens_current_program_and_preserves_new_work(self):
        self.fresh_work()
        result = self.prepare()
        self.assertNotEqual(self.fx.fx.inspect().returncode, 0)
        cancelled = preparation.cancel(self.runtime, self.destination)
        self.assertEqual(cancelled['operation_id'], result['operation_id'])
        self.assertFalse(cancelled['maintenance'])
        self.assertEqual(cancelled, preparation.cancel(self.runtime, self.destination))
        self.assertEqual(self.current, json.loads((self.fx.fx.control / ACTIVE).read_bytes()))
        self.assertEqual(self.before, self.fingerprint())
        self.assert_fresh_work(self.data)
        self.assertEqual(self.fx.fx.inspect().returncode, 0)

    def test_live_selection_or_data_lease_refuses_before_writes(self):
        for factory in (lambda: SelectionLease(self.runtime), lambda: InstanceLock(self.data / 'hub'),
                        lambda: InstanceLock(self.data / 'Laserplader')):
            with factory():
                with self.assertRaises(RuntimeError): self.prepare()
            self.assertFalse(self.destination.exists())
            self.assertFalse(self.fx.barrier.exists())
            self.assertEqual(self.current, self.fx.selected())

    def test_changed_receipt_previous_program_selection_or_schema_refuses_before_writes(self):
        plan = preparation._json(self.completed / activation.PLAN)
        receipt = activation._receipt_path(self.runtime, plan['operation_id'])
        manifest = self.completed / preparation.PREVIOUS / 'makerspace-release.json'
        selector = self.fx.selector
        original = {path: path.read_bytes() for path in (receipt, manifest, selector)}
        mutations = (
            (receipt, json.dumps(dict(json.loads(original[receipt]), plan_sha256='0' * 64)).encode()),
            (manifest, original[manifest] + b' '),
            (selector, activation._bytes(dict(self.current, revision=self.current['revision'] + 1))),
        )
        for path, content in mutations:
            with self.subTest(path=path):
                path.write_bytes(content)
                try:
                    with self.assertRaises(ValueError): self.prepare()
                    self.assertFalse(self.destination.exists())
                    self.assertFalse(self.fx.barrier.exists())
                finally: path.write_bytes(original[path])
        # Real unsupported schema, not a mocked compatibility checker.
        with closing(sqlite3.connect(self.data / 'hub/makerspace.sqlite3')) as db:
            db.execute('PRAGMA user_version=999'); db.commit()
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.fx.barrier.exists())


if __name__ == '__main__':
    unittest.main()
