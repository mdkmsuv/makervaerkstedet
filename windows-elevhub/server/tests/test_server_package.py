import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('bootstrap', ROOT / 'bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)
build_spec = importlib.util.spec_from_file_location('build_packages', ROOT.parent / 'build_packages.py')
build_packages = importlib.util.module_from_spec(build_spec)
build_spec.loader.exec_module(build_packages)


class ServerPackageTest(unittest.TestCase):
    def test_manifest_marks_external_print_requirement_only_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'server').mkdir()
            (root / 'server' / 'marker.txt').write_text('fixture', encoding='utf-8')
            build_packages.manifest(root, 'server')
            normal = json.loads((root / 'elevhub-package.json').read_text(encoding='utf-8'))
            self.assertNotIn('externalPrintBackendRequired', normal)
            (root / 'integrity.json').unlink()
            (root / 'elevhub-package.json').unlink()
            build_packages.manifest(root, 'server', reuse_print_backend=True)
            reused = json.loads((root / 'elevhub-package.json').read_text(encoding='utf-8'))
            self.assertIs(reused['externalPrintBackendRequired'], True)

    def test_contract_is_read_only_and_role_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for item in ('app', 'web', 'hub', 'server'):
                (root / item).mkdir()
            (root / 'runtime').mkdir()
            (root / 'runtime' / 'python.exe').write_bytes(b'fixture')
            (root / 'hub' / 'elevhub.py').write_text('', encoding='utf-8')
            (root / 'server' / 'provision.py').write_text('', encoding='utf-8')
            (root / 'data').mkdir()
            config = json.loads((ROOT / 'hub' / 'konfiguration.windows.json').read_text(encoding='utf-8'))
            (root / 'hub' / 'konfiguration.windows.json').write_text(json.dumps(config), encoding='utf-8')
            report = bootstrap.check(root, root / 'data')
            self.assertTrue(report)
            self.assertTrue((root / 'data').exists())

    def test_role_ip_override_and_backend_ports_are_explicit(self):
        self.assertEqual(bootstrap.validate_role_ip('198.51.100.106'), '198.51.100.106')
        with self.assertRaises(ValueError):
            bootstrap.validate_role_ip('127.0.0.1')
        config = json.loads((ROOT / 'hub' / 'konfiguration.windows.json').read_text(encoding='utf-8'))
        config['backend_ports'] = {'print': 18765, 'design': 18766, 'teacher': 18767}
        for app in config['apps']:
            if app['id'] == '3dprint':
                app['bagved'] = '127.0.0.1:18765'
            elif app['id'] in ('laser', 'folie'):
                app['bagved'] = '127.0.0.1:18766'
        commands = bootstrap._commands(Path('.'), Path('data'), config)
        self.assertEqual(commands[0][1][1], '-B')
        self.assertEqual(commands[1][1][1], '-B')
        self.assertEqual(commands[2][1][1], '-B')
        self.assertIn('18765', commands[0][1])
        self.assertIn('18766', commands[1][1])
        self.assertIn('18767', commands[1][1])

    def test_default_supervisor_owns_all_three_processes(self):
        config = json.loads((ROOT / 'hub' / 'konfiguration.windows.json').read_text(encoding='utf-8'))
        self.assertEqual([name for name, _ in bootstrap._commands(Path('.'), Path('data'), config)],
                         ['print', 'design', 'hub'])

    def test_external_print_leaves_design_and_hub(self):
        config = json.loads((ROOT / 'hub' / 'konfiguration.windows.json').read_text(encoding='utf-8'))
        config['external_backends'] = ['print']
        self.assertEqual([name for name, _ in bootstrap._commands(Path('.'), Path('data'), config)],
                         ['design', 'hub'])

    def test_external_print_and_design_leave_only_hub(self):
        config = json.loads((ROOT / 'hub' / 'konfiguration.windows.json').read_text(encoding='utf-8'))
        config['external_backends'] = ['print', 'design']
        self.assertEqual([name for name, _ in bootstrap._commands(Path('.'), Path('data'), config)],
                         ['hub'])

    def test_external_backends_reject_malformed_values(self):
        config = json.loads((ROOT / 'hub' / 'konfiguration.windows.json').read_text(encoding='utf-8'))
        for value in ('print', ['hub'], ['print', 'print'], [1]):
            config['external_backends'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                bootstrap._commands(Path('.'), Path('data'), config)

    def test_provision_accepts_migrated_server_endpoint(self):
        provision_path = ROOT / 'provision.py'
        provision_spec = importlib.util.spec_from_file_location('server_provision', provision_path)
        provision = importlib.util.module_from_spec(provision_spec)
        provision_spec.loader.exec_module(provision)
        self.assertEqual(provision.validate_server_url('https://198.51.100.106:8773'),
                         'https://198.51.100.106:8773')
        with self.assertRaises(ValueError):
            provision.validate_server_url('http://198.51.100.106:8773')

    def test_native_catalog_is_validated_without_secrets(self):
        apps_path = Path(__file__).parents[3] / 'linux-elevhub' / 'server' / 'elevhub' / 'apps.py'
        spec = importlib.util.spec_from_file_location('apps', apps_path)
        apps = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(apps)
        base = {'hub_port': 1, 'laerer_port': 2, 'apps': [
            {'id': 'cadoodle', 'navn': 'CaDoodle', 'type': 'native', 'native_app': 'cadoodle'},
            {'id': 'word', 'navn': 'Word', 'type': 'native', 'native_app': 'word'}]}
        self.assertIs(apps.valider(base, set()), base)
        bad = dict(base, apps=[dict(base['apps'][0], port=123)])
        with self.assertRaises(ValueError):
            apps.valider(bad, set())

    def test_native_apps_never_create_central_port_endpoints(self):
        import sys
        hub_root = Path(__file__).parents[3] / 'linux-elevhub' / 'server' / 'elevhub'
        sys.path.insert(0, str(hub_root))
        try:
            spec = importlib.util.spec_from_file_location('elevhub', hub_root / 'elevhub.py')
            elevhub = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(elevhub)
            apps = json.loads((ROOT / 'hub' / 'konfiguration.windows.json').read_text(encoding='utf-8'))['apps']
            endpoints = list(elevhub.elevindgange({'apps': apps}))
            self.assertNotIn('word', [a['id'] for a in endpoints])
            self.assertNotIn('cadoodle', [a['id'] for a in endpoints])
            self.assertIn('3dprint', [a['id'] for a in endpoints])
        finally:
            sys.path.pop(0)


if __name__ == '__main__':
    unittest.main()
