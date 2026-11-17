from pathlib import Path as _LayoutPath
import sys as _layout_sys
_LAYOUT_ROOT = next(p for p in _LayoutPath(__file__).resolve().parents if (p / 'REPOSITORY-LAYOUT.json').is_file())
_layout_sys.path[:0] = [str(_LAYOUT_ROOT), str(_LAYOUT_ROOT / 'system')]
from repository import source_path, repo_root
import os
"""Program/pause invariants using synthetic files and an explicit fake OS edge."""
import importlib.util
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch
from unittest.mock import Mock
from contextlib import contextmanager, nullcontext
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('inno_lifecycle',source_path('windows/inno/lifecycle.py', ROOT))
life=importlib.util.module_from_spec(spec);spec.loader.exec_module(life)
spec=importlib.util.spec_from_file_location('inno_build',source_path('windows/inno/build.py', ROOT))
build=importlib.util.module_from_spec(spec);spec.loader.exec_module(build)

class FakeOS:
    def __init__(self,data,*,health_fail=False,stop_fail=False,pause_fail=False):
        self.data=data;self.events=[];self.paused=False;self.holder=None;self.jobs=['printing','dispatching','attention'];self.health_fail=health_fail;self.stop_fail=stop_fail;self.pause_fail=pause_fail
    def protect_journal(self,path):self.events.append('protect')
    def preflight_update(self,program,journal):self.events.append('preflight')
    def acquire_pause(self,operation):
        self.paused=True;self.holder=operation;self.events.append('pause')
        if self.pause_fail:raise ValueError('laser_endpoint_missing')
    def assert_pause(self,operation):
        assert self.paused and self.holder==operation;self.events.append('assert')
    def release_hold(self,operation):
        # Like the real backends: the hold goes, the queue's pause flag stays.
        self.events.append('release_hold')
        if self.holder!=operation:return False
        self.holder=None;return True
    def stop_owned(self,journal):
        assert self.paused;self.events.append('stop_owned')
        if self.stop_fail:self.stop_fail=False;raise ValueError('process_remains')
    def restore_owned(self,journal):self.events.append('restore_owned')
    def configure_simple(self,package,journal):self.events.append('configure_simple')
    def backup_databases(self,journal):self.events.append('database_backup')
    def restore_simple(self,journal):self.events.append('restore_simple')
    def health(self):
        self.events.append('health')
        if self.health_fail:
            self.data.write_bytes(b'new submission after restart');raise ValueError('health_failed')

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.program=self.root/'program';self.package=self.root/'package';self.program.mkdir();self.package.mkdir()
        (self.program/'main.py').write_bytes(b'old program');(self.program/'installation.json').write_bytes(b'old ownership metadata')
        (self.package/'main.py').write_bytes(b'new program')
        self.data=self.root/'projects.sqlite3';self.data.write_bytes(b'original project and queue bytes')
        self.journal=self.root/'journals'/str(uuid.uuid4())
    def test_restore_simple_uses_installer_payload_for_updater_hook(self):
        native=life.NativeWindows.__new__(life.NativeWindows)
        native.payload=self.root/'payload';native.command=Mock()
        native.restore_simple(self.journal)
        native.command.assert_called_once_with(native.payload/'task_control.ps1',
            '-Action','RestoreSimple','-Journal',self.journal,'-PackageRoot',native.payload/'package')
    def test_active_and_full_queues_do_not_block_update_and_pause_precedes_owned_stop(self):
        native=FakeOS(self.data);result=life.update_server(self.package,self.program,self.journal,native)
        self.assertEqual(result['status'],'complete_paused');self.assertTrue(native.paused)
        # The queue stays paused, but the update's own hold is lifted last so the
        # teacher can resume the queue and the next update can start.
        self.assertIsNone(native.holder);self.assertTrue(result['hold_released'])
        self.assertEqual(native.events[-1],'release_hold');self.assertLess(native.events.index('health'),native.events.index('release_hold'))
        self.assertTrue(json.loads((self.journal/'state.json').read_text())['hold_released'])
        self.assertEqual(native.jobs,['printing','dispatching','attention'])
        self.assertLess(native.events.index('pause'),native.events.index('stop_owned'))
        self.assertLess(native.events.index('stop_owned'),native.events.index('database_backup'))
        self.assertEqual((self.program/'main.py').read_bytes(),b'new program')
        self.assertEqual((self.program/'installation.json').read_bytes(),b'old ownership metadata')
        self.assertEqual(self.data.read_bytes(),b'original project and queue bytes')
        self.assertNotIn('physical_stop',native.events)
    def test_failed_health_restores_only_program_and_preserves_new_submissions(self):
        native=FakeOS(self.data,health_fail=True)
        with self.assertRaisesRegex(ValueError,'health_failed'):life.update_server(self.package,self.program,self.journal,native)
        self.assertEqual((self.program/'main.py').read_bytes(),b'old program')
        self.assertEqual(self.data.read_bytes(),b'new submission after restart')
        self.assertTrue(native.paused)
        # Clean rollback: tasks restored first, then the hold is lifted.
        self.assertIsNone(native.holder);self.assertLess(native.events.index('restore_owned'),native.events.index('release_hold'))
        self.assertTrue(json.loads((self.journal/'state.json').read_text())['hold_released'])
        self.assertEqual(json.loads((self.journal/'state.json').read_bytes())['phase'],'program_restored_paused')
    def test_later_update_frees_space_held_by_earlier_finished_updates_only(self):
        native=FakeOS(self.data);first=self.journal;life.update_server(self.package,self.program,first,native)
        self.assertTrue((first/'program-before').is_dir())
        # An interrupted cleanup left the first update's previous folder behind.
        leftover=self.program.with_name(self.program.name+'.previous-'+first.name);leftover.mkdir();(leftover/'old.py').write_bytes(b'x')
        # A journal that did not finish cleanly must keep its backup.
        broken=self.root/'journals'/str(uuid.uuid4());(broken/'program-before').mkdir(parents=True)
        (broken/'state.json').write_text(json.dumps({'phase':'rollback_stop_failed_manual_recovery_required'}))
        (self.package/'main.py').write_bytes(b'newer program')
        second=self.root/'journals'/str(uuid.uuid4());result=life.update_server(self.package,self.program,second,native)
        self.assertFalse((first/'program-before').exists());self.assertFalse(leftover.exists())
        self.assertTrue((first/'state.json').is_file());self.assertTrue((second/'program-before').is_dir())
        self.assertTrue((broken/'program-before').is_dir())
        self.assertEqual(sorted(result['pruned']),sorted([leftover.name,first.name+'/program-before']))
    def test_cleanup_retries_a_briefly_locked_or_read_only_file(self):
        folder=self.root/'parked';folder.mkdir();locked=folder/'a.py';locked.write_bytes(b'x');os.chmod(locked,0o400)
        life.remove_tree(folder,attempts=2,pause=0);self.assertFalse(folder.exists())
    def test_two_updates_in_a_row_are_possible(self):
        native=FakeOS(self.data);life.update_server(self.package,self.program,self.journal,native)
        (self.package/'main.py').write_bytes(b'newer program')
        result=life.update_server(self.package,self.program,self.root/'journals'/str(uuid.uuid4()),native)
        self.assertEqual(result['status'],'complete_paused');self.assertEqual((self.program/'main.py').read_bytes(),b'newer program')
    def test_database_backup_failure_leaves_program_and_data_before_replacement(self):
        native=FakeOS(self.data)
        with patch.object(native,'backup_databases',side_effect=ValueError('database_backup_failed')):
            with self.assertRaisesRegex(ValueError,'database_backup_failed'):
                life.update_server(self.package,self.program,self.journal,native)
        self.assertFalse((self.journal/'program-before').exists())
        self.assertEqual((self.program/'main.py').read_bytes(),b'old program')
        self.assertEqual(self.data.read_bytes(),b'original project and queue bytes')
        self.assertIn('restore_owned',native.events);self.assertTrue(native.paused)
    def test_partial_pause_failure_keeps_queue_paused_and_does_not_stop_anything(self):
        native=FakeOS(self.data,pause_fail=True)
        with self.assertRaisesRegex(ValueError,'laser_endpoint_missing'):life.update_server(self.package,self.program,self.journal,native)
        self.assertTrue(native.paused);self.assertNotIn('stop_owned',native.events)
        self.assertEqual((self.program/'main.py').read_bytes(),b'old program')
    def test_failed_stop_restores_owned_task_state_without_program_replacement(self):
        native=FakeOS(self.data,stop_fail=True)
        with self.assertRaisesRegex(ValueError,'process_remains'):life.update_server(self.package,self.program,self.journal,native)
        self.assertIn('restore_owned',native.events);self.assertTrue(native.paused)
        self.assertEqual((self.program/'main.py').read_bytes(),b'old program')
    def test_source_package_change_after_validation_does_not_activate(self):
        native=FakeOS(self.data);expected=life.tree(self.package);(self.package/'main.py').write_bytes(b'changed after signature')
        with self.assertRaisesRegex(life.LifecycleError,'source_package_changed'):
            life.update_server(self.package,self.program,self.journal,native,expected_files=expected)
        self.assertEqual(native.events,[]);self.assertFalse(self.journal.exists())
        self.assertEqual((self.program/'main.py').read_bytes(),b'old program')

    def test_changed_backup_never_overwrites_program(self):
        backup=self.root/'backup';expected=life.backup_program(self.program,backup);(backup/'main.py').write_bytes(b'tampered')
        with self.assertRaisesRegex(life.LifecycleError,'program_backup_changed'):life.restore_program(self.program,backup,expected)
        self.assertEqual((self.program/'main.py').read_bytes(),b'old program')
    def test_payload_tamper_and_test_installer_refuse_before_native_actions(self):
        payload=self.root/'payload';(payload/'package/runtime').mkdir(parents=True);exe=payload/'package/runtime/python.exe';exe.write_bytes(b'synthetic')
        manifest=payload/'installer-payload.json';manifest.write_text(json.dumps({'schema':1,'role':'server','version':'2.0.0','test_artifact':True,'files':{'package/runtime/python.exe':life.digest(exe)}}))
        expected=life.digest(manifest)
        with patch.object(life,'NativeWindows') as native:
            self.assertEqual(life.run(payload,expected,'install')['status'],'validated_only')
            with self.assertRaisesRegex(life.LifecycleError,'test_artifact_installation_forbidden'):life.run(payload,expected,'install',apply=True)
            native.assert_not_called()
        exe.write_bytes(b'tampered')
        with self.assertRaisesRegex(life.LifecycleError,'payload_file_hash_mismatch'):life.run(payload,expected,'update')
    def test_role_privilege_separation_is_fixed_in_recipe(self):
        recipe=(source_path('windows/inno/Makervaerkstedet.iss', ROOT)).read_text()
        self.assertIn('PrivilegesRequired=admin',recipe);self.assertIn('PrivilegesRequired=lowest',recipe)
        self.assertNotIn('PrivilegesRequiredOverridesAllowed',recipe)
        source=(source_path('windows/inno/lifecycle.py', ROOT)).read_text()
        self.assertNotIn('assert_idle(',source)
        # The hold is lifted in exactly one place, never the queue's pause flag.
        self.assertEqual(source.count('.release('),1);self.assertIn("self.barrier.release('server')",source)
class OperationIdTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.payload=self.root/'payload'
        self.native=Mock(program=self.root/'program',state=self.root/'state')
        self.native.program.mkdir()
        self.marker=self.native.program/'runtime-release.json'
        self.marker.write_text(json.dumps({'schema':1,'role':'server','release_id':'2.1.0-20261117.3'}))
        self.value={'role':'server','version':'2.1.0-20261117.4','test_artifact':False,'files':{}}
        self.verify=self.enterContext(patch.object(life,'verify_payload',return_value=self.value))
        self.constructor=self.enterContext(patch.object(life,'NativeWindows',return_value=self.native))
        self.enterContext(patch.object(life,'WindowsInstallLock',return_value=nullcontext()))
        self.detect=self.enterContext(patch.object(life,'detect_action',return_value='update'))
        def update(package,program,journal,native,**kwargs):
            journal.mkdir(parents=True)
            return {'status':'complete_paused','operation_id':journal.name}
        self.update=self.enterContext(patch.object(life,'update_server',side_effect=update))
        self.identifier='11111111-2222-3333-4444-555555555555'

    def test_explicit_operation_binds_journal_and_refuses_reuse(self):
        with patch.object(life.uuid,'uuid4') as random_id:
            result=life.run(self.payload,'a'*64,'auto',apply=True,operation_id=self.identifier)
            self.assertEqual(result['operation_id'],self.identifier)
            self.assertEqual(self.update.call_args.args[2],self.native.state/'installer-updates'/self.identifier)
            with self.assertRaisesRegex(life.LifecycleError,'operation_journal_exists'):
                life.run(self.payload,'a'*64,'auto',apply=True,operation_id=self.identifier)
            random_id.assert_not_called()
        self.assertEqual(self.update.call_count,1)

    def test_invalid_operation_id_is_rejected_before_payload_or_native_actions(self):
        for identifier in ('', '00000000-0000-0000-0000-000000000000',
                           'AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE',
                           '{'+self.identifier+'}',self.identifier.replace('-',''),self.identifier+';whoami'):
            with self.subTest(identifier=identifier),self.assertRaisesRegex(life.LifecycleError,'invalid_operation_id'):
                life.run(self.payload,'a'*64,'auto',apply=True,operation_id=identifier)
        self.verify.assert_not_called();self.constructor.assert_not_called();self.update.assert_not_called()

    def test_operation_id_is_only_for_server_update(self):
        for action in ('install','resume'):
            with self.subTest(action=action),self.assertRaisesRegex(life.LifecycleError,'operation_id_requires_server_update'):
                life.run(self.payload,'a'*64,action,apply=True,operation_id=self.identifier)
        self.value['role']='laser_station'
        with self.assertRaisesRegex(life.LifecycleError,'operation_id_requires_server_update'):
            life.run(self.payload,'a'*64,'auto',apply=True,operation_id=self.identifier)
        self.constructor.assert_not_called();self.update.assert_not_called()

    def test_auto_install_does_not_use_operation_id(self):
        self.detect.return_value='install'
        with self.assertRaisesRegex(life.LifecycleError,'operation_id_requires_server_update'):
            life.run(self.payload,'a'*64,'auto',apply=True,operation_id=self.identifier)
        self.native.install_server.assert_not_called();self.update.assert_not_called()

    def test_manual_update_keeps_random_operation_id(self):
        self.marker.unlink()  # The manual installer's prior contract is unchanged.
        identifier=uuid.UUID('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')
        with patch.object(life.uuid,'uuid4',return_value=identifier) as random_id:
            result=life.run(self.payload,'a'*64,'auto',apply=True)
        random_id.assert_called_once_with()
        self.assertEqual(result['operation_id'],str(identifier))

    def test_admin_update_rechecks_newer_version_under_installation_lock(self):
        for installed in ('2.1.0-20261117.4','2.1.0-20261117.5','2.1.1-20261101.1'):
            self.marker.write_text(json.dumps({'schema':1,'role':'server','release_id':installed}))
            with self.subTest(installed=installed),self.assertRaisesRegex(life.LifecycleError,'server_update_version_not_newer'):
                life.run(self.payload,'a'*64,'auto',apply=True,operation_id=self.identifier)
        self.update.assert_not_called()
        self.assertFalse((self.native.state/'installer-updates').exists())

    def test_unknown_installed_version_refuses_before_journal_or_pause(self):
        for marker in (None,{}, {'schema':1,'role':'server','release_id':'unknown'}):
            if marker is None:self.marker.unlink(missing_ok=True)
            else:self.marker.write_text(json.dumps(marker))
            with self.subTest(marker=marker),self.assertRaisesRegex(life.LifecycleError,'installed_server_version_unknown'):
                life.run(self.payload,'a'*64,'auto',apply=True,operation_id=self.identifier)
        self.update.assert_not_called()
        self.assertFalse((self.native.state/'installer-updates').exists())

    def test_numeric_version_check_runs_while_installer_lock_is_held(self):
        self.value['version']='2.1.0-20261117.10'
        self.marker.write_text(json.dumps({'schema':1,'role':'server','release_id':'2.1.0-20261117.9'}))
        held=[]
        @contextmanager
        def lock(name):
            held.append(name)
            try:yield
            finally:held.pop()
        original=life.runtime_identity
        def identity(program,role):
            self.assertTrue(held)
            return original(program,role)
        with patch.object(life,'WindowsInstallLock',side_effect=lock),patch.object(life,'runtime_identity',side_effect=identity) as checked:
            self.assertEqual(life.run(self.payload,'a'*64,'auto',apply=True,operation_id=self.identifier)['operation_id'],self.identifier)
        checked.assert_called_once_with(self.native.program,'server')
        self.assertEqual(held,[])


class BuilderInputTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        compiler=self.root/'compiler';compiler.mkdir();(compiler/'ISCC.exe').write_bytes(b'synthetic compiler; never executed')
        package=self.root/'package';package.mkdir();self.package=package
        required={'runtime/python.exe','runtime/python314.dll','runtime/python314.zip','runtime/python314._pth','elevhub-package.json','school-package.json','makerspace-release.json','integrity.json','CREDITS.md','server/install.ps1','server/public-apt-cache-acl.ps1','server/verify.py','deployment/school-setup/Setup-School.ps1','deployment/school-setup/enrollment.py','hub/konfiguration.windows.json','hub/simple_updates.py','app/simple_release.py'}
        required |= {'server/server-update.ps1','server/server_update_worker.py','server/server-update-lifecycle.ps1','hub/server_update.py','hub/web/server-update.js'}
        from app.laser_station_install import REQUIRED as station_required
        required |= station_required | {'deployment/school-setup/Setup-LaserStation.ps1'}
        for name in required:
            file=package/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(b'synthetic fixture')
        (package/'elevhub-package.json').write_text(json.dumps({'requiresGenerationLayout':False}),encoding='utf-8')
        # Keep the real model3d receipt and all hash-bound runtime assets intact.
        receipt=source_path('web/vendor/model3d-vendor-manifest.json', ROOT)
        model3d=json.loads(receipt.read_text(encoding='utf-8'))
        names=[Path('web/vendor/model3d-vendor-manifest.json'),Path('web/assets/model3d/Metrophobic-Regular.ttf')]
        names += [Path('web/vendor')/name for name in model3d['outputs']]
        names += [Path('web/assets/licenses/model3d')/name for name in model3d['license_sha256']]
        for name in names:
            target=package/name;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(source_path(name.as_posix(), ROOT).read_bytes())
        verifier=self.root/'minisign.exe';verifier.write_bytes(b'synthetic verifier; never executed')
        license_file=self.root/'LICENSE';license_file.write_bytes(b'synthetic license fixture')
        provenance=self.root/'provenance.json';provenance.write_text('{"fixture":true}')
        self.value={'schema':1,'role':'server','version':'2.0.0','windows_version':'2.0.0.0','compiler_root':str(compiler),
            'compiler_files':{'ISCC.exe':build.sha(compiler/'ISCC.exe')},'package_root':str(package),
            'files':{path.relative_to(package).as_posix():build.sha(path) for path in package.rglob('*') if path.is_file()},'public_review':True,
            'release_trust':{'minisign_exe':str(verifier),'minisign_sha256':build.sha(verifier),'public_key':base64.b64encode(b'Ed'+b'0'*40).decode(),'license_file':str(license_file),'license_sha256':build.sha(license_file),'provenance_file':str(provenance),'provenance_sha256':build.sha(provenance)}}
    def test_full_hash_inventory_required_before_any_compiler_run(self):
        build.validate(self.value,test_artifact=True)
        (self.package/'runtime/python.exe').write_bytes(b'tampered')
        with patch.object(build.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'input_hash_mismatch'):build.build(self.value,self.root/'output',test_artifact=True)
            run.assert_not_called()
        self.assertFalse((self.root/'output').exists())
    def test_server_package_requires_real_cache_acl_helper(self):
        helper=self.package/'server/public-apt-cache-acl.ps1'
        helper.unlink();del self.value['files']['server/public-apt-cache-acl.ps1']
        with self.assertRaisesRegex(ValueError,'complete_role_runtime_required'):
            build.validate(self.value,test_artifact=True)
    def test_both_exe_roles_require_complete_station_assets_before_build(self):
        # Server EXE also offers the all-in-one role, which starts a local station.
        for role in ('server','laser_station'):
            with self.subTest(role=role):
                self.value['role']=role
                build.validate(self.value,test_artifact=True)
                css=self.package/'web/laser.css';original=css.read_bytes()
                css.unlink();del self.value['files']['web/laser.css']
                try:
                    with self.assertRaisesRegex(ValueError,'complete_role_runtime_required'):
                        build.validate(self.value,test_artifact=True)
                finally:
                    css.write_bytes(original);self.value['files']['web/laser.css']=build.sha(css)
    def test_fresh_package_gets_role_identity_during_staging_for_both_roles(self):
        # The documented school producer has no role-specific runtime identity.
        # Inno staging creates it after validating the producer's input files.
        identity=self.package/'runtime-release.json';identity.unlink()
        del self.value['files']['runtime-release.json']
        config=self.package/'hub/konfiguration.windows.json'
        config.write_text(json.dumps({'apps':[]}),encoding='utf-8')
        self.value['files']['hub/konfiguration.windows.json']=build.sha(config)
        def compile_fixture(arguments,**kwargs):
            out=Path(next(arg.split('=',1)[1] for arg in arguments if arg.startswith('/DOutputRoot=')))
            label=next(arg.split('=',1)[1] for arg in arguments if arg.startswith('/DOutputName='))
            (out/(label+'.exe')).write_bytes(b'synthetic compiler result; never executed')
        from app.laser_station_install import REQUIRED as station_required
        for role in ('server','laser_station'):
            with self.subTest(role=role):
                self.value['role']=role
                payload=self.root/(role+'-payload')
                with patch.object(build.subprocess,'run',side_effect=compile_fixture):
                    build.build(self.value,self.root/(role+'-output'),test_artifact=True,payload_output=payload)
                staged=payload/'package'
                self.assertEqual(json.loads((staged/'runtime-release.json').read_text()),
                                 {'schema':1,'role':role,'release_id':self.value['version']})
                self.assertTrue(all((staged/name).is_file() for name in station_required))
    def test_server_exe_rejects_generation_only_or_invalid_layout_before_build(self):
        marker=self.package/'elevhub-package.json'
        for flag in (True,'true',1,None):
            with self.subTest(flag=flag):
                marker.write_text(json.dumps({'requiresGenerationLayout':flag}),encoding='utf-8')
                self.value['files']['elevhub-package.json']=build.sha(marker)
                with self.assertRaisesRegex(ValueError,'server_installer_requires_flat_layout'):
                    build.validate(self.value,test_artifact=True)
        for compatible in ({'requiresGenerationLayout':False},{}):
            marker.write_text(json.dumps(compatible),encoding='utf-8')
            self.value['files']['elevhub-package.json']=build.sha(marker)
            build.validate(self.value,test_artifact=True)
    def test_modified_or_added_source_during_copy_cannot_be_resealed(self):
        original_copytree=build.shutil.copytree
        for mutation in ('modify','add'):
            target=self.root/('stage-'+mutation)
            def changed_copy(source,destination,*args,**kwargs):
                if Path(source)!=self.package:return original_copytree(source,destination,*args,**kwargs)
                if mutation=='modify':(self.package/'runtime/python.exe').write_bytes(b'changed during copy')
                else:(self.package/'added-after-validation.py').write_bytes(b'undeclared')
                return original_copytree(source,destination,*args,**kwargs)
            with patch.object(build.shutil,'copytree',side_effect=changed_copy):
                with self.assertRaisesRegex(ValueError,'input_hash_mismatch|inventory_mismatch'):
                    build.stage_verified(self.package,target,self.value['files'])
            (self.package/'runtime/python.exe').write_bytes(b'synthetic fixture')
            (self.package/'added-after-validation.py').unlink(missing_ok=True)
    def test_modified_compiler_copy_is_rejected_against_original_tool_pins(self):
        source=Path(self.value['compiler_root']);original_copytree=build.shutil.copytree
        def changed_copy(original,destination,**kwargs):
            (source/'ISCC.exe').write_bytes(b'modified compiler')
            return original_copytree(original,destination,**kwargs)
        with patch.object(build.shutil,'copytree',side_effect=changed_copy):
            with self.assertRaisesRegex(ValueError,'input_hash_mismatch'):
                build.stage_verified(source,self.root/'staged-compiler',self.value['compiler_files'])

    def test_separate_trust_files_are_bound_to_original_pins_after_copy(self):
        original_copy=build.shutil.copyfile
        for name in ('minisign','license','provenance'):
            field='minisign_exe' if name=='minisign' else name+'_file'
            pin='minisign_sha256' if name=='minisign' else name+'_sha256'
            source=Path(self.value['release_trust'][field]);original=source.read_bytes()
            def changed_copy(original_source,destination):
                source.write_bytes(b'changed between validate and copy')
                return original_copy(original_source,destination)
            with patch.object(build.shutil,'copyfile',side_effect=changed_copy):
                with self.assertRaisesRegex(ValueError,'copied_release_trust_hash_mismatch'):
                    build.copy_file_verified(source,self.root/('copied-'+name),self.value['release_trust'][pin])
            source.write_bytes(original)

    def test_nonfixture_build_rejects_fake_runtime(self):
        with self.assertRaisesRegex(ValueError,'invalid_python_executable'):build.validate(self.value)
    def test_generic_package_never_contains_private_identity_or_retired_java(self):
        for name,error in [('device.key','private_state'),('school-setup-state.json','private_state'),('runtime/jre21/java.exe','removed_native_3d_runtime')]:
            path=self.package/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'synthetic');self.value['files'][name]=build.sha(path)
            with self.assertRaisesRegex(ValueError,error):build.validate(self.value,test_artifact=True)
            path.unlink();del self.value['files'][name]
    def test_windows_payload_keeps_program_helpers_and_excludes_unit_modules(self):
        spec=importlib.util.spec_from_file_location('windows_distribution_builder',source_path('windows-elevhub/build_packages.py', ROOT))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        names=['test_release_import.py','test_retired_apps.py','release_import.py','apps.py','core.py']
        ignored=module.source_ignore(str(source_path('linux-elevhub/server/elevhub', ROOT)),names)
        self.assertTrue({'test_release_import.py','test_retired_apps.py'}<=ignored)
        self.assertFalse({'release_import.py','apps.py','core.py'}&ignored)

    def test_windows_neutral_template_no_longer_requests_cadoodle(self):
        config=json.loads((source_path('windows-elevhub/server/hub/konfiguration.windows.json', ROOT)).read_text(encoding='utf-8'))
        self.assertFalse(any(item.get('id')=='cadoodle' or item.get('native_app')=='cadoodle' for item in config['apps']))

if __name__=='__main__':unittest.main()
