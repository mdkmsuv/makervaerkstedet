"""Native reads are mocked; actual control/tar bytes remain publisher inputs."""
from copy import deepcopy
import hashlib
import importlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import Mock

from app.app_catalog import validate_catalog
from app.simple_release import REPOSITORY
from tests.test_app_catalog import descriptor


def descriptor_tar(app, *, duplicate=False, link=False, parent_link=False, alias_duplicate=False):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        if parent_link:
            parent=tarfile.TarInfo('./usr/share/makervaerkstedet/apps/'+app['id'])
            parent.type=tarfile.SYMTYPE;parent.linkname='/tmp/foreign';archive.addfile(parent)
        raw = json.dumps(app).encode()
        member = tarfile.TarInfo('./usr/share/makervaerkstedet/apps/' + app['id'] + '/app.json')
        member.size = len(raw)
        if link: member.type = tarfile.SYMTYPE; member.linkname = '/etc/passwd'
        archive.addfile(member, io.BytesIO(raw))
        if duplicate: archive.addfile(member, io.BytesIO(raw))
        if alias_duplicate:
            member.name='././usr/share/makervaerkstedet/apps/'+app['id']+'/app.json'
            archive.addfile(member,io.BytesIO(raw))
    return buffer.getvalue()


class AppCatalogProducerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('scripts.build_app_catalog'), 'App catalog producer is missing')
        self.producer = importlib.import_module('scripts.build_app_catalog')
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.app = descriptor()
        self.path = self.root / 'input.deb'; self.path.write_bytes(b'synthetic-deb')
        self.package = {'path': str(self.path), 'size': 13, 'sha256': hashlib.sha256(b'synthetic-deb').hexdigest(),
                        'package': self.app['package'], 'version': '1.0.0', 'architecture': 'all'}
        self.spec = {'schema': 2, 'version': '2026.11.14.1', 'sequence': 7, 'source_commit': 'a'*40,
                     'release_tag': 'app-noter-1.0.0', 'packages': [self.package], 'app_descriptors': [self.app]}

    def native(self, arguments, **kwargs):
        return self.package[arguments[-1].lower()] + '\n'

    def process(self, raw=None):
        process=Mock(); process.stdout=io.BytesIO(descriptor_tar(self.app) if raw is None else raw)
        process.wait.return_value=0;process.poll.return_value=0
        return process

    def test_catalog_uses_checked_deb_and_shared_schema_with_explicit_provenance(self):
        with patch.object(self.producer.subprocess, 'check_output', side_effect=self.native), patch.object(self.producer.subprocess,'Popen',return_value=self.process()):
            result = self.producer.build(self.spec)
        validate_catalog(result, repository=REPOSITORY)
        self.assertEqual(result['apps'], [{'descriptor': self.app, 'sequence': 7, 'source_commit': 'a'*40,
            'artifact': {'name': 'makervaerkstedet-app-noter_1.0.0_all.deb', 'size': 13,
                         'sha256': self.package['sha256'], 'url': 'https://github.com/mdkmsuv/makervaerkstedet/releases/download/app-noter-1.0.0/makervaerkstedet-app-noter_1.0.0_all.deb'}}])

    def test_catalog_rejects_unverified_bytes_and_wrong_native_control(self):
        for field, value in [('size', 12), ('sha256', '0'*64), ('version', '2.0.0'), ('architecture', 'amd64')]:
            changed = deepcopy(self.spec); changed['packages'][0][field] = value
            with self.subTest(field=field), patch.object(self.producer.subprocess, 'check_output', side_effect=self.native), patch.object(self.producer.subprocess,'Popen',return_value=self.process()), self.assertRaises(ValueError):
                self.producer.build(changed)

    def test_native_descriptor_must_be_exact_regular_unique_owned_app_json(self):
        changed = deepcopy(self.app); changed['description'] = 'Different packaged description'
        invalid=deepcopy(self.app);invalid['sdk']=True
        for raw in (descriptor_tar(changed), descriptor_tar(invalid), descriptor_tar(self.app, duplicate=True), descriptor_tar(self.app, link=True),descriptor_tar(self.app,parent_link=True),descriptor_tar(self.app,alias_duplicate=True)):
            with self.subTest(raw=hashlib.sha256(raw).hexdigest()), patch.object(self.producer.subprocess, 'check_output', side_effect=self.native), patch.object(self.producer.subprocess,'Popen',return_value=self.process(raw)), self.assertRaises(ValueError):
                self.producer.build(self.spec)

    def test_missing_immutable_commit_unsafe_tag_and_unmatched_packages_are_rejected(self):
        for field, value in [('source_commit', 'main'), ('source_commit', 'A'*40), ('sequence', True),
                             ('release_tag', '../latest'), ('app_descriptors', []), ('packages', [])]:
            changed = deepcopy(self.spec); changed[field] = value
            with self.subTest(field=field), patch.object(self.producer.subprocess, 'check_output', side_effect=self.native), patch.object(self.producer.subprocess,'Popen',return_value=self.process()), self.assertRaises(ValueError):
                self.producer.build(changed)


@unittest.skipUnless(shutil.which('dpkg-deb'),'Native Linux/WSL dpkg-deb required')
class NativeAppCatalogProducerTests(unittest.TestCase):
    def test_actual_debian_control_and_packaged_descriptor_are_checked(self):
        from scripts import build_app_catalog as producer
        with tempfile.TemporaryDirectory(prefix='mkv-native-catalog-') as directory:
            root=Path(directory);stage=root/'stage';(stage/'DEBIAN').mkdir(parents=True)
            app=descriptor();(stage/'DEBIAN/control').write_text('Package: makervaerkstedet-app-noter\nVersion: 1.0.0\nArchitecture: all\nMaintainer: Synthetic <test@example.invalid>\nDescription: Synthetic app fixture\n')
            target=stage/'usr/share/makervaerkstedet/apps/noter';target.mkdir(parents=True)
            (target/'app.json').write_text(json.dumps(app));package=root/'app.deb'
            subprocess.run(['dpkg-deb','--build','--root-owner-group',str(stage),str(package)],check=True,capture_output=True)
            entry={'path':str(package),'package':app['package'],'version':'1.0.0','architecture':'all','size':package.stat().st_size,'sha256':producer.sha(package)}
            spec={'schema':2,'version':'2026.11.14.1','sequence':7,'source_commit':'a'*40,'release_tag':'app-noter-1.0.0','packages':[entry],'app_descriptors':[app]}
            result=producer.build(spec)
            self.assertEqual(result['apps'][0]['descriptor'],app)
            wrong=deepcopy(spec);wrong['app_descriptors'][0]['description']='different'
            with self.assertRaisesRegex(ValueError,'packaged_app_descriptor_mismatch'):producer.build(wrong)
            wrong=deepcopy(spec);wrong['packages'][0]['version']='2.0.0';wrong['app_descriptors'][0]['version']='2.0.0'
            with self.assertRaisesRegex(ValueError,'package_control_mismatch'):producer.build(wrong)


if __name__ == '__main__': unittest.main()
