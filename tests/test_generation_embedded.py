"""Real embedded-Python generation selection; no installation or network use."""
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import PurePosixPath
from unittest.mock import patch

from app.generation_runtime import START_FILES, build_command
from app.instance import InstanceLock


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / '.runtime' / 'downloads' / 'python-3.14.7-embed-amd64.zip'
ARCHIVE_SHA256 = 'd297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15'


@unittest.skipUnless(os.name == 'nt' and ARCHIVE.is_file(), 'kræver Windows og den allerede cachede embedded Python')
class EmbeddedGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='embedded-generation-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / 'candidate'
        self.stable = self.root / 'stable'
        self._make_fixture()

    def _make_fixture(self):
        archive_bytes = ARCHIVE.read_bytes()
        digest = hashlib.sha256(archive_bytes).hexdigest()
        self.assertEqual(digest, ARCHIVE_SHA256)
        extracted = self.root / 'runtime-template'
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            for entry in archive.infolist():
                name = PurePosixPath(entry.filename)
                self.assertFalse(name.is_absolute() or '..' in name.parts or '\\' in entry.filename or ':' in entry.filename)
                self.assertTrue((extracted / entry.filename).resolve().is_relative_to(extracted.resolve()))
            archive.extractall(extracted)
        tag = 'python314'  # Pinned archive version; independent of the test runner.
        for root in (self.package, self.stable):
            root.mkdir(parents=True)
            shutil.copytree(extracted, root / 'runtime')
            runtime = root / 'runtime'
            for path in runtime.glob('*._pth'):
                if path.name != tag + '._pth':
                    path.unlink()
            (runtime / 'Lib' / 'site-packages').mkdir(parents=True, exist_ok=True)
            (runtime / (tag + '._pth')).write_text(
                f'{tag}.zip\n.\n..\nLib/site-packages\n', encoding='ascii')
            (root / 'app').mkdir()
            for relative in START_FILES:
                source = ROOT / relative
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            (root / 'app' / '__init__.py').write_text('', encoding='ascii')

        # The candidate sentinel starts a real selected-generation child.
        (self.package / 'app' / 'laser_station.py').write_text(
            'import json, subprocess, sys\n'
            'from pathlib import Path\n'
            'from app import generation_runtime as runtime\n'
            'def main():\n'
            '    command = runtime.child_command("app.boxes_worker", ["--child"])\n'
            '    result = subprocess.run(command, cwd=runtime.child_cwd(Path.cwd()), capture_output=True, text=True, timeout=15)\n'
            '    if result.returncode: raise RuntimeError(result.stderr)\n'
            '    print(json.dumps({"source": __file__, "executable": sys.executable, "child": json.loads(result.stdout)}))\n'
            'if __name__ == "__main__": main()\n', encoding='utf-8')
        (self.package / 'app' / 'boxes_worker.py').write_text(
            'import json, sys\n'
            'print(json.dumps({"marker": "candidate", "source": __file__, "executable": sys.executable}))\n',
            encoding='utf-8')
        # This must never be imported through the stable install root.
        (self.stable / 'app' / 'boxes_worker.py').write_text(
            'raise RuntimeError("stable decoy imported")\n', encoding='utf-8')
        for root in (self.package, self.stable):
            (root / 'deployment/school-setup').mkdir(parents=True)
        shutil.copy2(ROOT / 'deployment/school-setup/enrollment.py',
                     self.package / 'deployment/school-setup/enrollment.py')
        (self.stable / 'deployment/school-setup/enrollment.py').write_text(
            'raise RuntimeError("stable enrollment decoy executed")\n', encoding='utf-8')
        (self.package / 'CREDITS.md').write_text('Kreditering: Klostermarksskolen.\n', encoding='utf-8')
        files = {str(path.relative_to(self.package)).replace('\\', '/')
                 for path in self.package.rglob('*') if path.is_file()}
        records = {name: {'sha256': hashlib.sha256((self.package / name).read_bytes()).hexdigest(),
                          'size': (self.package / name).stat().st_size} for name in sorted(files)}
        manifest = {'format': 1, 'product': 'Makerværkstedet', 'release': 'embedded-fixture',
                    'credits': 'Kreditering: Klostermarksskolen.', 'data_contract': {'hub': 1, 'laser': 1},
                    'files': records}
        raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        (self.package / 'makerspace-release.json').write_bytes(raw)
        self.manifest_sha256 = hashlib.sha256(raw).hexdigest()

    def _command(self, entry='laser-station'):
        return build_command(self.package, self.stable, self.manifest_sha256, entry, ['--sentinel'])

    def _school_data(self):
        data = self.root / 'school data'
        spec = importlib.util.spec_from_file_location('embedded_test_provision', ROOT / 'windows-elevhub/server/provision.py')
        provision = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(provision)
        # This is a non-privileged temporary fixture.  The real helper still
        # enforces ACLs; the fixture explicitly owns this boundary.
        with patch.object(provision, '_restrict_private_path', return_value=None):
            provision._tls(data / 'security', '192.0.2.10')
        public_key = 'ssh-ed25519 ' + base64.b64encode(
            struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + b'x' * 32).decode('ascii')
        (data / 'school.json').write_text(json.dumps({
            'schemaVersion': 1, 'school_name': 'Embedded test school', 'server': '192.0.2.10',
            'role': 'vm', 'theme': 'green', 'student_subnet': '192.0.2.0/24',
            'admin_public_key': public_key}), encoding='utf-8')
        return data

    def _enrollment(self, data, action, *arguments):
        command = build_command(self.package, self.stable, self.manifest_sha256, 'enrollment',
                                [action, '--data-root', str(data), *arguments])
        environment = dict(os.environ, PYTHONPATH=str(self.stable))
        environment.pop('PYTHONHOME', None)
        return subprocess.run(command, cwd=self.stable, env=environment,
                              capture_output=True, text=True, timeout=25)

    def test_real_enrollment_reads_selected_school_and_writes_ticket(self):
        data = self._school_data()
        before = {p.name: p.read_bytes() for p in (data / 'security').iterdir()}
        exported = self.root / 'public connection.json'
        result = self._enrollment(data, 'connection', '--output', str(exported))
        self.assertEqual(result.returncode, 0, result.stderr)
        connection = json.loads(exported.read_text(encoding='utf-8'))
        self.assertEqual(connection['school_name'], 'Embedded test school')
        self.assertEqual(connection['server'], '192.0.2.10')
        self.assertEqual(connection['caCertificatePem'], before['ca.crt'].decode('ascii'))
        self.assertNotIn('PRIVATE KEY', exported.read_text(encoding='utf-8'))
        checked = self._enrollment(data, 'check')
        self.assertEqual(checked.returncode, 0, checked.stderr)
        issued = self._enrollment(data, 'ticket', '--name', 'pc-01')
        self.assertEqual(issued.returncode, 0, issued.stderr)
        # The local administrator receives the code; persisted state has only its hash.
        code = issued.stdout.strip().splitlines()[-1].split(': ')[-1].replace('-', '')
        self.assertRegex(code, r'^[A-Z2-7]{12}$')
        tickets = json.loads((data / 'security/enrollment.json').read_text())['tickets']
        self.assertEqual(set(tickets), {hashlib.sha256(code.encode('ascii')).hexdigest()})
        self.assertEqual(next(iter(tickets.values()))['computer'], 'pc-01')
        for filename, raw in before.items():
            self.assertEqual((data / 'security' / filename).read_bytes(), raw)
        self.assertFalse(list(self.package.rglob('*.pyc')))
        self.assertFalse(list(self.stable.rglob('*.pyc')))

    def test_real_enrollment_rejects_restore_hold_and_maintenance_lock(self):
        data = self._school_data()
        marker = data / '.restore-hold.json'
        marker.write_text('{}', encoding='utf-8')
        before = {p.relative_to(data): p.read_bytes() for p in data.rglob('*') if p.is_file()}
        # All entry actions must honour the hold before service creation or writes.
        for action in ('ticket', 'serve', 'connection', 'check'):
            with self.subTest(action=action):
                result = self._enrollment(data, action, '--name', 'pc-01',
                                          '--output', str(self.root / 'blocked.json'))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Gendannelsen er isoleret', result.stderr)
        self.assertEqual({p.relative_to(data): p.read_bytes() for p in data.rglob('*') if p.is_file()}, before)
        self.assertFalse((self.root / 'blocked.json').exists())
        marker.unlink()
        lock_folder = data / 'security/enrollment/operations'
        lock_folder.mkdir(parents=True)
        with InstanceLock(lock_folder):
            result = self._enrollment(data, 'ticket', '--name', 'pc-01')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('offline vedligeholdelse', result.stderr)
            self.assertFalse((data / 'security/enrollment.json').exists())
            self.assertFalse((data / 'security/enheder.lock').exists())
        retry = self._enrollment(data, 'ticket', '--name', 'pc-01')
        self.assertEqual(retry.returncode, 0, retry.stderr)

    def test_tampered_enrollment_cannot_issue_ticket(self):
        data = self._school_data()
        script = self.package / 'deployment/school-setup/enrollment.py'
        script.write_bytes(script.read_bytes() + b'\n# changed after manifest selection\n')
        result = self._enrollment(data, 'ticket', '--name', 'pc-01')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('manifestet', result.stderr)
        self.assertFalse((data / 'security/enrollment').exists())
        self.assertFalse((data / 'security/enrollment.json').exists())

    def test_real_embedded_runtime_selects_candidate_and_child(self):
        command = self._command()
        # Resolve the selection before the dispatcher changes its cwd.
        command[command.index('--package-root') + 1] = 'candidate'
        command[command.index('--runtime-root') + 1] = 'stable'
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertTrue(value['source'].startswith(str(self.package)))
        self.assertEqual(Path(value['executable']).resolve(), (self.stable / 'runtime' / 'python.exe').resolve())
        self.assertEqual(value['child']['marker'], 'candidate')
        self.assertTrue(value['child']['source'].startswith(str(self.package)))
        self.assertEqual(Path(value['child']['executable']).resolve(), (self.stable / 'runtime' / 'python.exe').resolve())

    def test_tampered_source_and_runtime_are_rejected_before_sentinel(self):
        source = self.package / 'app' / 'boxes_worker.py'
        original = source.read_bytes()
        source.write_bytes(original + b'\n# tampered\n')
        try:
            result = subprocess.run(self._command(), cwd=self.root, capture_output=True, text=True, timeout=25)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('candidate', result.stdout)
        finally:
            source.write_bytes(original)
        extra = self.stable / 'runtime' / 'Lib' / 'site-packages' / 'tampered_runtime.py'
        extra.write_bytes(b'# changed runtime inventory\n')
        try:
            result = subprocess.run(self._command(), cwd=self.root, capture_output=True, text=True, timeout=25)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('candidate', result.stdout)
        finally:
            extra.unlink()


if __name__ == '__main__':
    unittest.main()
