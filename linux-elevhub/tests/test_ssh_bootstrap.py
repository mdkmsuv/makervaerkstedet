"""Regression tests for the installer SSH bootstrap.

The preseed late_command is exercised by a POSIX shell with an ``in-target``
shim.  The shim maps absolute target paths into a temporary directory and
records the operations; no Linux service, VM, WSL instance, or real account is
involved.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRESEEDS = (
    ROOT / "testmiljoe" / "vm" / "preseed-usb.skabelon.cfg",
    ROOT / "testmiljoe" / "vm" / "preseed.skabelon.cfg",
)
# Synthetic SSH wire data made solely from fixed test bytes; never a school key.
import base64
import struct
_fixture_type = b'ssh-ed25519'
_fixture_wire = struct.pack('>I', len(_fixture_type)) + _fixture_type + struct.pack('>I', 32) + bytes(range(32))
PUBLIC_KEY = 'ssh-ed25519 ' + base64.b64encode(_fixture_wire).decode('ascii') + ' synthetic-fixture'
BASH = Path("C:/Program Files/Git/bin/bash.exe") if os.name == "nt" else Path("/bin/bash")


def late_command(preseed: Path) -> str:
    text = preseed.read_text(encoding="utf-8")
    match = re.search(
        r"^d-i preseed/late_command string \\\n+(.*?)(?=^\s*###|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise AssertionError(f"late_command missing in {preseed}")
    command = match.group(1).replace("\\\n", " ")
    command = command.replace("@@NOEGLE@@", PUBLIC_KEY)
    return command.strip()


SHIM = r'''#!/usr/bin/env bash
set -u
TARGET="$1"
LOG="$2"
FAIL="$3"
shift 3
COMMAND="${COMMAND//\/target/$TARGET}"
log() { printf '%s\n' "$*" >> "$LOG"; }
map_path() {
  case "$1" in
    /*) printf '%s%s' "$TARGET" "$1" ;;
    *) printf '%s' "$1" ;;
  esac
}
in-target() {
  local command="$1"; shift
  log "in-target $command $*"
  case "$command" in
    install)
      local mode="" owner="" group="" path
      while (($#)); do
        case "$1" in
          -d|-m) [[ "$1" == -m ]] && mode="$2" && shift ;;
          -o) owner="$2"; shift ;;
          -g) group="$2"; shift ;;
          *) path="$1"; mkdir -p "$(map_path "$path")" ;;
        esac
        shift
      done
      ;;
    chown|chmod|passwd)
      log "fake-$command $*"
      ;;
    ssh-keygen)
      [[ "$FAIL" == keygen ]] && { log "ssh-keygen failed"; return 42; }
      log "ssh-keygen ok"
      ;;
    /usr/sbin/sshd)
      [[ "$FAIL" == sshd ]] && { log "sshd -t failed"; return 43; }
      log "sshd -t ok"
      ;;
    systemctl)
      log "systemctl $*"
      ;;
    *) log "unexpected command: $command"; return 44 ;;
  esac
}
set +e
eval "$COMMAND"
status=$?
printf 'status=%s\n' "$status" >> "$LOG"
exit "$status"
'''


class SshBootstrapTests(unittest.TestCase):
    def run_command(self, command: str, failure: str = "") -> tuple[int, str, dict[str, str]]:
        with tempfile.TemporaryDirectory(prefix="elevhub-ssh-test-") as directory:
            temp = Path(directory)
            target = temp / "target"
            target.mkdir()
            (target / "etc/sudoers.d").mkdir(parents=True)
            (target / "etc/ssh/sshd_config.d").mkdir(parents=True)
            (target / "run/sshd").mkdir(parents=True)
            log = temp / "operations.log"
            shim = temp / "run.sh"
            shim.write_text(SHIM, encoding="utf-8", newline="\n")
            shim.chmod(0o755)
            env = os.environ.copy()
            env["COMMAND"] = command
            env["MSYS_NO_PATHCONV"] = "1"
            def git_path(path: Path) -> str:
                if os.name != "nt":
                    return str(path)
                drive = path.drive.rstrip(":").lower()
                tail = str(path).split(":", 1)[1].replace("\\", "/")
                return f"/{drive}{tail}"
            result = subprocess.run(
                [str(BASH), git_path(shim), git_path(target), git_path(log), failure],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            if result.stderr:
                log.write_text(log.read_text(encoding="utf-8") + result.stderr, encoding="utf-8")
            files = {
                str(path.relative_to(target)).replace("\\", "/"): path.read_text(encoding="utf-8")
                for path in target.rglob("*")
                if path.is_file()
            }
            return result.returncode, log.read_text(encoding="utf-8"), files

    def test_both_preseeds_have_same_ssh_flow_and_no_private_key(self) -> None:
        commands = [late_command(path) for path in PRESEEDS]
        for path, command in zip(PRESEEDS, commands):
            self.assertIn("openssh-server", path.read_text(encoding="utf-8"))
            self.assertIn("authorized_keys", command)
            self.assertIn("ssh-keygen -A", command)
            self.assertIn("/usr/sbin/sshd -t", command)
            self.assertIn("systemctl enable ssh.service", command)
            self.assertIn("for line in", command)
            self.assertNotIn("printf '%s\\n'", command)
            self.assertNotIn("elevhub_ed25519'", command)
            self.assertNotIn("PRIVATE", command.upper())
        self.assertEqual(
            [re.sub(r"@@(?:IP|VAERT|NAVN)@@", "PLACEHOLDER", command) for command in commands][0],
            [re.sub(r"@@(?:IP|VAERT|NAVN)@@", "PLACEHOLDER", command) for command in commands][1],
        )

    def test_success_validates_key_and_enables_ssh_afterwards(self) -> None:
        for preseed in PRESEEDS:
            with self.subTest(preseed=preseed.name):
                status, log, files = self.run_command(late_command(preseed))
                self.assertEqual(status, 0, log)
                self.assertIn("ssh-keygen ok", log)
                self.assertIn("sshd -t ok", log)
                self.assertIn("systemctl enable ssh.service", log)
                self.assertLess(log.index("ssh-keygen ok"), log.index("sshd -t ok"))
                self.assertLess(log.index("sshd -t ok"), log.index("systemctl enable ssh.service"))
                self.assertEqual(files.get("home/elevhub/.ssh/authorized_keys"), PUBLIC_KEY + "\n")
                config = files.get("etc/ssh/sshd_config.d/10-elevhub.conf", "")
                for line in ("PermitRootLogin no", "PasswordAuthentication no", "PubkeyAuthentication yes", "AllowUsers elevhub"):
                    self.assertIn(line, config)
                self.assertNotIn("elevhub_ed25519", "\n".join(files))

    def test_key_generation_failure_stops_before_enable(self) -> None:
        for preseed in PRESEEDS:
            with self.subTest(preseed=preseed.name):
                status, log, files = self.run_command(late_command(preseed), "keygen")
                self.assertNotEqual(status, 0)
                self.assertNotIn("systemctl enable ssh.service", log)
                self.assertIn("etc/ssh/sshd_config.d/10-elevhub.conf", files)

    def test_sshd_validation_failure_stops_before_enable(self) -> None:
        for preseed in PRESEEDS:
            with self.subTest(preseed=preseed.name):
                status, log, files = self.run_command(late_command(preseed), "sshd")
                self.assertNotEqual(status, 0)
                self.assertIn("ssh-keygen ok", log)
                self.assertNotIn("systemctl enable ssh.service", log)
                self.assertIn("etc/ssh/sshd_config.d/10-elevhub.conf", files)


if __name__ == "__main__":
    unittest.main()
