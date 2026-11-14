"""Contract tests for the local, student-visible update status channel."""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
FILE = ROOT / "ansible/roles/kiosk/files/update_status.py"
SPEC = importlib.util.spec_from_file_location("update_status_test", FILE)
update_status = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(update_status)


class UpdateStatusTest(unittest.TestCase):
    def write(self, root, value, mode=0o644):
        path = Path(root) / "elevhub-update-status.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        os.chmod(path, mode)
        return path

    def test_missing_or_malformed_status_is_invisible(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "missing.json"
            if os.name != 'nt':
                self.assertIsNone(update_status.read_status(path, boot_id="boot"))
            path.write_text("not json", encoding="ascii")
            self.assertIsNone(update_status.read_status(path, boot_id="boot"))

    def test_valid_current_status_is_read_and_described(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.write(root, {"schema": 1, "status": "applying", "phase": "installing",
                                     "release": "2026.10.13", "updated_at": 10, "boot_id": "boot"})
            value = update_status.read_status(path, boot_id="boot")
        self.assertEqual(value["status"], "applying")
        self.assertIn("OPDATERER", update_status.message(value))
        self.assertIn("GENSTART IKKE", update_status.message(value))

    def test_stale_boot_status_does_not_block_or_display(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.write(root, {"schema": 1, "status": "applying", "phase": "installing",
                                     "release": "2026.10.13", "updated_at": 10, "boot_id": "old"})
            self.assertIsNone(update_status.read_status(path, boot_id="new"))

    def test_non_root_writable_and_symlink_status_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.write(root, {"schema": 1, "status": "applying", "phase": "installing", "boot_id": "boot"}, 0o666)
            self.assertIsNone(update_status.read_status(path, boot_id="boot"))
            link = Path(root) / "link.json"
            link.symlink_to(path)
            self.assertIsNone(update_status.read_status(link, boot_id="boot"))

    def test_staged_restart_is_visible_but_not_active(self):
        value = {"schema": 1, "status": "staged_awaiting_restart", "phase": "checking",
                 "release": "2026.10.13", "updated_at": 10, "boot_id": "boot"}
        with tempfile.TemporaryDirectory() as root:
            path = self.write(root, value)
            value = update_status.read_status(path, boot_id="boot")
        self.assertIn("GENSTART", update_status.message(value))
        self.assertFalse(update_status.blocks_power(value))

    def test_active_update_blocks_power(self):
        for status, phase in (("applying", "installing"), ("recovering", "recovering"),
                              ("applying", "checking")):
            with self.subTest(status=status, phase=phase):
                self.assertTrue(update_status.blocks_power({"status": status, "phase": phase}))


if __name__ == "__main__":
    unittest.main()
