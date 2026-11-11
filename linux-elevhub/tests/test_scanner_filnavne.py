"""Files copied from old Windows USB media may have cp1252, not UTF-8, names."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'projekter'))
from projektarkiv import Projektarkiv  # noqa: E402
from scanner import Scanner, laesbar  # noqa: E402


class LaesbarTest(unittest.TestCase):
    def test_valid_names_are_unchanged(self):
        self.assertEqual(laesbar('Mit spil æøå.sb3'), 'Mit spil æøå.sb3')

    @unittest.skipUnless(os.name == 'posix', 'Only Linux exposes non-UTF-8 filename bytes as surrogates')
    def test_cp1252_bytes_become_the_intended_letters(self):
        raw = os.fsdecode(b'projekt-\xe6\xf8\xe5.sb3')  # "æøå" in cp1252
        self.assertEqual(laesbar(raw), 'projekt-æøå.sb3')


@unittest.skipUnless(os.name == 'posix', 'Linux filenames can hold non-UTF-8 bytes')
class Cp1252FilenameScanTest(unittest.TestCase):
    def test_file_with_cp1252_name_is_indexed_and_index_still_saves(self):
        with tempfile.TemporaryDirectory() as temp:
            projects = Path(temp) / 'Projekter'
            projects.mkdir()
            store = Projektarkiv(projects / '.elevhub')
            (projects / 'Scratch').mkdir()
            with open(os.path.join(os.fsencode(projects / 'Scratch'), b'mit-spil-\xe6.sb3'), 'wb') as f:
                f.write(b'PK\x05\x06' + b'\0' * 18)  # a minimal (empty) zip
            normal = projects / 'Scratch' / 'normalt.sb3'
            normal.write_bytes(b'PK\x05\x06' + b'\0' * 18)
            scanner = Scanner(projects, store)
            for _ in range(3):  # a file is read after two identical polls
                scanner.scan()
            names = {p['name'] for p in store.list()}
            self.assertIn('mit-spil-æ', names)
            self.assertIn('normalt', names)
            self.assertEqual(scanner.error, '')
            # The whole index must still be writable (one bad name must not block it).
            self.assertTrue(scanner.index.exists())
            self.assertIn('mit-spil-æ', scanner.index.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
