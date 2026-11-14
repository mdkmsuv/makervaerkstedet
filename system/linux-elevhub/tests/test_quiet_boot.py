import subprocess
import shutil
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/roles/kiosk"


class QuietBootTest(unittest.TestCase):
    def test_grub_fragment_preserves_existing_kernel_parameters(self):
        if shutil.which("sh") is None:
            self.skipTest("requires POSIX sh; run this test in WSL")
        config = (ROLE / "files/elevhub-quiet-boot.cfg").resolve()
        shell = """
set -eu
GRUB_CMDLINE_LINUX_DEFAULT='root=UUID=test ro'
. "$1"
printf '%s\n' "$GRUB_TIMEOUT_STYLE"
printf '%s\n' "$GRUB_TIMEOUT"
printf '%s\n' "$GRUB_CMDLINE_LINUX_DEFAULT"
"""
        result = subprocess.run(
            ["sh", "-c", shell, "quiet-boot-test", str(config)],
            check=True,
            capture_output=True,
            text=True,
        )
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0:2], ["hidden", "1"])
        self.assertIn("root=UUID=test ro", lines[2])
        self.assertIn("quiet", lines[2])
        self.assertIn("splash", lines[2])
        self.assertIn("loglevel=0", lines[2])
        self.assertIn("vt.global_cursor_default=0", lines[2])

if __name__ == "__main__":
    unittest.main()
