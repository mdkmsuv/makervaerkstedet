#!/usr/bin/env python3
"""Build a self-contained, JAR-only CaDoodle update directory and ZIP."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

VERSION = "0.46.094"
RELEASE_ID = "2026.10.06.17"
ENTRY = "com/commonwealthrobotics/Main.class"
HERE = Path(__file__).resolve().parent


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_jar(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"JAR findes ikke: {path}")
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ValueError(f"Korrupt JAR: {path}")
            return hashlib.sha256(archive.read(ENTRY)).hexdigest()
    except (zipfile.BadZipFile, KeyError) as error:
        raise ValueError(f"Ugyldig CaDoodle-JAR: {path}: {error}") from error


def add_platform(root: Path, platform: str, source: Path, build_date: str) -> None:
    entry_hash = validate_jar(source)
    destination = root / platform
    destination.mkdir()
    payload = destination / "CaDoodle-Kloster.jar"
    shutil.copy2(source, payload)
    manifest = {
        "schema": 1,
        "product": "CaDoodle",
        "app_version": VERSION,
        "release_id": RELEASE_ID,
        "build_date": build_date,
        "platform": platform,
        "payload": payload.name,
        "sha256": sha(payload),
        "size": payload.stat().st_size,
        "compatibility": {"required_entry": ENTRY, "entry_sha256": entry_hash},
        "runtime": {"required_modules": ["java.logging", "jdk.zipfs"]} if platform == "windows" else {},
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows-jar", type=Path, required=True)
    parser.add_argument("--linux-jar", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    sources = [args.windows_jar.resolve(), args.linux_jar.resolve()]
    out = args.out.resolve()
    archives = [out.with_suffix(".zip"), out.with_name(out.name + "-windows.zip"), out.with_name(out.name + "-linux.zip")]
    if out.exists() or any(path.exists() for path in archives):
        parser.error("Output findes allerede; vÃ¦lg en ny --out-sti.")
    if out == HERE or HERE in out.parents or out in HERE.parents:
        parser.error("Output mÃ¥ ikke vÃ¦re kildekodens mappe eller dens overordnede mappe.")
    for source in sources:
        if source == out or out in source.parents:
            parser.error("Output mÃ¥ ikke indeholde input-JAR.")
        validate_jar(source)
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".cadoodle-update-", dir=out.parent))
    try:
        build_date = datetime.now(timezone.utc).date().isoformat()
        add_platform(temporary, "windows", sources[0], build_date)
        add_platform(temporary, "linux", sources[1], build_date)
        for name in ("Install-Windows.ps1", "Start-Update.cmd", "install_linux.py", "README.md"):
            shutil.copy2(HERE / name, temporary / name)
        checksums = []
        for file in sorted(temporary.rglob("*")):
            if file.is_file():
                checksums.append((file.relative_to(temporary), sha(file)))
        (temporary / "SHA256SUMS.txt").write_text("\n".join(f"{value}  {path.as_posix()}" for path, value in checksums) + "\n", encoding="utf-8")
        zip_stages = []
        try:
            for archive_path, names in (
                (archives[0], None),
                (archives[1], {"windows", "Install-Windows.ps1", "Start-Update.cmd", "README.md", "SHA256SUMS.txt"}),
                (archives[2], {"linux", "install_linux.py", "README.md", "SHA256SUMS.txt"}),
            ):
                stage = out.parent / f".{archive_path.name}.{os.getpid()}.tmp"
                zip_stages.append((stage, archive_path))
                with zipfile.ZipFile(stage, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for file in sorted(temporary.rglob("*")):
                        relative = file.relative_to(temporary)
                        if file.is_file() and relative.name != "SHA256SUMS.txt" and (names is None or relative.parts[0] in names):
                            archive.write(file, Path(out.name) / relative)
                    included = [(path, value) for path, value in checksums if names is None or path.parts[0] in names]
                    archive.writestr(str(Path(out.name) / "SHA256SUMS.txt"), "\n".join(f"{value}  {path.as_posix()}" for path, value in included) + "\n")
            os.replace(temporary, out)
            for stage, archive_path in zip_stages:
                os.replace(stage, archive_path)
        finally:
            for stage, _ in zip_stages:
                stage.unlink(missing_ok=True)
    finally:
        if temporary.exists():
            resolved = temporary.resolve()
            if resolved.parent != out.parent.resolve() or not resolved.name.startswith(".cadoodle-update-"):
                raise RuntimeError(f"Refusing to remove unexpected temporary directory: {resolved}")
            shutil.rmtree(temporary)
    print(f"Pakken er klar: {out}, {', '.join(map(str, archives))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
