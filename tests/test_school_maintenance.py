"""Real generations/databases; injected Windows actions never modify this host."""
import copy
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from app import generation_activation as activation
from app import generation_host as policy
from app import generation_transaction as preparation
from app import installation_backup as backup
from app import school_maintenance as operator
from app import school_maintenance_host as windows
from app.generation_selection import ACTIVE, TRANSITION, SelectionLease
from tests import test_generation_activation as activation_fixture
from tests import test_generation_transaction as fixture
from tests import test_generation_host as host_fixture


class FixtureController:
    def __init__(self, case):
        self.host = activation_fixture.OfflineHost(case)
        self.calls = []
        self.fail = None

    def capture(self, runtime, record):
        self.calls.append('capture')
        if self.fail == 'capture':
            raise ValueError('foreign task')
        # A real shared lock here proves the operator did not preemptively
        # acquire the generation's exclusive lock while services still live.
        with SelectionLease(runtime):
            return {'fixture': 'original task states'}

    def stop(self, runtime, record, receipt):
        self.calls.append('stop')
        if self.fail == 'stop':
            raise RuntimeError('interrupted stop')

    def secure(self, runtime, folder, source, target):
        self.calls.append('secure')
        assert (Path(runtime) / 'control' / TRANSITION).is_file()
        assert Path(target['data_root']) == folder / 'data'
        if self.fail == 'secure':
            raise RuntimeError('interrupted ACLs')

    def start(self, runtime, record, receipt):
        self.calls.append('start')
        assert not (Path(runtime) / 'control' / TRANSITION).exists()
        with SelectionLease(runtime) as lease:
            assert record == lease.record
        if self.fail == 'start':
            raise RuntimeError('interrupted start')


@unittest.skipUnless(sys.platform == 'win32' and fixture.install_fixture.ARCHIVE.is_file(),
                     'requires cached Windows embedded runtime')
class SchoolMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixture.GenerationTransactionTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        plates = self.fx.data.server / 'Laserplader'
        plates.mkdir()
        (plates / 'backup-protocol.json').write_text(json.dumps({'protocol': 1, 'role': 'laser_station'}))
        station = self.fx.data.server / 'laserstation/laser-installation.json'
        station.parent.mkdir()
        station.write_text(json.dumps({'plates': str(plates), 'port': 8790}))
        self.final = self.fx.install.root / 'operator-final'
        self.controller = FixtureController(self)

    def execute(self, action, **kwargs):
        return operator.execute(action, runtime_root=self.fx.runtime, folder=self.fx.target,
                                controller=self.controller, **kwargs)

    def update(self):
        return self.execute('update', program_root=self.fx.next, materialized_folder=self.final)

    def journal(self):
        return preparation._json(operator.journal_path(self.fx.runtime, self.fx.target))

    def test_complete_update_and_retry_preserve_live_new_work(self):
        result = self.update()
        self.assertEqual(result['state'], 'complete')
        self.assertTrue(result['services_restored'])
        self.assertFalse(result['maintenance'])
        self.assertEqual(self.controller.calls, ['capture', 'stop', 'secure', 'start'])
        selected = json.loads((self.fx.control / ACTIVE).read_bytes())
        self.assertEqual(Path(selected['data_root']), self.final / 'data')
        newer = self.final / 'data/newer.txt'
        newer.write_text('Work created after update')
        self.fx.next.rename(self.fx.install.root / 'detached-candidate')
        with SelectionLease(self.fx.runtime):
            self.assertEqual(self.execute('resume'), result)
        self.assertEqual(self.controller.calls, ['capture', 'stop', 'secure', 'start'])
        self.assertEqual(newer.read_text(), 'Work created after update')
        with patch.object(operator, 'InstanceLock', side_effect=AssertionError('status must be read-only')):
            self.assertEqual(self.execute('status'), result)
        with self.assertRaisesRegex(ValueError, 'tilbageførsel'):
            self.execute('cancel')

    def test_partial_stop_reuses_durable_original_task_receipt(self):
        self.controller.fail = 'stop'
        with self.assertRaisesRegex(RuntimeError, 'interrupted stop'):
            self.update()
        self.assertEqual(self.journal()['phase'], 'stopping')
        self.assertFalse(self.fx.target.exists())
        self.controller.fail = None
        self.assertEqual(self.execute('resume')['state'], 'complete')
        self.assertEqual(self.controller.calls.count('capture'), 1)
        self.assertEqual(self.controller.calls.count('stop'), 2)

    def test_acl_failure_cancel_retains_all_copies_and_original_data(self):
        self.controller.fail = 'secure'
        source_bytes = (self.fx.data.server / 'installation-owner.json').read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'interrupted ACLs'):
            self.update()
        self.assertTrue(self.fx.control.joinpath(TRANSITION).exists())
        self.assertEqual(self.journal()['phase'], 'securing')
        self.assertTrue(list(self.final.rglob(backup.HOLD_FILE)))
        self.assertEqual(self.execute('cancel')['state'], 'cancelled')
        self.assertEqual((self.fx.control / ACTIVE).read_bytes(), self.fx.original)
        self.assertEqual((self.fx.data.server / 'installation-owner.json').read_bytes(), source_bytes)
        self.assertTrue(self.fx.target.exists())
        self.assertTrue(list(self.final.rglob(backup.HOLD_FILE)))
        self.assertEqual(self.execute('cancel')['state'], 'cancelled')
        self.assertEqual(self.controller.calls.count('start'), 1)

    def test_published_interruption_resumes_without_old_preparation(self):
        original = activation._save_state
        def crash(folder, plan, phase, recovery_selection=None):
            if phase == 'releasing':
                raise RuntimeError('power loss after publication')
            return original(folder, plan, phase, recovery_selection)
        with patch.object(activation, '_save_state', side_effect=crash):
            with self.assertRaisesRegex(RuntimeError, 'power loss'):
                self.update()
        self.assertEqual(self.journal()['phase'], 'activating')
        selected = json.loads((self.fx.control / ACTIVE).read_bytes())
        self.assertEqual(Path(selected['data_root']), self.final / 'data')
        self.assertTrue((self.fx.control / TRANSITION).exists())
        with patch.object(preparation, 'resume', side_effect=AssertionError('must resume activation')):
            self.assertEqual(self.execute('resume')['state'], 'complete')

    def test_published_interruption_cancel_uses_compensating_revision(self):
        original = activation._save_state
        def crash(folder, plan, phase, recovery_selection=None):
            if phase == 'releasing':
                raise RuntimeError('power loss')
            return original(folder, plan, phase, recovery_selection)
        with patch.object(activation, '_save_state', side_effect=crash):
            with self.assertRaises(RuntimeError):
                self.update()
        self.assertEqual(self.execute('cancel')['state'], 'cancelled')
        selected = json.loads((self.fx.control / ACTIVE).read_bytes())
        source = json.loads(self.fx.original)
        self.assertEqual(selected, dict(source, revision=source['revision'] + 2))
        self.assertTrue(list(self.final.rglob(backup.HOLD_FILE)))

    def test_incomplete_activation_plan_can_cancel_before_publication(self):
        original = activation._save_state
        def crash(folder, plan, phase, recovery_selection=None):
            if phase == 'staged':
                raise RuntimeError('power loss after plan before receipt')
            return original(folder, plan, phase, recovery_selection)
        with patch.object(activation, '_save_state', side_effect=crash):
            with self.assertRaisesRegex(RuntimeError, 'power loss'):
                self.update()
        self.assertTrue((self.fx.target / activation.PLAN).exists())
        self.assertFalse((self.fx.target / activation.STATE).exists())
        self.assertEqual((self.fx.control / ACTIVE).read_bytes(), self.fx.original)
        self.assertEqual(self.execute('cancel')['state'], 'cancelled')
        self.assertTrue(list(self.final.rglob(backup.HOLD_FILE)))

    def test_restart_failure_resume_does_not_recopy_new_live_data(self):
        self.controller.fail = 'start'
        with self.assertRaisesRegex(RuntimeError, 'interrupted start'):
            self.update()
        self.assertEqual(self.journal()['phase'], 'starting')
        self.assertFalse((self.fx.control / TRANSITION).exists())
        newer = self.final / 'data/work-during-restart.txt'
        newer.write_text('preserve this')
        self.controller.fail = None
        with patch.object(preparation, 'resume', side_effect=AssertionError('no new snapshot')):
            self.assertEqual(self.execute('resume')['state'], 'complete')
        self.assertEqual(newer.read_text(), 'preserve this')
        self.assertEqual(self.controller.calls.count('secure'), 1)

    def test_rollback_keeps_work_saved_after_update(self):
        self.update()
        newer = self.final / 'data/after-update.txt'
        newer.write_text('current school work')
        rollback_folder = self.fx.install.root / 'operator-rollback'
        rollback_final = self.fx.install.root / 'operator-rollback-final'
        result = operator.execute('rollback', runtime_root=self.fx.runtime,
            folder=rollback_folder, materialized_folder=rollback_final,
            completed_folder=self.fx.target, controller=self.controller)
        self.assertEqual(result['state'], 'complete')
        selected = json.loads((self.fx.control / ACTIVE).read_bytes())
        self.assertEqual(selected['manifest_sha256'], json.loads(self.fx.original)['manifest_sha256'])
        self.assertEqual((rollback_final / 'data/after-update.txt').read_text(), 'current school work')
        self.assertTrue(self.final.exists())
        with closing(sqlite3.connect(rollback_final / 'data/printvaerksted/queue.sqlite3')) as db:
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0], 'true')

    def test_partial_materialization_is_not_overwritten_and_can_cancel(self):
        from app import generation_materialization as materialization
        with patch.object(materialization, '_copy_rebased_data', side_effect=RuntimeError('copy interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'copy interrupted'):
                self.update()
        self.assertTrue(self.final.exists())
        with self.assertRaises((OSError, ValueError)):
            self.execute('resume')
        self.assertEqual(self.execute('cancel')['state'], 'cancelled')
        self.assertTrue(self.final.exists())
        self.assertTrue((self.final / backup.HOLD_FILE).exists())

    def test_invalid_request_before_task_mutation(self):
        for values in ({}, {'program_root': self.fx.next, 'backup_source': self.fx.next},
                       {'program_root': self.fx.next, 'completed_folder': self.fx.next}):
            with self.assertRaises(ValueError):
                self.execute('update', materialized_folder=self.final, **values)
        self.assertEqual(self.controller.calls, [])

    def test_failed_capture_can_be_cancelled_without_any_task_mutation(self):
        self.controller.fail = 'capture'
        with self.assertRaisesRegex(ValueError, 'foreign task'):
            self.update()
        self.assertEqual(self.execute('cancel')['state'], 'cancelled')
        self.assertEqual(self.execute('status')['state'], 'cancelled')
        self.assertEqual(self.controller.calls, ['capture'])

    def test_restore_uses_verified_history_preserves_current_original_data(self):
        historical = self.fx.install.root / 'operator-historical-backup'
        backup.snapshot(self.fx.data.profile, historical)
        current = self.fx.data.server / 'new-work-after-backup.txt'
        current.write_text('Retain even while restoring older data')
        result = self.execute('restore', program_root=self.fx.next,
                              materialized_folder=self.final, backup_source=historical)
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(current.read_text(), 'Retain even while restoring older data')
        self.assertFalse((self.final / 'data/new-work-after-backup.txt').exists())
        self.assertFalse((self.fx.control / TRANSITION).exists())
        self.assertTrue(historical.exists())

    def test_real_materialized_locks_are_created_secured_and_excluded_from_seal(self):
        from app import generation_materialization as materialization
        from app.generation_lock import GenerationLock
        from app.instance import InstanceLock
        data = self.fx.data.server
        for child in ('logs', 'local-ai', 'admin-ssh', 'security/enrollment/operations'):
            (data / child).mkdir(parents=True, exist_ok=True)
        (data / 'admin-ssh/id_ed25519').write_bytes(b'test-only-key')
        (data / 'school.json').write_text(json.dumps({'schemaVersion': 1, 'role': 'makerspace'}))
        (data / 'security/enrollment/backup-protocol.json').write_text(json.dumps({'protocol': 1, 'role': 'enrollment'}))
        owner = preparation._json(data / 'installation-owner.json')
        owner.update(roleIp='192.0.2.10', teacherPort=8768, teacherAddress='127.0.0.1')
        (data / 'installation-owner.json').write_text(json.dumps(owner))
        config = preparation._json(data / 'konfiguration.json')
        config.pop('makerspace_laser_queue')
        (data / 'konfiguration.json').write_text(json.dumps(config))
        station = preparation._json(data / 'laserstation/laser-installation.json')
        station['role'] = 'Station'
        (data / 'laserstation/laser-installation.json').write_text(json.dumps(station))
        prepared = self.fx.prepare()
        material = materialization.materialize(self.fx.target / preparation.PREPARED,
            self.final, expected_operation_id=prepared['operation_id'])
        source = json.loads(self.fx.original)
        selected = dict(source, revision=source['revision'] + 1,
            program_root=material['program_root'], data_root=material['data_root'],
            manifest_sha256=material['manifest_sha256'])
        target_data = Path(selected['data_root'])
        required_locks = {'instance.lock', 'hub/instance.lock', 'printvaerksted/instance.lock',
            'designvaerksted/instance.lock', 'Laserplader/instance.lock', 'security/enheder.lock',
            'security/enrollment/instance.lock', 'security/enrollment/operations/instance.lock'}
        self.assertTrue(all(not (target_data / path).exists() for path in required_locks))
        manifest_before = (self.final / materialization.MANIFEST).read_bytes()
        applied = {}
        seen = []

        def snapshot(payload):
            request = json.loads(payload)
            self.assertEqual(request['target_record'], selected)
            for relative in required_locks:
                self.assertTrue((target_data / relative).is_file(), relative)
                with self.assertRaises(RuntimeError):
                    with InstanceLock((target_data / relative).parent, filename=Path(relative).name):
                        pass
            with self.assertRaises(RuntimeError):
                with GenerationLock(self.fx.control / 'generation.lock', exclusive=False):
                    pass
            seen.append('snapshot')
            owner_target = preparation._json(target_data / 'installation-owner.json')
            tasks = []
            for name, principal, entry, tail, description in (
                ('ElevhubServer', policy.SERVICE, 'server', ['--role-ip',owner_target['roleIp'],
                  '--teacher-port',str(owner_target['teacherPort']),'--teacher-address',owner_target['teacherAddress']],
                  'ElevhubServer owner='+owner_target['ownerId']),
                ('ElevhubSchoolEnrollment', policy.SYSTEM, 'enrollment', ['serve'], 'Elevhub skole-enrollment')):
                tasks.append(dict(name=name,principal=principal,logon_type='ServiceAccount',run_level='Highest',
                    description=description,actions=[dict(execute=str(self.fx.runtime / 'runtime/python.exe'),
                    working_directory=str(self.fx.runtime),arguments=policy._command(str(self.fx.runtime),
                    selected['installation_id'],entry,tail))]))
            roots = {self.fx.runtime, Path(source['program_root']), Path(source['data_root']),
                     Path(selected['program_root']), target_data}
            paths = {path for root in roots for path in (root, *root.parents, *root.rglob('*'))}
            rows = []
            for path in sorted(paths):
                rules = applied.get(path)
                if rules is None:
                    rights, private = policy.RX, False
                    for data_root in (data, target_data):
                        if path == data_root or data_root in path.parents:
                            relative = path.relative_to(data_root).as_posix().lower()
                            component = relative.split('/')[0]
                            private = component == 'admin-ssh' or relative == 'installation-owner.json'
                            if component in {'logs','hub','printvaerksted','designvaerksted','local-ai',
                                             'laserstation','laserplader'} or relative in {'instance.lock','security/enheder.lock'}:
                                rights = policy.MODIFY
                    rules = [host_fixture.rule(policy.SYSTEM,0x1F01FF),host_fixture.rule(policy.ADMIN,0x1F01FF)]
                    if not private:
                        rules.append(host_fixture.rule(policy.SERVICE,rights))
                rows.append(dict(path=str(path),reparse=False,owner=policy.ADMIN,dacl_present=True,
                                 unsupported=False,acl=rules))
            return dict(tasks=tasks,processes=[],paths=rows,metadata=dict(
                owner=owner_target,school=preparation._json(target_data / 'school.json'),
                config=preparation._json(target_data / 'konfiguration.json'),laser={
                name:dict(config=preparation._json(root / 'laserstation/laser-installation.json'),
                          protocol=preparation._json(root / 'Laserplader/backup-protocol.json'))
                for name, root in (('source',data),('target',target_data))}))

        def execute_acl(script, request):
            self.assertEqual(script, windows.ACL_SCRIPT)
            seen.append('acl')
            for row in request['paths']:
                path = Path(row['path'])
                self.assertTrue(path.exists())
                applied[path] = [host_fixture.rule(grant['sid'],grant['mask']) for grant in row['grants']]
            for relative in required_locks:
                self.assertIn(target_data / relative, applied)
            self.assertEqual({rule['sid'] for rule in applied[target_data / 'security/enrollment/instance.lock']},
                             {policy.SYSTEM,policy.ADMIN,policy.SERVICE})
            self.assertEqual(next(rule['mask'] for rule in applied[target_data / 'instance.lock']
                                  if rule['sid'] == policy.SERVICE),policy.MODIFY)
            return {'secured': True}

        controller = windows.WindowsMaintenanceController(policy.WindowsGenerationHost(snapshot), execute_acl)
        controller.secure(self.fx.runtime, self.final, source, selected)
        self.assertEqual(seen, ['snapshot','acl','snapshot'])
        self.assertEqual((self.final / materialization.MANIFEST).read_bytes(), manifest_before)
        self.assertEqual(materialization.verify_materialized(self.final,
            expected_operation_id=prepared['operation_id']), material)
        self.assertTrue(all((target_data / path).is_file() for path in required_locks))
        self.assertTrue(list(self.final.rglob(backup.HOLD_FILE)))


class WindowsControllerTests(unittest.TestCase):
    def setUp(self):
        self.record = host_fixture.record(host_fixture.TARGET, host_fixture.DATA)
        self.value = host_fixture.snapshot()
        roots = {policy._canonical(host_fixture.RUNTIME), policy._canonical(host_fixture.TARGET),
                 policy._canonical(host_fixture.DATA)}
        ancestors = {'c:\\', r'c:\program files', 'd:\\', r'd:\prepared'}
        self.value['paths'] = [row for row in self.value['paths']
            if policy._canonical(row['path']) in ancestors or policy._under(row['path'], roots)]
        self.states = [{'name': 'ElevhubServer', 'enabled': True, 'running': True},
                       {'name': 'ElevhubSchoolEnrollment', 'enabled': False, 'running': False}]
        self.calls = []
        self.host = policy.WindowsGenerationHost(lambda _: copy.deepcopy(self.value))
        def run(script, request):
            self.calls.append((script, request))
            return copy.deepcopy(self.states) if request['action'] == 'capture' else {'ok': True}
        self.controller = windows.WindowsMaintenanceController(self.host, run, timeout=0)

    def test_capture_stop_start_uses_validated_receipt_and_original_states(self):
        receipt = self.controller.capture(host_fixture.RUNTIME, self.record)
        self.controller.stop(host_fixture.RUNTIME, self.record, receipt)
        with patch.object(windows, '_present', return_value=False):
            self.controller.start(host_fixture.RUNTIME, self.record, receipt)
        self.assertEqual([request['action'] for _, request in self.calls], ['capture','stop','start','capture'])
        self.assertEqual(self.calls[2][1]['states'], self.states)
        self.assertIn('Disable-ScheduledTask', self.calls[1][0])
        self.assertNotIn('Stop-Process', self.calls[1][0])

    def test_foreign_tasks_and_corrupt_receipts_never_mutate_tasks(self):
        self.value['tasks'][0]['description'] = 'someone else'
        with self.assertRaises(ValueError):
            self.controller.capture(host_fixture.RUNTIME, self.record)
        self.assertEqual(self.calls, [])
        self.value['tasks'][0]['description'] = 'ElevhubServer owner=' + host_fixture.GUID
        receipt = self.controller.capture(host_fixture.RUNTIME, self.record)
        receipt['states'].append(self.states[0])
        count = len(self.calls)
        with self.assertRaises(ValueError):
            self.controller.stop(host_fixture.RUNTIME, self.record, receipt)
        self.assertEqual(len(self.calls), count)

    def test_surviving_children_native_apps_and_pending_barrier_refuse_start(self):
        receipt = self.controller.capture(host_fixture.RUNTIME, self.record)
        for process in ({'name':'python.exe','path':host_fixture.RUNTIME+r'\runtime\python.exe',
                         'command_line':'child','pid':os.getpid()+1},
                        {'name':'Beam Studio.exe','path':r'C:\Beam\Beam Studio.exe',
                         'command_line':'beam','pid':os.getpid()+1}):
            self.value['processes'] = [process]
            with self.assertRaises(ValueError):
                self.controller.stop(host_fixture.RUNTIME, self.record, receipt)
        count = len(self.calls)
        with patch.object(windows, '_present', return_value=True):
            with self.assertRaises(RuntimeError):
                self.controller.start(host_fixture.RUNTIME, self.record, receipt)
        self.assertEqual(len(self.calls), count)

    def test_acl_plan_private_keys_writable_components_and_readonly_program(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / 'new'
            data = folder / 'data'
            for child in ('admin-ssh','hub','laserstation','Laserplader','security','local-ai'):
                (data / child).mkdir(parents=True)
            (folder / 'program').mkdir()
            private_tls = ('security/ca.key', 'security/tls-initialization.json',
                           'security/tls-renewal.json', 'security/tls-renewal-receipt.json',
                           'security/.tls-renewal.json.owned.tmp')
            for child in ('installation-owner.json','admin-ssh/id_ed25519','security/server.key','hub/jobs.txt', *private_tls):
                (data / child).write_bytes(b'fixture')
            rows = windows.acl_plan(folder, data, 'makerspace')
            mapping = {Path(row['path']).relative_to(folder).as_posix():
                       {grant['sid']: grant['mask'] for grant in row['grants']} for row in rows}
            for path in ('data/installation-owner.json','data/admin-ssh/id_ed25519', *('data/' + name for name in private_tls)):
                self.assertEqual(set(mapping[path]), {policy.SYSTEM, policy.ADMIN})
            self.assertEqual(mapping['data/hub/jobs.txt'][policy.SERVICE], policy.MODIFY)
            self.assertEqual(mapping['data/Laserplader'][policy.USERS], policy.MODIFY)
            self.assertEqual(mapping['program'][policy.SERVICE], policy.RX)
            self.assertNotIn(policy.USERS, mapping['data/security/server.key'])
            with self.assertRaises(ValueError):
                windows.acl_plan(folder, data / 'hub', 'makerspace')


if __name__ == '__main__':
    unittest.main()
