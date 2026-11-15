"""Public contracts for independently published, optional kiosk apps."""
from pathlib import Path as _LayoutPath
import sys as _layout_sys
_LAYOUT_ROOT = next(p for p in _LayoutPath(__file__).resolve().parents if (p / 'REPOSITORY-LAYOUT.json').is_file())
_layout_sys.path[:0] = [str(_LAYOUT_ROOT), str(_LAYOUT_ROOT / 'system')]
import copy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from app import simple_release

catalog = (importlib.import_module('app.app_catalog')
           if importlib.util.find_spec('app.app_catalog') else None)
REPOSITORY = 'mdkmsuv/makervaerkstedet'


def descriptor(app_id='noter', port=8799, *, project=True):
    return {
        'schema': 2, 'id': app_id, 'navn': 'Noter', 'description': 'Lokale noter',
        'category': 'ide', 'icon': 'assets/icon.svg', 'version': '1.0.0',
        'package': 'makervaerkstedet-app-' + app_id, 'architecture': 'all',
        'sdk': 1, 'min_core': '2.1.0~20261114.1',
        'start': f'http://127.0.0.1:{port}/index.html', 'offline': True,
        'aflevering': None, 'kraever_internet': False, 'licens': 'GPL-3.0-or-later',
        'project': {'kind': app_id, 'extension': {'scratch':'.sb3','laser':'.laser.json','folie':'.svg','3dprint':'.mkv3d','microbit':'.microbit.json'}.get(app_id,'.noter.json'), 'max_bytes': 1_000_000} if project else None,
        'capabilities': {'required': ['projects-v1'] if project else [], 'optional': ['theme-v1']},
    }


def envelope(app=None):
    app = descriptor() if app is None else app
    filename = f"{app['package']}_{app['version']}_{app['architecture']}.deb"
    return {
        'schema': 2, 'kind': 'makervaerkstedet-app-catalog', 'repository': REPOSITORY,
        'role': 'student', 'platform': 'debian-amd64', 'component': 'app-catalog',
        'version': '2026.11.14.1', 'sequence': 1,
        'apps': [{'descriptor': app, 'sequence': 1, 'source_commit': 'a' * 40,
                  'artifact': {'name': filename, 'size': 123, 'sha256': 'b' * 64,
                               'url': f'https://github.com/{REPOSITORY}/releases/download/app-noter-1.0.0/{filename}'}}],
    }


class CatalogContractTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(catalog, 'Independent app catalog contract is not implemented')

    def test_new_app_and_stateless_app_need_no_hardcoded_core_id(self):
        for value in (descriptor(), descriptor('quiz', 8800, project=False)):
            self.assertEqual(catalog.validate_app_descriptor(value), value)

    def test_app_cannot_claim_sdk_available_on_the_unmigrated_core(self):
        value=descriptor();value['min_core']='2.0.0~20261114.2'
        self.assertFalse(catalog.compatibility(value,core_version='2.0.0~20261114.2')['compatible'])

    def test_existing_ids_and_three_d_component_namespace_are_preserved(self):
        for app_id in ('laser', 'folie', '3dprint', 'scratch', 'microbit'):
            value = descriptor(app_id)
            if app_id == 'scratch':
                value['package'] = 'makervaerkstedet-scratch'
            self.assertEqual(catalog.validate_app_descriptor(value)['id'], app_id)
            self.assertEqual(catalog.component_for_app(app_id), 'app-' + app_id)

    def test_descriptor_rejects_arbitrary_os_package_and_cross_app_package(self):
        for package in ('curl', 'makervaerkstedet-elev', 'makervaerkstedet-app-laser'):
            value = descriptor(); value['package'] = package
            with self.subTest(package=package), self.assertRaises(ValueError):
                catalog.validate_app_descriptor(value)

    def test_artifact_filename_binds_exact_version_architecture_and_safe_tag(self):
        for filename in ('makervaerkstedet-app-noter_0.9.0_all.deb',
                         'makervaerkstedet-app-noter_1.0.0_amd64.deb'):
            value = envelope(); value['apps'][0]['artifact']['name'] = filename
            value['apps'][0]['artifact']['url'] = f'https://github.com/{REPOSITORY}/releases/download/v1/{filename}'
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                catalog.validate_catalog(value, repository=REPOSITORY)
        for tag in ('', '..', '%2e%2e', 'v1%2felsewhere'):
            value = envelope(); item = value['apps'][0]['artifact']
            item['url'] = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{item['name']}"
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                catalog.validate_catalog(value, repository=REPOSITORY)

    def test_corrupt_highwater_fails_with_fixed_catalog_error(self):
        value = envelope(); value['_manifest_sha256'] = 'c' * 64
        for state in ({'apps': []}, {'apps': {'noter': {'sequence': True, 'sha256': 'x'}}},
                      {'apps': {'noter': None}}, {'sequence': '1'}):
            with self.subTest(state=state), self.assertRaises(catalog.CatalogError):
                catalog.accept_catalog(value, state)

    def test_same_app_version_keeps_exact_bytes_even_after_removal_and_new_sequences(self):
        value=envelope();value['_manifest_sha256']='c'*64
        state=catalog.accept_catalog(value,{})
        absent=copy.deepcopy(value);absent.update(sequence=2,apps=[],_manifest_sha256='d'*64)
        state=catalog.accept_catalog(absent,state)
        changed=copy.deepcopy(value);changed.update(sequence=3,_manifest_sha256='e'*64)
        changed['apps'][0]['sequence']=2
        changed['apps'][0]['artifact']['sha256']='f'*64
        with self.assertRaisesRegex(catalog.CatalogError,'app_version_identity_conflict'):
            catalog.accept_catalog(changed,state)
        # Republishing the same exact bytes from another approved release URL is safe.
        changed['apps'][0]['artifact']['sha256']='b'*64
        changed['apps'][0]['artifact']['url']=changed['apps'][0]['artifact']['url'].replace('app-noter-1.0.0','republish')
        self.assertEqual(catalog.accept_catalog(changed,state)['sequence'],3)

    def test_catalog_rejects_project_extension_conflict_between_different_apps(self):
        value=envelope();other=envelope(descriptor('quiz',8800))['apps'][0]
        value['apps'].append(other)
        with self.assertRaisesRegex(catalog.CatalogError,'duplicate_catalog_project_format'):
            catalog.validate_catalog(value,repository=REPOSITORY)

    def test_catalog_rejects_suffix_overlap_in_either_order(self):
        alpha=descriptor('alpha',8890);alpha['project']['extension']='.mkv'
        beta=descriptor('beta',8891);beta['project']['extension']='.beta.mkv'
        for first,second in ((alpha,beta),(beta,alpha)):
            value=envelope(first);value['apps'].append(envelope(second)['apps'][0])
            with self.assertRaisesRegex(catalog.CatalogError,'duplicate_catalog_project_format'):
                catalog.validate_catalog(value,repository=REPOSITORY)

    def test_local_start_is_strictly_loopback_and_does_not_claim_system_ports(self):
        for start in ('https://example.com/', 'http://localhost:8799/',
                      'http://127.0.0.1:8790/', 'http://127.0.0.1:8791/',
                      'http://127.0.0.1:8799/../etc/passwd', 'http://127.0.0.1:8799/%2e%2e/x',
                      'http://127.0.0.1:8799/?token=secret'):
            value = descriptor(); value['start'] = start
            with self.subTest(start=start), self.assertRaises(ValueError):
                catalog.validate_app_descriptor(value)

    def test_project_and_icon_paths_are_bounded_and_declarative(self):
        invalid = [('icon', '../outside.svg'), ('icon', 'https://example.com/i.svg')]
        for field, content in invalid:
            value = descriptor(); value[field] = content
            with self.subTest(content=content), self.assertRaises(ValueError):
                catalog.validate_app_descriptor(value)

    def test_new_project_format_cannot_claim_another_app_or_generic_file_type(self):
        for kind,extension in (('scratch','.noter.json'),('noter','.sb3'),('noter','.json'),
                               ('noter','.cad.stl'),('noter','.v2.hex'),('noter','.note.svg'),('noter','.note.laser.json')):
            value=descriptor();value['project'].update(kind=kind,extension=extension)
            with self.subTest(kind=kind,extension=extension),self.assertRaises(ValueError):catalog.validate_app_descriptor(value)
        for content in ({'kind': 'noter', 'extension': '../x', 'max_bytes': 100},
                        {'kind': 'noter', 'extension': '.note', 'max_bytes': 51 * 1024**2}):
            value = descriptor(); value['project'] = content
            with self.subTest(content=content), self.assertRaises(ValueError):
                catalog.validate_app_descriptor(value)

    def test_unknown_fields_and_boolean_integer_confusion_are_rejected(self):
        for field, content in (('schema', True), ('sdk', True), ('offline', 1), ('run_as_root', True)):
            value = descriptor(); value[field] = content
            with self.subTest(field=field), self.assertRaises(ValueError):
                catalog.validate_app_descriptor(value)

    def test_queue_is_optional_but_delivery_protocol_must_be_declared(self):
        value = descriptor(); value['aflevering'] = 'laser-submit'
        with self.assertRaises(ValueError): catalog.validate_app_descriptor(value)
        value['capabilities']['optional'].append('laser-submit-v1')
        self.assertEqual(catalog.validate_app_descriptor(value)['aflevering'], 'laser-submit')
        value['aflevering'] = 'run-physical-laser'
        with self.assertRaises(ValueError): catalog.validate_app_descriptor(value)

    def test_unknown_required_capability_is_visible_but_blocks_compatibility(self):
        value = descriptor(); value['capabilities']['required'].append('new-queue-v1')
        catalog.validate_app_descriptor(value)
        result = catalog.compatibility(value, core_version='2.1.0~20261114.1')
        self.assertFalse(result['compatible'])
        self.assertIn('missing_capability:new-queue-v1', result['reasons'])
        value['capabilities']['required'].remove('new-queue-v1')
        value['capabilities']['optional'].append('new-queue-v1')
        self.assertTrue(catalog.compatibility(value, core_version='2.1.0~20261114.1')['compatible'])

    def test_debian_version_comparison_honours_tilde_epoch_and_revision(self):
        value = descriptor()
        self.assertFalse(catalog.compatibility(value, core_version='2.0.0~20261114.2')['compatible'])
        self.assertTrue(catalog.compatibility(value, core_version='2.1.0')['compatible'])
        value['min_core'] = '1:2.0-2'
        self.assertFalse(catalog.compatibility(value, core_version='1:2.0-1')['compatible'])
        self.assertTrue(catalog.compatibility(value, core_version='1:2.0-10')['compatible'])

    def test_catalog_is_bound_to_repository_role_platform_and_precise_artifact(self):
        value = envelope()
        self.assertEqual(catalog.validate_catalog(value, repository=REPOSITORY), value)
        mutations = [('repository', 'attacker/repo'), ('role', 'server'), ('platform', 'windows-amd64')]
        for field, content in mutations:
            bad = copy.deepcopy(value); bad[field] = content
            with self.subTest(field=field), self.assertRaises(ValueError):
                catalog.validate_catalog(bad, repository=REPOSITORY)
        for field, content in (('sha256', 'not-a-hash'), ('size', True),
                               ('url', 'https://attacker.example/app.deb')):
            bad = copy.deepcopy(value); bad['apps'][0]['artifact'][field] = content
            with self.subTest(field=field), self.assertRaises(ValueError):
                catalog.validate_catalog(bad, repository=REPOSITORY)

    def test_duplicate_app_and_port_are_rejected(self):
        value = envelope(); value['apps'].append(copy.deepcopy(value['apps'][0]))
        with self.assertRaises(ValueError): catalog.validate_catalog(value, repository=REPOSITORY)
        value['apps'][1] = envelope(descriptor('quiz'))['apps'][0]
        with self.assertRaises(ValueError): catalog.validate_catalog(value, repository=REPOSITORY)

    def test_verified_catalog_uses_existing_pinned_crypto_and_binds_exact_bytes(self):
        raw = json.dumps(envelope()).encode(); signature = b'test-signature'
        args = dict(minisign_exe=Path('pinned-minisign'), minisign_sha256='c' * 64,
                    public_key='reviewed-key', repository=REPOSITORY)
        with patch.object(simple_release, '_minisign') as verify:
            result = catalog.verify_catalog(raw, signature, **args)
        verify.assert_called_once_with(raw, signature, args['minisign_exe'], args['minisign_sha256'], args['public_key'])
        self.assertEqual(result['_manifest_sha256'], hashlib.sha256(raw).hexdigest())
        with patch.object(simple_release, '_minisign', side_effect=simple_release.ReleaseVerificationError('signature_failed')):
            with self.assertRaises(ValueError): catalog.verify_catalog(raw, signature, **args)

    def test_duplicate_json_keys_and_unsigned_catalog_cannot_be_accepted(self):
        raw = json.dumps(envelope()).replace('"schema": 2', '"schema": 2, "schema": 2', 1).encode()
        args = dict(minisign_exe=Path('tool'), minisign_sha256='c' * 64, public_key='key', repository=REPOSITORY)
        with patch.object(simple_release, '_minisign'):
            with self.assertRaises(ValueError): catalog.verify_catalog(raw, b'sig', **args)
        with self.assertRaises(ValueError): catalog.accept_catalog(envelope(), {})

    def test_highwater_and_same_sequence_fork_survive_app_removal(self):
        value = envelope(); value['_manifest_sha256'] = 'a' * 64
        state = catalog.accept_catalog(value, {})
        self.assertEqual(catalog.accept_catalog(value, state), state)
        fork = copy.deepcopy(value); fork['_manifest_sha256'] = 'b' * 64
        with self.assertRaises(ValueError): catalog.accept_catalog(fork, state)
        newer = copy.deepcopy(value); newer['sequence'] = 2; newer['_manifest_sha256'] = 'c' * 64
        newer['apps'] = []
        state = catalog.accept_catalog(newer, state)
        with self.assertRaises(ValueError): catalog.accept_catalog(value, state)
        self.assertEqual(state['sequence'], 2)


if __name__ == '__main__':
    unittest.main()
