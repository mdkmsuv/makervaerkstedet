"""Persistent SYSTEM-side bridge from verified GitHub assets to teacher queues.

Run from a protected controller directory with an independent Python runtime.
Browser requests select release/target IDs only. All transport configuration
and paths are read from the installation's protected local config file.
Kreditering: Klostermarksskolen.
"""
from __future__ import annotations
import argparse
import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import tempfile
import threading
import zipfile
import tarfile
from contextlib import contextmanager
if __package__ in (None, ""):
    # The scheduled task uses Python isolated mode: only this protected module
    # directory is explicitly added, never the process/user working directory.
    sys.path.insert(0, str(Path(__file__).absolute().parent))
try:
    from .common import digest, lease, load, plain, read, write, write_public_config, version_key
    from . import linux, server
except ImportError:
    from common import digest, lease, load, plain, read, write, write_public_config, version_key
    import linux, server

ROLES = ("linux_student", "windows_laser_teacher", "windows_server")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}")
SHA = re.compile(r"[a-f0-9]{64}")


def protected(path):
    """The SYSTEM input directory must not be writable/readable by ordinary users."""
    path = plain(path)
    if os.name != "nt":
        if path.stat().st_mode & 0o077:
            raise ValueError("controller_configuration_not_private")
        return
    encoded = base64.b64encode(str(path).encode()).decode()
    script = """
$ErrorActionPreference='Stop'
$p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('PATH'))
$a=Get-Acl -LiteralPath $p
foreach($r in $a.Access) {
 if($r.AccessControlType -eq 'Allow') {
  $sid=$r.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
  if($sid -notin @('S-1-5-18','S-1-5-32-544')) { exit 5 }
 }
}
$deleteChild=[Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles
$trusted=(New-Object Security.Principal.NTAccount('NT SERVICE','TrustedInstaller')).Translate([Security.Principal.SecurityIdentifier]).Value
for($parent=Split-Path $p -Parent; $parent; $parent=Split-Path $parent -Parent) {
 foreach($r in (Get-Acl -LiteralPath $parent).Access) {
  if($r.AccessControlType -eq 'Allow' -and -not ($r.PropagationFlags -band 2) -and ($r.FileSystemRights -band $deleteChild)) {
   $sid=$r.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
   if($sid -notin @('S-1-5-18','S-1-5-32-544',$trusted)) { exit 6 }
  }
 }
}
""".replace("PATH", encoded)
    command = base64.b64encode(script.encode("utf-16le")).decode()
    ps = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if subprocess.run([str(ps), "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
                      capture_output=True, timeout=20).returncode:
        raise ValueError("controller_configuration_not_private")


def shared_boundary(data_root):
    """Refuse a writable catalog/directory even when started outside the installer."""
    if os.name != "nt":
        return
    encoded = base64.b64encode(str(plain(data_root)).encode()).decode()
    script = """
$ErrorActionPreference='Stop'
$p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('PATH'))
$delete=[Security.AccessControl.FileSystemRights]::Delete -bor [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles
$denied=0
foreach($r in (Get-Acl -LiteralPath $p).Access) {
 $sid=$r.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
 if($sid -eq 'S-1-5-19' -and $r.AccessControlType -eq 'Deny' -and -not ($r.PropagationFlags -band 2)) { $denied=$denied -bor ($r.FileSystemRights -band $delete) }
}
if(($denied -band $delete) -ne $delete) { exit 5 }
$updates=Join-Path $p 'updates'
foreach($r in (Get-Acl -LiteralPath $updates).Access) {
 $sid=$r.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
 if($r.AccessControlType -eq 'Allow' -and -not ($r.PropagationFlags -band 2) -and $sid -notin @('S-1-5-18','S-1-5-32-544') -and ($r.FileSystemRights -band $delete)) { exit 6 }
}
$write=[Security.AccessControl.FileSystemRights]::Write -bor $delete -bor [Security.AccessControl.FileSystemRights]::ChangePermissions -bor [Security.AccessControl.FileSystemRights]::TakeOwnership
foreach($name in @('catalog.json','targets.json')) {
 $file=Join-Path $updates $name
 if(Test-Path -LiteralPath $file) {
  foreach($r in (Get-Acl -LiteralPath $file).Access) {
   $sid=$r.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
   if($r.AccessControlType -eq 'Allow' -and $sid -notin @('S-1-5-18','S-1-5-32-544') -and ($r.FileSystemRights -band $write)) { exit 7 }
  }
 }
}
""".replace("PATH", encoded)
    command = base64.b64encode(script.encode("utf-16le")).decode()
    ps = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if subprocess.run([str(ps), "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
                      capture_output=True, timeout=20).returncode:
        raise ValueError("shared_update_boundary_not_protected")


def validate_profile(row):
    if (not isinstance(row, dict) or row.get("role") not in ROLES
            or not ID.fullmatch(row.get("id", "")) or not ID.fullmatch(row.get("worker_target", ""))
            or not isinstance(row.get("name"), str) or not 1 <= len(row["name"]) <= 160):
        raise ValueError("invalid_local_target_profile")
    if "baseline_version" in row and version_key(row["baseline_version"]) is None:
        raise ValueError("invalid_verified_baseline")
    if row["role"] == "windows_server":
        if row.get("layout") != "school-flat-v1":
            raise ValueError("unsupported_server_layout")
        return row
    for key in ("ssh_key", "known_hosts"):
        if not Path(row.get(key, "")).is_absolute():
            raise ValueError("transport_profile_path_required")
        plain(row[key])
    if not re.fullmatch(r"[A-Za-z0-9.-]+", row.get("host_key_alias", "")):
        raise ValueError("invalid_host_key_alias")
    if row["role"] == "linux_student":
        if not linux.NAME.fullmatch(row["worker_target"]) or not SHA.fullmatch(row.get("identity_sha256", "")):
            raise ValueError("invalid_linux_profile_identity")
        for key in ("registry", "identity_file"):
            if not Path(row.get(key, "")).is_absolute():
                raise ValueError("linux_registry_required")
        if not isinstance(row.get("allowed_networks"), list) or not row["allowed_networks"]:
            raise ValueError("linux_network_scope_required")
        for network in row["allowed_networks"]:
            linux.ipaddress.ip_network(network, strict=True)
        if row.get("host_key_policy", "pinned") not in ("pinned", "enrollment_proof"):
            raise ValueError("invalid_host_key_policy")
    return row


class Controller:
    def __init__(self, config_path, *, check_permissions=True):
        self.config_path = plain(config_path)
        if check_permissions:
            protected(self.config_path)
            protected(self.config_path.parent)
        self.config = read(self.config_path)
        if self.config.get("schema") != 1 or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", self.config.get("repository", "")):
            raise ValueError("protected_repository_required")
        for key in ("data_root", "work_root", "store_root", "staging_root", "code_root"):
            if not Path(self.config.get(key, "")).is_absolute():
                raise ValueError("absolute_controller_path_required")
            setattr(self, key, plain(self.config[key]))
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.store_root.mkdir(parents=True, exist_ok=True)
        if check_permissions:
            protected(self.work_root)
            protected(self.store_root)
            protected(self.code_root)
            shared_boundary(self.data_root)
        self.profiles = [validate_profile(p) for p in self.config.get("profiles", [])]
        if check_permissions:
            for profile in self.profiles:
                if profile["role"] != "windows_server":
                    protected(profile["ssh_key"])
                    protected(Path(profile["ssh_key"]).parent)
                    known = plain(profile["known_hosts"])
                    protected(known.parent)
                    if known.exists():
                        protected(known)
        if len({p["id"] for p in self.profiles}) != len(self.profiles):
            raise ValueError("duplicate_target_profile")
        if len([p for p in self.profiles if p["role"] == "windows_server"]) > 1:
            raise ValueError("multiple_local_servers")
        module_root = self.code_root / "linux-elevhub/server/elevhub"
        sys.path.insert(0, str(module_root))
        self.importer = load(module_root / "release_import.py", "github_controller_import")
        self.feed_module = load(module_root / "release_feed.py", "github_controller_feed")
        self.admin = load(module_root / "update_admin.py", "github_controller_admin").UpdateAdministration(self.data_root)
        self.feed = self.feed_module.ReleaseFeed(self.data_root)
        self.device_updates = load(module_root / "device_updates.py", "github_device_updates").DeviceUpdates(self.admin, self.feed)

    def provision_import(self, receipt):
        """Materialize a verified immutable store receipt and publish queue IDs."""
        role, version = receipt["role"], receipt["version"]
        if role not in ROLES or not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}(?:\.\d+)?", version):
            raise ValueError("invalid_import_role_version")
        identifier = "github-" + role + "-" + version
        root = plain(self.work_root / identifier)
        profiles = [p for p in self.profiles if p["role"] == role]
        # Imported packages remain available without students; provisioning an
        # empty role never fabricates targets or enrollment.
        if not profiles:
            return {"release_id": identifier, "ready": False, "code": "local_profile_missing"}
        if (root / "config.json").exists():
            config = read(root / "config.json")
            if config.get("package_sha256") != receipt["package_sha256"]:
                raise ValueError("immutable_release_collision")
            if config.get("profiles") != profiles:
                raise ValueError("local_profile_changed")
        else:
            root.mkdir(parents=True, exist_ok=True)
            package = root / "package"
            if not package.exists():
                self.importer.materialize_verified_package(receipt, package)
            config = {"schema": 1, "release_id": identifier, "role": role, "version": version,
                      "package_sha256": receipt["package_sha256"], "profiles": profiles}
            if role == "linux_student":
                config = linux.prepare(root, package, config, self.code_root)
            elif role == "windows_server":
                candidate = package / "candidate"
                updater = load(self.code_root / "scripts/school_server_update/update.py", "github_candidate_verify")
                manifest = updater.verify(candidate)
                if manifest["version"] != version:
                    raise ValueError("server_package_version_mismatch")
                config.update(candidate=str(candidate), candidate_sha256=digest(candidate / "candidate.json"))
            else:
                archive = package / "station-code.zip"
                # Legacy manifest packages are accepted as the station archive.
                if not archive.exists() and receipt.get("marker") == "manifest.json":
                    archive = plain(receipt["archive"])
                runner = self.code_root / "scripts/queued_laser_update/remote_runner.py"
                laser_validator = load(runner, "github_station_package_verify")
                with tempfile.TemporaryDirectory(prefix="station-verify-", dir=root) as temporary:
                    manifest = laser_validator.unpack(archive, digest(archive), Path(temporary))
                if manifest["release"] != version:
                    raise ValueError("laser_package_version_mismatch")
                shutil.copy2(archive, root / "station-code.zip")
                shutil.copy2(runner, root / "remote_runner.py")
                config.update(archive_sha256=digest(root / "station-code.zip"),
                              runner_sha256=digest(root / "remote_runner.py"))
            write(root / "config.json", config)
        entry = {"id": identifier, "role": role, "version": version, "reviewed": True,
                 "worker_root": str(root), "manifest_sha256": digest(root / "config.json"),
                 "package_sha256": receipt["package_sha256"]}
        catalog_path = self.data_root / "updates/catalog.json"
        targets_path = self.data_root / "updates/targets.json"
        catalog = read(catalog_path) if catalog_path.exists() else {"schema": 1, "releases": []}
        targets = read(targets_path) if targets_path.exists() else {"schema": 1, "targets": []}
        old = next((r for r in catalog["releases"] if r["id"] == identifier), None)
        if old is not None and old != entry:
            raise ValueError("catalog_release_collision")
        if old is None:
            catalog["releases"].append(entry)
        for profile in profiles:
            target = next((r for r in targets["targets"] if r["id"] == profile["id"]), None)
            fields = {k: profile[k] for k in ("id", "name", "role", "worker_target")}
            if role == "windows_server":
                fields.update(transport="local_server", controller_config=str(self.config_path),
                              controller_sha256=digest(self.config_path))
            else:
                for key in ("ssh_key", "known_hosts", "host_key_alias", "host_key_policy",
                            "identity_file", "identity_sha256", "identity_name"):
                    if key in profile:
                        fields[key] = profile[key]
            if target is None:
                target = {**fields, "release_ids": []}
                targets["targets"].append(target)
            elif any(target.get(k) != v for k, v in fields.items()):
                raise ValueError("existing_target_profile_changed")
            if identifier not in target["release_ids"]:
                target["release_ids"].append(identifier)
        # Append the release first: all intermediate target references remain
        # valid. A crash is reconciled idempotently before the feed says imported.
        write_public_config(catalog_path, catalog)
        write_public_config(targets_path, targets)
        self.admin.heartbeat(identifier)
        return {"release_id": identifier, "ready": True}

    def import_downloads(self):
        ready = self.feed.ready_packages(self.staging_root)
        if not ready:
            return
        # Re-fetch authoritative metadata from the protected repository, not the
        # LocalService database or a browser-supplied hash/path.
        fresh = self.feed.discover(self.config["repository"])
        for package in ready:
            trusted = next((p for p in fresh["packages"] if p["asset"] == package["asset"]), None)
            if trusted is None or package["release_id"] != fresh["release_id"]:
                self.feed.mark_outcome(package["download_id"], "blocked", "release_no_longer_current")
                continue
            descriptor = {**trusted, "version": fresh["version"], "release_id": fresh["release_id"]}
            try:
                receipt = self.importer.import_ready_package(package, self.store_root,
                    trusted_repository=self.config["repository"], trusted_package=descriptor)
                result = self.provision_import(receipt)
                if result["ready"]:
                    self.feed.mark_outcome(package["download_id"], "imported")
                else:
                    self.feed.mark_outcome(package["download_id"], "blocked", "local_profile_missing")
            except (ValueError, KeyError, TypeError, SyntaxError, zipfile.BadZipFile, tarfile.TarError):
                self.feed.mark_outcome(package["download_id"], "blocked", "package_import_rejected")

    def laser_tick(self, root, config, profile, invoke=subprocess.run):
        worker = load(self.code_root / "scripts/queued_laser_update/queue_update.py", "github_laser_worker")
        name = profile["worker_target"]
        if not self.admin.worker_allowed(config["release_id"], name):
            return
        local = {**profile, "schema": 1, "role": config["role"], "release": config["version"],
                 "target": name, "release_id": config["release_id"], "archive_sha256": config["archive_sha256"],
                 "runner_sha256": config["runner_sha256"], "require_known_version": True}
        try:
            worker.validate(root, local)
            job = self.admin.worker_job(config["release_id"], name)
            self.admin.report(config["release_id"], name, "checking")
            response = worker.deliver(root, local, job["id"], invoke)
            if response["status"] == "complete":
                if response.get("release") != config["version"] or response.get("operation") != job["id"]:
                    raise ValueError("laser_completion_binding_failed")
                self.admin.report(config["release_id"], name, "complete", role_verified=True,
                                  installed_version=config["version"], package_sha256=config["package_sha256"])
            else:
                self.admin.report(config["release_id"], name, response["status"], code=response.get("code"))
        except (OSError, ValueError, KeyError):
            self.admin.report(config["release_id"], name, "blocked", code="station_update_failed")

    @contextmanager
    def readiness(self, release):
        stop = threading.Event()
        def pulse():
            while not stop.wait(30):
                try:
                    self.feed.importer_heartbeat()
                    self.admin.heartbeat(release)
                except Exception:
                    pass
        worker = threading.Thread(target=pulse, name="update-readiness", daemon=True)
        worker.start()
        try:
            yield
        finally:
            stop.set()
            worker.join(timeout=1)

    def tick(self):
        self.feed.importer_heartbeat()
        try:
            self.import_downloads()
        except Exception:
            write(self.work_root / "import-status.json", {"status": "retry", "code": "import_unavailable", "at": time.time()})
        # Explicit local-admin orders survive closing the UI and wait for import.
        # This only advances verified server-side requests; transport remains below.
        try:
            self.device_updates.process_pending()
        except Exception:
            write(self.work_root / "device-request-status.json", {"status": "retry", "code": "local_request_unavailable", "at": time.time()})
        # No discovery failure may suspend already authorized offline queues.
        for root in sorted(self.work_root.glob("github-*")):
            path = root / "config.json"
            if not path.is_file():
                continue
            try:
                config = read(path)
                release = config["release_id"]
                self.admin.heartbeat(release)
                for profile in config["profiles"]:
                    try:
                        if config["role"] == "linux_student":
                            with self.readiness(release):
                                linux.tick(root, config, profile, self.admin)
                        elif config["role"] == "windows_laser_teacher":
                            with self.readiness(release):
                                self.laser_tick(root, config, profile)
                        elif config["role"] == "windows_server":
                            server.tick(root, config, profile, self.admin, self.code_root)
                    except Exception:
                        write(root / "controller-status.json", {"status": "blocked", "code": "local_profile_invalid", "at": time.time()})
            except Exception:
                write(root / "controller-status.json", {"status": "blocked", "code": "worker_configuration_invalid", "at": time.time()})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    controller = Controller(args.config)
    with lease(controller.work_root):
        while True:
            controller.tick()
            if args.once:
                return
            time.sleep(30)


if __name__ == "__main__":
    main()
