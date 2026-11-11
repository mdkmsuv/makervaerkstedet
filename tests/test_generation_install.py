"""Isolation tests for the first-install generation pointer."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile
from unittest.mock import patch

from app.instance import InstanceLock
from tests.test_generation_embedded import ARCHIVE_SHA256

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / '.runtime' / 'downloads' / 'python-3.14.7-embed-amd64.zip'

_spec = importlib.util.spec_from_file_location('generation_install_tested', ROOT / 'windows-elevhub/server/generation_install.py')
generation_install = importlib.util.module_from_spec(_spec)
_old_path = sys.path[:]
_old_bytecode = sys.dont_write_bytecode
try:
    _spec.loader.exec_module(generation_install)
finally:
    sys.path[:] = _old_path
    sys.dont_write_bytecode = _old_bytecode


@unittest.skipUnless(sys.platform == 'win32' and ARCHIVE.is_file(),
                     'kræver Windows og den allerede cachede embedded Python')
class GenerationInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='generation-install-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stable = self.root / 'stable'
        self.program = self.stable / 'program'
        self.data = self.root / 'data'
        self.stable.mkdir()
        self.data.mkdir()
        # The installer expects its pre-created, administrator-owned marker;
        # InstanceLock leases this same file during initialization.
        (self.data / 'instance.lock').write_bytes(b'1')
        self.installation_id = '0123456789abcdef0123456789abcdef'
        self._make_program()
        self._write_owner()

    def _source_cohort(self):
        """Use the same complete source cohort as the release tests."""
        from tests.test_release_compatibility import ReleaseCompatibilityTests
        fixture = ReleaseCompatibilityTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        return fixture._source_cohort()

    def _runtime_template(self):
        extracted = self.root / 'runtime-template'
        raw = ARCHIVE.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), ARCHIVE_SHA256)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            archive.extractall(extracted)
        tag = 'python314'
        for item in extracted.glob('*._pth'):
            if item.name != tag + '._pth':
                item.unlink()
        (extracted / 'Lib' / 'site-packages').mkdir(parents=True, exist_ok=True)
        (extracted / (tag + '._pth')).write_text(
            f'{tag}.zip\n.\n..\nLib/site-packages\n', encoding='ascii')
        return extracted

    def _make_program(self):
        source = self._source_cohort()
        shutil.copytree(source, self.program)
        # These are the two first-install files required by check_package().
        for relative in ('windows-elevhub/server/generation_install.py',
                         'windows-elevhub/server/generation-startup.ps1'):
            target = self.program / relative.split('/', 1)[1]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        runtime = self._runtime_template()
        shutil.copytree(runtime, self.program / 'runtime')
        shutil.copytree(runtime, self.stable / 'runtime')
        for relative in ('app/generation_runtime.py', 'app/generation_selection.py',
                         'app/generation_lock.py'):
            target = self.stable / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        (self.stable / 'app' / '__init__.py').write_text('', encoding='ascii')

        files = {str(path.relative_to(self.program)).replace('\\', '/')
                 for path in self.program.rglob('*') if path.is_file()
                 and path.name != 'makerspace-release.json'}
        records = {name: {'sha256': hashlib.sha256((self.program / name).read_bytes()).hexdigest(),
                          'size': (self.program / name).stat().st_size}
                   for name in sorted(files)}
        manifest = {'format': 1, 'product': 'Makerværkstedet',
                    'release': 'generation-install-fixture',
                    'credits': 'Kreditering: Klostermarksskolen.',
                    'data_contract': {'hub': 1, 'print': 1, 'laser': 1},
                    'files': records}
        raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
        (self.program / 'makerspace-release.json').write_bytes(raw)
        self.manifest_sha256 = hashlib.sha256(raw).hexdigest()

    def _write_owner(self, **changes):
        value = {'schemaVersion': 1, 'product': 'ElevhubServer',
                 'state': 'installing', 'startupProtocol': 'generation-v1',
                 'taskName': 'ElevhubServer', 'ownerId': str(uuid.UUID(self.installation_id)),
                 'installRoot': str(self.stable), 'dataRoot': str(self.data)}
        value.update(changes)
        (self.data / 'installation-owner.json').write_text(
            json.dumps(value, sort_keys=True), encoding='utf-8')

    def _initialize(self):
        return generation_install.initialize(self.program, self.stable, self.data,
                                             self.installation_id)

    def test_initialize_writes_active_record_and_school_admin_can_inspect(self):
        checked = subprocess.run([str(self.program / 'runtime/python.exe'), '-B',
                                  str(self.program / 'server/generation_install.py'),
                                  '--package-root', str(self.program), '--check-package'],
                                 capture_output=True, text=True, timeout=35)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertFalse((self.stable / 'control').exists())
        result = subprocess.run([str(self.program / 'runtime/python.exe'), '-B',
                                 str(self.program / 'server/generation_install.py'),
                                 '--package-root', str(self.program), '--runtime-root', str(self.stable),
                                 '--data-root', str(self.data), '--installation-id', self.installation_id],
                                capture_output=True, text=True, timeout=35)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record['revision'], 1)
        control = self.stable / 'control'
        self.assertTrue((control / 'active-generation.json').is_file())
        self.assertTrue((control / 'generation.lock').is_file())
        self.assertFalse((control / 'selection-transition.json').exists())

        command = [str(self.stable / 'runtime' / 'python.exe'), '-I', '-B',
                   str(self.stable / 'app' / 'generation_runtime.py'),
                   '--runtime-root', str(self.stable), '--active',
                   '--expected-installation-id', self.installation_id,
                   '--entry', 'school-admin', '--', 'inspect',
                   '--data-root', str(self.data)]
        result = subprocess.run(command, cwd=self.stable, capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        inspected = json.loads(result.stdout)
        self.assertEqual(inspected['installation_id'], self.installation_id)
        self.assertEqual(Path(inspected['data_root']).resolve(), self.data.resolve())

    def test_installed_or_wrong_owner_is_rejected_before_control_creation(self):
        self._write_owner(state='installed')
        with self.assertRaises(ValueError):
            self._initialize()
        self.assertFalse((self.stable / 'control').exists())

        self._write_owner(state='installing', ownerId='f' * 32)
        with self.assertRaises(ValueError):
            self._initialize()
        self.assertFalse((self.stable / 'control').exists())

    def test_partial_control_is_rejected_and_does_not_get_completed(self):
        control = self.stable / 'control'
        control.mkdir()
        (control / 'selection-transition.json').write_text('{}', encoding='ascii')
        with self.assertRaises((FileExistsError, ValueError)):
            self._initialize()
        self.assertFalse((control / 'active-generation.json').exists())

    def test_tampered_program_helper_or_manifest_is_rejected(self):
        helper = self.program / 'app' / 'generation_selection.py'
        original = helper.read_bytes()
        helper.write_bytes(original + b'\n# tampered\n')
        try:
            with self.assertRaises(ValueError):
                self._initialize()
            self.assertFalse((self.stable / 'control').exists())
        finally:
            helper.write_bytes(original)

        manifest_path = self.program / 'makerspace-release.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        first = sorted(manifest['files'])[0]
        digest = manifest['files'][first]['sha256']
        manifest['files'][first]['sha256'] = ('0' if digest[0] != '0' else '1') + digest[1:]
        manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding='utf-8')
        with self.assertRaises(ValueError):
            self._initialize()
        self.assertFalse((self.stable / 'control').exists())

    def test_existing_instance_lock_is_rejected_without_control(self):
        held = InstanceLock(self.data)
        try:
            with self.assertRaises(RuntimeError):
                self._initialize()
        finally:
            held.close()
        self.assertFalse((self.stable / 'control').exists())

    def test_failure_after_control_creation_leaves_transition_and_blocks_start(self):
        original = generation_install._write_new
        calls = []

        def fail_pending(path, value):
            calls.append(Path(path).name)
            if len(calls) == 2:
                raise OSError('injected failure after control creation')
            return original(path, value)

        with patch.object(generation_install, '_write_new', side_effect=fail_pending):
            with self.assertRaises(OSError):
                self._initialize()
        control = self.stable / 'control'
        self.assertEqual(calls, ['selection-transition.json', 'active-generation.json.pending'])
        self.assertTrue((control / 'selection-transition.json').is_file())
        self.assertFalse((control / 'active-generation.json').exists())
        self.assertTrue((control / 'generation.lock').is_file())
        with self.assertRaises(RuntimeError):
            # The stable selector must fail closed on the unfinished transition.
            from app.generation_selection import SelectionLease
            SelectionLease(self.stable)


if __name__ == '__main__':
    unittest.main()
