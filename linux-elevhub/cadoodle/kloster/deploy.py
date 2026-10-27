#!/usr/bin/env python3
"""Install/restore a built Kloster JAR, refusing unexpected upstream versions."""
from pathlib import Path
import argparse
import hashlib
import os
import shutil
import subprocess
import zipfile
import json
from urllib.parse import urlsplit

BASE = Path("/home/elev/bin/CaDoodle-ApplicationInstall/0.46.094")
TARGET = BASE / "CaDoodle-Application.jar"
BACKUP = BASE / "CaDoodle-Application.official.jar"
OFFICIAL = "e51ecdbedc64e98d78c63b05431395a7979d20c76a42b065dede88b3a5918a09"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("jar", type=Path, nargs="?")
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--print-url", help="Pupil Printværksted origin, e.g. https://server:8770")
    parser.add_argument("--print-ca", type=Path, help="Trusted school CA certificate on this VM")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Run with sudo.")
    if args.print_url:
        url = urlsplit(args.print_url)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
            raise SystemExit("Supply only the pupil Printværksted origin.")
        if args.print_ca and not args.print_ca.is_file():
            raise SystemExit("The school CA certificate is missing.")
    # Do not replace an open archive: Java may lazily read class/resources later.
    result = subprocess.run(["pgrep", "-u", "elev", "-f", "CaDoodle-Application.jar"], capture_output=True)
    if result.returncode == 0:
        raise SystemExit("Close CaDoodle before installing/restoring the extension.")
    if not BACKUP.exists():
        if sha(TARGET) != OFFICIAL:
            raise SystemExit("The original installed JAR has an unexpected hash.")
        shutil.copy2(TARGET, BACKUP)
    if sha(BACKUP) != OFFICIAL:
        raise SystemExit("The official backup has an unexpected hash.")
    source = BACKUP if args.restore else args.jar
    if source is None:
        raise SystemExit("Supply the built CaDoodle-Kloster.jar.")
    if not args.restore:
        with zipfile.ZipFile(source) as archive:
            if archive.testzip() is not None:
                raise SystemExit("The built archive is corrupt.")
            archive.getinfo("com/commonwealthrobotics/kloster/KlosterWorkshop.class")
    pending = TARGET.with_suffix(".jar.pending")
    shutil.copy2(source, pending)
    shutil.chown(pending, user="elev", group="elev")
    pending.chmod(0o644)
    os.replace(pending, TARGET)
    if args.print_url:
        config = Path("/etc/elevhub/cadoodle-print.json")
        config.parent.mkdir(parents=True, exist_ok=True)
        data = {"url": args.print_url.rstrip("/")}
        if args.print_ca:
            data["ca_file"] = str(args.print_ca.resolve())
        config.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        config.chmod(0o644)
    print("Restored official CaDoodle." if args.restore else "Installed Kloster extension 1.3.")
    print("SHA256:", sha(TARGET))


if __name__ == "__main__":
    main()
