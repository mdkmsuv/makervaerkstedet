#!/usr/bin/env python3
"""Safely seed CaDoodle's per-user application cache from its official ZIP."""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
from pathlib import Path, PurePosixPath
try:
    import pwd
except ImportError:  # The unit tests also run on the Windows controller.
    class _Pwd:
        @staticmethod
        def getpwnam(name: str):
            raise RuntimeError("pwd is only available on Linux")
    pwd = _Pwd()
import subprocess
import tempfile
import zipfile


VERSION = "0.46.094"
RUNTIME_ARCHIVE = "zulu25.36.15-ca-fx-jdk25.0.4-linux_x64.tar.gz"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_member(name: str) -> PurePosixPath:
    if "\\" in name or "\x00" in name:
        raise ValueError(f"unsafe archive path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts or any(":" in part for part in path.parts):
        raise ValueError(f"unsafe archive path: {name!r}")
    return path


def is_symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def bootstrap(archive: Path, home: Path, jar_sha256: str,
              runtime_archive: Path | None = None) -> bool:
    base = home / "bin"
    target = base / "CaDoodle-ApplicationInstall"
    jar = target / VERSION / "CaDoodle-Application.jar"
    runtime_target = target / RUNTIME_ARCHIVE
    if subprocess.run(["pgrep", "-u", "elev", "-f", "CaDoodle-Application.jar"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        raise RuntimeError("Close CaDoodle before bootstrapping its application cache.")
    if not archive.is_file():
        raise FileNotFoundError(archive)

    def reject_symlink_chain(path: Path) -> None:
        current = Path(path.anchor or os.sep)
        for part in path.parts[1:]:
            current /= part
            if current.is_symlink():
                raise ValueError(f"symlink in installation path: {current}")

    reject_symlink_chain(home)
    reject_symlink_chain(base)
    account = pwd.getpwnam("elev")
    changed = False
    created: list[Path] = []
    with zipfile.ZipFile(archive) as source:
        members: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
        seen: set[PurePosixPath] = set()
        for info in source.infolist():
            path = safe_member(info.filename)
            prefix = PurePosixPath("CaDoodle-ApplicationInstall")
            if not path.parts or path.parts[0] != prefix.parts[0]:
                raise ValueError(f"archive entry outside installation prefix: {info.filename!r}")
            if path in seen:
                raise ValueError(f"duplicate archive path: {info.filename!r}")
            seen.add(path)
            if is_symlink(info):
                raise ValueError(f"symlink in archive: {info.filename!r}")
            members.append((info, path))

        prefix = PurePosixPath("CaDoodle-ApplicationInstall")
        required = prefix / VERSION / "CaDoodle-Application.jar"
        jar_info = next((info for info, path in members if path == required), None)
        if jar_info is None:
            raise ValueError("archive does not contain the expected application JAR")

        # Read and verify the official JAR before touching the installation.
        jar_bytes = source.read(jar_info)
        if hashlib.sha256(jar_bytes).hexdigest() != jar_sha256.lower():
            raise RuntimeError("bootstrap archive contains an unexpected application JAR")

        required_metadata = {
            prefix / "currentversion.txt": f"{VERSION}\n".encode(),
            prefix / VERSION / "jvm.json": None,
            prefix / VERSION / "gitcache.zip": None,
        }
        for required_path, expected in required_metadata.items():
            info = next((item for item, path in members if path == required_path), None)
            if info is None or info.is_dir() or info.filename.endswith("/"):
                raise ValueError(f"archive lacks required CaDoodle resource: {required_path}")
            if expected is not None and source.read(info) != expected:
                raise ValueError(f"archive has an unexpected {required_path.name}")

        existing_jar = jar.exists() or jar.is_symlink()
        if existing_jar and (jar.is_symlink() or not jar.is_file()):
            raise ValueError(f"unsafe existing destination: {jar}")

        for info, relative in members:
            destination = base / Path(*relative.parts)
            reject_symlink_chain(destination.parent)
            if destination.is_symlink():
                raise ValueError(f"unsafe existing destination: {destination}")
            missing_parents: list[Path] = []
            cursor = destination.parent
            while not cursor.exists():
                missing_parents.append(cursor)
                cursor = cursor.parent
            destination.parent.mkdir(parents=True, exist_ok=True)
            created.extend(reversed(missing_parents))
            if info.is_dir() or info.filename.endswith("/"):
                if destination.exists() and not destination.is_dir():
                    raise ValueError(f"unsafe existing destination: {destination}")
                if not destination.exists():
                    created.append(destination)
                destination.mkdir(parents=True, exist_ok=True)
                continue
            # Never replace an installed application JAR: it may be the Kloster
            # overlay. Other bootstrap resources are safely refreshed below.
            if destination.exists():
                if destination.is_symlink() or not destination.is_file():
                    raise ValueError(f"unsafe existing destination: {destination}")
                if relative == required or relative not in required_metadata:
                    continue
                existing = destination.read_bytes()
                if existing == source.read(info):
                    continue
            temporary = _atomic_extract(source, info, destination)
            os.replace(temporary, destination)
            changed = True
            created.append(destination)

    if not jar.is_file():
        raise RuntimeError("installed CaDoodle application JAR is missing")
    if not existing_jar and digest(jar) != jar_sha256.lower():
        raise RuntimeError("installed CaDoodle application JAR does not match the official hash")

    if runtime_archive is not None:
        runtime_archive = Path(runtime_archive)
        reject_symlink_chain(runtime_archive)
        reject_symlink_chain(runtime_target)
        runtime_changed = _prepare_runtime(runtime_archive, runtime_target, target)
        changed = changed or runtime_changed
        if runtime_target not in created:
            created.append(runtime_target)

    set_ownership(created, account)

    # The upstream updater treats this directory's existence as its first-run
    # guard. Create it only after every offline resource is ready. Callers that
    # do not provide a local runtime keep the legacy bootstrap behavior and do
    # not get a marker that would force the updater past runtime provisioning.
    if runtime_archive is None:
        return changed

    plugin_dir = home / "bin" / "BowlerStudioInstall"
    reject_symlink_chain(plugin_dir)
    if plugin_dir.is_symlink() or (plugin_dir.exists() and not plugin_dir.is_dir()):
        raise ValueError(f"unsafe existing destination: {plugin_dir}")
    if not plugin_dir.exists():
        plugin_dir.mkdir(parents=True, exist_ok=True)
        created.append(plugin_dir)
        changed = True
    set_ownership([plugin_dir], account)
    return changed


def set_ownership(paths: list[Path], account) -> None:
    if not hasattr(os, "chown"):
        return
    for path in paths:
        try:
            os.chown(path, account.pw_uid, account.pw_gid)
        except PermissionError:
            raise RuntimeError(f"could not set ownership on {path}")


def _atomic_extract(source: zipfile.ZipFile, info: zipfile.ZipInfo,
                    destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{destination.name}.bootstrap-",
                                     dir=destination.parent, delete=False) as output:
        temporary = Path(output.name)
        with source.open(info) as input_stream:
            shutil.copyfileobj(input_stream, output, 1024 * 1024)
    return temporary


def _prepare_runtime(source: Path, destination: Path, target: Path) -> bool:
    if source.name != RUNTIME_ARCHIVE or not source.is_file() or source.is_symlink():
        raise ValueError(f"runtime archive is not a regular local file: {source}")
    if destination.is_symlink() or (destination.exists() and not destination.is_file()):
        raise ValueError(f"unsafe existing destination: {destination}")
    if destination.exists() and digest(destination) == digest(source):
        return False
    target.mkdir(parents=True, exist_ok=True)
    expected = digest(source)
    temporary: Path | None = None
    try:
        with source.open("rb") as input_stream, tempfile.NamedTemporaryFile(
                prefix=f".{destination.name}.bootstrap-", dir=target, delete=False) as output:
            temporary = Path(output.name)
            shutil.copyfileobj(input_stream, output, 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        if digest(temporary) != expected:
            raise RuntimeError("runtime archive changed while it was being copied")
        os.replace(temporary, destination)
        temporary = None
        return True
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", required=True, type=Path)
    parser.add_argument("--home", default="/home/elev", type=Path)
    parser.add_argument("--jar-sha256", required=True)
    parser.add_argument("--runtime-archive", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Run as root so the extracted cache can be owned by elev.")
    print("extracted" if bootstrap(args.zip, args.home, args.jar_sha256,
                                    args.runtime_archive) else "already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
