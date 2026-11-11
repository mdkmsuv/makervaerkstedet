"""Adfærdsprøver for den opt-in projektbackup-rolle.

Shell- og restic-prøverne kører kun på Linux. De ændrer udelukkende
midlertidige testmapper og starter ingen systemd-enheder.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    import yaml  # Findes i Debian/Ansible-miljøet; er ikke en app-afhængighed.
except ImportError:
    yaml = None


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible" / "roles" / "projektbackup"
LINUX_ONLY = unittest.skipIf(os.name == "nt", "Linux shell/restic-prøve; springes over på Windows")


def render_backup_script(**values: str) -> str:
    """Render den lille Ansible-template med samme shell-quote-semantik."""
    template = (ROLE / "templates" / "projektbackup-backup.sh.j2").read_text(
        encoding="utf-8"
    )
    pattern = re.compile(r"{{\s*(\w+)\s*\|\s*quote\s*}}")
    return pattern.sub(lambda match: shlex.quote(values[match.group(1)]), template)


class ProjektbackupRoleTests(unittest.TestCase):
    @unittest.skipUnless(yaml, 'YAML-konfiguration afprøves i Debian/Ansible-miljøet.')
    def test_defaults_are_opt_in_and_do_not_choose_a_repository(self) -> None:
        defaults = yaml.safe_load(
            (ROLE / "defaults" / "main.yml").read_text(encoding="utf-8")
        )
        self.assertFalse(defaults["projektbackup_aktiveret"])
        self.assertEqual(defaults["projektbackup_maaltype"], "mounted")
        self.assertEqual(defaults["projektbackup_repository"], "")
        self.assertFalse(defaults["projektbackup_password_overwrite"])

    @LINUX_ONLY
    def test_rendered_backup_script_has_shell_syntax(self) -> None:
        rendered = render_backup_script(
            projektbackup_kilde="/home/elev/Projekter",
            projektbackup_repository="/mnt/projektbackup/restic",
            projektbackup_mountpoint="/mnt/projektbackup",
            projektbackup_maaltype="mounted",
            projektbackup_password_file="/etc/projektbackup/restic-password",
        )
        result = subprocess.run(
            ["/bin/sh", "-n"], input=rendered, text=True, capture_output=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    @LINUX_ONLY
    def test_missing_mount_fails_before_repository_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="projektbackup-test-") as temp:
            root = Path(temp)
            source = root / "Projekter"
            source.mkdir(mode=0o755)
            missing_mount = root / "disk-not-mounted"
            repository = missing_mount / "restic"
            rendered = render_backup_script(
                projektbackup_kilde=str(source),
                projektbackup_repository=str(repository),
                projektbackup_mountpoint=str(missing_mount),
                projektbackup_maaltype="mounted",
                projektbackup_password_file=str(root / "password"),
            )
            result = subprocess.run(
                ["/bin/sh"], input=rendered, text=True, capture_output=True
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ikke monteret", result.stderr)
            self.assertFalse(repository.exists())

    @LINUX_ONLY
    def test_source_symlink_is_rejected_before_mount_or_repository(self) -> None:
        with tempfile.TemporaryDirectory(prefix="projektbackup-test-") as temp:
            root = Path(temp)
            real_source = root / "real-projects"
            real_source.mkdir(mode=0o755)
            source_link = root / "Projekter"
            source_link.symlink_to(real_source, target_is_directory=True)
            rendered = render_backup_script(
                projektbackup_kilde=str(source_link),
                projektbackup_repository=str(root / "repo"),
                projektbackup_mountpoint=str(root / "disk"),
                projektbackup_maaltype="mounted",
                projektbackup_password_file=str(root / "password"),
            )
            result = subprocess.run(
                ["/bin/sh"], input=rendered, text=True, capture_output=True
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlink", result.stderr)

    @unittest.skipUnless(shutil.which("restic"), "restic er ikke installeret lokalt")
    @LINUX_ONLY
    def test_restic_backup_delete_and_restore_roundtrip(self) -> None:
        restic = shutil.which("restic")
        assert restic is not None
        with tempfile.TemporaryDirectory(prefix="projektbackup-test-") as temp:
            root = Path(temp)
            source = root / "Projekter"
            repository = root / "repository"
            password_file = root / "password"
            restore_target = root / "restored"
            source.mkdir()
            original = source / "tidligere.txt"
            original.write_text("første version\n", encoding="utf-8")
            # Local apps keep their version history under this hidden folder;
            # restic must retain it together with the visible project files.
            archive = source / ".elevhub" / "versioner" / "projekt-1"
            archive.mkdir(parents=True)
            (archive / "1.bin").write_bytes(b"editable history")
            password_file.write_text("test-only-password", encoding="utf-8")

            def run(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [
                        restic,
                        "--repo",
                        str(repository),
                        "--password-file",
                        str(password_file),
                        "--no-cache",
                        *args,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )

            run("init")
            run("backup", "--one-file-system", str(source))
            snapshots = json.loads(run("snapshots", "--json").stdout)
            self.assertEqual(len(snapshots), 1)

            original.unlink()
            original.write_text("senere version\n", encoding="utf-8")
            restore_target.mkdir()
            run("restore", snapshots[0]["short_id"], "--target", str(restore_target))
            restored = next(restore_target.rglob("tidligere.txt"))
            self.assertEqual(restored.read_text(encoding="utf-8"), "første version\n")
            restored_history = next(restore_target.rglob("1.bin"))
            self.assertEqual(restored_history.read_bytes(), b"editable history")


if __name__ == "__main__":
    unittest.main()
