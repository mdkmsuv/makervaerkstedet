"""Modern repository inputs retain the native simulation's exact package scope."""
from copy import deepcopy
import json
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import build_apt_repository as apt
from tests.test_app_catalog import descriptor
from tests.test_app_catalog_producer import descriptor_tar
from unittest.mock import Mock


class ModernGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def inputs(self, release_set, app_descriptors=None):
        descriptors = [] if app_descriptors is None else app_descriptors
        if release_set == 'app' and app_descriptors is None: descriptors = [descriptor()]
        if release_set == 'migration' and app_descriptors is None:
            descriptors = [descriptor(identity, 8800 + index) for index, identity in
                           enumerate(('laser', 'folie', '3dprint', 'scratch', 'microbit'))]
            descriptors[3]['package'] = 'makervaerkstedet-scratch'
        identities = [('makervaerkstedet-elev', 'all'), ('makervaerkstedet-updater', 'amd64'),
                      ('makervaerkstedet-matchbox', 'amd64')]
        identities += [(app['package'], app['architecture']) for app in descriptors]
        packages = [{'package': name, 'version': '1.0.0', 'architecture': arch,
                     'sha256': 'a' * 64, 'size': 7, 'path': str(self.root / (name + '.deb'))}
                    for name, arch in identities]
        receipt = {'schema': 2, 'release_set': release_set, 'target': 'debian13-amd64-empty-status',
                   'all_packages_simulated': True, 'actual_install': False, 'isolated_paths': 44,
                   'apt_config_sha256': 'b' * 64, 'debian_keyring_sha256': apt.DEBIAN13_KEYRING_SHA256,
                   'packages': {p['package']: dict(version=p['version'], architecture=p['architecture'],
                                                 sha256=p['sha256'], file=Path(p['path']).name) for p in packages}}
        path = self.root / 'gate.json'; path.write_text(json.dumps(receipt))
        return {'schema': 2, 'release_set': release_set, 'packages': packages,
                'app_descriptors': descriptors, 'test_repository': True,
                'install_gate': {'path': str(path), 'sha256': apt.sha(path)}}

    def test_all_modern_exact_sets_accept_the_native_versioned_receipt(self):
        for release_set in ('base', 'app', 'migration'):
            spec = self.inputs(release_set)
            with self.subTest(release_set=release_set):
                self.assertEqual(apt.validate_install_gate(spec['install_gate'], spec['packages'],
                    release_set=release_set, app_descriptors=spec['app_descriptors']), spec['install_gate']['sha256'])

    def test_app_catalog_growth_keeps_all_packages_bound_to_native_gate(self):
        spec = self.inputs('app', [descriptor(project=False), descriptor('lommeregner', 8890, project=False)])
        self.assertEqual(apt.validate_install_gate(spec['install_gate'], spec['packages'],
            release_set='app', app_descriptors=spec['app_descriptors']), spec['install_gate']['sha256'])
        with self.assertRaises(ValueError):
            apt.validate_install_gate(spec['install_gate'], spec['packages'][:-1],
                release_set='app', app_descriptors=spec['app_descriptors'])

    def test_modern_receipt_cannot_downgrade_to_legacy_or_cross_release_sets(self):
        spec = self.inputs('base')
        for release_set in ('legacy', 'app', 'migration'):
            with self.subTest(release_set=release_set), self.assertRaises(ValueError):
                apt.validate_install_gate(spec['install_gate'], spec['packages'], release_set=release_set,
                                          app_descriptors=[])

    def test_modern_set_rejects_extra_missing_duplicate_and_unbound_apps(self):
        spec = self.inputs('app')
        variants = [spec['packages'][:-1], spec['packages'] + [spec['packages'][-1]],
                    spec['packages'] + [dict(spec['packages'][-1], package='curl')]]
        for packages in variants:
            with self.subTest(packages=len(packages)), self.assertRaises(ValueError):
                apt.validate_install_gate(spec['install_gate'], packages, release_set='app',
                                          app_descriptors=spec['app_descriptors'])
        changed = deepcopy(spec['app_descriptors']); changed[0]['version'] = '2.0.0'
        with self.assertRaisesRegex(ValueError, 'app_package_identity_mismatch'):
            apt.validate_install_gate(spec['install_gate'], spec['packages'], release_set='app', app_descriptors=changed)

    def test_legacy_receipt_with_modern_extension_is_still_rejected(self):
        spec = self.inputs('base')
        receipt = json.loads(Path(spec['install_gate']['path']).read_bytes()); receipt['schema'] = 1
        Path(spec['install_gate']['path']).write_text(json.dumps(receipt)); spec['install_gate']['sha256'] = apt.sha(spec['install_gate']['path'])
        with self.assertRaisesRegex(ValueError, 'clean_debian13_simulation_required'):
            apt.validate_install_gate(spec['install_gate'], spec['packages'])

    def prepare_build(self):
        spec=self.inputs('app')
        for entry in spec['packages']:
            Path(entry['path']).write_bytes(b'package');entry['sha256']=apt.sha(entry['path'])
        gate=json.loads(Path(spec['install_gate']['path']).read_bytes())
        for entry in spec['packages']:gate['packages'][entry['package']]['sha256']=entry['sha256']
        Path(spec['install_gate']['path']).write_text(json.dumps(gate));spec['install_gate']['sha256']=apt.sha(spec['install_gate']['path'])
        gpg=self.root/'gpg';gpg.write_bytes(b'fixture');home=self.root/'private';home.mkdir()
        return spec,dict(gpg_exe=gpg,gpg_sha256=apt.sha(gpg),gnupg_home=home,key_fingerprint='A'*40)

    def native(self,spec):
        def output(arguments,**kwargs):
            if arguments[0]=='dpkg-deb':
                entry=next(p for p in spec['packages'] if p['path']==arguments[2])
                return entry[arguments[-1].lower()]+'\n'
            if arguments[0]=='apt-ftparchive':return b'Date: fixture\n'
            return b'public-key'
        return output

    def signer(self,arguments,**kwargs):
        if arguments[0]=='dpkg-scanpackages':return subprocess.CompletedProcess(arguments,0,b'Package: fixture\n')
        Path(arguments[arguments.index('--output')+1]).write_bytes(b'signature')
        return subprocess.CompletedProcess(arguments,0)

    def test_modern_repository_build_checks_embedded_app_and_emits_scope_binding(self):
        spec,options=self.prepare_build()
        process=Mock();process.stdout=io.BytesIO(descriptor_tar(spec['app_descriptors'][0]));process.wait.return_value=0;process.poll.return_value=0
        with patch.object(apt.subprocess,'check_output',side_effect=self.native(spec)),patch.object(apt.subprocess,'run',side_effect=self.signer),patch.object(apt.subprocess,'Popen',return_value=process):
            receipt=apt.build(spec,self.root/'repo',**options)
        self.assertEqual((receipt['schema'],receipt['release_set']),(2,'app'))
        self.assertEqual(receipt['app_descriptors'],spec['app_descriptors'])
        self.assertEqual(set(path.name for path in (self.root/'repo/pool').iterdir()),
                         {f"{p['package']}_{p['version']}_{p['architecture']}.deb" for p in spec['packages']})

    def test_mismatched_embedded_descriptor_stops_before_repository_output_or_signing(self):
        spec,options=self.prepare_build();changed=deepcopy(spec['app_descriptors'][0]);changed['description']='unapproved descriptor'
        process=Mock();process.stdout=io.BytesIO(descriptor_tar(changed));process.wait.return_value=0;process.poll.return_value=0
        with patch.object(apt.subprocess,'check_output',side_effect=self.native(spec)),patch.object(apt.subprocess,'run') as signer,patch.object(apt.subprocess,'Popen',return_value=process),self.assertRaisesRegex(ValueError,'packaged_app_descriptor_mismatch'):
            apt.build(spec,self.root/'repo',**options)
        signer.assert_not_called();self.assertFalse((self.root/'repo').exists())


if __name__ == '__main__': unittest.main()
