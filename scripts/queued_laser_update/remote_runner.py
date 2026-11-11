"""Guarded legacy Windows station code update; never starts a machine or app."""
from __future__ import annotations
import argparse
from contextlib import ExitStack, contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import stat
import subprocess
import sys
import tempfile
import uuid
import zipfile

FORMAT = "makervaerkstedet-station-code-v1"
HEX = re.compile(r"[0-9a-f]{64}\Z")


def plain(path):
    path = Path(os.path.abspath(path))
    for parent in (path, *path.parents):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("link_or_reparse")
    return path


def digest(path):
    with plain(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def strict_json(value):
    def pairs(items):
        result = {}
        for key, content in items:
            if key in result: raise ValueError("duplicate_json_key")
            result[key] = content
        return result
    result = json.loads(value, object_pairs_hook=pairs)
    if not isinstance(result, dict): raise ValueError("invalid_json_object")
    return result


def read_json(path):
    path = plain(path)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("metadata_too_large")
    return strict_json(path.read_text(encoding="utf-8-sig"))


def atomic(path, data):
    path = plain(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path, value):
    atomic(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def relative(name):
    return (isinstance(name, str) and bool(name)
            and str(PurePosixPath(name)) == name and not name.startswith("/")
            and not any(c in name for c in '\\:*?"<>|')
            and all(part not in ("", ".", "..") and not part.endswith((" ", "."))
                    and not re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
                    for part in name.split("/"))
            and all(ord(c) >= 32 for c in name))


def allowed(name):
    return relative(name) and (name.startswith(("app/", "web/")) or name in {
        "CREDITS.md", "LICENSE", "LICENSE-SCOPE.md"})


def unpack(archive, expected, work):
    if not HEX.fullmatch(expected) or digest(archive) != expected:
        raise ValueError("archive_hash_mismatch")
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        names = [entry.filename for entry in entries]
        if (len(names) != len(set(n.casefold() for n in names)) or len(names) > 3000
                or sum(entry.file_size for entry in entries) > 300 * 1024**2
                or any(not relative(n) for n in names)
                or any(stat.S_ISLNK(entry.external_attr >> 16) for entry in entries)):
            raise ValueError("unsafe_archive")
        if "manifest.json" not in names or source.getinfo("manifest.json").file_size > 1024**2:
            raise ValueError("missing_manifest")
        manifest = strict_json(source.read("manifest.json"))
        if (set(manifest) != {"format", "release", "files"} or manifest["format"] != FORMAT
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", manifest["release"])
                or not isinstance(manifest["files"], dict)
                or set(names) != set(manifest["files"]) | {"manifest.json"}
                or not {"app/laser_station.py", "app/instance.py", "app/backup_guard.py",
                        "web/laserstation.html", "CREDITS.md"}.issubset(manifest["files"])):
            raise ValueError("invalid_manifest")
        for name, info in manifest["files"].items():
            if (not allowed(name) or set(info) != {"sha256", "size"}
                    or not HEX.fullmatch(info["sha256"]) or type(info["size"]) is not int
                    or not 0 <= info["size"] <= 32 * 1024**2):
                raise ValueError("invalid_manifest_entry")
            content = source.read(name)
            if len(content) != info["size"] or hashlib.sha256(content).hexdigest() != info["sha256"]:
                raise ValueError("payload_hash_mismatch")
            if name.endswith(".py"):
                compile(content, name, "exec")
            atomic(Path(work) / name, content)
    return manifest


def candidate_probe(runtime, candidate):
    # Embedded Python ignores cwd and PYTHONPATH; insert the candidate explicitly.
    code = ("import sys;sys.path.insert(0,sys.argv[1]);"
            "import app.laser_station,app.laser_station_install;"
            "from pathlib import Path;"
            "assert Path(app.laser_station.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())")
    result = subprocess.run([str(runtime), "-B", "-c", code, str(candidate)],
                            capture_output=True, timeout=45)
    if result.returncode:
        raise ValueError("candidate_runtime_incompatible")


@contextmanager
def idle_lease(install, config, candidate):
    # Hold the station's plate lock and its port across writing and recovery.
    plate_path, port = config.get("plates"), config.get("port", 8790)
    if (not isinstance(plate_path, str) or not Path(plate_path).is_absolute()
            or type(port) is not int or not 1024 <= port <= 65535):
        raise ValueError("station_configuration_incomplete")
    plates = plain(plate_path)
    if not plates.is_dir():
        raise ValueError("station_plates_missing")
    spec = importlib.util.spec_from_file_location("update_instance", candidate / "app/instance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with ExitStack() as stack:
        try:
            stack.enter_context(module.InstanceLock(install, filename="code-update.lock"))
            stack.enter_context(module.InstanceLock(plates))
            listener = stack.enter_context(socket.socket())
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", port))
        except (RuntimeError, OSError):
            raise BlockingIOError("station_busy") from None
        script = ("$ErrorActionPreference='Stop';Get-CimInstance Win32_Process | "
                  "Select-Object ProcessId,Name,ExecutablePath | ConvertTo-Json -Compress")
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, timeout=20, check=True)
        processes = json.loads(result.stdout)
        if isinstance(processes, dict):
            processes = [processes]
        for process in processes:
            name = (process.get("Name") or "").casefold()
            executable = (process.get("ExecutablePath") or "").casefold()
            if any(part in name for part in ("beam", "flux", "ghost")):
                raise BlockingIOError("laser_software_busy")
            if (process.get("ProcessId") != os.getpid() and executable
                    and executable.startswith(str(install).casefold() + os.sep)):
                raise BlockingIOError("station_process_busy")
        yield


def restore_interrupted(install, backup, journal):
    """Restore known before/after bytes, refusing to erase unrelated edits."""
    for name, record in journal["records"].items():
        if not allowed(name) and name not in ("integrity.json", "queued-release.json"):
            raise ValueError("invalid_recovery_path")
        target = plain(install / name)
        actual = digest(target) if target.is_file() else None
        if actual not in (record["before"], record["after"]):
            raise ValueError("recovery_requires_review")
        if record["before"] is not None and digest(backup / "before" / name) != record["before"]:
            raise ValueError("backup_hash_mismatch")
    for name, record in journal["records"].items():
        target = plain(install / name)
        if record["before"] is None:
            target.unlink(missing_ok=True)
        else:
            atomic(target, (backup / "before" / name).read_bytes())
    journal["state"] = "rolled_back"
    write_json(backup / "journal.json", journal)


def apply(install, candidate, manifest, operation, archive_hash, require_known_version=False, baseline_version=None):
    if not re.fullmatch(r"[0-9a-f]{32}", operation):
        raise ValueError("invalid_operation")
    install, candidate = plain(install), plain(candidate)
    backup = plain(install / "queued-update-backups" / operation)
    journal_path = backup / "journal.json"
    if journal_path.exists():
        journal = read_json(journal_path)
        if (journal.get("operation") != operation or journal.get("archive_sha256") != archive_hash
                or journal.get("install") != str(install)):
            raise ValueError("operation_mismatch")
        if journal["state"] == "complete":
            if not all(digest(install / name) == record["after"]
                       for name, record in journal["records"].items()):
                raise ValueError("completed_installation_changed")
            return {"status": "complete", "release": manifest["release"], "operation": operation}
        if journal["state"] in ("prepared", "applying"):
            restore_interrupted(install, backup, journal)
            return {"status": "failed_rolled_back", "release": manifest["release"], "operation": operation}
        if journal["state"] != "rolled_back":
            raise ValueError("unknown_journal_state")
        # A new invocation after a reported rollback is the teacher's explicit
        # retry. Preserve the prior evidence and build a fresh journal.
        restore_interrupted(install, backup, journal)
        previous = plain(backup.with_name(operation + "-attempt-" + uuid.uuid4().hex))
        backup.rename(previous)
    if backup.exists():
        raise ValueError("incomplete_backup_requires_review")
    if require_known_version:
        def version(value):
            if not isinstance(value, str) or not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}(?:\.\d{1,8})?", value):
                return None
            parts = tuple(map(int, value.split(".")))
            return parts + (0,) * (4 - len(parts))
        marker = install / "queued-release.json"
        previous = read_json(marker) if marker.exists() else {}
        current = version(previous.get("release")) or version(baseline_version)
        desired = version(manifest["release"])
        if current is None or desired is None:
            raise ValueError("installed_version_unknown")
        if desired <= current:
            # Equal versions require the identical archive and actual installed
            # file evidence. No implicit reinstall under an existing version.
            if (desired == current and previous.get("archive_sha256") == archive_hash
                    and all(digest(install / name) == info["sha256"] for name, info in manifest["files"].items())):
                return {"status": "complete", "release": manifest["release"], "operation": operation,
                        "configuration_preserved": True}
            raise ValueError("installed_version_not_older")
    config = plain(install / "laser-installation.json")
    config_hash = digest(config)
    new = {}
    for name, info in manifest["files"].items():
        content = plain(candidate / name).read_bytes()
        if len(content) != info["size"] or hashlib.sha256(content).hexdigest() != info["sha256"]:
            raise ValueError("candidate_changed_after_unpack")
        new[name] = content
    integrity_path = plain(install / "integrity.json")
    integrity = read_json(integrity_path) if integrity_path.is_file() else {}
    table = integrity.get("files", integrity)
    if not isinstance(table, dict):
        raise ValueError("invalid_installed_integrity")
    table.update({name: info["sha256"] for name, info in manifest["files"].items()})
    new["integrity.json"] = (json.dumps(integrity, indent=2) + "\n").encode()
    new["queued-release.json"] = (json.dumps({
        "release": manifest["release"], "operation": operation,
        "scope": "station code; existing runtime and configuration preserved",
        "archive_sha256": archive_hash,
    }, indent=2) + "\n").encode()
    records = {}
    backup.mkdir(parents=True, exist_ok=False)
    for name, content in new.items():
        target = plain(install / name)
        old = target.read_bytes() if target.is_file() else None
        records[name] = {"before": hashlib.sha256(old).hexdigest() if old is not None else None,
                         "after": hashlib.sha256(content).hexdigest()}
        if old is not None:
            atomic(backup / "before" / name, old)
    journal = {"format": FORMAT, "operation": operation, "install": str(install),
               "archive_sha256": archive_hash, "state": "prepared", "records": records,
               "configuration_sha256": config_hash}
    write_json(journal_path, journal)
    try:
        journal["state"] = "applying"
        write_json(journal_path, journal)
        for name, content in new.items():
            target = plain(install / name)
            actual = digest(target) if target.is_file() else None
            if actual != records[name]["before"]:
                raise ValueError("installation_changed_during_update")
            atomic(target, content)
        if digest(config) != config_hash:
            raise ValueError("configuration_changed_during_update")
        candidate_probe(install / "runtime/python.exe", install)
        if not all(digest(install / name) == record["after"] for name, record in records.items()):
            raise ValueError("post_update_hash_mismatch")
        journal["state"] = "complete"
        write_json(journal_path, journal)
    except Exception:
        restore_interrupted(install, backup, journal)
        raise
    return {"status": "complete", "release": manifest["release"], "operation": operation,
            "files": len(manifest["files"]), "configuration_preserved": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--install-path", type=Path, required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--require-known-version", action="store_true")
    parser.add_argument("--baseline-version")
    args = parser.parse_args()
    install = plain(args.install_path)
    config = read_json(install / "laser-installation.json")
    if (config.get("role") != "Station" or not all(plain(install/name).is_file() for name in (
            "app/laser_station.py", "app/instance.py", "app/backup_guard.py",
            "web/laserstation.html", "runtime/python.exe"))):
        raise ValueError("not_an_existing_laser_station")
    with tempfile.TemporaryDirectory(prefix="maker-station-update-") as temporary:
        candidate = Path(temporary)
        manifest = unpack(args.archive, args.sha256, candidate)
        candidate_probe(install / "runtime/python.exe", candidate)
        with idle_lease(install, config, candidate):
            print(json.dumps(apply(install, candidate, manifest, args.operation_id, args.sha256,
                                   args.require_known_version, args.baseline_version)))


if __name__ == "__main__":
    try:
        main()
    except BlockingIOError:
        print(json.dumps({"status": "waiting_for_idle"}))
        raise SystemExit(75)
    except Exception as error:
        code = {"installed_version_unknown": "version_unknown",
                "installed_version_not_older": "version_not_older"}.get(str(error), "station_update_failed")
        print(json.dumps({"status": "blocked", "code": code}))
        raise SystemExit(1)
