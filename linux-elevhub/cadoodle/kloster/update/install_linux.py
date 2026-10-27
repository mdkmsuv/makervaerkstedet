#!/usr/bin/env python3
"""Install or roll back the pinned CaDoodle JAR and its Elevhub cache."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path

VERSION = "0.46.094"
ENTRY = "com/commonwealthrobotics/Main.class"
DEFAULT_ROOT = Path("/home/elev/bin/CaDoodle-ApplicationInstall/0.46.094")
DEFAULT_CACHE = Path("/var/cache/elevhub/CaDoodle-Kloster.jar")
DEFAULT_LOCK = Path("/home/elev/.local/state/elevhub/cadoodle.lock")
DEFAULT_STATE = Path("/var/lib/elevhub/cadoodle-update")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def entry_digest(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ValueError(f"Korrupt JAR: {path}")
            return hashlib.sha256(archive.read(ENTRY)).hexdigest()
    except (zipfile.BadZipFile, KeyError) as error:
        raise ValueError(f"Ugyldig CaDoodle-JAR: {path}") from error


def load_package(package: Path) -> tuple[dict, Path]:
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("schema"), manifest.get("product"), manifest.get("app_version"), manifest.get("platform")) != (1, "CaDoodle", VERSION, "linux"):
        raise ValueError("Uforenelig opdateringspakke.")
    if manifest.get("payload") != "CaDoodle-Kloster.jar":
        raise ValueError("Ugyldig payload-sti i manifest.")
    payload = package / manifest["payload"]
    if not payload.is_file() or payload.stat().st_size != manifest.get("size") or digest(payload) != manifest.get("sha256"):
        raise ValueError("Payloadens størrelse eller checksum matcher ikke manifestet.")
    if manifest.get("compatibility") != {"required_entry": ENTRY, "entry_sha256": entry_digest(payload)}:
        raise ValueError("Payloadens CaDoodle-entry matcher ikke manifestet.")
    return manifest, payload


@contextmanager
def launcher_lock(path: Path):
    parent_created = not path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    if parent_created and path == DEFAULT_LOCK:
        account = pwd.getpwnam("elev")
        os.chown(path.parent, account.pw_uid, account.pw_gid)
    created = not path.exists()
    with path.open("a+b") as handle:
        if created and path == DEFAULT_LOCK:
            account = pwd.getpwnam("elev")
            os.chown(path, account.pw_uid, account.pw_gid)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("CaDoodle-start er i gang; prøv igen senere.") from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def assert_not_running() -> None:
    try:
        result = subprocess.run(["pgrep", "-af", "CaDoodle-Application.jar|CaDoodle-Kloster.jar"], capture_output=True, text=True, check=False)
    except OSError as error:
        raise ValueError(f"Kan ikke kontrollere kørende CaDoodle: {error}") from error
    if result.returncode not in (0, 1):
        raise ValueError(f"pgrep fejlede ({result.returncode}); installation afbrudt.")
    if result.returncode == 1:
        return
    for line in result.stdout.splitlines():
        match = re.match(r"^(\d+)\s+(.+)$", line)
        if not match:
            raise ValueError("Kunne ikke læse pgrep-resultat.")
        pid = int(match.group(1))
        try:
            command = (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
        except FileNotFoundError:
            continue  # Processen sluttede mellem pgrep og opslaget.
        except OSError as error:
            raise ValueError(f"Kan ikke kontrollere proces {pid}: {error}") from error
        if not command or not command[0]:
            continue
        executable = Path(os.fsdecode(command[0])).name.lower()
        if executable in ("java", "javaw", "cadoodle", "cadoodle-application") and any(
            b"CaDoodle-Application.jar" in arg or b"CaDoodle-Kloster.jar" in arg for arg in command[1:]
        ):
            raise ValueError("CaDoodle kører; opdateringen udsættes.")


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.pending")
    try:
        shutil.copy2(source, stage)
        if destination.is_file():
            old_stat = destination.stat()
            os.chown(stage, old_stat.st_uid, old_stat.st_gid)
            os.chmod(stage, stat.S_IMODE(old_stat.st_mode))
        if digest(stage) != digest(source):
            raise ValueError(f"Staged checksum mismatch: {destination}")
        os.replace(stage, destination)
    finally:
        stage.unlink(missing_ok=True)


def atomic_json(data: dict, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.pending")
    try:
        stage.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(stage, destination)
    finally:
        stage.unlink(missing_ok=True)


def current_hash(path: Path) -> str | None:
    return digest(path) if path.is_file() else None


def restore(receipt: dict, transaction: Path, target: Path, cache: Path) -> None:
    originals = receipt["original"]
    for name, path in (("target", target), ("cache", cache)):
        expected = originals[name]
        if expected is None:
            path.unlink(missing_ok=True)
            continue
        backup = transaction / f"{name}.jar"
        if digest(backup) != expected:
            raise ValueError(f"Backup for {name} er ændret; ingen gendannelse udført.")
        atomic_copy(backup, path)


def install(manifest: dict, payload: Path, target: Path, cache: Path, state: Path) -> None:
    if entry_digest(target) != manifest["compatibility"]["entry_sha256"]:
        raise ValueError("Installeret JAR passer ikke til CaDoodle 0.46.094.")
    new_hash = manifest["sha256"]
    if current_hash(target) == new_hash and current_hash(cache) == new_hash:
        print("Allerede opdateret.")
        return
    state.mkdir(parents=True, exist_ok=True)
    transaction = state / f"transaction-{uuid.uuid4().hex}"
    transaction.mkdir(mode=0o700)
    receipt = {
        "schema": 1,
        "target": str(target), "cache": str(cache),
        "original": {"target": digest(target), "cache": current_hash(cache)},
        "installed": {"target": new_hash, "cache": new_hash},
    }
    shutil.copy2(target, transaction / "target.jar")
    if receipt["original"]["cache"] is not None:
        shutil.copy2(cache, transaction / "cache.jar")
    if digest(transaction / "target.jar") != receipt["original"]["target"] or (
        receipt["original"]["cache"] is not None and digest(transaction / "cache.jar") != receipt["original"]["cache"]
    ):
        raise ValueError("Backup kunne ikke verificeres.")
    active = state / "active.json"
    previous_active = active.read_bytes() if active.is_file() else None
    try:
        atomic_copy(payload, target)
        atomic_copy(payload, cache)
        if current_hash(target) != new_hash or current_hash(cache) != new_hash:
            raise ValueError("Installeret JAR eller cache kunne ikke verificeres.")
        atomic_json(receipt, transaction / "receipt.json")
        atomic_json({"transaction": transaction.name}, active)
        (state / "last-rollback.json").unlink(missing_ok=True)
    except Exception as error:
        try:
            restore(receipt, transaction, target, cache)
            if previous_active is None:
                active.unlink(missing_ok=True)
            else:
                stage = active.with_name(f".{active.name}.{uuid.uuid4().hex}.pending")
                stage.write_bytes(previous_active)
                os.replace(stage, active)
        except Exception as restore_error:
            raise RuntimeError(f"Installationen fejlede, og gendannelse fejlede: {restore_error}; backup: {transaction}") from error
        raise
    print(f"CaDoodle er opdateret. Backup: {transaction}")


def rollback(target: Path, cache: Path, state: Path, dry_run: bool = False) -> None:
    active = state / "active.json"
    if not active.is_file():
        marker = state / "last-rollback.json"
        if marker.is_file():
            previous = json.loads(marker.read_text(encoding="utf-8"))
            if previous.get("target") == str(target) and previous.get("cache") == str(cache) and previous.get("original") == {"target": current_hash(target), "cache": current_hash(cache)}:
                print("Allerede rullet tilbage.")
                return
        raise ValueError("Ingen aktiv opdateringskvittering fundet.")
    reference = json.loads(active.read_text(encoding="utf-8"))["transaction"]
    if not re.fullmatch(r"transaction-[0-9a-f]{32}", reference):
        raise ValueError("Ugyldig transaktionsreference.")
    transaction = state / reference
    receipt = json.loads((transaction / "receipt.json").read_text(encoding="utf-8"))
    if receipt.get("schema") != 1 or receipt.get("target") != str(target) or receipt.get("cache") != str(cache):
        raise ValueError("Backup tilhører en anden installation.")
    if receipt.get("installed") != {"target": current_hash(target), "cache": current_hash(cache)}:
        raise ValueError("Aktuel JAR eller cache er ændret siden opdateringen; rollback afbrudt.")
    for name in ("target", "cache"):
        expected = receipt["original"][name]
        if expected is not None and digest(transaction / f"{name}.jar") != expected:
            raise ValueError(f"Backup for {name} er ændret.")
    if dry_run:
        print(f"Dry-run: rollback kontrolleret for {target} og {cache}.")
        return
    snapshot = state / f".rollback-{uuid.uuid4().hex}"
    snapshot.mkdir(mode=0o700)
    shutil.copy2(target, snapshot / "target.jar")
    shutil.copy2(cache, snapshot / "cache.jar")
    marker = state / "last-rollback.json"
    try:
        restore(receipt, transaction, target, cache)
        if receipt["original"] != {"target": current_hash(target), "cache": current_hash(cache)}:
            raise ValueError("Rollback kunne ikke verificeres.")
        atomic_json({"target": str(target), "cache": str(cache), "original": receipt["original"]}, marker)
        active.unlink()
    except Exception as error:
        if marker.is_file():
            marker.unlink()
        try:
            atomic_copy(snapshot / "target.jar", target)
            atomic_copy(snapshot / "cache.jar", cache)
        except Exception as restore_error:
            raise RuntimeError(f"Rollback fejlede, og den opdaterede version kunne ikke gendannes: {restore_error}; snapshot: {snapshot}") from error
        raise
    finally:
        if active.exists() is False or {"target": current_hash(target), "cache": current_hash(cache)} == receipt["installed"]:
            (snapshot / "target.jar").unlink(missing_ok=True)
            (snapshot / "cache.jar").unlink(missing_ok=True)
            snapshot.rmdir()
    print(f"CaDoodle er rullet tilbage med {transaction}.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--install-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    if not args.rollback and args.package is None:
        parser.error("Angiv --package eller --rollback.")
    if not args.dry_run and os.geteuid() != 0:
        parser.error("Kør installationen som root.")
    target = (args.install_root / "CaDoodle-Application.jar").resolve()
    cache = args.cache.resolve()
    state = args.state_dir.resolve()
    if not target.is_file():
        raise ValueError(f"Mangler installeret CaDoodle: {target}")
    entry_digest(target)
    with launcher_lock(args.lock.resolve()):
        assert_not_running()
        if args.rollback:
            rollback(target, cache, state, dry_run=args.dry_run)
        else:
            manifest, payload = load_package(args.package.resolve())
            if entry_digest(target) != manifest["compatibility"]["entry_sha256"]:
                raise ValueError("Installeret JAR passer ikke til CaDoodle 0.46.094.")
            if args.dry_run:
                print(f"Dry-run: {payload} -> {target} og {cache}.")
            else:
                install(manifest, payload, target, cache, state)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as error:
        print(f"CaDoodle-opdatering afbrudt: {error}", file=sys.stderr)
        raise SystemExit(1)
