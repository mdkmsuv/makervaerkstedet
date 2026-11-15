"""Distributable packages exclude private development features and runtimes."""
from pathlib import Path as _LayoutPath
import sys as _layout_sys
_LAYOUT_ROOT = next(p for p in _LayoutPath(__file__).resolve().parents if (p / 'REPOSITORY-LAYOUT.json').is_file())
_layout_sys.path[:0] = [str(_LAYOUT_ROOT), str(_LAYOUT_ROOT / 'system')]
from repository import source_path, repo_root
import importlib.util
import inspect
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from app import laser_station_install as station

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, source_path(relative, ROOT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load('feature_package_builder', 'windows-elevhub/build_packages.py')
updater = load('feature_update_builder', 'scripts/school_server_update/update.py')
school = load('feature_school_builder', 'scripts/build_school_package.py')


class PackageFeatureBoundaryTests(unittest.TestCase):
    def test_builders_offer_no_private_feature_switch(self):
        for function in (builder.build_server, builder.validate_build_inputs, school.build):
            self.assertNotIn('include_local_ai', inspect.signature(function).parameters)
        self.assertNotIn('server.ai', builder.ARTIFACT_NAMES)

    def test_source_copy_leaves_private_development_files_at_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.mkdir()
            for name in ('laser_server.py', 'local_ai.py', 'server_ai_artifact.py', 'bed_ai.py', 'training_samples.py', 'plate_camera_client.py', 'camera-batch.js', 'plate-camera.js'):
                (source / name).write_text('# fixture\n')
            builder.copy_tree(source, root / 'package')
            self.assertEqual({p.name for p in (root / 'package').iterdir()}, {'laser_server.py'})
            self.assertTrue((source / 'local_ai.py').exists())

    def test_server_package_contains_station_web_dependencies_without_optional_editors(self):
        # Exercise the real source/documentation staging. Only binary/tool edges
        # are replaced; a source layout change must not silently break a station.
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'package'
            target.mkdir()
            artifacts = {name: {'version': 'fixture'} for name in ('server.tools', 'server.licenses')}
            with patch.object(builder, 'load_artifact_manifest', return_value={}), \
                 patch.object(builder, 'require_artifacts', return_value=artifacts), \
                 patch.object(builder, 'validate_build_inputs'), \
                 patch.object(builder, 'runtime'), \
                 patch.object(builder, 'copy_artifact', side_effect=lambda name, value, output: output.mkdir(parents=True, exist_ok=True)):
                builder.build_server(target)
            missing = sorted(name for name in station.WEB_FILES if not (target / name).is_file())
            self.assertEqual(missing, [], 'Native teacher/station assets must remain complete after app separation')
            for name in station.WEB_FILES:
                self.assertEqual((target / name).read_bytes(), source_path(name, ROOT).read_bytes())
            for name in ('web/laser.html', 'web/model3d.html', 'web/laservaerksted'):
                self.assertFalse((target / name).exists(), name)

    def test_external_artifacts_cannot_smuggle_private_models_or_runtime(self):
        for name in ('model.onnx', 'local_ai.py', 'plate_camera_client.py', 'camera-batch.js', 'plate-camera.js', 'torch/__init__.py', 'timm/__init__.py', 'safetensors/__init__.py', 'ai/model.bin'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary)
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
                with self.assertRaisesRegex(ValueError, 'excluded private feature'):
                    builder._validate_public_artifact('server.tools', source)

    def test_external_archive_is_also_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            with zipfile.ZipFile(source / 'runtime.zip', 'w') as archive:
                archive.writestr('Lib/site-packages/onnxruntime/__init__.py', '')
            with self.assertRaisesRegex(ValueError, 'excluded private feature'):
                builder._validate_public_artifact('server.tools', source)

    def test_trusted_hash_does_not_authorize_private_runtime(self):
        for name in ('runtime/Lib/site-packages/torch/__init__.py', 'runtime/model.onnx', 'runtime/inference.zip'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                trusted = root / 'trusted'
                path = trusted / name
                path.parent.mkdir(parents=True)
                if path.suffix == '.zip':
                    with zipfile.ZipFile(path, 'w') as archive:
                        archive.writestr('rembg/__init__.py', '')
                else:
                    path.write_bytes(b'private runtime fixture')
                (trusted / 'integrity.json').write_text(json.dumps({'files': {name: updater.hashfile(path)}}))
                with self.assertRaisesRegex(ValueError, 'Excluded private feature'):
                    updater.build(root / 'source', trusted, root / 'candidate', 'test')
                self.assertFalse((root / 'candidate').exists())
                self.assertTrue(path.exists())

    def test_explicit_datatraener_artifact_may_include_educational_models(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / 'model.onnx').write_bytes(b'educational model fixture')
            builder._validate_public_artifact('client.datatraener', source)
            with self.assertRaisesRegex(ValueError, 'excluded private feature'):
                builder._validate_public_artifact('server.tools', source)

    def test_declared_trusted_runtime_is_rejected_during_candidate_verify(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'candidate.json').write_text(json.dumps({
                'format': updater.FORMAT, 'files': {'hub': {}, 'print': {}},
                'trusted_runtime': {'runtime/torch/__init__.py': '0' * 64}}))
            with self.assertRaisesRegex(ValueError, 'Excluded private feature in trusted runtime'):
                updater.verify(root)

    def test_windows_package_excludes_retired_app_paths_and_keeps_administration(self):
        for name in ('datatraener/tfjs.min.js', 'datatraener/model.onnx', 'datatraener/model.pt'):
            self.assertTrue(builder.private_feature_path(name))
        for name in ('scratch/index.html', 'school_maintenance.py'):
            self.assertFalse(builder.private_feature_path(name))
            self.assertFalse(updater.private_feature_path(name))


if __name__ == '__main__':
    unittest.main()
