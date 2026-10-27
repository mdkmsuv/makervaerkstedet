#!/usr/bin/env python3
"""Forget CaDoodle's last active file once at each Linux boot.

The CaDoodle workspace and all saved projects remain untouched.  The helper is
safe to run before every launcher attempt: it does nothing while CaDoodle is
running and never rewrites a malformed or unexpected configuration file.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import tempfile
import stat
try:
    import fcntl
except ImportError:  # Windows controller tests; the deployed target is Linux.
    fcntl = None


ACTIVE_KEY = "CaDoodleActiveFile"
DEFAULT_WORKSPACE = Path("Documents") / "CaDoodle-workspace"


def boot_id() -> str | None:
    """Return the kernel boot id; refuse to reset if Linux cannot provide it."""
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        value = ""
    return value or None


def cadoodle_running() -> bool:
    """Use the same process identity as the installer/bootstrap checks."""
    result = subprocess.run(
        ["pgrep", "-u", str(os.getuid()), "-f", "CaDoodle-Application.jar"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise RuntimeError(f"pgrep failed with exit code {result.returncode}")


def reset_once(home: Path, state_dir: Path, running=cadoodle_running,
               current_boot_id=boot_id) -> bool:
    """Remove only the active-file reference once per boot.

    Returns True when the JSON was changed.  A marker is written only after a
    successful atomic replacement, so a failed update can be retried safely.
    """
    if running():
        return False
    identity = current_boot_id()
    if not identity:
        return False
    marker = state_dir / "cadoodle-fresh-session.boot"
    try:
        if marker.read_text(encoding="ascii").strip() == identity:
            return False
    except FileNotFoundError:
        pass

    def mark_handled() -> None:
        state_dir.mkdir(parents=True, exist_ok=True)
        marker_tmp = state_dir / "cadoodle-fresh-session.boot.tmp"
        marker_tmp.write_text(identity + "\n", encoding="ascii")
        os.replace(marker_tmp, marker)

    config = home / DEFAULT_WORKSPACE / "ConfigurationDatabase.json"
    try:
        original = config.read_text(encoding="utf-8-sig")
        document = json.loads(original)
    except FileNotFoundError:
        mark_handled()
        return False
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(document, dict):
        return False
    section = document.get("CaDoodle")
    if section is None:
        mark_handled()
        return False
    if not isinstance(section, dict) or config.is_symlink():
        return False

    changed = ACTIVE_KEY in section
    if changed:
        del section[ACTIVE_KEY]
        state_dir.mkdir(parents=True, exist_ok=True)
        mode = stat.S_IMODE(config.stat().st_mode)
        fd, temporary_name = tempfile.mkstemp(prefix="ConfigurationDatabase.", suffix=".tmp",
                                               dir=config.parent)
        temporary = Path(temporary_name)
        try:
            os.chmod(temporary, mode)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
                json.dump(document, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, config)
        finally:
            temporary.unlink(missing_ok=True)

    mark_handled()
    return changed


@contextmanager
def launcher_lock(state_dir: Path):
    """Share the launcher's nonblocking lock for the reset/launch boundary."""
    state_dir.mkdir(parents=True, exist_ok=True)
    handle = (state_dir / "cadoodle.lock").open("a+")
    try:
        if fcntl is None:
            yield True
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--state-dir", type=Path,
                        default=Path.home() / ".local/state/elevhub")
    args = parser.parse_args()
    with launcher_lock(args.state_dir) as acquired:
        if acquired:
            reset_once(args.home, args.state_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
