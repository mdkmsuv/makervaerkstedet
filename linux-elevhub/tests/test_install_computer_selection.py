"""Fail-closed tests for the reusable USB computer-profile selector."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
SELECT = ROOT / "installation/select-computer.sh"
COPY = ROOT / "installation/copy-selected-profile.sh"


@unittest.skipUnless(os.name == "posix", "installer shell tests require POSIX /bin/sh")
class ComputerSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.profiles = self.root / "profiles"
        self.profiles.mkdir()
        (self.profiles / "names.txt").write_text(
            "vaerksted-01\nvaerksted-02\n", encoding="ascii"
        )
        for name in ("vaerksted-01", "vaerksted-02"):
            profile = self.profiles / name
            profile.mkdir()
            (profile / "settings.json").write_text(
                json.dumps({"computer": name, "server": "192.0.2.50"}), encoding="utf-8"
            )
            (profile / "device.json").write_text(
                json.dumps({"computer": name, "token": name + "-token", "ui_token": name + "-ui"}),
                encoding="utf-8",
            )
            (profile / "ca.crt").write_text("CA-" + name, encoding="ascii")
            (profile / "admin.pub").write_text("ssh-ed25519 " + name, encoding="ascii")
        self.templates = self.root / "computer-selection.templates"
        self.templates.write_text("Template: elevhub/computer\n", encoding="ascii")
        self.log = self.root / "debconf.log"
        self.confmodule = self.root / "confmodule.sh"
        self.confmodule.write_text(textwrap.dedent("""
            db_progress() { return 0; }
            db_x_loadtemplatefile() { return 0; }
            db_subst() { printf 'subst:%s:%s:%s\\n' "$1" "$2" "$3" >> "$ELEVHUB_TEST_LOG"; }
            db_fset() { return 0; }
            db_set() { printf 'set:%s:%s\\n' "$1" "$2" >> "$ELEVHUB_TEST_LOG"; }
            db_input() { return 0; }
            db_go() { return "${ELEVHUB_TEST_DB_GO_RC:-0}"; }
            db_get() {
                case "$1" in
                    elevhub/computer) RET="${ELEVHUB_TEST_SELECTED:--- vælg computer --}" ;;
                    elevhub/computer-confirm) RET="${ELEVHUB_TEST_CONFIRM:-false}" ;;
                    *) RET="" ;;
                esac
                return 0
            }
        """).lstrip(), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def env(self, state=None):
        return {
            **os.environ,
            "ELEVHUB_TEST_MODE": "1",
            "ELEVHUB_TEST_CONFMODULE": str(self.confmodule),
            "ELEVHUB_TEST_LOG": str(self.log),
            "ELEVHUB_PROFILES_ROOT": str(self.profiles),
            "ELEVHUB_NAMES_FILE": str(self.profiles / "names.txt"),
            "ELEVHUB_TEMPLATES_FILE": str(self.templates),
            "ELEVHUB_COMPUTER_STATE": str(state or self.root / "selected-name"),
        }

    def run_script(self, script, env, *, target=None):
        if target is not None:
            env = {**env, "ELEVHUB_TARGET_ROOT": str(target)}
        return subprocess.run(["/bin/sh", str(script)], env=env,
                              capture_output=True, text=True, timeout=5)

    def test_selection_records_confirmed_name_and_hostname(self):
        env = self.env()
        env.update(ELEVHUB_TEST_SELECTED="vaerksted-02", ELEVHUB_TEST_CONFIRM="true")
        result = self.run_script(SELECT, env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "selected-name").read_text(), "vaerksted-02\n")
        log = self.log.read_text()
        self.assertIn("set:netcfg/hostname:vaerksted-02", log)
        self.assertIn("set:netcfg/get_hostname:vaerksted-02", log)
        self.assertNotIn("token", log)

    def test_back_cancels_before_partman(self):
        env = self.env()
        env.update(ELEVHUB_TEST_DB_GO_RC="30")
        result = self.run_script(SELECT, env)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "selected-name").exists())

    def test_sentinel_and_unconfirmed_selection_fail(self):
        for extra in ({}, {"ELEVHUB_TEST_SELECTED": "vaerksted-02", "ELEVHUB_TEST_CONFIRM": "false"}):
            with self.subTest(extra=extra):
                env = self.env()
                env.update(extra)
                result = self.run_script(SELECT, env)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "selected-name").exists())

    def test_partman_retry_reuses_confirmed_selection(self):
        state = self.root / "selected-name"
        state.write_text("vaerksted-02\n", encoding="ascii")
        env = self.env(state)
        env.update(ELEVHUB_TEST_DB_GO_RC="30", ELEVHUB_TEST_SELECTED="vaerksted-01")
        result = self.run_script(SELECT, env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state.read_text(), "vaerksted-02\n")
        self.assertIn("set:netcfg/hostname:vaerksted-02", self.log.read_text())

    def test_invalid_profile_list_fails_closed(self):
        (self.profiles / "names.txt").write_text("vaerksted-01\n../escape\n", encoding="ascii")
        env = self.env()
        env.update(ELEVHUB_TEST_SELECTED="vaerksted-01", ELEVHUB_TEST_CONFIRM="true")
        result = self.run_script(SELECT, env)
        self.assertNotEqual(result.returncode, 0)

    def test_copy_revalidates_and_copies_only_selected_four_files(self):
        state = self.root / "selected-name"
        state.write_text("vaerksted-02\n", encoding="ascii")
        target = self.root / "target"
        result = self.run_script(COPY, self.env(state), target=target)
        self.assertEqual(result.returncode, 0, result.stderr)
        installed = target / "etc/elevhub-install"
        self.assertEqual(sorted(p.name for p in installed.iterdir()),
                         ["admin.pub", "ca.crt", "device.json", "settings.json"])
        self.assertIn("vaerksted-02", (installed / "device.json").read_text())
        self.assertNotIn("vaerksted-01", (installed / "device.json").read_text())
        for path in installed.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_copy_rejects_unlisted_name_and_missing_profile_file(self):
        state = self.root / "selected-name"
        state.write_text("vaerksted-03\n", encoding="ascii")
        target = self.root / "target"
        result = self.run_script(COPY, self.env(state), target=target)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((target / "etc/elevhub-install").exists())

        state.write_text("vaerksted-01\n", encoding="ascii")
        (self.profiles / "vaerksted-01/admin.pub").unlink()
        result = self.run_script(COPY, self.env(state), target=target)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
