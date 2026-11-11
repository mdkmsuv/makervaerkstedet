"""Real package/program manifests must be complete without circular hashes."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

from scripts.build_school_package import digest, write_release_manifest
from tests import test_release_compatibility as fixtures

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('school_package_verify', ROOT / 'windows-elevhub/server/verify.py')
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class SchoolPackageManifestTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReleaseCompatibilityTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.program = self.fixture._minimal_program({'hub': 1, 'print': 1, 'laser': 1})
        (self.program / 'makerspace-release.json').unlink()
        for name in ('runtime/python.exe', 'app/process_tree.py', 'app/paths.py', 'server/verify.py',
                     'server/provision.py', 'server/install.ps1', 'server/fjern.ps1', 'server/launchers/enroll.ps1'):
            path = self.program / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'')
        (self.program / 'web').mkdir(exist_ok=True)
        config = ROOT / 'windows-elevhub/server/hub/konfiguration.windows.json'
        (self.program / 'hub/konfiguration.windows.json').write_bytes(config.read_bytes())
        (self.program / 'elevhub-package.json').write_text(json.dumps({
            'schemaVersion': 1, 'product': 'Elevhub', 'role': 'server', 'server': None,
            'platform': 'windows-x64', 'containsCredentials': False, 'requiresSchoolSetup': True}), encoding='utf-8')
        files = {path.relative_to(self.program).as_posix(): digest(path)
                 for path in self.program.rglob('*') if path.is_file()}
        (self.program / 'integrity.json').write_text(json.dumps({'algorithm': 'sha256', 'files': files}), encoding='utf-8')

    def test_full_program_manifest_binds_transport_without_hash_cycle(self):
        release = write_release_manifest(self.program, 'test-generation')
        manifest = json.loads((self.program / 'makerspace-release.json').read_text(encoding='utf-8'))
        self.assertIn('integrity.json', manifest['files'])
        transport = json.loads((self.program / 'integrity.json').read_text(encoding='utf-8'))
        self.assertNotIn('makerspace-release.json', transport['files'])
        self.assertTrue(verify.verify(self.program, '192.0.2.1')['ok'])
        self.assertEqual(release['data_contract'], {'hub': 1, 'print': 1, 'laser': 1})

    def test_extra_unhashed_file_or_changed_program_manifest_refused(self):
        write_release_manifest(self.program, 'test-generation')
        extra = self.program / 'unverified.txt'
        extra.write_text('unexpected', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'filliste'):
            verify.verify(self.program, '192.0.2.1')
        extra.unlink()
        path = self.program / 'makerspace-release.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        manifest['files']['integrity.json']['sha256'] = '0' * 64
        path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'kontrolsum'):
            verify.verify(self.program, '192.0.2.1')


if __name__ == '__main__':
    unittest.main()
