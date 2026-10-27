"""Contract tests for explicit, hash-bound Windows build artifacts."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('windows_build_packages', ROOT / 'windows-elevhub' / 'build_packages.py')
build_packages = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_packages)
SCHOOL_SPEC = importlib.util.spec_from_file_location('school_package_builder', ROOT / 'scripts' / 'build_school_package.py')
school_builder = importlib.util.module_from_spec(SCHOOL_SPEC)
SCHOOL_SPEC.loader.exec_module(school_builder)


class WindowsArtifactManifestTests(unittest.TestCase):
    def make_manifest(self, root, names=None):
        names = names or build_packages.ARTIFACT_NAMES
        artifacts = {}
        for index, name in enumerate(names):
            source = root / name.replace('.', '/')
            source.mkdir(parents=True)
            (source / 'payload.txt').write_text(f'{name}-{index}', encoding='utf-8')
            artifacts[name] = {
                'path': str(source),
                'version': f'{name}-20261012',
                'sha256': build_packages.artifact_tree_digest(source),
            }
        manifest = root / 'artifacts.json'
        manifest.write_text(json.dumps({'schemaVersion': 1, 'artifacts': artifacts}), encoding='utf-8')
        return manifest, artifacts

    def test_manifest_binds_versions_and_tree_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest, expected = self.make_manifest(Path(temporary))
            loaded = build_packages.load_artifact_manifest(manifest)
            self.assertEqual(loaded['artifacts']['server.tools']['version'], 'server.tools-20261012')
            self.assertEqual(loaded['artifacts']['client.cadoodle']['sha256'], expected['client.cadoodle']['sha256'])

    def test_changed_source_is_rejected_before_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, _ = self.make_manifest(root)
            (root / 'server/tools/payload.txt').write_text('changed', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'server.tools.*SHA-256'):
                build_packages.load_artifact_manifest(manifest)

    def test_server_sources_do_not_include_private_features(self):
        with tempfile.TemporaryDirectory() as temporary:
            names = build_packages.ARTIFACT_NAMES
            manifest, _ = self.make_manifest(Path(temporary), names)
            loaded = build_packages.load_artifact_manifest(manifest)
            self.assertNotIn('server.ai', loaded['artifacts'])
            self.assertEqual(set(build_packages.require_artifacts(
                loaded, ('server.tools', 'server.licenses'))),
                {'server.tools', 'server.licenses'})

    def test_private_printer_and_queue_data_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, _ = self.make_manifest(root)
            private = root / 'server/tools/Skolens-printere-privat.json'
            private.write_text('{}', encoding='utf-8')
            # Re-sealing the digest must not make a forbidden source public.
            value = json.loads(manifest.read_text(encoding='utf-8'))
            source = root / 'server/tools'
            value['artifacts']['server.tools']['sha256'] = build_packages.artifact_tree_digest(source)
            manifest.write_text(json.dumps(value), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'privat printer'):
                build_packages.load_artifact_manifest(manifest)

    def test_public_profiles_certificates_and_runtime_names_are_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, _ = self.make_manifest(root)
            source = root / 'server/tools'
            (source / 'Bambu Studio/resources/profiles/BBL').mkdir(parents=True)
            (source / 'Bambu Studio/resources/profiles/BBL/printer-profile.json').write_text('{}', encoding='utf-8')
            (source / 'public-ca.pem').write_text(
                '-----BEGIN CERTIFICATE-----\npublic\n-----END CERTIFICATE-----\n', encoding='ascii')
            (source / 'queue.py').write_text('def queue(): pass\n', encoding='utf-8')
            (source / 'credential-manager.py').write_text('class CredentialManager: pass\n', encoding='utf-8')
            value = json.loads(manifest.read_text(encoding='utf-8'))
            value['artifacts']['server.tools']['sha256'] = build_packages.artifact_tree_digest(source)
            manifest.write_text(json.dumps(value), encoding='utf-8')
            loaded = build_packages.load_artifact_manifest(manifest)
            self.assertEqual(loaded['artifacts']['server.tools']['path'], source.resolve())

    def test_private_key_content_is_rejected_even_with_arbitrary_filename(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, _ = self.make_manifest(root)
            source = root / 'server/tools'
            (source / 'runtime.bin').write_bytes(b'prefix\n-----BEGIN PRIVATE KEY-----\nsecret\n')
            value = json.loads(manifest.read_text(encoding='utf-8'))
            value['artifacts']['server.tools']['sha256'] = build_packages.artifact_tree_digest(source)
            manifest.write_text(json.dumps(value), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'privat nøgle'):
                build_packages.load_artifact_manifest(manifest)

    def test_private_connection_access_code_and_settings_queue_database_are_rejected(self):
        for relative, expected in (
                ('connection.json', 'forbindelsesdata'),
                ('settings/queue.sqlite3', 'settings-/kødatabase')):
            with self.subTest(relative=relative):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    manifest, _ = self.make_manifest(root)
                    private = root / 'server/tools' / relative
                    private.parent.mkdir(parents=True, exist_ok=True)
                    if relative == 'connection.json':
                        private.write_text('{"host":"printer","access_code":"secret"}', encoding='utf-8')
                    else:
                        private.write_bytes(b'queue data')
                    value = json.loads(manifest.read_text(encoding='utf-8'))
                    value['artifacts']['server.tools']['sha256'] = build_packages.artifact_tree_digest(root / 'server/tools')
                    manifest.write_text(json.dumps(value), encoding='utf-8')
                    with self.assertRaisesRegex(ValueError, expected):
                        build_packages.load_artifact_manifest(manifest)

    def test_duplicate_keys_and_boolean_schema_version_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            duplicate = root / 'duplicate.json'
            duplicate.write_text('{"schemaVersion":1,"schemaVersion":1,"artifacts":{}}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'kunne ikke læses'):
                build_packages.load_artifact_manifest(duplicate)
            boolean = root / 'boolean.json'
            boolean.write_text('{"schemaVersion":true,"artifacts":{}}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'schemaVersion=1'):
                build_packages.load_artifact_manifest(boolean)

    def test_output_overlap_is_rejected_for_any_manifest_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, _ = self.make_manifest(root)
            loaded = build_packages.load_artifact_manifest(manifest)
            with self.assertRaisesRegex(ValueError, 'build-outputtet'):
                build_packages.require_artifacts(loaded, ('server.licenses',), root / 'server')

    def test_actual_copy_accepts_public_payload_and_rejects_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'tools'
            source.mkdir()
            (source / 'profiles').mkdir()
            (source / 'profiles/BBL-printer-profile.json').write_text('{}', encoding='utf-8')
            (source / 'public.pem').write_text('-----BEGIN CERTIFICATE-----\nca\n', encoding='ascii')
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'schemaVersion': 1, 'artifacts': {
                'server.tools': {'path': str(source), 'version': 'test',
                                 'sha256': build_packages.artifact_tree_digest(source)},
            }}), encoding='utf-8')
            entry = build_packages.load_artifact_manifest(manifest)['artifacts']['server.tools']
            target = root / 'copied'
            build_packages.copy_artifact('server.tools', entry, target)
            self.assertEqual((target / 'public.pem').read_text(encoding='ascii'), '-----BEGIN CERTIFICATE-----\nca\n')
            (source / 'changed.txt').write_text('changed', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'ændret efter manifestvalidering'):
                build_packages.copy_artifact('server.tools', entry, root / 'copied-again')

    def test_symlinked_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'tools'
            source.mkdir()
            real = root / 'real.txt'
            real.write_text('public', encoding='utf-8')
            try:
                os.symlink(real, source / 'linked.txt')
            except (OSError, NotImplementedError):
                self.skipTest('symlink creation is unavailable on this host')
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'schemaVersion': 1, 'artifacts': {
                'server.tools': {'path': str(source), 'version': 'test', 'sha256': '0' * 64},
            }}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'symlinks|reparse'):
                build_packages.load_artifact_manifest(manifest)

    def test_reparse_parent_cannot_reach_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real = root / 'real'
            real.mkdir()
            (real / 'payload.txt').write_text('public', encoding='utf-8')
            alias = root / 'alias'
            try:
                os.symlink(real, alias, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest('directory symlink creation is unavailable on this host')
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'schemaVersion': 1, 'artifacts': {
                'server.tools': {'path': str(alias), 'version': 'test',
                                 'sha256': build_packages.artifact_tree_digest(real)},
            }}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'symlink|junction|reparse'):
                build_packages.load_artifact_manifest(manifest)

    def test_server_builder_copies_only_manifest_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, _ = self.make_manifest(root)
            output = root / 'output'; output.mkdir()
            copied = []

            def record(source, target):
                copied.append((Path(source), Path(target)))
                if Path(source).is_dir():
                    Path(target).mkdir(parents=True, exist_ok=True)

            # This test exercises artifact selection/copying. Real runtime
            # ZIP/wheel preflight is covered by test_windows_build_inputs.
            with mock.patch.object(build_packages, 'validate_build_inputs',
                                   return_value={}), \
                 mock.patch.object(build_packages, 'runtime'), \
                 mock.patch.object(build_packages, 'copy_tree', side_effect=record), \
                 mock.patch.object(build_packages.shutil, 'copy2', side_effect=record):
                build_packages.build_server(output, artifact_manifest=manifest)

            sources = {source for source, _ in copied}
            self.assertIn(root / 'server/tools', sources)
            self.assertIn(root / 'server/licenses', sources)
            self.assertFalse((output / 'ai').exists())
            self.assertFalse(any('Elevhub-VM' in str(source) or 'Printvaerksted-' in str(source) for source in sources))
            package = json.loads((output / 'printvaerksted-package.json').read_text(encoding='utf-8'))
            self.assertEqual(package['version'], 'server.tools-20261012')

    def test_client_builder_is_disabled_before_output_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / 'output'
            with self.assertRaisesRegex(ValueError, 'Windows-elevklienten.*Linux'):
                build_packages.build_client(output)
            self.assertFalse(output.exists())

    def test_cli_rejects_client_role_before_output_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / 'output'
            argv = ['build_packages.py', '--role', 'client', '--output', str(output),
                    '--artifact-manifest', str(root / 'artifacts.json')]
            with mock.patch.object(sys, 'argv', argv), self.assertRaises(SystemExit) as raised:
                build_packages.main()
            self.assertEqual(raised.exception.code, 2)
            self.assertFalse(output.exists())

    def test_missing_manifest_fails_without_legacy_fallback(self):
        with self.assertRaisesRegex(ValueError, 'artifact-manifest'):
            build_packages.load_artifact_manifest(None)

    def test_package_manifest_records_verified_artifact_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, _ = self.make_manifest(root)
            output = root / 'output'; output.mkdir()
            (output / 'server').mkdir()
            build_packages.manifest(output, 'server', artifact_manifest=manifest)
            package = json.loads((output / 'elevhub-package.json').read_text(encoding='utf-8'))
            self.assertEqual(package['artifactSources']['server.tools']['version'], 'server.tools-20261012')
            self.assertEqual(package['artifactSources']['client.cadoodle']['sha256'],
                             json.loads(manifest.read_text(encoding='utf-8'))['artifacts']['client.cadoodle']['sha256'])
            self.assertEqual(len(package['artifactManifestSha256']), 64)

    def test_school_builder_refuses_missing_manifest_before_output_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'school'
            with self.assertRaisesRegex(ValueError, 'artifact-manifest'):
                school_builder.build(output, 'vm', 'Test', 'green')
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
