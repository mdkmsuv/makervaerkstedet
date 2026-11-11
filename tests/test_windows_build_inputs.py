"""Regression tests for portable, hash-bound Windows package inputs."""

import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'windows_build_packages_inputs', ROOT / 'windows-elevhub' / 'build_packages.py')
build_packages = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_packages)


class WindowsBuildInputTests(unittest.TestCase):
    def test_actual_dual_python_tag_metadata_is_compatible_but_python2_alone_is_not(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            filename = 'colorama-0.4.6-py2.py3-none-any.whl'
            files = {'colorama-0.4.6.dist-info/METADATA': b'Name: colorama\nVersion: 0.4.6\n',
                     'colorama-0.4.6.dist-info/WHEEL': b'Wheel-Version: 1.0\nTag: py2-none-any\nTag: py3-none-any\n'}
            self.write_zip(root / filename, files)
            self.assertEqual(build_packages._single_wheel(root, 'colorama-').name, filename)
            files['colorama-0.4.6.dist-info/WHEEL'] = b'Wheel-Version: 1.0\nTag: py2-none-any\n'
            self.write_zip(root / filename, files)
            with self.assertRaisesRegex(ValueError, 'Python 3 kræves'):
                build_packages._single_wheel(root, 'colorama-')

    def write_zip(self, path, files):
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as package:
            for name, content in files.items():
                package.writestr(name, content)

    def make_manifest(self, root, omit=None):
        omit = set(omit or ())
        artifacts = {}
        for index, name in enumerate(build_packages.ARTIFACT_NAMES):
            if name in omit:
                continue
            source = root / 'inputs' / name.replace('.', '/')
            source.mkdir(parents=True)
            (source / 'payload.txt').write_text(f'{name}-{index}', encoding='utf-8')
            if name == 'runtime.python':
                self.write_zip(source / build_packages.PYTHON_ARCHIVE, {
                    'python.exe': b'python-runtime',
                    'python314.dll': b'dll',
                })
            elif name == 'runtime.wheels':
                for wheel_index, prefix in enumerate(build_packages.SERVER_WHEEL_PREFIXES):
                    version, native = build_packages.WHEEL_REQUIREMENTS[prefix]
                    tags = 'cp314-cp314-win_amd64' if native else 'py3-none-any'
                    distribution_name = prefix[:-1].replace('_', '-')
                    self.write_zip(source / f'{prefix}{version}-{tags}.whl', {
                        f'package{wheel_index}/__init__.py': b'wheel',
                        f'package{wheel_index}-1.dist-info/METADATA': (
                            f'Metadata-Version: 2.1\nName: {distribution_name}\nVersion: {version}\n'
                        ).encode('ascii'),
                        f'package{wheel_index}-1.dist-info/WHEEL': (
                            'Wheel-Version: 1.0\nGenerator: test\n'
                            f'Tag: {tags}\n'
                        ).encode('ascii'),
                    })
            elif name == 'client.webview2':
                for relative in build_packages.WEBVIEW2_FILES:
                    path = source / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'webview2')
            elif name == 'client.shell':
                for relative in build_packages.SHELL_FILES:
                    (source / relative).write_bytes(b'shell')
            elif name == 'client.circuitjs':
                (source / 'circuitjs.html').write_text('<html></html>', encoding='utf-8')
            artifacts[name] = {
                'path': str(source), 'version': f'{name}-test',
                'sha256': build_packages.artifact_tree_digest(source),
            }
        manifest = root / 'manifest.json'
        manifest.write_text(json.dumps({'schemaVersion': 1, 'artifacts': artifacts}), encoding='utf-8')
        return manifest

    def patch_python_hash(self, manifest):
        archive = Path(json.loads(manifest.read_text(encoding='utf-8'))['artifacts']['runtime.python']['path']) / build_packages.PYTHON_ARCHIVE
        return mock.patch.object(build_packages, 'PYTHON_HASH', build_packages.digest(archive))

    def test_schema_v1_manifest_accepts_explicit_runtime_and_client_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = self.make_manifest(Path(temporary))
            with self.patch_python_hash(manifest):
                loaded = build_packages.load_artifact_manifest(manifest)
                self.assertEqual(
                    set(build_packages.validate_build_inputs(loaded, 'client')),
                    {'runtime.python', 'client.webview2', 'client.shell', 'client.circuitjs'})
                self.assertEqual(
                    set(build_packages.validate_build_inputs(loaded, 'server')),
                    {'runtime.python', 'runtime.wheels'})

    def test_missing_binary_input_fails_before_output_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.make_manifest(root)
            missing = root / 'inputs/client/shell/ElevhubShell.exe'
            missing.unlink()
            value = json.loads(manifest.read_text(encoding='utf-8'))
            value['artifacts']['client.shell']['sha256'] = build_packages.artifact_tree_digest(
                root / 'inputs/client/shell')
            manifest.write_text(json.dumps(value), encoding='utf-8')
            output = root / 'out'
            with self.patch_python_hash(manifest):
                with self.assertRaisesRegex(ValueError, 'client.shell.*ElevhubShell.exe'):
                    build_packages.validate_build_inputs(
                        build_packages.load_artifact_manifest(manifest), 'client')
            self.assertFalse(output.exists())

    def test_client_builder_is_disabled_before_binary_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / 'output'
            with self.assertRaisesRegex(ValueError, 'Windows-elevklienten.*Linux'):
                build_packages.build_client(output)
            self.assertFalse(output.exists())

    def test_runtime_extracts_valid_small_archives_and_rejects_stale_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.make_manifest(root)
            target = root / 'package'
            target.mkdir()
            (target / 'runtime').mkdir()
            (target / 'runtime/python.exe').write_bytes(b'stale')
            with self.patch_python_hash(manifest):
                loaded = build_packages.load_artifact_manifest(manifest)
                with self.assertRaisesRegex(ValueError, 'frisk outputmappe'):
                    build_packages.runtime(
                        target, server=True,
                        inputs=build_packages.require_artifacts(
                            loaded, ('runtime.python', 'runtime.wheels')))
                shutil.rmtree(target / 'runtime')
                build_packages.runtime(
                    target, server=True,
                    inputs=build_packages.require_artifacts(
                        loaded, ('runtime.python', 'runtime.wheels')))
            self.assertEqual((target / 'runtime/python.exe').read_bytes(), b'python-runtime')
            self.assertEqual((target / 'runtime/Lib/site-packages/package0/__init__.py').read_bytes(), b'wheel')

    def test_zip_preflight_rejects_windows_aliases_ads_devices_and_limits(self):
        cases = (
            {'Runtime/file.txt': b'a', 'runtime/FILE.txt': b'b'},
            {'runtime/data:stream': b'a'},
            {'CON.txt': b'a'},
            {'../escape.txt': b'a'},
        )
        for files in cases:
            with self.subTest(files=files):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    manifest = self.make_manifest(root)
                    value = json.loads(manifest.read_text(encoding='utf-8'))
                    archive = root / 'inputs/runtime/python' / build_packages.PYTHON_ARCHIVE
                    self.write_zip(archive, files)
                    value['artifacts']['runtime.python']['sha256'] = build_packages.artifact_tree_digest(
                        root / 'inputs/runtime/python')
                    manifest.write_text(json.dumps(value), encoding='utf-8')
                    with self.patch_python_hash(manifest):
                        loaded = build_packages.load_artifact_manifest(manifest)
                        with self.assertRaises(ValueError):
                            build_packages.validate_build_inputs(loaded, 'client')

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.make_manifest(root)
            value = json.loads(manifest.read_text(encoding='utf-8'))
            archive = root / 'inputs/runtime/python' / build_packages.PYTHON_ARCHIVE
            self.write_zip(archive, {'python.exe': b'123456789'})
            value['artifacts']['runtime.python']['sha256'] = build_packages.artifact_tree_digest(
                root / 'inputs/runtime/python')
            manifest.write_text(json.dumps(value), encoding='utf-8')
            with self.patch_python_hash(manifest), \
                 mock.patch.object(build_packages, 'MAX_ARCHIVE_MEMBER_BYTES', 4):
                loaded = build_packages.load_artifact_manifest(manifest)
                with self.assertRaisesRegex(ValueError, 'for stort ZIP-medlem'):
                    build_packages.validate_build_inputs(loaded, 'client')

    def test_wheel_cannot_overwrite_existing_runtime_site_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.make_manifest(root)
            value = json.loads(manifest.read_text(encoding='utf-8'))
            archive = root / 'inputs/runtime/python' / build_packages.PYTHON_ARCHIVE
            self.write_zip(archive, {
                'python.exe': b'python-runtime',
                'python314.dll': b'dll',
                'Lib/site-packages/package0/__init__.py': b'from-python',
            })
            value['artifacts']['runtime.python']['sha256'] = build_packages.artifact_tree_digest(
                root / 'inputs/runtime/python')
            wheel = root / 'inputs/runtime/wheels' / 'paho_mqtt-2.1.0-py3-none-any.whl'
            self.write_zip(wheel, {
                'package0/__init__.py': b'collision',
                'package0-1.dist-info/METADATA': b'Metadata-Version: 2.1\nName: paho-mqtt\nVersion: 2.1.0\n',
                'package0-1.dist-info/WHEEL': b'Wheel-Version: 1.0\nTag: py3-none-any\n',
            })
            value['artifacts']['runtime.wheels']['sha256'] = build_packages.artifact_tree_digest(
                root / 'inputs/runtime/wheels')
            manifest.write_text(json.dumps(value), encoding='utf-8')
            with self.patch_python_hash(manifest):
                loaded = build_packages.load_artifact_manifest(manifest)
                target = root / 'package'
                with self.assertRaisesRegex(ValueError, 'kolliderende ZIP-navn|eksisterende fil'):
                    build_packages.runtime(
                        target, server=True,
                        inputs=build_packages.require_artifacts(
                            loaded, ('runtime.python', 'runtime.wheels')))

    def test_wheel_version_and_windows_tag_are_pinned(self):
        cases = ('paho_mqtt-9.9.9-py3-none-any.whl',
                 'paho_mqtt-2.1.0-cp314-cp314-win_amd64.whl',
                 'paho_mqtt-2.1.0-py3-none-any.metadata-bad.whl',
                 'paho_mqtt-2.1.0-py3-none-any.wheel-bad.whl')
        for bad_name in cases:
            with self.subTest(bad_name=bad_name):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    manifest = self.make_manifest(root)
                    wheel_dir = root / 'inputs/runtime/wheels'
                    for wheel in wheel_dir.glob('paho_mqtt-*.whl'):
                        wheel.unlink()
                    metadata_version = '9.9.9' if bad_name.endswith('metadata-bad.whl') else '2.1.0'
                    wheel_tag = 'cp314-cp314-win_amd64' if bad_name.endswith('wheel-bad.whl') else 'py3-none-any'
                    actual_name = bad_name.replace('.metadata-bad.whl', '.whl').replace('.wheel-bad.whl', '.whl')
                    self.write_zip(wheel_dir / actual_name, {
                        'package/__init__.py': b'bad',
                        'package-1.dist-info/METADATA': f'Metadata-Version: 2.1\nName: paho-mqtt\nVersion: {metadata_version}\n'.encode('ascii'),
                        'package-1.dist-info/WHEEL': f'Wheel-Version: 1.0\nTag: {wheel_tag}\n'.encode('ascii'),
                    })
                    value = json.loads(manifest.read_text(encoding='utf-8'))
                    value['artifacts']['runtime.wheels']['sha256'] = build_packages.artifact_tree_digest(wheel_dir)
                    manifest.write_text(json.dumps(value), encoding='utf-8')
                    with self.patch_python_hash(manifest):
                        loaded = build_packages.load_artifact_manifest(manifest)
                        with self.assertRaisesRegex(ValueError, 'runtime.wheels'):
                            build_packages.validate_build_inputs(loaded, 'server')

        for bad_tags in ('cp313-cp313-win_amd64', 'cp999-cp999-win_amd64', 'cp999-abi3-win_amd64'):
            with self.subTest(bad_tags=bad_tags):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    manifest = self.make_manifest(root)
                    wheel_dir = root / 'inputs/runtime/wheels'
                    for wheel in wheel_dir.glob('pillow-*.whl'):
                        wheel.unlink()
                    self.write_zip(wheel_dir / f'pillow-12.3.0-{bad_tags}.whl', {
                        'PIL/__init__.py': b'bad',
                        'Pillow-1.dist-info/METADATA': b'Metadata-Version: 2.1\nName: Pillow\nVersion: 12.3.0\n',
                        'Pillow-1.dist-info/WHEEL': f'Wheel-Version: 1.0\nTag: {bad_tags}\n'.encode('ascii'),
                    })
                    value = json.loads(manifest.read_text(encoding='utf-8'))
                    value['artifacts']['runtime.wheels']['sha256'] = build_packages.artifact_tree_digest(wheel_dir)
                    manifest.write_text(json.dumps(value), encoding='utf-8')
                    with self.patch_python_hash(manifest):
                        loaded = build_packages.load_artifact_manifest(manifest)
                        with self.assertRaisesRegex(ValueError, 'runtime.wheels'):
                            build_packages.validate_build_inputs(loaded, 'server')

    def test_main_preflight_rejects_bad_archive_before_output_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.make_manifest(root)
            archive = root / 'inputs/runtime/python' / build_packages.PYTHON_ARCHIVE
            self.write_zip(archive, {'../escape.txt': b'bad'})
            value = json.loads(manifest.read_text(encoding='utf-8'))
            value['artifacts']['runtime.python']['sha256'] = build_packages.artifact_tree_digest(
                root / 'inputs/runtime/python')
            manifest.write_text(json.dumps(value), encoding='utf-8')
            output = root / 'new-output'
            with self.patch_python_hash(manifest), \
                 mock.patch.object(sys, 'argv', [
                     'build_packages.py', '--role', 'server', '--output', str(output),
                     '--artifact-manifest', str(manifest)]):
                with self.assertRaises(ValueError):
                    build_packages.main()
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
