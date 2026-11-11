"""Focused checks for the Danish keyboard setup used by fresh USB installs."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PRESEED = ROOT / "testmiljoe/vm/preseed-usb.skabelon.cfg"
TASKS = ROOT / "ansible/roles/grundsystem/tasks/tastatur.yml"
MAIN = ROOT / "ansible/roles/grundsystem/tasks/main.yml"
KIOSK_SESSION = ROOT / "ansible/roles/kiosk/templates/kiosk-session.j2"


class TastaturSetupTests(unittest.TestCase):
    def test_usb_preseed_installs_and_pins_danish_pc105(self):
        text = PRESEED.read_text(encoding="utf-8")
        self.assertIn("d-i console-setup/ask_detect boolean false", text)
        self.assertIn("d-i keyboard-configuration/xkb-keymap select dk", text)
        self.assertIn("d-i keyboard-configuration/modelcode string pc105", text)
        self.assertIn("d-i keyboard-configuration/layoutcode string dk", text)
        self.assertIn("d-i keyboard-configuration/variantcode string\n", text)
        self.assertIn("d-i keyboard-configuration/optionscode string\n", text)
        self.assertIn("console-setup keyboard-configuration", text)

    def test_grundsystem_task_persists_and_activates_console_layout(self):
        text = TASKS.read_text(encoding="utf-8")
        self.assertIn("console-setup", text)
        self.assertIn("keyboard-configuration", text)
        for line in (
            'XKBMODEL="pc105"',
            'XKBLAYOUT="dk"',
            'XKBVARIANT=""',
            'XKBOPTIONS=""',
        ):
            self.assertIn(line, text)
        self.assertIn("/usr/bin/setupcon", text)
        self.assertIn("--save-only", text)
        # Saving the generated keymap is safe over SSH.  A live load can
        # collide with X/console state, so boot services do the activation.
        self.assertNotIn("--keyboard-only", text)
        self.assertNotIn("--force", text)
        self.assertNotIn("state: started", text)
        self.assertNotIn("state: restarted", text)
        self.assertIn("keyboard-setup.service", text)
        self.assertIn("- console-setup.service", text)

    def test_task_is_included_before_kiosk_role_changes_x(self):
        main = MAIN.read_text(encoding="utf-8")
        self.assertIn("ansible.builtin.include_tasks: tastatur.yml", main)
        # The kiosk session remains the final per-session X11 activation.
        session = KIOSK_SESSION.read_text(encoding="utf-8")
        self.assertIn("setxkbmap {{ kiosk_tastatur }}", session)


if __name__ == "__main__":
    unittest.main()
