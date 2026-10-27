"""Small neutral ZIP fixtures; no release assets, installers or uploads."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import tarfile
import unittest
from unittest.mock import patch
import zipfile
from scripts import build_update_manifest as builder


class ManifestBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.source=self.root/'source';(self.source/'deployment').mkdir(parents=True)
        (self.source/'CREDITS.md').write_text((builder.public_source.AUTHOR_CREDIT+"\n"+builder.public_source.CREDIT),encoding='utf-8')
        policy={'schemaVersion':1,'project':'Makerværkstedet','credit':builder.public_source.CREDIT,'sourceRoots':[],
            'explicitFiles':['CREDITS.md'],'includeExtensions':['.md'],'requiredDocs':['CREDITS.md']}
        (self.source/'deployment/public-source-manifest.json').write_text(json.dumps(policy),encoding='utf-8')
        self.output=self.root/'makervaerkstedet-update.json'
    def archive(self,files=None,marker_changes=None,legacy=False):
        files=files or {'CREDITS.md':(builder.public_source.AUTHOR_CREDIT+"\n"+builder.public_source.CREDIT).encode(),'worker.py':b'x=1\n'}
        self.binary_pins=[]
        if not legacy:
            inner={
                'assets/CaDoodle.deb':b'neutral deb fixture',
                'assets/CaDoodle-ApplicationInstall.zip':b'neutral bootstrap fixture',
                'assets/CaDoodle-Kloster.jar':b'neutral jar fixture',
                'assets/scratch/index.html':b'<html></html>',
                'assets/datatraener/index.html':b'<html></html>',
                'linux-elevhub/ansible/roles/kiosk/tasks/main.yml':b'# Fixture role',
                'linux-elevhub/ansible/roles/cadoodle/tasks/main.yml':b'# Fixture role',
                'linux-elevhub/kiosk-window-guard/bin/matchbox-window-manager':b'neutral matchbox fixture',
                'linux-elevhub/server/elevhub/release.json':json.dumps({'release_id':'2026.10.20','expected_client_release':'2026.10.20'}).encode(),
                **files}
            self.binary_pins=[hashlib.sha256(inner[name]).hexdigest() for name in (
                'assets/CaDoodle.deb','assets/CaDoodle-ApplicationInstall.zip','assets/CaDoodle-Kloster.jar',
                'linux-elevhub/kiosk-window-guard/bin/matchbox-window-manager')]
            manifest={'version':'2026.10.20','files':{name:hashlib.sha256(data).hexdigest() for name,data in inner.items()}}
            inner['manifest.json']=json.dumps(manifest).encode();stream=io.BytesIO()
            with tarfile.open(fileobj=stream,mode='w:gz') as tar:
                for name,data in inner.items():
                    info=tarfile.TarInfo(name);info.size=len(data);tar.addfile(info,io.BytesIO(data))
            files={'CREDITS.md':(builder.public_source.AUTHOR_CREDIT+"\n"+builder.public_source.CREDIT).encode(),'payload.tar.gz':stream.getvalue()}
        marker={'schema':1,'role':'linux_student','format':'linux-queue-v1','version':'2026.10.20',
                'files':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}}
        marker.update(marker_changes or {})
        marker_name='update-package.json'
        if legacy:
            marker_name='manifest.json';marker={'format':'makervaerkstedet-station-code-v1','release':'2026.10.20',
                'files':{name:{'size':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in files.items()}}
        archive=self.root/'package.zip'
        with zipfile.ZipFile(archive,'w') as z:
            for name,data in files.items():z.writestr(name,data)
            z.writestr(marker_name,json.dumps(marker))
        return {'path':archive,'role':'windows_laser_teacher' if legacy else 'linux_student',
            'format':'laser-code-v1' if legacy else 'linux-queue-v1','expected_sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
    def build(self,package,**kwargs):
        kwargs.setdefault('approved_binary_sha256',self.binary_pins)
        return builder.build_manifest(self.source,'2026.10.20','Neutral release notes.',[package],self.output,**kwargs)
    def server_archive(self,expected_client_release='2026.10.20',release_id='2026.10.20'):
        credit=(builder.public_source.AUTHOR_CREDIT+"\n"+builder.public_source.CREDIT).encode()
        runtime={
            'runtime/python.exe':b'python executable fixture',
            'runtime/python314.dll':b'python dll fixture',
            'runtime/python314.zip':b'python stdlib fixture',
        }
        role_files={}
        trusted_runtime={name:hashlib.sha256(data).hexdigest() for name,data in runtime.items()}
        for role in ('hub','print'):
            role_files[role]={'CREDITS.md':credit,**runtime}
        role_files['hub']['hub/release.json']=json.dumps({
            'release_id':release_id,'expected_client_release':expected_client_release,
        }).encode()
        inventories={role:{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}
                     for role,files in role_files.items()}
        candidate={'format':'makervaerkstedet-school-flat-update-v1','version':'2026.10.20',
                   'files':inventories,'trusted_runtime':trusted_runtime}
        files={'CREDITS.md':credit,'candidate/candidate.json':json.dumps(candidate).encode()}
        for role,entries in role_files.items():
            files.update({f'candidate/{role}/{name}':data for name,data in entries.items()})
        marker={'schema':1,'role':'windows_server','format':'school-server-v1','version':'2026.10.20',
                'files':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}}
        archive=self.root/'server-package.zip'
        with zipfile.ZipFile(archive,'w') as z:
            for name,data in files.items():z.writestr(name,data)
            z.writestr('update-package.json',json.dumps(marker))
        self.binary_pins=list(trusted_runtime.values())
        return {'path':archive,'role':'windows_server','format':'school-server-v1',
                'expected_sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
    def test_valid_reviewed_zip_emits_feed_compatible_metadata_no_paths_or_upload(self):
        package=self.archive()
        with patch('urllib.request.urlopen',side_effect=AssertionError('Publication is never automatic')):manifest=self.build(package)
        self.assertEqual(json.loads(self.output.read_text(encoding='utf-8')),manifest)
        self.assertEqual(manifest['packages'][0]['sha256'],package['expected_sha256'])
        self.assertNotIn(str(self.root),self.output.read_text(encoding='utf-8'))
        with self.assertRaises(FileExistsError):self.build(package)
    def test_source_privacy_preflight_blocks_before_any_manifest_is_written(self):
        package=self.archive()
        # The scanner sees the generated sensitive JSON, never a real credential.
        (self.source/'CREDITS.md').write_text((builder.public_source.AUTHOR_CREDIT+"\n"+builder.public_source.CREDIT)+'\n'+json.dumps({'pass'+'word':'blocked'}))
        with self.assertRaises(builder.public_source.ExportError):self.build(package)
        self.assertFalse(self.output.exists())
    def test_package_cannot_omit_author_attribution(self):
        package=self.archive({'CREDITS.md':builder.public_source.CREDIT.encode(),'worker.py':b'x=1\n'})
        with self.assertRaisesRegex(ValueError,'package_credit_missing'):
            self.build(package)
        self.assertFalse(self.output.exists())

    def test_package_private_field_or_runtime_inventory_is_never_publishable(self):
        files={'CREDITS.md':(builder.public_source.AUTHOR_CREDIT+"\n"+builder.public_source.CREDIT).encode(),'settings.json':json.dumps({'pass'+'word':'blocked'}).encode()}
        with self.assertRaisesRegex(ValueError,'privacy_preflight'):self.build(self.archive(files))
        files={'CREDITS.md':(builder.public_source.AUTHOR_CREDIT+"\n"+builder.public_source.CREDIT).encode(),'known_hosts':b'fixture'}
        with self.assertRaisesRegex(ValueError,'runtime_or_identity'):self.build(self.archive(files))
        self.assertFalse(self.output.exists())
    def test_wrong_hash_corrupted_zip_or_inner_role_version_are_rejected(self):
        package=self.archive();package['expected_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'unverified_package'):self.build(package)
        for change in ({'role':'windows_server'},{'version':'2026.10.21'},{'format':'school-server-v1'}):
            with self.subTest(change=change),self.assertRaisesRegex(ValueError,'role_version'):self.build(self.archive(marker_changes=change))
        package=self.archive();package['path'].write_bytes(b'not a ZIP');package['expected_sha256']=hashlib.sha256(b'not a ZIP').hexdigest()
        with self.assertRaises(zipfile.BadZipFile):self.build(package)
    def test_traversal_and_unapproved_binary_members_fail_closed(self):
        for name,data in (('../escape.py',b'x=1\n'),('runtime/python.exe',b'opaque binary fixture')):
            files={'CREDITS.md':(builder.public_source.AUTHOR_CREDIT+"\n"+builder.public_source.CREDIT).encode(),name:data}
            with self.subTest(name=name),self.assertRaises(ValueError):self.build(self.archive(files))
        self.assertFalse(self.output.exists())
    def test_strict_legacy_laser_adapter_preserves_actual_format_contract(self):
        files={'CREDITS.md':(builder.public_source.AUTHOR_CREDIT+"\n"+builder.public_source.CREDIT).encode(),'app/laser_station.py':b'x=1\n',
            'app/instance.py':b'x=1\n','app/backup_guard.py':b'x=1\n','web/laserstation.html':b'<html></html>'}
        manifest=self.build(self.archive(files,legacy=True))
        self.assertEqual(manifest['packages'][0]['role'],'windows_laser_teacher')
        self.assertEqual(manifest['packages'][0]['format'],'laser-code-v1')

    def test_server_only_manifest_can_keep_older_expected_client_release(self):
        package=self.server_archive('2026.10.19')
        manifest=self.build(package)
        self.assertEqual(manifest['version'],'2026.10.20')
        self.assertEqual(manifest['packages'][0]['role'],'windows_server')

    def test_release_version_order_treats_missing_build_as_zero(self):
        self.assertEqual(builder._version_key('2026.10.20'),builder._version_key('2026.10.20.0'))
        self.assertLess(builder._version_key('2026.10.20.2'),builder._version_key('2026.10.20.10'))

    def test_server_only_manifest_rejects_future_or_invalid_client_release(self):
        for client_release in ('2026.10.21','not-a-version'):
            with self.subTest(client_release=client_release):
                with self.assertRaisesRegex(ValueError,'server_candidate_release_mismatch'):
                    self.build(self.server_archive(client_release))
                self.assertFalse(self.output.exists())

    def test_server_only_manifest_still_requires_server_release_id_to_match(self):
        package=self.server_archive('2026.10.19',release_id='2026.10.19')
        with self.assertRaisesRegex(ValueError,'server_candidate_release_mismatch'):
            self.build(package)
        self.assertFalse(self.output.exists())

    def test_direct_server_archive_review_still_requires_aligned_release_versions(self):
        package=self.server_archive('2026.10.19')
        with zipfile.ZipFile(package['path']) as archive:
            files=builder.feed.strict_json(archive.read('update-package.json'))['files']
            with self.assertRaisesRegex(ValueError,'server_candidate_release_mismatch'):
                builder.verify_server_inventory(archive,files,'2026.10.20')

    def test_full_system_manifest_still_requires_aligned_server_client_release(self):
        linux=self.archive();linux_pins=self.binary_pins[:]
        linux_path=self.root/'linux-package.zip';shutil.copyfile(linux['path'],linux_path);linux['path']=linux_path
        laser_files={'CREDITS.md':(builder.public_source.AUTHOR_CREDIT+"\n"+builder.public_source.CREDIT).encode(),
            'app/laser_station.py':b'x=1\n','app/instance.py':b'x=1\n','app/backup_guard.py':b'x=1\n',
            'web/laserstation.html':b'<html></html>'}
        laser=self.archive(laser_files,legacy=True)
        server=self.server_archive('2026.10.19')
        packages=[linux,laser,server]
        pins=linux_pins+self.binary_pins[:]
        with self.assertRaisesRegex(ValueError,'server_candidate_release_mismatch'):
            builder.build_manifest(self.source,'2026.10.20','Neutral release notes.',packages,
                                   self.output,approved_binary_sha256=pins)
        self.assertFalse(self.output.exists())
    def test_package_inventory_can_exceed_feed_metadata_bound(self):
        files={'CREDITS.md':(builder.public_source.AUTHOR_CREDIT+"\n"+builder.public_source.CREDIT).encode(),'app/laser_station.py':b'x=1\n',
            'app/instance.py':b'x=1\n','app/backup_guard.py':b'x=1\n','web/laserstation.html':b'<html></html>'}
        files.update({f'web/assets/part-{number:04}.js':b'const value=1;\n' for number in range(3000)})
        package=self.archive(files,legacy=True)
        with zipfile.ZipFile(package['path']) as archive:
            self.assertGreater(archive.getinfo('manifest.json').file_size,builder.feed.MAX_MANIFEST)
        self.assertEqual(self.build(package)['packages'][0]['role'],'windows_laser_teacher')

    def test_linux_payload_rejects_private_operational_modules(self):
        credit=(builder.public_source.AUTHOR_CREDIT+'\n'+builder.public_source.CREDIT).encode()
        for name in ('app/local_ai.py','app/plate_camera_client.py','web/camera-batch.js',
                     'web/plate-camera.js','app/laser_vision.py','runtime/torch/__init__.py'):
            with self.subTest(name=name):
                package=self.archive({'CREDITS.md':credit,name:b'# private feature fixture\n'})
                with self.assertRaisesRegex(ValueError,'operational_ai_member'):
                    self.build(package)
                self.assertFalse(self.output.exists())

    def test_prebuilt_server_and_laser_archives_reject_private_members(self):
        credit=(builder.public_source.AUTHOR_CREDIT+'\n'+builder.public_source.CREDIT).encode()
        for role,format_ in (('windows_server','school-server-v1'),('windows_laser_teacher','laser-code-v1')):
            for name in ('app/local_ai.py','app/plate_camera_client.py','web/camera-batch.js','web/plate-camera.js'):
                with self.subTest(role=role,name=name):
                    prefix='candidate/hub/' if role=='windows_server' else ''
                    package=self.archive({'CREDITS.md':credit,prefix+name:b'# private feature fixture\n'},legacy=True)
                    with self.assertRaisesRegex(ValueError,'operational_ai_member'):
                        builder.verify_zip(package['path'],role,format_,'2026.10.20',
                                           package['expected_sha256'],{},set())

    def test_reviewed_binary_pin_cannot_authorize_private_nested_runtime(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            archive.writestr('Lib/site-packages/torch/__init__.py','')
        data=stream.getvalue()
        with self.assertRaisesRegex(ValueError,'operational_ai_member'):
            builder.review_stream('candidate/hub/runtime/python314.zip',io.BytesIO(data),len(data),{},
                                  {hashlib.sha256(data).hexdigest()})

    def test_csharp_source_is_privacy_scanned_even_when_its_hash_is_pinned(self):
        data=b'class Fixture { string '+b'pass'+b'word = "sensitive-fixture-value"; }\n'
        with self.assertRaisesRegex(ValueError,'package_privacy_preflight_failed'):
            builder.review_stream('candidate/hub/server/ServerSetup.cs',io.BytesIO(data),len(data),{},
                                  {hashlib.sha256(data).hexdigest()})

    def test_windows_manifest_is_privacy_scanned_when_pinned(self):
        data=b'<assembly '+b'pass'+b'word="sensitive-fixture-value"/>'
        with self.assertRaisesRegex(ValueError,'package_privacy_preflight_failed'):
            builder.review_stream('candidate/hub/server/setup.manifest',io.BytesIO(data),len(data),{},
                                  {hashlib.sha256(data).hexdigest()})

    def test_webmanifest_is_privacy_scanned_when_pinned(self):
        data=b'{"'+b'pass'+b'word":"sensitive-fixture-value"}'
        with self.assertRaisesRegex(ValueError,'package_privacy_preflight_failed'):
            builder.review_stream('candidate/hub/web/manifest.webmanifest',io.BytesIO(data),len(data),{},
                                  {hashlib.sha256(data).hexdigest()})

    def test_educational_datatraener_models_remain_publishable(self):
        for name in ('assets/datatraener/model.tflite','assets/datatraener/model.onnx',
                     'linux-elevhub/server/datatraener/models/model.pt'):
            with self.subTest(name=name):
                data=b'educational model fixture'
                want=hashlib.sha256(data).hexdigest()
                self.assertEqual(builder.review_stream(name,io.BytesIO(data),len(data),{}, {want}),want)


if __name__=='__main__':unittest.main()
