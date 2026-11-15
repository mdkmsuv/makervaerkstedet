"""Independent archives cannot claim the base, another app, or pupil data."""
from pathlib import Path as _LayoutPath
import sys as _layout_sys
_LAYOUT_ROOT = next(p for p in _LayoutPath(__file__).resolve().parents if (p / 'REPOSITORY-LAYOUT.json').is_file())
_layout_sys.path[:0] = [str(_LAYOUT_ROOT), str(_LAYOUT_ROOT / 'system')]
from repository import source_path, repo_root
import importlib.util
import json
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

from tests.test_app_catalog import descriptor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(source_path('linux-elevhub/packaging', ROOT)))
from build_debian import build
packages = (__import__('app.app_packages', fromlist=['x'])
            if importlib.util.find_spec('app.app_packages') else None)


class AppOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(packages, 'Independent app package ownership is missing')
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'index.html').write_text('<h1>Noter</h1>')

    def records(self, path='usr/share/makervaerkstedet/apps/noter/www/index.html'):
        return {'schema': 1, 'files': [{'source': 'index.html', 'path': path, 'tree': False}]}

    def test_new_app_build_inputs_bind_descriptor_and_only_own_program_files(self):
        spec = packages.prepare(self.root, descriptor(), self.records(), self.root/'stage')
        self.assertEqual(spec['package'], 'makervaerkstedet-app-noter')
        self.assertEqual({v['path'] for v in spec['files']}, {
            'usr/share/makervaerkstedet/apps/noter/app.json',
            'usr/share/makervaerkstedet/apps/noter/www/index.html'})
        self.assertEqual(spec['depends'], ['makervaerkstedet-elev (>= 2.1.0~20261114.1)'])

    def test_scratch_reviewed_wrapper_can_override_its_pinned_legacy_asset(self):
        app=json.loads((ROOT/'apps/scratch/app.json').read_text(encoding='utf-8'))
        old=self.root/'old.js';old.write_bytes(b'old school-specific wrapper')
        new=self.root/'index.html';target='usr/local/share/elevhub/apps/scratch/creative/creative-app.js'
        legacy={'package':'makervaerkstedet-scratch','files':[{'source':str(old),'path':target,'mode':0o644,'sha256':hashlib.sha256(old.read_bytes()).hexdigest()}]}
        spec=packages.prepare(self.root,app,self.records(target),self.root/'stage',scratch_lock=legacy)
        entry=next(item for item in spec['files'] if item['path']==target)
        self.assertEqual(entry['source'],str(new));self.assertEqual(len(spec['files']),2)

    def test_app_cannot_own_other_app_system_configuration_or_data(self):
        for target in ('var/lib/elevhub/projekter/a', 'etc/elevhub/client.json',
                       'home/elev/a', 'usr/bin/python3',
                       'usr/share/makervaerkstedet/apps/laser/www/index.html',
                       'usr/share/makervaerkstedet/apps/noter/www/../app.json'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                packages.prepare(self.root, descriptor(), self.records(target), self.root/'stage')

    def test_duplicate_destinations_and_symlink_sources_are_rejected(self):
        record = self.records(); record['files'] *= 2
        with self.assertRaises(ValueError):
            packages.prepare(self.root, descriptor(), record, self.root/'stage')

    def test_base_set_needs_no_apps_and_migration_is_explicit(self):
        base = [{'package': name} for name in ('makervaerkstedet-elev',
                'makervaerkstedet-updater', 'makervaerkstedet-matchbox')]
        self.assertEqual(packages.release_packages(base, 'base'), {v['package'] for v in base})
        with self.assertRaises(ValueError): packages.release_packages(base[:-1], 'base')
        app = packages.prepare(self.root, descriptor(), self.records(), self.root/'stage')
        self.assertEqual(len(packages.release_packages(base+[app], 'app')), 4)
        with self.assertRaises(ValueError): packages.release_packages(base+[app], 'base')
        with self.assertRaises(ValueError): packages.release_packages(base+[{'package': 'curl'}], 'app')

    def test_app_release_can_add_a_new_app_without_dropping_existing_choices(self):
        base = [{'package': name} for name in packages.BASE_PACKAGES]
        specs = []
        for index, app_id in enumerate(('idebog', 'lommeregner')):
            specs.append(packages.prepare(self.root, descriptor(app_id, 8890 + index, project=False),
                self.records(f'usr/share/makervaerkstedet/apps/{app_id}/www/index.html'),
                self.root / app_id))
        self.assertEqual(packages.release_packages(base + specs, 'app'),
                         packages.BASE_PACKAGES | {spec['package'] for spec in specs})
        with self.assertRaises(ValueError): packages.release_packages(base, 'app')
        with self.assertRaises(ValueError): packages.release_packages(base[:-1] + specs, 'app')
        with self.assertRaises(ValueError): packages.release_packages(base + specs + [specs[0]], 'app')

    def test_repository_descriptors_are_independent_and_base_allowlist_has_no_app_ui(self):
        ids = set()
        for folder in sorted((ROOT/'apps').iterdir()):
            app = json.loads((folder/'app.json').read_text(encoding='utf-8'))
            packages.validate_app_descriptor(app); ids.add(app['id'])
            manifest = json.loads((folder/'files.json').read_text())
            self.assertTrue(manifest['files'] or app['id'] == 'scratch')
        self.assertTrue(packages.BUILTIN_APPS <= ids)
        allowlist = json.loads((source_path('linux-elevhub/packaging/base-design-files.json', ROOT)).read_text())
        app_ui = {'web/laser.html', 'web/laservaerksted', 'web/model3d.html', 'web/model3d.css'}
        self.assertFalse(app_ui.intersection(allowlist))

    def test_real_3d_file_manifest_cannot_package_a_stale_or_unverified_bundle(self):
        app=json.loads((ROOT/'apps/3dprint/app.json').read_text(encoding='utf-8'))
        manifest=json.loads((ROOT/'apps/3dprint/files.json').read_text())
        for item in manifest['files']:
            path=self.root/item['source']
            if item['tree']:path=path/'placeholder.ttf'
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b'{}' if path.name=='model3d-vendor-manifest.json' else b'stale build')
        with self.assertRaisesRegex(ValueError,'model3d_complete_bundle_required'):
            packages.prepare(self.root,app,manifest,self.root/'stage')

if __name__ == '__main__': unittest.main()
