import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from app.generation_runtime import START_FILES, build_command, validate_generation


ROOT = Path(__file__).resolve().parents[1]


class GenerationRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.package = self.root / 'candidate'
        self.stable = self.root / 'stable'
        self._make_fixture()

    def tearDown(self):
        self.temp.cleanup()

    def _make_fixture(self):
        launcher = ROOT / 'app' / 'generation_runtime.py'
        for root in (self.package, self.stable):
            (root / 'app').mkdir(parents=True)
            for relative in START_FILES:
                source = ROOT / relative
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            (root / 'app' / '__init__.py').write_text('# regular package fixture\n', encoding='utf-8')
        (self.package / 'app' / 'laser_station.py').write_text(
            "import json, os, sys\n"
            "print(json.dumps({'cwd': os.getcwd(), 'origin': __file__, "
            "'pythonpath': os.environ.get('PYTHONPATH'), 'argv': sys.argv}, sort_keys=True))\n",
            encoding='utf-8')
        (self.package / 'hub').mkdir()
        (self.package / 'hub' / 'elevhub.py').write_text(
            "import json, os, helper\nprint(json.dumps({'hub': __file__, 'helper': helper.VALUE, 'cwd': os.getcwd()}, sort_keys=True))\n",
            encoding='utf-8')
        (self.package / 'hub' / 'helper.py').write_text('VALUE = "candidate"\n', encoding='utf-8')
        (self.stable / 'hub').mkdir()
        (self.stable / 'hub' / 'helper.py').write_text('VALUE = "stable-decoy"\n', encoding='utf-8')
        (self.package / 'CREDITS.md').write_text('Kreditering: Klostermarksskolen.\n', encoding='utf-8')
        tag = f'python{sys.version_info.major}{sys.version_info.minor}'
        for root in (self.package, self.stable):
            runtime = root / 'runtime'
            (runtime / 'Lib' / 'site-packages').mkdir(parents=True)
            (runtime / 'python.exe').write_bytes(b'fixture only; never executed')
            (runtime / (tag + '.zip')).write_bytes(b'embedded-runtime-fixture')
            (runtime / (tag + '._pth')).write_text(
                f'{tag}.zip\n.\n..\nLib/site-packages\n', encoding='utf-8')
        files = {str(path.relative_to(self.package)).replace('\\', '/')
                 for path in self.package.rglob('*') if path.is_file()}
        records = {}
        for name in sorted(files):
            raw = (self.package / name).read_bytes()
            records[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
        manifest = {'format': 1, 'product': 'Makerværkstedet', 'release': 'fixture-1',
                    'credits': 'Kreditering: Klostermarksskolen.',
                    'data_contract': {'hub': 1}, 'files': records}
        raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode('utf-8')
        (self.package / 'makerspace-release.json').write_bytes(raw)
        self.manifest_sha256 = hashlib.sha256(raw).hexdigest()

    def _reseal(self):
        manifest_path = self.package / 'makerspace-release.json'
        value = json.loads(manifest_path.read_text(encoding='utf-8'))
        files = {str(path.relative_to(self.package)).replace('\\', '/')
                 for path in self.package.rglob('*') if path.is_file() and path != manifest_path}
        value['files'] = {}
        for name in sorted(files):
            raw = (self.package / name).read_bytes()
            value['files'][name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')
        manifest_path.write_bytes(raw)
        self.manifest_sha256 = hashlib.sha256(raw).hexdigest()

    def test_valid_generation_and_build_command(self):
        result = validate_generation(self.package, self.stable, self.manifest_sha256)
        self.assertEqual(result, {'release': 'fixture-1', 'manifest_sha256': self.manifest_sha256})
        command = build_command(self.package, self.stable, self.manifest_sha256, 'laser-station', ['--sentinel'])
        self.assertEqual(command[:4], [str(self.stable / 'runtime/python.exe'), '-B', '-I',
                                       str(self.stable / 'app/generation_runtime.py')])
        self.assertEqual(command[-2:], ['--', '--sentinel'])

    def test_manifest_hash_and_exact_runtime_inventory_are_required(self):
        with self.assertRaisesRegex(ValueError, 'identitet'):
            validate_generation(self.package, self.stable, '0' * 64)
        (self.package / 'extra.txt').write_text('extra', encoding='utf-8')
        with self.assertRaises(ValueError):
            validate_generation(self.package, self.stable, self.manifest_sha256)

    def test_valid_resealed_candidate_still_requires_stable_runtime_and_launcher(self):
        tag = f'python{sys.version_info.major}{sys.version_info.minor}'
        (self.package / 'runtime' / (tag + '.zip')).write_bytes(b'new-candidate-runtime')
        self._reseal()
        with self.assertRaises(ValueError):
            validate_generation(self.package, self.stable, self.manifest_sha256)
        shutil.copy2(self.package / 'runtime' / (tag + '.zip'), self.stable / 'runtime' / (tag + '.zip'))
        extra = self.stable / 'runtime' / 'extra.dll'
        extra.write_bytes(b'extra')
        with self.assertRaises(ValueError):
            validate_generation(self.package, self.stable, self.manifest_sha256)
        extra.unlink()
        (self.stable / 'app' / 'generation_runtime.py').write_text('changed launcher', encoding='utf-8')
        with self.assertRaises(ValueError):
            validate_generation(self.package, self.stable, self.manifest_sha256)

    def test_each_stable_start_helper_is_sealed(self):
        for relative in START_FILES:
            with self.subTest(relative=relative):
                path = self.stable / relative
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b'\n# tampered stable helper\n')
                    with self.assertRaises(ValueError):
                        validate_generation(self.package, self.stable, self.manifest_sha256)
                finally:
                    path.write_bytes(original)

    def test_requested_entry_requires_role_source(self):
        with self.assertRaises(ValueError):
            validate_generation(self.package, self.stable, self.manifest_sha256, entry='print')

    def test_enrollment_requires_its_manifest_bound_source(self):
        with self.assertRaisesRegex(ValueError, 'indgang'):
            validate_generation(self.package, self.stable, self.manifest_sha256, entry='enrollment')
        script = self.package / 'deployment/school-setup/enrollment.py'
        script.parent.mkdir(parents=True)
        shutil.copy2(ROOT / 'deployment/school-setup/enrollment.py', script)
        self._reseal()
        validate_generation(self.package, self.stable, self.manifest_sha256, entry='enrollment')
        script.write_bytes(script.read_bytes() + b'\n# changed after selection\n')
        with self.assertRaises(ValueError):
            validate_generation(self.package, self.stable, self.manifest_sha256, entry='enrollment')

    def test_enrollment_bytecode_is_rejected_even_when_manifested(self):
        cached = self.package / 'deployment/school-setup/__pycache__/enrollment.cpython-314.pyc'
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b'old enrollment module')
        self._reseal()
        with self.assertRaisesRegex(ValueError, 'bytecode'):
            validate_generation(self.package, self.stable, self.manifest_sha256)

    def test_sealed_stale_bytecode_is_rejected(self):
        cached = self.package / 'app/__pycache__/laser_station.cpython-314.pyc'
        cached.parent.mkdir()
        cached.write_bytes(b'stale cached module')
        self._reseal()
        with self.assertRaisesRegex(ValueError, 'bytecode'):
            validate_generation(self.package, self.stable, self.manifest_sha256)

    def test_linked_program_root_is_rejected(self):
        link = self.root / 'linked-program'
        try:
            link.symlink_to(self.package, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable')
        with self.assertRaisesRegex(ValueError, 'links'):
            validate_generation(link, self.stable, self.manifest_sha256)

    def test_pth_policy_rejects_import_site_absolute_and_traversal(self):
        tag = f'python{sys.version_info.major}{sys.version_info.minor}'
        path = self.package / 'runtime' / (tag + '._pth')
        from app.generation_runtime import _pth
        for lines in (f'{tag}.zip\n.\n..\nimport site\n',
                      f'{tag}.zip\n.\n..\nC:/absolute\n',
                      f'{tag}.zip\n.\n..\n../escape\n'):
            path.write_text(lines, encoding='utf-8')
            with self.assertRaises(ValueError):
                _pth(self.package / 'runtime')
        path.write_text(f'{tag}.zip\n.\n..\nLib/site-packages\n', encoding='utf-8')
        duplicate = self.package / 'runtime' / 'other._pth'
        duplicate.write_text('', encoding='utf-8')
        try:
            with self.assertRaises(ValueError):
                _pth(self.package / 'runtime')
        finally:
            duplicate.unlink()

    def test_subprocess_import_selection_uses_candidate_and_cleans_decoys(self):
        harness = self.root / 'harness.py'
        harness.write_text(
            'import importlib.util, json, sys\n'
            'spec = importlib.util.spec_from_file_location("generation_runtime", sys.argv[1])\n'
            'module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)\n'
            'module.prepare_imports(sys.argv[2], sys.argv[3], "laser-station")\n'
            'import app.generation_runtime as chosen\n'
            'chosen.bind_generation(sys.argv[2], sys.argv[3], sys.argv[4])\n'
            'print(json.dumps({"child": chosen.child_command("app.laser_station", ["x"]), "bound": True}))\n'
            'try: chosen.bind_generation(sys.argv[2], sys.argv[3] + "/../other-runtime", sys.argv[4])\n'
            'except ValueError: print(json.dumps({"rebind_rejected": True}))\n'
            'module.dispatch(sys.argv[2], "laser-station", ["sentinel"])\n',
            encoding='utf-8')
        decoy = self.root / 'decoy'
        (decoy / 'app').mkdir(parents=True)
        (decoy / 'app' / '__init__.py').write_text('raise RuntimeError("decoy imported")', encoding='utf-8')
        (decoy / 'app' / 'laser_station.py').write_text('raise RuntimeError("decoy imported")', encoding='utf-8')
        (decoy / 'helper.py').write_text('VALUE = "cwd-decoy"', encoding='utf-8')
        (self.root / 'other-runtime').mkdir()
        env = dict(os.environ, PYTHONPATH=str(decoy))
        env.pop('PYTHONHOME', None)
        completed = subprocess.run([sys.executable, '-I', '-B', str(harness), str(self.package / 'app' / 'generation_runtime.py'),
                                    str(self.package), str(self.stable), self.manifest_sha256], capture_output=True, text=True,
                                   cwd=decoy, env=env, timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        lines = [json.loads(line) for line in completed.stdout.splitlines()]
        child = next(item for item in lines if 'child' in item)
        result = next(item for item in lines if 'origin' in item)
        self.assertTrue(child['bound'])
        self.assertTrue(next(item for item in lines if 'rebind_rejected' in item)['rebind_rejected'])
        self.assertEqual(child['child'][0], str(self.stable / 'runtime' / 'python.exe'))
        self.assertTrue(result['origin'].startswith(str(self.package)))
        self.assertIsNone(result['pythonpath'])
        self.assertEqual(Path(result['cwd']).resolve(), (self.stable / 'runtime').resolve())

        hub_harness = self.root / 'hub-harness.py'
        hub_harness.write_text(
            'import importlib.util, pkgutil, sys\n'
            'spec = importlib.util.spec_from_file_location("generation_runtime", sys.argv[1])\n'
            'module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)\n'
            'module.prepare_imports(sys.argv[2], sys.argv[3], "hub")\n'
            'module.dispatch(sys.argv[2], "hub", [])\n', encoding='utf-8')
        hub_run = subprocess.run([sys.executable, '-I', '-B', str(hub_harness), str(self.package / 'app' / 'generation_runtime.py'),
                                  str(self.package), str(self.stable)], capture_output=True, text=True,
                                 cwd=decoy, env=env, timeout=20)
        self.assertEqual(hub_run.returncode, 0, hub_run.stderr)
        hub_result = json.loads(hub_run.stdout)
        self.assertTrue(hub_result['hub'].startswith(str(self.package)))
        self.assertEqual(hub_result['helper'], 'candidate')


if __name__ == '__main__':
    unittest.main()
