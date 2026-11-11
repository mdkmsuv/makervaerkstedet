"""Exercise download reassembly with Windows' built-in PowerShell, using tiny fixtures."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('split_release_asset', ROOT / 'scripts/split_release_asset.py')
builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)


@unittest.skipUnless(os.name == 'nt', 'Windows release download workflow')
class ReleasePartsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'example.zip'; self.source.write_bytes(bytes(range(256)) * 9)
        self.parts = self.root / 'parts'
        self.manifest = builder.split(self.source, self.parts, 500)
        self.manifest_path = self.parts / 'example.zip.parts.json'

    def run_join(self):
        return subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                               str(self.parts / 'Join-Release.ps1'), '-Manifest', str(self.manifest_path)],
                              capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)

    def test_round_trip_and_existing_destination_is_preserved(self):
        result = self.run_join(); self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.parts / self.source.name).read_bytes(), self.source.read_bytes())
        self.assertNotEqual(self.run_join().returncode, 0)
        self.assertEqual((self.parts / self.source.name).read_bytes(), self.source.read_bytes())

    def test_corrupted_part_rejected_and_partial_removed(self):
        part = self.parts / self.manifest['parts'][1]['name']
        part.write_bytes(b'X' * part.stat().st_size)
        self.assertNotEqual(self.run_join().returncode, 0)
        self.assertFalse((self.parts / self.source.name).exists())
        self.assertEqual(list(self.parts.glob('*.partial')), [])

    def test_missing_part_rejected(self):
        (self.parts / self.manifest['parts'][0]['name']).unlink()
        self.assertNotEqual(self.run_join().returncode, 0)
        self.assertFalse((self.parts / self.source.name).exists())

    def test_traversal_rejected(self):
        self.manifest['file'] = '../escape.zip'
        self.manifest_path.write_text(json.dumps(self.manifest), encoding='utf-8')
        self.assertNotEqual(self.run_join().returncode, 0)
        self.assertFalse((self.root / 'escape.zip').exists())


if __name__ == '__main__':
    unittest.main()
