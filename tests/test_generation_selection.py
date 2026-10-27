"""Tests for the shared active-generation selector (no activation or services)."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from app.generation_lock import GenerationLock
from app.generation_selection import SelectionLease, bind_arguments


class GenerationSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = self.root / 'runtime-root'
        self.program = self.root / 'program'
        self.data = self.root / 'school-data'
        self.runtime.mkdir(); self.program.mkdir(); self.data.mkdir()
        (self.program / 'nested').mkdir()
        (self.runtime / 'control').mkdir()
        (self.runtime / 'control' / 'generation.lock').write_bytes(b'lock marker')
        (self.data / 'printvaerksted').mkdir()
        (self.data / 'designvaerksted').mkdir()
        (self.data / 'hub').mkdir()
        (self.data / 'konfiguration.json').write_text('{}', encoding='utf-8')
        plates = self.data / 'Laserplader'
        plates.mkdir()
        (self.data / 'laserstation').mkdir()
        (self.data / 'laserstation' / 'laser-installation.json').write_text(
            json.dumps({'plates': str(plates)}), encoding='utf-8')
        self.record = {
            'format': 1,
            'installation_id': 'a' * 32,
            'revision': 7,
            'runtime_root': str(self.runtime.resolve()),
            'program_root': str(self.program.resolve()),
            'data_root': str(self.data.resolve()),
            'manifest_sha256': 'b' * 64,
        }
        self._write_selection()

    def tearDown(self):
        self.temp.cleanup()

    def _write_selection(self, record=None):
        record = self.record if record is None else record
        (self.runtime / 'control' / 'active-generation.json').write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':')), encoding='utf-8')

    def _selection_sha256(self):
        return hashlib.sha256((self.runtime / 'control' / 'active-generation.json').read_bytes()).hexdigest()

    def test_reads_record_and_digest_and_holds_shared_lock(self):
        lease = SelectionLease(self.runtime)
        try:
            self.assertEqual(lease.record, self.record)
            self.assertEqual(lease.sha256, hashlib.sha256(
                json.dumps(self.record, ensure_ascii=False, sort_keys=True,
                           separators=(',', ':')).encode()).hexdigest())
            with self.assertRaises(RuntimeError):
                GenerationLock(self.runtime / 'control' / 'generation.lock', exclusive=True)
        finally:
            lease.close()
        with GenerationLock(self.runtime / 'control' / 'generation.lock', exclusive=True):
            pass

    def test_context_and_expected_digest_and_transition_guard(self):
        with SelectionLease(self.runtime, expected_sha256=self._selection_sha256()) as lease:
            self.assertEqual(lease.record['revision'], 7)
        with self.assertRaises(ValueError):
            SelectionLease(self.runtime, expected_sha256='c' * 64)
        transition = self.runtime / 'control' / 'selection-transition.json'
        transition.write_text('{}', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            SelectionLease(self.runtime)
        transition.unlink()
        with SelectionLease(self.runtime) as lease:
            self.assertEqual(lease.record, self.record)

    def test_hold_marker_in_data_root_or_ancestor_blocks_and_releases_lock(self):
        for marker in (self.data / '.restore-hold.json', self.root / '.restore-hold.json'):
            marker.write_text('{}', encoding='utf-8')
            try:
                with self.assertRaises(RuntimeError):
                    SelectionLease(self.runtime)
                with GenerationLock(self.runtime / 'control' / 'generation.lock', exclusive=True):
                    pass
            finally:
                marker.unlink()

    def test_rejects_bad_record_shapes_paths_types_and_hashes(self):
        cases = [
            ('extra field', dict(self.record, extra=True)),
            ('bad format', dict(self.record, format=True)),
            ('bad installation', dict(self.record, installation_id='A' * 32)),
            ('bad revision', dict(self.record, revision=0)),
            ('relative runtime', dict(self.record, runtime_root='runtime')),
            ('missing program', dict(self.record, program_root=str(self.root / 'gone'))),
            ('nested data', dict(self.record, data_root=str(self.program / 'nested'))),
            ('bad manifest', dict(self.record, manifest_sha256='x' * 64)),
        ]
        for label, record in cases:
            with self.subTest(label=label):
                self._write_selection(record)
                with self.assertRaises((ValueError, FileNotFoundError)):
                    SelectionLease(self.runtime)
        with self.assertRaises(ValueError):
            SelectionLease(self.runtime, expected_sha256='0' * 64)

    def test_expected_pointer_hash_becomes_stale_after_revision_change(self):
        old_hash = self._selection_sha256()
        self._write_selection(dict(self.record, revision=8))
        with self.assertRaises(ValueError):
            SelectionLease(self.runtime, expected_sha256=old_hash)

    def test_missing_or_oversized_pointer_is_rejected_without_creating_files(self):
        path = self.runtime / 'control' / 'active-generation.json'
        original = path.read_bytes()
        path.write_bytes(b'{' + b'x' * (64 * 1024) + b'}')
        with self.assertRaises(ValueError):
            SelectionLease(self.runtime)
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            SelectionLease(self.runtime)
        self.assertFalse(path.exists())
        path.write_bytes(original)

    def test_rejects_duplicate_or_unknown_envelope_keys(self):
        path = self.runtime / 'control' / 'active-generation.json'
        raw = json.loads(path.read_text(encoding='utf-8'))
        raw['unexpected'] = True
        path.write_text(json.dumps(raw), encoding='utf-8')
        with self.assertRaises(ValueError):
            SelectionLease(self.runtime)
        duplicate = json.dumps(self.record, separators=(',', ':')).replace(
            '"format":1,', '"format":1,"format":1,')
        path.write_text(duplicate, encoding='utf-8')
        with self.assertRaises(ValueError):
            SelectionLease(self.runtime)

    def test_binds_expected_data_paths_and_normalizes_existing_arguments(self):
        record = dict(self.record)
        for entry, expected in (
            ('server', ['--data-root', str(self.data.resolve())]),
            ('enrollment', ['--data-root', str(self.data.resolve())]),
            ('print', ['--data', str((self.data / 'printvaerksted').resolve())]),
            ('print-server', ['--data', str((self.data / 'printvaerksted').resolve())]),
            ('design', ['--data', str((self.data / 'designvaerksted').resolve())]),
            ('hub', ['--data', str((self.data / 'hub').resolve()),
                     '--konfiguration', str((self.data / 'konfiguration.json').resolve())]),
            ('laser-station', ['--config', str((self.data / 'laserstation' / 'laser-installation.json').resolve())]),
        ):
            with self.subTest(entry=entry):
                args = bind_arguments(record, entry, expected)
                self.assertEqual(args, expected)
        self.assertEqual(bind_arguments(record, 'server', ['--data-root=' + str(self.data)]),
                         ['--data-root', str(self.data.resolve())])
        self.assertEqual(bind_arguments(record, 'boxes-worker', ['--sentinel']), ['--sentinel'])

    def test_laser_configuration_must_point_to_actual_data_plates(self):
        other = self.root / 'other-plates'
        other.mkdir()
        config = self.data / 'laserstation' / 'laser-installation.json'
        config.write_text(json.dumps({'plates': str(other)}), encoding='utf-8')
        with self.assertRaises(ValueError):
            bind_arguments(self.record, 'laser-station', [])

    def test_rejects_wrong_duplicate_and_abbreviated_bound_paths(self):
        wrong = self.root / 'wrong'
        for entry, args in (
            ('server', ['--data-root', str(wrong)]),
            ('print', ['--data', str(wrong)]),
            ('laser-station', ['--config=' + str(wrong)]),
            ('server', ['--data-root=' + str(self.data), '--data-root', str(self.data)]),
            ('server', ['--data-ro', str(self.data)]),
        ):
            with self.subTest(entry=entry, args=args), self.assertRaises(ValueError):
                bind_arguments(self.record, entry, args)


if __name__ == '__main__':
    unittest.main()
