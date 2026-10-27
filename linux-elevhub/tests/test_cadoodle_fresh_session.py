"""Tests for the narrow CaDoodle active-file reset."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "cadoodle/fresh_session.py"
SPEC = importlib.util.spec_from_file_location("fresh_session", MODULE_PATH)
fresh_session = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fresh_session)


class FreshSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.workspace = self.home / "Documents/CaDoodle-workspace"
        self.workspace.mkdir(parents=True)
        self.state = self.home / ".local/state/elevhub"
        self.config = self.workspace / "ConfigurationDatabase.json"
        self.project = self.home / "Projekter/3D-print/CaDoodle/old.doodle"
        self.project.parent.mkdir(parents=True)
        self.project.write_text('{"saved":true}', encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self):
        self.config.write_text(json.dumps({
            "CaDoodle": {"CaDoodleActiveFile": str(self.project), "CaDoodleWorkspace": str(self.workspace)},
            "Other": {"keep": "me"},
        }, indent=2), encoding="utf-8")

    def test_removes_only_active_reference_once_per_boot(self):
        self.write_config()
        self.assertTrue(fresh_session.reset_once(self.home, self.state,
                                                 running=lambda: False,
                                                 current_boot_id=lambda: "boot-a"))
        document = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertNotIn("CaDoodleActiveFile", document["CaDoodle"])
        self.assertEqual(document["Other"], {"keep": "me"})
        self.assertTrue(self.project.is_file())
        self.assertFalse(fresh_session.reset_once(self.home, self.state,
                                                  running=lambda: False,
                                                  current_boot_id=lambda: "boot-a"))

    def test_running_cadoodle_does_nothing_and_does_not_mark_boot(self):
        self.write_config()
        self.assertFalse(fresh_session.reset_once(self.home, self.state,
                                                  running=lambda: True,
                                                  current_boot_id=lambda: "boot-a"))
        self.assertIn("CaDoodleActiveFile", json.loads(self.config.read_text())["CaDoodle"])
        self.assertFalse((self.state / "cadoodle-fresh-session.boot").exists())

    def test_malformed_config_is_left_untouched(self):
        self.config.write_text("not json", encoding="utf-8")
        self.assertFalse(fresh_session.reset_once(self.home, self.state,
                                                  running=lambda: False,
                                                  current_boot_id=lambda: "boot-a"))
        self.assertEqual(self.config.read_text(encoding="utf-8"), "not json")

    def test_json_list_is_left_untouched(self):
        self.config.write_text("[]", encoding="utf-8")
        self.assertFalse(fresh_session.reset_once(self.home, self.state,
                                                  running=lambda: False,
                                                  current_boot_id=lambda: "boot-a"))
        self.assertEqual(self.config.read_text(encoding="utf-8"), "[]")

    def test_same_boot_keeps_new_active_reference(self):
        self.write_config()
        fresh_session.reset_once(self.home, self.state, running=lambda: False,
                                 current_boot_id=lambda: "boot-a")
        document = json.loads(self.config.read_text(encoding="utf-8"))
        document["CaDoodle"]["CaDoodleActiveFile"] = "new.doodle"
        self.config.write_text(json.dumps(document), encoding="utf-8")
        self.assertFalse(fresh_session.reset_once(self.home, self.state,
                                                  running=lambda: False,
                                                  current_boot_id=lambda: "boot-a"))
        self.assertEqual(json.loads(self.config.read_text())["CaDoodle"]["CaDoodleActiveFile"], "new.doodle")

    def test_second_boot_resets_again(self):
        self.write_config()
        fresh_session.reset_once(self.home, self.state, running=lambda: False,
                                 current_boot_id=lambda: "boot-a")
        document = json.loads(self.config.read_text(encoding="utf-8"))
        document["CaDoodle"]["CaDoodleActiveFile"] = "new.doodle"
        self.config.write_text(json.dumps(document), encoding="utf-8")
        self.assertTrue(fresh_session.reset_once(self.home, self.state,
                                                 running=lambda: False,
                                                 current_boot_id=lambda: "boot-b"))
        self.assertNotIn("CaDoodleActiveFile", json.loads(self.config.read_text())["CaDoodle"])

    def test_symlink_config_is_left_untouched(self):
        self.write_config()
        target = self.workspace / "real-config.json"
        target.write_text(json.dumps({"CaDoodle": {"CaDoodleActiveFile": "old.doodle"}}), encoding="utf-8")
        self.config.unlink()
        self.config.symlink_to(target)
        self.assertFalse(fresh_session.reset_once(self.home, self.state,
                                                  running=lambda: False,
                                                  current_boot_id=lambda: "boot-a"))
        self.assertIn("CaDoodleActiveFile", json.loads(target.read_text())["CaDoodle"])

    def test_missing_config_is_marked_and_later_active_file_is_preserved(self):
        self.assertFalse(fresh_session.reset_once(self.home, self.state,
                                                  running=lambda: False,
                                                  current_boot_id=lambda: "boot-a"))
        self.write_config()
        self.assertFalse(fresh_session.reset_once(self.home, self.state,
                                                  running=lambda: False,
                                                  current_boot_id=lambda: "boot-a"))
        self.assertIn("CaDoodleActiveFile", json.loads(self.config.read_text())["CaDoodle"])

    def test_config_without_cadoodle_section_is_marked(self):
        self.config.write_text(json.dumps({"Other": {"keep": True}}), encoding="utf-8")
        self.assertFalse(fresh_session.reset_once(self.home, self.state,
                                                  running=lambda: False,
                                                  current_boot_id=lambda: "boot-a"))
        self.assertEqual((self.state / "cadoodle-fresh-session.boot").read_text().strip(), "boot-a")

    def test_missing_boot_id_does_not_mark_or_reset(self):
        self.write_config()
        self.assertFalse(fresh_session.reset_once(self.home, self.state,
                                                  running=lambda: False,
                                                  current_boot_id=lambda: None))
        self.assertIn("CaDoodleActiveFile", json.loads(self.config.read_text())["CaDoodle"])
        self.assertFalse((self.state / "cadoodle-fresh-session.boot").exists())

    @unittest.skipIf(fresh_session.fcntl is None, "fcntl lock is Linux-only")
    def test_launcher_lock_is_nonblocking(self):
        with fresh_session.launcher_lock(self.state) as acquired:
            self.assertTrue(acquired)
            with fresh_session.launcher_lock(self.state) as nested:
                self.assertFalse(nested)


if __name__ == "__main__":
    unittest.main()
