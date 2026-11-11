import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.server import teacher_code_for_installation


class SchoolTeacherAccessTests(unittest.TestCase):
    def test_ordinary_installation_preserves_locally_changed_code(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            path = Path(folder) / 'laererkode.txt'
            code = teacher_code_for_installation(path)
            self.assertGreaterEqual(len(code), 16)
            path.write_text('local-replacement-code', encoding='ascii')
            self.assertEqual(teacher_code_for_installation(path), 'local-replacement-code')

    def test_fresh_schools_get_different_codes_and_restart_preserves(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'ELEVHUB_SCHOOL_MODE': '1'}):
            path = Path(folder) / 'laererkode.txt'
            first = teacher_code_for_installation(path)
            self.assertGreaterEqual(len(first), 16)
            self.assertEqual(first, teacher_code_for_installation(path))
            second = teacher_code_for_installation(Path(folder) / 'another-school.txt')
            self.assertNotEqual(first, second)

    def test_malformed_existing_code_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'ELEVHUB_SCHOOL_MODE': '1'}):
            path = Path(folder) / 'laererkode.txt'
            path.write_text('x', encoding='ascii')
            with self.assertRaises(ValueError):
                teacher_code_for_installation(path)
            self.assertEqual(path.read_text(), 'x')


if __name__ == '__main__':
    unittest.main()
