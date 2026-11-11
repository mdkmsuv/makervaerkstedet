"""Privileged, non-executing import from the LocalService release feed.

The output is an immutable package store receipt. Runtime provisioners create
worker roots from local transport profiles; this module never writes a worker
configuration and never executes package contents. Kreditering: Klostermarksskolen.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib, json, os, re, stat, tempfile, zipfile
from pathlib import Path, PurePosixPath

try:
    from .release_feed import MAX_MANIFEST, MAX_PACKAGE, ROLES, plain, strict_json, valid_asset
except ImportError:
    from release_feed import MAX_MANIFEST, MAX_PACKAGE, ROLES, plain, strict_json, valid_asset

MAX_ENTRIES = 100_000; MAX_UNPACKED = 8 * 1024 ** 3; MAX_MEMBER = 512 * 1024 ** 2
MAX_INVENTORY = 16 * 1024 ** 2
VERSION = re.compile(r"\d{4}\.\d{2}\.\d{2}(?:\.\d{1,8})?")
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
MARKER = "update-package.json"
PRIVATE_PARTS = {"data", "queues", "backups", "logs", "profiles"}
PRIVATE_NAMES = {"known_hosts", "deployment_key", "enheder.json", "status.json", "installation-owner.json"}
FORBIDDEN = set('\\:*?"<>|')

class ImportInvalid(ValueError): pass
class ImportConflict(ValueError): pass

def _member(name):
    if (not isinstance(name, str) or not name or str(PurePosixPath(name)) != name or name.startswith("/")
            or any(c in FORBIDDEN or ord(c) < 32 for c in name)):
        raise ImportInvalid("unsafe_package_member")
    parts = name.split("/")
    if any(not p or p in {".", ".."} or p.endswith((" ", ".")) for p in parts): raise ImportInvalid("unsafe_package_member")
    if any(re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", p) for p in parts): raise ImportInvalid("unsafe_package_member")
    return name

def _hash(path):
    with path.open("rb") as stream: return hashlib.file_digest(stream, "sha256").hexdigest()

def _release_key(role, version):
    value = f"{role}@{version}"
    if not re.fullmatch(r"[a-z_]+@\d{4}\.\d{2}\.\d{2}(?:\.\d{1,8})?", value): raise ImportInvalid("invalid_release_key")
    return value

def _validate_ready(package):
    required = {"role", "format", "version", "asset", "sha256", "size", "path", "repository", "release_id", "download_id"}
    if not isinstance(package, dict) or not required.issubset(package): raise ImportInvalid("invalid_ready_package")
    role = package.get("role")
    if not isinstance(role, str) or role not in ROLES or package.get("format") != ROLES[role]: raise ImportInvalid("invalid_ready_package")
    version = package.get("version")
    if not isinstance(version, str) or not VERSION.fullmatch(version): raise ImportInvalid("invalid_ready_package")
    if not valid_asset(package.get("asset")) or not isinstance(package.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", package["sha256"]): raise ImportInvalid("invalid_ready_package")
    if type(package.get("size")) is not int or not 0 < package["size"] < MAX_PACKAGE: raise ImportInvalid("invalid_ready_package")
    if not isinstance(package.get("download_id"), str) or not re.fullmatch(r"[a-f0-9]{32}", package["download_id"]): raise ImportInvalid("invalid_ready_package")
    if type(package.get("release_id")) is not int or package["release_id"] <= 0 or not isinstance(package.get("repository"), str) or not REPOSITORY.fullmatch(package["repository"]): raise ImportInvalid("invalid_ready_package")
    try: path = plain(Path(package["path"]))
    except (TypeError, ValueError, OSError): raise ImportInvalid("invalid_ready_package")
    if not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size != package["size"] or _hash(path) != package["sha256"]: raise ImportInvalid("staged_package_not_verified")
    return path, role, version

def _inventory(archive, role, format_, version):
    entries = archive.infolist(); all_names = [_member(e.filename) for e in entries]; file_entries = [e for e in entries if not e.is_dir()]; names = {e.filename for e in file_entries}
    if (not file_entries or len(entries) > MAX_ENTRIES or len(set(all_names)) != len(entries) or len({n.casefold() for n in names}) != len(names) or sum(max(0, e.file_size) for e in entries) > MAX_UNPACKED or any(e.flag_bits & 1 or (stat.S_IFMT((e.external_attr >> 16) & 0xffff) not in (0, stat.S_IFREG)) for e in file_entries)): raise ImportInvalid("unsafe_package_archive")
    marker_name = MARKER if MARKER in names else "manifest.json" if format_ == "laser-code-v1" else None
    if marker_name is None or archive.getinfo(marker_name).file_size > MAX_INVENTORY: raise ImportInvalid("package_marker_missing")
    marker = strict_json(archive.read(marker_name))
    declared_sizes = {}
    if marker_name == MARKER:
        if set(marker) != {"schema", "role", "format", "version", "files"} or type(marker.get("schema")) is not int or marker["schema"] != 1 or marker.get("role") != role or marker.get("format") != format_ or marker.get("version") != version or not isinstance(marker.get("files"), dict): raise ImportInvalid("package_role_version_mismatch")
        files = marker["files"]
    else:
        if set(marker) != {"format", "release", "files"} or marker.get("format") != "makervaerkstedet-station-code-v1" or marker.get("release") != version or role != "windows_laser_teacher" or not isinstance(marker.get("files"), dict): raise ImportInvalid("legacy_laser_marker_mismatch")
        files = {}; declared_sizes = {}
        for name, info in marker["files"].items():
            if not isinstance(info, dict) or set(info) != {"sha256", "size"} or type(info["size"]) is not int: raise ImportInvalid("invalid_package_manifest")
            files[name] = info["sha256"]
            declared_sizes[name] = info["size"]
    if set(names) != set(files) | {marker_name} or "CREDITS.md" not in files: raise ImportInvalid("package_inventory_mismatch")
    inventory = {}
    for name, digest in files.items():
        _member(name)
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest): raise ImportInvalid("invalid_package_manifest")
        info = archive.getinfo(name)
        member_limit = MAX_PACKAGE if Path(name).name == "payload.tar.gz" else MAX_MEMBER
        if info.file_size > member_limit or (marker_name == "manifest.json" and declared_sizes.get(name) != info.file_size):
            raise ImportInvalid("package_file_hash_mismatch")
        with archive.open(name) as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise ImportInvalid("package_file_hash_mismatch")
        parts = {p.casefold() for p in PurePosixPath(name).parts}
        if parts & PRIVATE_PARTS or Path(name).name.casefold() in PRIVATE_NAMES or Path(name).suffix.casefold() in {".pem", ".key", ".pfx", ".p12", ".sqlite", ".sqlite3", ".db", ".crt"}: raise ImportInvalid("runtime_or_identity_data_in_package")
        inventory[name] = {"sha256": digest, "size": info.file_size}
    marker_info = archive.getinfo(marker_name)
    inventory[marker_name] = {"sha256": hashlib.sha256(archive.read(marker_name)).hexdigest(), "size": marker_info.file_size}
    if archive.getinfo("CREDITS.md").file_size > MAX_MANIFEST or b"Klostermarksskolen" not in archive.read("CREDITS.md"):
        raise ImportInvalid("package_credit_missing")
    return marker_name, inventory

@contextmanager
def _lock(root):
    path = plain(root / "import.lock")
    with path.open("a+b") as stream:
        try:
            if os.name == "nt":
                import msvcrt; stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl; fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError) as error: raise ImportConflict("import_busy") from error
        try: yield
        finally:
            try:
                if os.name == "nt": stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except (OSError, IOError): pass

def _atomic_copy(source, target):
    temp = plain(target.with_name(target.name + "." + next(tempfile._get_candidate_names()) + ".part"))
    try:
        with source.open("rb") as src, temp.open("xb") as dst:
            while block := src.read(64 * 1024): dst.write(block)
            dst.flush(); os.fsync(dst.fileno())
        os.replace(temp, target)
    finally: temp.unlink(missing_ok=True)

def _read_receipt(path):
    try: value = strict_json(path.read_bytes())
    except (OSError, ValueError): raise ImportInvalid("invalid_import_receipt")
    if type(value.get("schema")) is not int or value.get("schema") != 1 or not isinstance(value.get("inventory"), dict): raise ImportInvalid("invalid_import_receipt")
    return value

def import_ready_package(package, store_root, *, trusted_repository=None, trusted_package=None):
    """Store one feed package and return an immutable receipt; never extract or execute it."""
    path, role, version = _validate_ready(package)
    if not isinstance(trusted_repository, str) or not REPOSITORY.fullmatch(trusted_repository) or not isinstance(trusted_package, dict): raise ImportInvalid("trusted_authority_required")
    fields = ("role", "format", "version", "asset", "sha256", "size", "release_id")
    if package.get("repository") != trusted_repository or any(package.get(field) != trusted_package.get(field) for field in fields): raise ImportInvalid("authoritative_release_mismatch")
    root = plain(Path(store_root)); root.mkdir(parents=True, exist_ok=True)
    if os.name != "nt": root.chmod(0o700)
    with _lock(root):
        key = _release_key(role, version); packages = plain(root / "packages" / key); packages.mkdir(parents=True, exist_ok=True)
        archive_target = plain(packages / package["asset"]); receipt_path = plain(packages / "import-receipt.json")
        if archive_target.exists() and not receipt_path.exists(): archive_target.unlink()
        if receipt_path.exists():
            receipt = _read_receipt(receipt_path)
            expected = {"repository": trusted_repository, "release_id": package["release_id"], "role": role, "format": package["format"], "version": version, "asset": package["asset"], "package_sha256": package["sha256"], "package_size": package["size"], "archive": str(archive_target)}
            if any(receipt.get(key) != value for key, value in expected.items()): raise ImportConflict("package_already_imported_differently")
            if not archive_target.is_file() or archive_target.stat().st_size != package["size"] or _hash(archive_target) != package["sha256"]: raise ImportConflict("imported_package_changed")
            with zipfile.ZipFile(archive_target) as archive: marker, inventory = _inventory(archive, role, package["format"], version)
            if inventory != receipt.get("inventory"): raise ImportConflict("imported_inventory_changed")
            return {**receipt, "idempotent": True}
        with zipfile.ZipFile(path) as archive: marker, inventory = _inventory(archive, role, package["format"], version)
        if _hash(path) != package["sha256"]: raise ImportInvalid("staged_package_changed")
        _atomic_copy(path, archive_target)
        if _hash(archive_target) != package["sha256"]: raise ImportInvalid("stored_package_changed")
        receipt = {"schema": 1, "repository": trusted_repository, "release_id": package["release_id"], "download_id": package["download_id"], "role": role, "format": package["format"], "version": version, "asset": package["asset"], "package_sha256": package["sha256"], "package_size": package["size"], "marker": marker, "inventory": inventory, "archive": str(archive_target)}
        temp = plain(receipt_path.with_name(receipt_path.name + "." + next(tempfile._get_candidate_names()) + ".tmp"))
        try:
            with temp.open("x", encoding="utf-8") as stream:
                json.dump(receipt, stream, ensure_ascii=False, indent=2, sort_keys=True); stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
            os.replace(temp, receipt_path)
        except Exception:
            temp.unlink(missing_ok=True); archive_target.unlink(missing_ok=True); raise
        return {**receipt, "idempotent": False}

def materialize_verified_package(receipt, destination):
    """Safely unpack a store receipt for a runtime provisioner.

    The destination is a staging package root only. No worker configuration is
    created and no member is executed; every extracted byte is checked against
    the receipt inventory before the directory is published by the caller.
    """
    if not isinstance(receipt, dict) or type(receipt.get("schema")) is not int or receipt.get("schema") != 1:
        raise ImportInvalid("invalid_import_receipt")
    archive = plain(Path(receipt.get("archive", "")))
    if not archive.is_file() or archive.stat().st_size != receipt.get("package_size") or _hash(archive) != receipt.get("package_sha256"):
        raise ImportInvalid("stored_package_changed")
    root = plain(Path(destination)); root.parent.mkdir(parents=True, exist_ok=True)
    if root.exists(): raise ImportConflict("materialize_exists")
    temporary = plain(root.with_name(root.name + ".staging"))
    if temporary.exists():
        raise ImportConflict("materialize_busy")
    temporary.mkdir()
    try:
        with zipfile.ZipFile(archive) as source:
            marker, inventory = _inventory(source, receipt["role"], receipt["format"], receipt["version"])
            if marker != receipt.get("marker") or inventory != receipt.get("inventory"):
                raise ImportInvalid("import_receipt_inventory_mismatch")
            for name, info in inventory.items():
                target = plain(temporary / name); target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(name) as input_stream, target.open("xb") as output:
                    while block := input_stream.read(64 * 1024): output.write(block)
                if target.stat().st_size != info["size"] or _hash(target) != info["sha256"]:
                    raise ImportInvalid("materialized_file_changed")
        os.replace(temporary, root)
    except Exception:
        import shutil
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {"package_root": str(root), "release_id": receipt["release_id"], "role": receipt["role"], "version": receipt["version"], "package_sha256": receipt["package_sha256"]}
