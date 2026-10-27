"""Clean source + explicit external fixtures exercise every role builder.

No installed programs, school data, large archives, or network access are used.
"""
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import build_github_release as builder
from scripts import build_update_manifest as verifier


class GitHubReleasePlanTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';self.source.mkdir()
        self.output=self.root/'publisher';self.external=self.root/'external';self.external.mkdir()
        self.write('CREDITS.md',(verifier.public_source.AUTHOR_CREDIT+"\n"+verifier.public_source.CREDIT))
        self.write('LICENSE','Fixture license');self.write('LICENSE-SCOPE.md','Fixture scope')
        self.write('app/laser_station_install.py',
            "APP_FILES=tuple('app/'+name for name in ('laser_station.py','instance.py','backup_guard.py'))\n"
            "WEB_FILES=tuple('web/'+name for name in ('laserstation.html',))\n")
        for name in ('laser_station.py','instance.py','backup_guard.py'):self.write('app/'+name,'value=1\n')
        self.write('web/laserstation.html','<html>Fixture</html>')
        self.write('app/school_appearance.py','LOCAL_APPEARANCE_VERSION = 1\n')
        self.write('app/server.py','pass\n')
        self.write('app/laser_server.py','pass\n')
        self.write('web/school-theme.js','pass\n')
        self.write('linux-elevhub/server/elevhub/elevhub.py','LOCAL_APPEARANCE_VERSION = 1\n')
        self.write('linux-elevhub/server/elevhub/school_appearance.py','LOCAL_APPEARANCE_VERSION = 1\n')
        dirs={'roles','projekter','kredsloeb','microbit-controller','assets','matchbox-source'}
        for name in builder.LINUX_PATHS:
            if name=='CREDITS.md':continue
            path=Path(name)
            if path.name in dirs:self.write(name+'/README.md','Fixture software')
            elif path.suffix=='.json':self.write(name,json.dumps({'release_id':'2026.10.13','expected_client_release':'2026.10.13'}))
            else:self.write(name,'# Fixture software\n')
        self.write('linux-elevhub/ansible/roles/kiosk/tasks/main.yml',
                   '    kiosk_lokal_design_filer:\n      - app/laser_station.py\n  block:\n')
        self.write('linux-elevhub/ansible/roles/cadoodle/tasks/main.yml','# Fixture role\n')
        self.write('third_party/boxes.py/boxes/__init__.py','# Fixture library\n')
        self.write('windows-elevhub/server/hub/konfiguration.windows.json','{}')
        self.write('scripts/school_server_update/update.py',(builder.ROOT/'scripts/school_server_update/update.py').read_text(encoding='utf-8'))
        self.policy()
        self.manifest=self.root/'build-input-manifest.json'

    def write(self,name,text):
        path=self.source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')

    def policy(self):
        path=self.source/'deployment/public-source-manifest.json';path.parent.mkdir(exist_ok=True)
        files=sorted(p.relative_to(self.source).as_posix() for p in self.source.rglob('*') if p.is_file() and p!=path)
        path.write_text(json.dumps({'schemaVersion':1,'project':'Makerværkstedet','credit':verifier.public_source.CREDIT,
            'sourceRoots':[],'explicitFiles':files,'includeExtensions':['.md','.py','.json','.html','.yml'],
            'requiredDocs':['CREDITS.md']}),encoding='utf-8')

    def spec(self,roles=('windows_laser_teacher',)):
        packages={};pins=[]
        def item(path):
            if path.is_dir():
                h=hashlib.sha256()
                for child in sorted(path.rglob('*')):
                    if child.is_file():
                        name=child.relative_to(path).as_posix().encode();data=child.read_bytes()
                        h.update(len(name).to_bytes(8,'big'));h.update(name);h.update(len(data).to_bytes(8,'big'));h.update(data)
                digest=h.hexdigest()
            else:digest=builder._sha256(path)
            return {'path':str(path),'sha256':digest,'public_review':'Synthetic neutral test input, no school data.'}
        if 'linux_student' in roles:
            external={}
            for name in ('deb','bootstrap','kloster','matchbox'):
                path=self.external/name;path.write_bytes(b'opaque fixture '+name.encode());pins.append(builder._sha256(path));external[name]=item(path)
            for name in ('scratch','datatraener'):
                path=self.external/name;path.mkdir(exist_ok=True);(path/'index.html').write_text('<html>Local app</html>')
                external[name]=item(path)
            packages['linux_student']={'external':external}
        if 'windows_server' in roles:
            trusted=self.external/'trusted';runtime=trusted/'runtime';runtime.mkdir(parents=True,exist_ok=True)
            for name in ('python.exe','python314.dll','python314.zip'):
                path=runtime/name
                if name.endswith('.zip'):
                    with zipfile.ZipFile(path,'w') as archive:
                        archive.writestr(zipfile.ZipInfo('fixture.py',date_time=(1980,1,1,0,0,0)),b'# fixture stdlib\n')
                else:path.write_bytes(b'fixture runtime '+name.encode())
                pins.append(builder._sha256(path))
            (trusted/'integrity.json').write_text(json.dumps({'files':{'runtime/'+p.name:builder._sha256(p) for p in runtime.iterdir()}}))
            packages['windows_server']={'trusted_runtime':item(trusted)}
        if 'windows_laser_teacher' in roles:packages['windows_laser_teacher']={}
        value={'schema':1,'packages':packages,'approved_binary_sha256':pins}
        self.manifest.write_text(json.dumps(value),encoding='utf-8');return value

    def build(self,output=None,version='2026.10.21',scope='selected'):
        return builder.build_release(self.source,'mdkmsuv/makervaerkstedet',version,'Neutral release notes.',[],
                                     output or self.output,input_manifest=self.manifest,scope=scope)

    def test_clean_source_builds_all_role_archives_with_bound_versions(self):
        self.spec(tuple(verifier.feed.ROLES));plan=self.build(scope='system')
        self.assertEqual(plan['scope'],'system')
        self.assertEqual({row['role'] for row in plan['assets']},set(verifier.feed.ROLES))
        for row in plan['assets']:
            path=self.output/row['asset'];self.assertTrue(path.is_file());self.assertEqual(builder._sha256(path),row['sha256'])
        with zipfile.ZipFile(self.output/'linux-update.zip') as archive:
            with archive.open('payload.tar.gz') as payload,tarfile.open(fileobj=payload,mode='r|gz') as inner:
                files={entry.name:inner.extractfile(entry).read() for entry in inner if entry.isfile()}
            self.assertEqual(json.loads(files['linux-elevhub/server/elevhub/release.json'])['release_id'],'2026.10.21')
            self.assertEqual(json.loads(files['manifest.json'])['version'],'2026.10.21')
        with zipfile.ZipFile(self.output/'server-update.zip') as archive:
            candidate=json.loads(archive.read('candidate/candidate.json'));self.assertEqual(candidate['version'],'2026.10.21')
            self.assertEqual(json.loads(archive.read('candidate/hub/hub/release.json'))['release_id'],'2026.10.21')
        with zipfile.ZipFile(self.output/'station-code.zip') as archive:
            self.assertEqual(json.loads(archive.read('manifest.json'))['release'],'2026.10.21')
        self.assertEqual(json.loads((self.source/'linux-elevhub/server/elevhub/release.json').read_text())['release_id'],'2026.10.13')

    def test_same_inputs_generate_byte_identical_assets_for_all_roles(self):
        self.spec(tuple(verifier.feed.ROLES));first=self.build();second=self.build(self.root/'again')
        self.assertEqual(first,second)
        for row in first['assets']:
            self.assertEqual((self.output/row['asset']).read_bytes(),(self.root/'again'/row['asset']).read_bytes())

    def test_system_release_rejects_each_missing_role_before_writing_outputs(self):
        roles=set(verifier.feed.ROLES)
        for missing in sorted(roles):
            with self.subTest(missing=missing):
                self.spec(tuple(roles-{missing}))
                with self.assertRaisesRegex(ValueError,'system_release_requires_all_roles'):
                    self.build(scope='system')
                self.assertFalse(self.output.exists())

    def test_prebuilt_partial_release_requires_explicit_selected_scope(self):
        self.spec()
        packages=builder.build_packages(self.source,'2026.10.21',self.manifest,self.root/'packages',scope='selected')
        with self.assertRaisesRegex(ValueError,'system_release_requires_all_roles'):
            builder.build_release(self.source,'mdkmsuv/makervaerkstedet','2026.10.21','Fixture',packages,self.output)
        self.assertFalse(self.output.exists())
        plan=builder.build_release(self.source,'mdkmsuv/makervaerkstedet','2026.10.21','Fixture',packages,self.output,scope='selected')
        self.assertEqual(plan['scope'],'selected')
        self.assertEqual([row['role'] for row in plan['assets']],['windows_laser_teacher'])

    def test_unallowlisted_local_source_never_enters_server_package(self):
        self.spec(('windows_server',));self.write('app/not-reviewed.py','unreviewed=1\n')
        self.build()
        with zipfile.ZipFile(self.output/'server-update.zip') as archive:
            self.assertFalse(any('not-reviewed' in name for name in archive.namelist()))

    def test_opaque_external_without_independent_review_pin_fails(self):
        value=self.spec(('linux_student',));value['approved_binary_sha256']=[]
        self.manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'opaque_binary_requires_public_review'):self.build()
        self.assertFalse((self.output/'release-plan.json').exists())

    def test_private_text_inside_linux_payload_is_not_hidden_by_outer_pin(self):
        value=self.spec(('linux_student',))
        # Add a private text file only after the source export policy was fixed.
        path=self.external/'scratch'/'settings.json';path.write_text(json.dumps({'pass'+'word':'blocked'}))
        # Refresh the explicitly selected directory digest without blessing its text.
        self.spec(('linux_student',))
        packages=builder.build_packages(self.source,'2026.10.21',self.manifest,self.root/'packages',scope='selected')
        with zipfile.ZipFile(packages[0]['path']) as archive:outer_pin=hashlib.sha256(archive.read('payload.tar.gz')).hexdigest()
        with self.assertRaisesRegex(ValueError,'package_privacy_preflight_failed'):
            builder.build_release(self.source,'mdkmsuv/makervaerkstedet','2026.10.21','Fixture',packages,self.output,
                                  approved_binary_sha256=[outer_pin],scope='selected')

    def test_tampered_inner_server_version_fails_even_with_fresh_outer_hashes(self):
        self.spec(('windows_server',));packages=builder.build_packages(self.source,'2026.10.21',self.manifest,self.root/'packages',scope='selected')
        path=packages[0]['path']
        with zipfile.ZipFile(path) as archive:files={name:archive.read(name) for name in archive.namelist()}
        name='candidate/candidate.json';candidate=json.loads(files[name]);candidate['version']='2026.10.22'
        files[name]=json.dumps(candidate).encode();marker=json.loads(files['update-package.json'])
        marker['files'][name]=hashlib.sha256(files[name]).hexdigest();files['update-package.json']=json.dumps(marker).encode()
        with zipfile.ZipFile(path,'w') as archive:
            for name,data in files.items():archive.writestr(name,data)
        packages[0]['expected_sha256']=builder._sha256(path)
        with self.assertRaisesRegex(ValueError,'server_candidate_version_format_mismatch'):
            builder.build_release(self.source,'mdkmsuv/makervaerkstedet','2026.10.21','Fixture',packages,self.output,scope='selected')

    def test_wrong_external_hash_missing_matchbox_or_review_fail(self):
        value=self.spec(('linux_student',));value['packages']['linux_student']['external']['deb']['sha256']='0'*64
        self.manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'build_input_hash_mismatch'):self.build()
        value=self.spec(('linux_student',));del value['packages']['linux_student']['external']['matchbox']
        self.manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'linux_external_inputs_incomplete'):self.build()
        value=self.spec(('linux_student',));del value['packages']['linux_student']['external']['deb']['public_review']
        self.manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'explicit_public_input_review_required'):self.build()

    def test_streamed_assets_do_not_use_read_bytes_for_tar_or_runtime(self):
        self.spec(tuple(verifier.feed.ROLES));original=Path.read_bytes
        def guard(path):
            if path.name in {'payload.tar.gz','python.exe','python314.zip','python314.dll'}:raise AssertionError('large member read into memory')
            return original(path)
        with patch.object(Path,'read_bytes',guard):self.build()

    def test_cli_can_build_from_clean_source_with_no_hidden_imports(self):
        self.spec();notes=self.root/'notes.txt';notes.write_text('CLI fixture')
        command=[sys.executable,'-B',str(builder.ROOT/'scripts/build_github_release.py'),'--source-root',str(self.source),
                 '--repository','mdkmsuv/makervaerkstedet','--version','2026.10.21','--notes',str(notes),
                 '--output-dir',str(self.output),'--build-input-manifest',str(self.manifest)]
        result=subprocess.run(command,cwd=self.root,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('system_release_requires_all_roles',result.stderr)
        self.assertFalse(self.output.exists())
        command+=['--scope','selected']
        result=subprocess.run(command,cwd=self.root,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr);self.assertTrue((self.output/'station-code.zip').is_file())

    def test_copied_prebuilt_assets_are_present_beside_manifest(self):
        self.spec();packages=builder.build_packages(self.source,'2026.10.21',self.manifest,self.root/'packages',scope='selected')
        plan=builder.build_release(self.source,'mdkmsuv/makervaerkstedet','2026.10.21','Fixture',packages,self.output,scope='selected')
        self.assertTrue((self.output/plan['assets'][0]['asset']).is_file())

    def test_outputs_are_not_overwritten_and_invalid_repository_rejected(self):
        self.spec();self.build()
        with self.assertRaisesRegex(FileExistsError,'publication_output_exists'):self.build()
        with self.assertRaisesRegex(ValueError,'invalid_repository'):
            builder.build_release(self.source,'https://github.com/example','2026.10.21','',[],self.root/'bad')

    def test_ci_output_inside_source_still_uses_isolated_export(self):
        self.spec();plan=self.build(self.source/'publisher')
        self.assertTrue((self.source/'publisher'/plan['assets'][0]['asset']).is_file())

    def test_opaque_member_larger_than_512_mib_is_streamed(self):
        class Repeated:
            def __init__(self):self.remaining=513*1024**2
            def read(self,count):
                if not 0<count<=1024**2:raise AssertionError('unbounded read')
                count=min(count,self.remaining);self.remaining-=count;return b'\0'*count
        h=hashlib.sha256();source=Repeated()
        for block in iter(lambda:source.read(1024**2),b''):h.update(block)
        self.assertEqual(verifier.review_stream('runtime/python.exe',Repeated(),513*1024**2,{}, {h.hexdigest()}),h.hexdigest())

    def test_pinned_text_still_receives_privacy_review(self):
        data=json.dumps({'pass'+'word':'blocked'}).encode()
        with self.assertRaisesRegex(ValueError,'package_privacy_preflight_failed'):
            verifier.review_stream('settings.json',io.BytesIO(data),len(data),{}, {hashlib.sha256(data).hexdigest()})

    def input_zip(self,members):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            for name,data in members.items():archive.writestr(name,data)
        return stream.getvalue()

    def fetch(self,data,sha=None,url=None):
        class Response(io.BytesIO):pass
        return builder.fetch_build_inputs(url or 'https://github.com/mdkmsuv/makervaerkstedet/releases/download/inputs/inputs.zip',
            sha or hashlib.sha256(data).hexdigest(),'mdkmsuv/makervaerkstedet',self.root/'download',
            opener=lambda *_:Response(data))

    def test_input_download_requires_repo_url_pin_safe_paths_and_manifest(self):
        data=self.input_zip({'build-input-manifest.json':json.dumps({'schema':1,'packages':{'windows_laser_teacher':{}}})})
        path=self.fetch(data);self.assertTrue(path.is_file())
        for url in ('http://github.com/mdkmsuv/makervaerkstedet/releases/download/v/x.zip',
                    'https://example.org/inputs.zip','https://github.com/other/repo/releases/download/v/x.zip'):
            with self.subTest(url=url),self.assertRaisesRegex(ValueError,'untrusted_build_inputs_url'):self.fetch(data,url=url)
        shutil.rmtree(self.root/'download')
        with self.assertRaisesRegex(ValueError,'build_inputs_hash_mismatch'):self.fetch(data,'0'*64)
        with self.assertRaisesRegex(ValueError,'unsafe_package_member'):self.fetch(self.input_zip({'../escape.py':b'x'}))
        with self.assertRaisesRegex(ValueError,'build_input_manifest_missing'):self.fetch(self.input_zip({'file.py':b'x'}))

    def test_public_input_bundle_cannot_reference_absolute_or_parent_paths(self):
        for name in ('../private','/private'):
            manifest={'schema':1,'packages':{'windows_server':{'trusted_runtime':{'path':name,'sha256':'0'*64,'public_review':'Fixture external public review.'}}}}
            with self.subTest(path=name),self.assertRaisesRegex(ValueError,'unsafe_package_member'):
                self.fetch(self.input_zip({'build-input-manifest.json':json.dumps(manifest)}))


if __name__=='__main__':unittest.main()
