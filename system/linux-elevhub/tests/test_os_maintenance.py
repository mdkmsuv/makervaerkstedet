import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
SOURCE=Path(__file__).parents[1]/"administration/os_maintenance.py"
SPEC=importlib.util.spec_from_file_location("student_maintenance",SOURCE)
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)
class Maintenance(unittest.TestCase):
    def test_unknown_kernel_selection_is_not_a_false_no_reboot(self):
        with tempfile.TemporaryDirectory() as temp:
            value=M.status(Path(temp),"6.12-old")
        self.assertIsNone(value["reboot_required"]);self.assertFalse(value["automatic_reboot"])
    def test_debian_flag_is_honoured_without_creating_any_other_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/"run").mkdir();(root/"run/reboot-required").touch()
            self.assertTrue(M.status(root,"6.12-old")["reboot_required"])
            self.assertEqual([p.name for p in (root/"run").iterdir()],["reboot-required"])
    def test_changed_selected_kernel_requires_separate_operator_reboot(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(M.os,"readlink",return_value="boot/vmlinuz-6.12-new"),patch.object(M.Path,"is_symlink",return_value=True):
            value=M.status(Path(temp),"6.12-old")
        self.assertTrue(value["kernel_reboot_required"]);self.assertTrue(value["reboot_required"])
if __name__=="__main__":unittest.main()
