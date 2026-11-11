"""Source contract tests with injected CLI results; NOT minisign crypto evidence."""
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from app import simple_release as release

NOW=datetime(2026,11,4,tzinfo=timezone.utc)
KEY=base64.b64encode(b'Ed'+b'0'*40).decode()
class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.tool=self.root/'test-fixture.exe';self.tool.write_bytes(b'synthetic not executable')
        self.pin=hashlib.sha256(self.tool.read_bytes()).hexdigest()
        self.value={'schema':1,'repository':release.REPOSITORY,'role':'student','platform':'debian-amd64','component':'studentcore',
            'version':'2.0.0~20261104.1','sequence':7,'artifacts':[
              {'name':'makervaerkstedet-elev_2.0.0~20261104.1_all.deb','package':'makervaerkstedet-elev',
               'version':'2.0.0~20261104.1','architecture':'all','sha256':hashlib.sha256(b'package').hexdigest(),'size':7}]}
        self.kw={'minisign_exe':self.tool,'minisign_sha256':self.pin,'public_key':KEY,'repository':release.REPOSITORY,
            'role':'student','platform':'debian-amd64','component':'studentcore','now':NOW}
    def verify(self,value=None,raw=None):
        with patch.object(release,'_minisign'):
            return release.verify_manifest(raw if raw is not None else json.dumps(value or self.value).encode(),b'sig',**self.kw)
    def error(self,code,*,value=None,raw=None):
        with self.assertRaises(release.ReleaseVerificationError) as caught:self.verify(value=value,raw=raw)
        self.assertEqual(caught.exception.code,code)
    def test_retired_datatraener_component_is_rejected(self):
        self.kw['component'] = self.value['component'] = 'datatraener'
        self.value['artifacts'][0]['package'] = 'makervaerkstedet-datatraener'
        self.error('component_not_allowed')
        self.assertEqual(set(release.DEBIAN_COMPONENTS), {'studentcore', 'scratch', 'matchbox', 'updater'})

    def test_original_byte_hash_and_debian_version_are_preserved(self):
        raw=json.dumps(self.value,indent=3).encode();verified=self.verify(raw=raw)
        self.assertEqual(verified['_manifest_sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(verified['artifacts'][0]['version'],'2.0.0~20261104.1')
        self.assertEqual(release.release_scope(verified),(release.REPOSITORY,'student','debian-amd64','studentcore'))
    def test_duplicate_and_reserved_fields_rejected_at_any_depth(self):
        self.error('duplicate_json_key',raw=b'{"schema":1,"schema":1}')
        for key in ('_manifest_sha256','_verified'):
            value=deepcopy(self.value);value['artifacts'][0][key]='forged';self.error('reserved_field',value=value)
    def test_invalid_json_numbers_and_bounded_input_fail_with_fixed_codes(self):
        self.error('invalid_json_number',raw=b'{"schema":NaN}')
        self.error('invalid_manifest_json',raw=b'{"schema":'+b'1'*5000+b'}')
        with self.assertRaisesRegex(release.ReleaseVerificationError,'invalid_manifest_size'):
            release.verify_manifest(b'x'*(release.MAX_MANIFEST+1),b'sig',**self.kw)

    def test_scope_bindings_and_role_platform_are_enforced(self):
        for key,value,code in [('repository','evil/repo','repository_mismatch'),('role','unknown','role_mismatch'),
                              ('platform','ubuntu-amd64','platform_mismatch'),('component','different','component_mismatch')]:
            with self.subTest(key=key):changed=deepcopy(self.value);changed[key]=value;self.error(code,value=changed)
        self.kw.update(role='server');self.value['role']='server';self.error('role_platform_mismatch')
    def test_old_release_has_no_age_gate_and_expiry_is_not_a_schema_field(self):
        self.kw['now']=datetime(2099,1,1,tzinfo=timezone.utc)
        self.verify()
        self.value['expires_utc']='2026-12-01T00:00:00Z'
        self.error('invalid_manifest_fields')
    def test_empty_artifacts_boolean_sequence_and_extra_fields_rejected(self):
        for key,value,code in [('artifacts',[],'invalid_artifact_list'),('sequence',True,'invalid_sequence'),('schema',True,'unsupported_schema')]:
            changed=deepcopy(self.value);changed[key]=value;self.error(code,value=changed)
        self.value['url']='https://evil.invalid';self.error('invalid_manifest_fields')
    def test_unsafe_names_and_case_collisions_rejected(self):
        for name in ('../payload.deb','C:payload.deb','CON.deb','payload.deb.','https://bad/p.deb','a\\b.deb'):
            changed=deepcopy(self.value);changed['artifacts'][0]['name']=name;self.error('unsafe_artifact_name',value=changed)
        self.value['artifacts'].append(dict(self.value['artifacts'][0],name=self.value['artifacts'][0]['name'].upper()))
        self.error('duplicate_artifact_name')
    def test_debian_package_and_architecture_required(self):
        for key,value,code in [('package',None,'invalid_debian_package'),('architecture','arm64','invalid_debian_architecture')]:
            changed=deepcopy(self.value);changed['artifacts'][0][key]=value;self.error(code,value=changed)
    def test_debian_artifact_version_cannot_hide_a_downgrade(self):
        self.value['artifacts'][0]['version']='1.0.0';self.error('artifact_version_mismatch')

    def test_component_package_allowlist_blocks_os_packages(self):
        self.value['artifacts'][0]['package']='openssh-server';self.error('package_component_mismatch')
        self.kw['component']='arbitrary';self.value['component']='arbitrary';self.error('component_not_allowed')

    def test_updater_has_its_own_pinned_package_mapping(self):
        self.kw['component']='updater';self.value['component']='updater'
        self.value['artifacts'][0].update(package='makervaerkstedet-updater',architecture='amd64')
        self.verify()
        self.value['artifacts'][0]['architecture']='all';self.error('package_component_mismatch')

    def test_windows_allows_null_package_and_requires_amd64_exe(self):
        self.kw.update(role='server',platform='windows-amd64',component='server');self.value.update(role='server',platform='windows-amd64',component='server')
        self.value['artifacts'][0].update(name='MakervaerkstedetSetup.exe',package=None,architecture='amd64',version='2.0.0')
        self.verify()
        self.value['artifacts'][0]['package']='something';self.error('invalid_windows_artifact')
    def test_artifact_size_and_hash_fail_closed(self):
        path=self.root/'artifact';path.write_bytes(b'package');artifact=self.value['artifacts'][0]
        release.verify_artifact(path,artifact)
        path.write_bytes(b'tampered');
        with self.assertRaisesRegex(release.ReleaseVerificationError,'artifact_size_mismatch'):release.verify_artifact(path,artifact)
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(release.ReleaseVerificationError,'artifact_hash_mismatch'):release.verify_artifact(path,artifact)
    def test_high_water_is_scoped_and_never_decreases_on_explicit_rollback(self):
        verified=self.verify();scope=release.release_scope(verified);highest={scope:9,(release.REPOSITORY,'student','debian-amd64','other'):100}
        with self.assertRaisesRegex(release.ReleaseVerificationError,'rollback_authorization_required'):release.accept_sequence(verified,highest)
        with self.assertRaisesRegex(release.ReleaseVerificationError,'rollback_hash_mismatch'):release.accept_sequence(verified,highest,rollback_manifest_sha256='0'*64)
        self.assertEqual(release.accept_sequence(verified,highest,rollback_manifest_sha256=verified['_manifest_sha256']),9)
        self.assertEqual(release.accept_sequence(verified,{(release.REPOSITORY,'student','debian-amd64','other'):100}),7)
    def test_artifact_url_is_project_repository_only(self):
        verified=self.verify();url=release.artifact_url(verified,verified['artifacts'][0])
        self.assertTrue(url.startswith('https://github.com/mdkmsuv/makervaerkstedet/releases/download/v'))
        self.assertNotIn('evil',url)
    def test_cli_pin_is_mandatory_before_runner(self):
        raw=json.dumps(self.value).encode()
        with patch.object(release.subprocess,'run') as runner:
            self.kw['minisign_sha256']='0'*64
            with self.assertRaisesRegex(release.ReleaseVerificationError,'verifier_hash_mismatch'):
                release.verify_manifest(raw,b'sig',**self.kw)
            runner.assert_not_called()
    def test_source_only_cli_invocation_uses_private_pinned_copy_and_exact_bytes(self):
        raw=json.dumps(self.value,indent=4).encode()
        def fake_run(arguments,**kwargs):
            self.assertNotEqual(Path(arguments[0]),self.tool)
            self.assertEqual(Path(arguments[0]).read_bytes(),self.tool.read_bytes())
            self.assertEqual(Path(arguments[arguments.index('-m')+1]).read_bytes(),raw)
            self.assertEqual(Path(arguments[arguments.index('-x')+1]).read_bytes(),b'sig')
            self.assertFalse(kwargs['shell']);self.assertEqual(kwargs['timeout'],10)
            return subprocess.CompletedProcess(arguments,0)
        with patch.object(release.subprocess,'run',side_effect=fake_run):
            verified=release.verify_manifest(raw,b'sig',**self.kw)
        self.assertEqual(verified['_manifest_sha256'],hashlib.sha256(raw).hexdigest())
    def test_source_only_cli_rejection_timeout_and_absence_have_fixed_error_codes(self):
        raw=json.dumps(self.value).encode()
        for result,code in [(subprocess.CompletedProcess([],1),'invalid_signature'),(subprocess.TimeoutExpired('fixture',10),'verifier_unavailable')]:
            with patch.object(release.subprocess,'run',side_effect=result if isinstance(result,Exception) else None,return_value=result):
                with self.assertRaises(release.ReleaseVerificationError) as caught:release.verify_manifest(raw,b'sig',**self.kw)
                self.assertEqual(caught.exception.code,code)
        self.tool.unlink()
        with self.assertRaisesRegex(release.ReleaseVerificationError,'verifier_unavailable'):release.verify_manifest(raw,b'sig',**self.kw)
    def test_signature_must_pass_before_json_parsing(self):
        with patch.object(release.subprocess,'run',return_value=subprocess.CompletedProcess([],1)):
            with self.assertRaisesRegex(release.ReleaseVerificationError,'invalid_signature'):
                release.verify_manifest(b'not json',b'sig',**self.kw)
if __name__=='__main__':unittest.main()
