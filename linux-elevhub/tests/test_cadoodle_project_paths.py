"""Exercise first-start project routing without touching a real home directory."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


@unittest.skipUnless(os.name == 'posix', 'Linux project symlink/ownership test')
class ProjectPathsTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('cadoodle_setup',
            Path(__file__).resolve().parents[1] / 'cadoodle/klargoer.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory(prefix='cadoodle-project-paths-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.original = self.home / 'Documents/CaDoodle-workspace/MyCaDoodleProjects'
        self.projects = self.home / 'Projekter/3D-print/CaDoodle'

    def run_setup(self):
        self.module.prepare_project_paths(self.home, os.getuid(), os.getgid())

    def test_clean_install_can_save_before_any_gui_launch(self):
        self.run_setup()
        self.assertTrue(self.original.is_symlink())
        (self.original / 'model.doodle').write_text('student work')
        self.run_setup()
        self.assertEqual((self.projects / 'model.doodle').read_text(), 'student work')

    def test_existing_documents_are_moved_without_loss(self):
        self.original.mkdir(parents=True)
        (self.original / 'existing.doodle').write_text('keep me')
        self.run_setup()
        self.assertTrue(self.original.is_symlink())
        self.assertEqual((self.projects / 'existing.doodle').read_text(), 'keep me')

    def test_two_existing_folders_are_never_merged_or_overwritten(self):
        for folder, content in [(self.original, 'old'), (self.projects, 'new')]:
            folder.mkdir(parents=True)
            (folder / 'same-name.doodle').write_text(content)
        self.run_setup()
        self.assertFalse(self.original.is_symlink())
        self.assertEqual((self.original / 'same-name.doodle').read_text(), 'old')
        self.assertEqual((self.projects / 'same-name.doodle').read_text(), 'new')


if __name__ == '__main__':
    unittest.main()
