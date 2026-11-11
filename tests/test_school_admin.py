"""Embedded active-generation tests for the read-only school-admin entry."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

from app.generation_lock import GenerationLock
from app.generation_runtime import build_active_command
from tests import test_generation_embedded as embedded


@unittest.skipUnless(os.name == 'nt' and embedded.ARCHIVE.is_file(),
                     'kræver den cachede Windows embedded Python')
class SchoolAdminActiveTests(unittest.TestCase):
    """Compose the embedded fixture; do not inherit its test methods."""

    def setUp(self):
        self.fixture = embedded.EmbeddedGenerationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.package = self.fixture.package
        self.stable = self.fixture.stable
        self.data = self.root / 'school-data'
        self._copy_admin_and_reseal()
        self.control = self.stable / 'control'
        self.control.mkdir()
        self.lock_path = self.control / 'generation.lock'
        self.lock_path.write_bytes(b'1')
        self.record = {
            'format': 1, 'installation_id': 'a' * 32, 'revision': 4,
            'runtime_root': str(self.stable), 'program_root': str(self.package),
            'data_root': str(self.data), 'manifest_sha256': self.fixture.manifest_sha256,
        }
        self._write_selection()

    def _copy_admin_and_reseal(self):
        shutil.copy2(embedded.ROOT / 'app/school_admin.py', self.package / 'app/school_admin.py')
        manifest_path = self.package / 'makerspace-release.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['files'] = {
            path.relative_to(self.package).as_posix(): {
                'size': path.stat().st_size,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in self.package.rglob('*')
            if path.is_file() and path != manifest_path
        }
        raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
        manifest_path.write_bytes(raw)
        self.fixture.manifest_sha256 = hashlib.sha256(raw).hexdigest()

    def _write_selection(self):
        raw = json.dumps(self.record, sort_keys=True).encode('utf-8')
        target = self.control / 'active-generation.json'
        with GenerationLock(self.lock_path, exclusive=True):
            pending = target.with_suffix('.tmp')
            pending.write_bytes(raw)
            os.replace(pending, target)
        self.selected_hash = hashlib.sha256(raw).hexdigest()

    def _run(self, action, expected=None, installation_id=None):
        command = build_active_command(self.stable, 'school-admin',
                                       [action, '--data-root', str(self.data)], expected)
        if installation_id is not None:
            command[command.index('--entry'):command.index('--entry')] = ['--expected-installation-id', installation_id]
        return subprocess.run(command, cwd=self.root,
                                            capture_output=True, text=True, timeout=25)

    def _write_common_config(self, role='vm', teacher='192.168.1.20', server='192.168.1.20'):
        self.data.mkdir(parents=True, exist_ok=True)
        (self.data / 'konfiguration.json').write_text(json.dumps({
            'laerer_adresse': teacher, 'elev_adresse': server, 'laerer_port': 8780,
            'backend_ports': {'teacher': 8767},
        }), encoding='utf-8')
        (self.data / 'school.json').write_text(json.dumps({'role': role}), encoding='utf-8')

    def test_inspect_and_codes_are_selected_and_missing_code_is_null(self):
        self._write_common_config()
        (self.data / 'printvaerksted').mkdir()
        (self.data / 'printvaerksted/laererkode.txt').write_text('print-code\n', encoding='utf-8')
        (self.data / 'designvaerksted').mkdir()
        result = self._run('inspect')
        self.assertEqual(result.returncode, 0, result.stderr)
        inspect = json.loads(result.stdout)
        self.assertEqual(inspect['data_root'], str(self.data))
        self.assertEqual(inspect['program_root'], str(self.package))
        self.assertEqual(inspect['installation_id'], 'a' * 32)
        self.assertEqual(inspect['revision'], 4)
        codes = self._run('codes')
        self.assertEqual(codes.returncode, 0, codes.stderr)
        self.assertEqual(json.loads(codes.stdout), [
            {'label': 'Printværksted', 'code': 'print-code'},
            {'label': 'Laserværksted', 'code': None},
        ])
        self.assertNotIn('PRIVATE KEY', codes.stdout)

    def test_urls_vm_and_makerspace_use_expected_hosts_and_ports(self):
        self._write_common_config(role='vm')
        result = self._run('urls')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            'teacher': 'https://192.168.1.20:8780',
            'design': 'http://127.0.0.1:8767',
            'controlpanel': 'https://192.168.1.20:8780/kontrolpanel',
            'laser': None,
        })
        (self.data / 'school.json').write_text(json.dumps({'role': 'makerspace'}), encoding='utf-8')
        station = self.data / 'laserstation'
        station.mkdir()
        (station / 'laser-installation.json').write_text(json.dumps({'port': 8790}), encoding='utf-8')
        result = self._run('urls')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['laser'], 'http://127.0.0.1:8790/')

    def test_unsafe_config_restore_hold_and_selection_mismatch_fail_closed(self):
        self._write_common_config(teacher='10.0.0.8', server='192.168.1.20')
        result = self._run('urls')
        self.assertNotEqual(result.returncode, 0)
        (self.data / '.restore-hold.json').write_text('{}', encoding='utf-8')
        result = self._run('inspect')
        self.assertNotEqual(result.returncode, 0)
        (self.data / '.restore-hold.json').unlink()
        result = self._run('inspect', expected='0' * 64)
        self.assertNotEqual(result.returncode, 0)
        result = self._run('inspect', installation_id='b' * 32)
        self.assertNotEqual(result.returncode, 0)
        result = self._run('inspect', installation_id='a' * 32)
        self.assertEqual(result.returncode, 0, result.stderr)
        with GenerationLock(self.lock_path, exclusive=True):
            pass  # Success and errors both release their lease.

    def test_laser_url_only_needs_public_station_configuration(self):
        station = self.data / 'laserstation'
        station.mkdir(parents=True)
        (station / 'laser-installation.json').write_text(json.dumps({'port': 8801}), encoding='utf-8')
        result = self._run('laser-url')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'laser': 'http://127.0.0.1:8801/'})
        for invalid in (True, '8801', 0, 65536):
            (station / 'laser-installation.json').write_text(json.dumps({'port': invalid}), encoding='utf-8')
            self.assertNotEqual(self._run('laser-url').returncode, 0)


if __name__ == '__main__':
    unittest.main()
