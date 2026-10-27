import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from app.instance import InstanceLock
from app.release_compatibility import CREDIT, REQUIRED, SHARED, check, inspect_program


ROOT = Path(__file__).resolve().parents[1]


class ReleaseCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _manifest(self, program, files, data_contract=None):
        records = {}
        for name in sorted(files):
            path = program / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(b'')
            raw = path.read_bytes()
            records[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
        value = {'format': 1, 'product': 'Makerværkstedet', 'release': 'test-release',
                 'credits': CREDIT, 'data_contract': data_contract or {'hub': 1}, 'files': records}
        (program / 'makerspace-release.json').write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return value

    def _minimal_program(self, data_contract=None, core=b''):
        program = self.root / 'minimal-program'
        files = SHARED | set().union(*(REQUIRED[role] for role in (data_contract or {'hub': 1})))
        files = set(files)
        if core:
            (program / 'app/core.py').parent.mkdir(parents=True, exist_ok=True)
            (program / 'app/core.py').write_bytes(core)
        (program / 'CREDITS.md').parent.mkdir(parents=True, exist_ok=True)
        (program / 'CREDITS.md').write_text(CREDIT + '\n', encoding='utf-8')
        self._manifest(program, files, data_contract)
        return program

    def _source_cohort(self):
        program = self.root / 'source-cohort'
        for source in (ROOT / 'app').rglob('*.py'):
            target = program / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        hub_source = ROOT / 'linux-elevhub' / 'server' / 'elevhub'
        for source in hub_source.glob('*.py'):
            if not source.name.startswith('test_'):
                target = program / 'hub' / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        bootstrap = program / 'server' / 'bootstrap.py'
        bootstrap.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / 'windows-elevhub' / 'server' / 'bootstrap.py', bootstrap)
        enrollment = program / 'deployment/school-setup/enrollment.py'
        enrollment.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / 'deployment/school-setup/enrollment.py', enrollment)
        for relative in ('hub/web/makerspace.html', 'hub/web/makerspace.js', 'hub/web/makerspace.css',
                         'hub/web/makerspace-printers.js',
                         'hub/web/makerspace-print-workflows.js', 'hub/web/makerspace-lasers.js'):
            source = hub_source / relative.removeprefix('hub/')
            target = program / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        (program / 'CREDITS.md').write_text('Kreditering: Klostermarksskolen.\n', encoding='utf-8')
        files = {str(path.relative_to(program)).replace('\\', '/') for path in program.rglob('*') if path.is_file()}
        files.remove('CREDITS.md')
        self._manifest(program, files | {'CREDITS.md'}, {'hub': 1, 'print': 1, 'laser': 1})
        return program

    def _real_profile(self):
        from tests.test_installation_backup import InstallationBackupTests
        fixture = InstallationBackupTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        return fixture.profile, fixture

    def test_real_program_source_cohort_passes_and_sources_remain_unchanged(self):
        program = self._source_cohort()
        before = {p: hashlib.sha256(p.read_bytes()).digest() for p in program.rglob('*') if p.is_file()}
        result = inspect_program(program)
        self.assertEqual(result['data_contract'], {'hub': 1, 'print': 1, 'laser': 1})
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).digest() for p in before})

    def test_missing_static_dependency_is_rejected_even_when_hashes_are_valid(self):
        program = self._minimal_program({'hub': 1, 'print': 1, 'laser': 1}, b'from app.missing_dependency import value\n')
        with self.assertRaisesRegex(ValueError, 'lokal Python-afhængighed'):
            inspect_program(program)

    def test_source_mutation_between_manifest_scan_and_ast_scan_is_rejected(self):
        import app.release_compatibility as module
        program = self._minimal_program({'hub': 1, 'print': 1, 'laser': 1}, b'')
        target = program / 'app/core.py'
        original = module._dependencies

        def mutate_then_scan(root, files):
            target.write_bytes(target.read_bytes() + b'\n# changed during scan\n')
            return original(root, files)

        with patch.object(module, '_dependencies', side_effect=mutate_then_scan):
            with self.assertRaisesRegex(ValueError, 'ændredes under kontrollen'):
                module.inspect_program(program)

    def test_unlisted_program_file_is_rejected(self):
        program = self._minimal_program()
        (program / 'app' / 'unlisted.py').write_text('value = 1\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            inspect_program(program)

    def test_file_added_during_dependency_scan_is_rejected(self):
        import app.release_compatibility as module
        program = self._minimal_program()
        original = module._dependencies

        def add_then_scan(root, files):
            (root / 'app' / 'late.py').write_text('value = 1\n', encoding='utf-8')
            return original(root, files)

        with patch.object(module, '_dependencies', side_effect=add_then_scan):
            with self.assertRaises(ValueError):
                module.inspect_program(program)

    def test_duplicate_manifest_key_is_rejected(self):
        program = self._minimal_program()
        raw = (program / 'makerspace-release.json').read_text(encoding='utf-8')
        raw = raw.replace('{"format": 1,', '{"format": 1, "format": 1,', 1)
        (program / 'makerspace-release.json').write_text(raw, encoding='utf-8')
        with self.assertRaises(ValueError):
            inspect_program(program)

    def test_manifest_shape_hash_and_credit_fail_closed(self):
        program = self._minimal_program()
        value = json.loads((program / 'makerspace-release.json').read_text(encoding='utf-8'))
        value['format'] = True
        (program / 'makerspace-release.json').write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaises(ValueError):
            inspect_program(program)
        program = self._minimal_program()
        (program / 'CREDITS.md').write_text('uden kreditering', encoding='utf-8')
        value = json.loads((program / 'makerspace-release.json').read_text(encoding='utf-8'))
        value['files']['CREDITS.md'] = {'sha256': hashlib.sha256((program / 'CREDITS.md').read_bytes()).hexdigest(),
                                        'size': (program / 'CREDITS.md').stat().st_size}
        (program / 'makerspace-release.json').write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'kreditering'):
            inspect_program(program)
        program = self._minimal_program()
        target = program / next(iter(SHARED))
        target.write_bytes(target.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'kontrolsum'):
            inspect_program(program)

    def test_preflight_rejects_data_contract_mismatch(self):
        profile, _fixture = self._real_profile()
        program = self._minimal_program({'hub': 1})
        with self.assertRaisesRegex(ValueError, 'matcher ikke kontrakten'):
            check(profile, program)

    def test_preflight_accepts_real_components_without_changing_business_data(self):
        profile, fixture = self._real_profile()
        program = self._source_cohort()
        watched = [fixture.hub / 'makerspace.sqlite3', fixture.print / 'queue.sqlite3',
                   fixture.laser / 'afleveringer.sqlite3', fixture.server / 'konfiguration.json']
        before = {path: path.read_bytes() for path in watched}
        result = check(profile, program)
        self.assertTrue(result['compatible_at_check'])
        self.assertFalse(result['deployment_ready'])
        self.assertEqual(result['remaining'], ['program_transaction', 'backup_restore_validation',
                                               'physical_queue_reconciliation'])
        self.assertEqual(before, {path: path.read_bytes() for path in watched})
        lock = InstanceLock(fixture.server)
        lock.close()

    def test_preflight_rejects_live_data_lock(self):
        profile, fixture = self._real_profile()
        program = self._minimal_program({'hub': 1, 'print': 1, 'laser': 1})
        lock = InstanceLock(fixture.server)
        try:
            with self.assertRaises(RuntimeError):
                check(profile, program)
        finally:
            lock.close()


if __name__ == '__main__':
    unittest.main()
