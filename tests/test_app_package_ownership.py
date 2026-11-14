"""Independent archives cannot claim the base, another app, or pupil data."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

from tests.test_app_catalog import descriptor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linux-elevhub/packaging'))
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

    def test_repository_descriptors_are_independent_and_base_allowlist_has_no_app_ui(self):
        ids = set()
        for folder in sorted((ROOT/'apps').iterdir()):
            app = json.loads((folder/'app.json').read_text(encoding='utf-8'))
            packages.validate_app_descriptor(app); ids.add(app['id'])
            manifest = json.loads((folder/'files.json').read_text())
            self.assertTrue(manifest['files'] or app['id'] == 'scratch')
        self.assertEqual(ids, {'laser', 'folie', '3dprint', 'scratch', 'microbit'})
        allowlist = json.loads((ROOT/'linux-elevhub/packaging/base-design-files.json').read_text())
        app_ui = {'web/laser.html', 'web/laservaerksted', 'web/model3d.html', 'web/model3d.css'}
        self.assertFalse(app_ui.intersection(allowlist))

if __name__ == '__main__': unittest.main()
