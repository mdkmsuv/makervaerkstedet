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
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('inno_lifecycle',ROOT/'windows/inno/lifecycle.py')
life=importlib.util.module_from_spec(spec);spec.loader.exec_module(life)
spec=importlib.util.spec_from_file_location('inno_build',ROOT/'windows/inno/build.py')
build=importlib.util.module_from_spec(spec);spec.loader.exec_module(build)

class FakeOS:
    def __init__(self,data,*,health_fail=False,stop_fail=False,pause_fail=False):
        self.data=data;self.events=[];self.paused=False;self.jobs=['printing','dispatching','attention'];self.health_fail=health_fail;self.stop_fail=stop_fail;self.pause_fail=pause_fail
    def protect_journal(self,path):self.events.append('protect')
    def preflight_update(self,program,journal):self.events.append('preflight')
    def acquire_pause(self,operation):
        self.paused=True;self.holder=operation;self.events.append('pause')
        if self.pause_fail:raise ValueError('laser_endpoint_missing')
    def assert_pause(self,operation):
        assert self.paused and self.holder==operation;self.events.append('assert')
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
    def test_active_and_full_queues_do_not_block_update_and_pause_precedes_owned_stop(self):
        native=FakeOS(self.data);result=life.update_server(self.package,self.program,self.journal,native)
        self.assertEqual(result['status'],'complete_paused');self.assertTrue(native.paused)
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
        self.assertEqual(json.loads((self.journal/'state.json').read_bytes())['phase'],'program_restored_paused')
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
        recipe=(ROOT/'windows/inno/Makervaerkstedet.iss').read_text()
        self.assertIn('PrivilegesRequired=admin',recipe);self.assertIn('PrivilegesRequired=lowest',recipe)
        self.assertNotIn('PrivilegesRequiredOverridesAllowed',recipe)
        source=(ROOT/'windows/inno/lifecycle.py').read_text()
        self.assertNotIn('assert_idle(',source);self.assertNotIn('.release(',source)
class BuilderInputTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        compiler=self.root/'compiler';compiler.mkdir();(compiler/'ISCC.exe').write_bytes(b'synthetic compiler; never executed')
        package=self.root/'package';package.mkdir();self.package=package
        required={'runtime/python.exe','runtime/python314.dll','runtime/python314.zip','runtime/python314._pth','elevhub-package.json','school-package.json','makerspace-release.json','integrity.json','CREDITS.md','server/install.ps1','server/public-apt-cache-acl.ps1','server/verify.py','deployment/school-setup/Setup-School.ps1','deployment/school-setup/enrollment.py','hub/konfiguration.windows.json','hub/simple_updates.py','app/simple_release.py'}
        for name in required:
            file=package/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(b'synthetic fixture')
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
        for name,error in [('device.key','private_state'),('runtime/jre21/java.exe','removed_native_3d_runtime')]:
            path=self.package/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'synthetic');self.value['files'][name]=build.sha(path)
            with self.assertRaisesRegex(ValueError,error):build.validate(self.value,test_artifact=True)
            path.unlink();del self.value['files'][name]
    def test_windows_payload_keeps_program_helpers_and_excludes_unit_modules(self):
        spec=importlib.util.spec_from_file_location('windows_distribution_builder',ROOT/'windows-elevhub/build_packages.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        names=['test_release_import.py','test_retired_apps.py','release_import.py','apps.py','core.py']
        ignored=module.source_ignore(str(ROOT/'linux-elevhub/server/elevhub'),names)
        self.assertTrue({'test_release_import.py','test_retired_apps.py'}<=ignored)
        self.assertFalse({'release_import.py','apps.py','core.py'}&ignored)

    def test_windows_neutral_template_no_longer_requests_cadoodle(self):
        config=json.loads((ROOT/'windows-elevhub/server/hub/konfiguration.windows.json').read_text(encoding='utf-8'))
        self.assertFalse(any(item.get('id')=='cadoodle' or item.get('native_app')=='cadoodle' for item in config['apps']))

if __name__=='__main__':unittest.main()
