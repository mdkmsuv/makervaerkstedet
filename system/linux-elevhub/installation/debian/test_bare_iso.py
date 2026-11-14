"""Native signed repository and inert payload tests; no installation or boot."""
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import build_iso
import install_payload


class BareISO(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signer = tempfile.TemporaryDirectory(prefix='mkv-iso-signer-')
        cls.home = Path(cls.signer.name)
        cls.home.chmod(0o700)
        subprocess.run(['gpg', '--homedir', str(cls.home), '--batch', '--passphrase', '',
                        '--quick-generate-key', 'Synthetic ISO QA <qa@example.invalid>',
                        'ed25519', 'sign', '1d'], check=True, capture_output=True)
        cls.key = subprocess.check_output(['gpg', '--homedir', str(cls.home), '--export'])

    @classmethod
    def tearDownClass(cls):
        subprocess.run(['gpgconf', '--homedir', str(cls.home), '--kill', 'all'], capture_output=True)
        cls.signer.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mkv-bare-iso-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root/'apt'
        (self.repo/'pool').mkdir(parents=True)
        (self.repo/'makervaerkstedet.gpg').write_bytes(self.key)
        self.entries = {}
        for name, arch in build_iso.PACKAGES.items():
            if name == 'makervaerkstedet-scratch':
                continue
            version = ('2.1.0~20261114.1' if name == 'makervaerkstedet-elev' else
                       '1.1.0~20261114.1' if name == 'makervaerkstedet-updater' else '1.2.2+guard.1')
            self.add_package(name, arch, version)
        self.gate = dict(schema=2, release_set='base', target='debian13-amd64-empty-status',
                         all_packages_simulated=True, actual_install=False, packages=self.entries,
                         isolated_paths=19,apt_config_sha256='a'*64,debian_keyring_sha256='b'*64)
        self.receipt = self.root/'gate.json'
        self.addCleanup(patch.stopall)
        patch.object(build_iso, 'KEY_SHA', hashlib.sha256(self.key).hexdigest()).start()

    def add_package(self, name, arch='all', version='1.0'):
        filename = name+'_'+version+'_'+arch+'.deb'
        file = self.repo/'pool'/filename
        file.write_bytes(b'synthetic archive parser fixture '+name.encode())
        self.entries[name] = dict(file=filename, version=version, architecture=arch, sha256=build_iso.sha(file))

    def seal(self):
        blocks = []
        for name, entry in self.entries.items():
            file = self.repo/'pool'/entry['file']
            blocks.append('\n'.join([f'Package: {name}', f'Version: {entry["version"]}',
                f'Architecture: {entry["architecture"]}', f'Filename: pool/{entry["file"]}',
                f'SHA256: {entry["sha256"]}', f'Size: {file.stat().st_size}', '']))
        (self.repo/'Packages').write_text('\n'.join(blocks))
        (self.repo/'Packages.gz').write_bytes(gzip.compress((self.repo/'Packages').read_bytes()))
        (self.repo/'Release').write_text('SHA256:\n'+''.join(
            f' {build_iso.sha(self.repo/name)} {(self.repo/name).stat().st_size} {name}\n'
            for name in ('Packages', 'Packages.gz')))
        for arguments in (['--clearsign', '--output', str(self.repo/'InRelease')],
                          ['--detach-sign', '--output', str(self.repo/'Release.gpg')]):
            subprocess.run(['gpg', '--homedir', str(self.home), '--batch', '--yes', *arguments,
                            str(self.repo/'Release')], check=True, capture_output=True)
        self.receipt.write_text(json.dumps(self.gate))

    def validate(self, release_set='base'):
        return build_iso.validated_repository(self.repo, self.receipt,
                                             build_iso.sha(self.receipt), release_set=release_set)

    def test_modern_exact_base_three_signed_repository(self):
        self.seal()
        packages, files = self.validate()
        self.assertEqual(len(packages), 3)
        self.assertFalse(any('scratch' in name for name in files))

    def test_missing_extra_and_migration_sets_are_not_bare_iso(self):
        original = copy.deepcopy(self.entries)
        for mode in ('missing', 'scratch', 'migration'):
            with self.subTest(mode=mode):
                self.entries.clear(); self.entries.update(copy.deepcopy(original))
                if mode == 'missing': self.entries.pop('makervaerkstedet-updater')
                else: self.add_package('makervaerkstedet-scratch')
                self.gate['release_set'] = 'migration' if mode == 'migration' else 'base'
                self.seal()
                with self.assertRaises(ValueError): self.validate()

    def test_old_core_or_updater_cannot_be_a_modern_base(self):
        for name in ('makervaerkstedet-elev', 'makervaerkstedet-updater'):
            with self.subTest(name=name):
                old = self.entries[name]['version']; self.entries[name]['version'] = '1.0'
                self.seal()
                with self.assertRaises(ValueError): self.validate()
                self.entries[name]['version'] = old

    def test_modern_gate_requires_the_complete_native_receipt(self):
        for mode in ('missing_isolation','boolean_isolation','bad_evidence_hash','extra_field'):
            original=copy.deepcopy(self.gate)
            if mode=='missing_isolation': self.gate.pop('isolated_paths')
            elif mode=='boolean_isolation': self.gate['isolated_paths']=True
            elif mode=='bad_evidence_hash': self.gate['apt_config_sha256']='wrong'
            else: self.gate['ignored_override']='legacy'
            self.seal()
            with self.subTest(mode=mode),self.assertRaises(ValueError): self.validate()
            self.gate=original;self.gate['packages']=self.entries

    def test_signed_release_and_cleartext_must_both_match(self):
        self.seal()
        for name in ('Release.gpg', 'InRelease'):
            with self.subTest(name=name):
                original = (self.repo/name).read_bytes(); (self.repo/name).write_bytes(b'tampered')
                with self.assertRaises(ValueError): self.validate()
                (self.repo/name).write_bytes(original)

    def test_two_valid_signatures_on_different_release_bytes_rejected(self):
        self.seal()
        (self.repo/'Release').write_bytes((self.repo/'Release').read_bytes()+b'Label: changed\n')
        subprocess.run(['gpg','--homedir',str(self.home),'--batch','--yes','--detach-sign',
                        '--output',str(self.repo/'Release.gpg'),str(self.repo/'Release')],check=True,capture_output=True)
        with self.assertRaisesRegex(ValueError,'signed_release_content_mismatch'): self.validate()

    def test_archive_and_recipe_index_tampering_rejected(self):
        self.seal()
        for name in ('Packages', 'pool/'+next(iter(self.entries.values()))['file']):
            with self.subTest(name=name):
                original = (self.repo/name).read_bytes(); (self.repo/name).write_bytes(b'tampered')
                with self.assertRaises(ValueError): self.validate()
                (self.repo/name).write_bytes(original)

    def test_legacy_four_requires_explicit_legacy_mode(self):
        self.add_package('makervaerkstedet-scratch')
        self.gate.pop('release_set'); self.gate['schema'] = 1; self.seal()
        with self.assertRaises(ValueError): self.validate()
        self.assertEqual(len(self.validate('legacy')[0]), 4)

    def payload(self):
        self.seal()
        packages, _ = self.validate()
        payload = self.root/'payload'; payload.mkdir()
        (payload/'public.txt').write_text('public payload')
        manifest = dict(schema=2, release_set='base', packages=packages,
                        files={'public.txt': build_iso.sha(payload/'public.txt')})
        (payload/'install-lock.json').write_text(json.dumps(manifest))
        return payload, manifest

    def test_schema_two_payload_exact_set_and_duplicate_rejected(self):
        payload, manifest = self.payload()
        self.assertEqual(len(install_payload.check(payload)['packages']), 3)
        manifest['packages'].append(manifest['packages'][0])
        (payload/'install-lock.json').write_text(json.dumps(manifest))
        with self.assertRaises(ValueError): install_payload.check(payload)

    def test_payload_package_pins_cannot_downgrade_or_add_apps(self):
        payload, manifest = self.payload()
        for mode in ('old_core','scratch','migration','boolean_schema'):
            changed=copy.deepcopy(manifest)
            if mode=='old_core':
                next(item for item in changed['packages'] if item['package']=='makervaerkstedet-elev')['version']='2.0.0~20261114.2'
            elif mode=='scratch': changed['packages'].append(dict(package='makervaerkstedet-scratch',version='1.0',architecture='all'))
            elif mode=='migration': changed['release_set']='migration'
            else: changed['schema']=True
            (payload/'install-lock.json').write_text(json.dumps(changed))
            with self.subTest(mode=mode),self.assertRaises(ValueError): install_payload.check(payload)

    def test_bare_recipe_rejects_app_dependencies_and_preserves_debian_dependencies(self):
        self.assertIn('python3',build_iso.recipe_dependencies('python3 (>= 3.11), makervaerkstedet-matchbox (= 1.2.2)', 'base'))
        for dependency in ('makervaerkstedet-scratch','makervaerkstedet-app-laser'):
            with self.subTest(dependency=dependency),self.assertRaisesRegex(ValueError,'optional_app_in_base_recipe'):
                build_iso.recipe_dependencies('python3, '+dependency+' (>= 1.0)','base')

    def test_payload_missing_file_extra_file_and_link_are_rejected(self):
        payload, manifest = self.payload()
        for mode in ('missing', 'extra', 'link'):
            with self.subTest(mode=mode):
                target = payload/'public.txt'
                if mode == 'missing': target.unlink()
                elif mode == 'extra': (payload/'unlocked.py').write_text('unexpected')
                else: target.unlink(); target.symlink_to(self.root/'gate.json')
                with self.assertRaises(ValueError): install_payload.check(payload)
                if target.is_symlink(): target.unlink()
                target.write_text('public payload'); (payload/'unlocked.py').unlink(missing_ok=True)

    def test_cli_defaults_to_base_and_requires_explicit_legacy(self):
        parser = build_iso.argument_parser()
        args = []
        for name in ('base-iso', 'repo', 'release-receipt', 'output', 'evidence-dir'):
            args.extend(['--'+name, '/unused'])
        args.extend(['--release-receipt-sha256', 'a'*64])
        self.assertEqual(parser.parse_args(args).release_set, 'base')
        self.assertEqual(parser.parse_args(args+['--release-set', 'legacy']).release_set, 'legacy')


if __name__ == '__main__':
    unittest.main()
