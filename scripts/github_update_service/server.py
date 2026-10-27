"""Durable local-server bridge. Kreditering: Klostermarksskolen."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
try:
    from .common import load, plain, read, write, require_upgrade
except ImportError:
    from common import load, plain, read, write, require_upgrade


def inspect_result(root, config, updater):
    journal = root / "server-journal"
    path = journal / "journal.json"
    if not path.exists():
        return "recovery_required"
    record = read(path)
    if (record.get("candidate_sha256") != config["candidate_sha256"]
            or record.get("version") != config["version"]):
        return "recovery_required"
    if record.get("state") == "rolled_back" and record.get("rollback_verified") is True:
        return "failed_rolled_back"
    if record.get("state") == "complete":
        updater.installed(Path(config["candidate"]), config["candidate_sha256"])
        return "complete"
    # Do not overwrite databases that may have received new submissions.
    return "recovery_required"


def tick(root, config, profile, admin, code_root, invoke=subprocess.run):
    release, name = config["release_id"], profile["worker_target"]
    if profile.get("layout") != "school-flat-v1":
        raise ValueError("unsupported_server_layout")
    if not admin.worker_allowed(release, name):
        return
    updater = load(code_root / "scripts/school_server_update/update.py", "github_school_updater")
    job = admin.worker_job(release, name)
    transaction = plain(root / ("operation-" + job["id"]))
    transaction.mkdir(exist_ok=True)
    state = transaction / "controller.json"
    try:
        result_path = transaction / "result.json"
        if result_path.exists() and read(result_path).get("status") == "failed_rolled_back":
            # Reaching this branch requires a new queue claim after the teacher
            # explicitly retried a blocked job. Preserve the former attempt.
            transaction.rename(transaction.with_name(transaction.name + "-attempt-" + uuid.uuid4().hex))
            transaction.mkdir()
        manifest = updater.verify(Path(config["candidate"]), config["candidate_sha256"])
        if manifest["version"] != config["version"]:
            raise ValueError("server_version_mismatch")
        if state.exists():
            # Reconcile a crash after native completion and before reporting.
            status = ("recovery_required" if result_path.exists() and read(result_path).get("status") == "recovery_required"
                      else inspect_result(transaction, config, updater))
        else:
            for installed_root in updater.INSTALLS.values():
                if Path(sys.executable).absolute().is_relative_to(installed_root.absolute()):
                    raise ValueError("controller_runtime_inside_updated_installation")
                marker = installed_root / "school-update.json"
                observed = read(marker).get("version") if marker.exists() else None
                try:
                    require_upgrade(observed, config["version"], profile.get("baseline_version"))
                except ValueError as error:
                    code = "version_unknown" if str(error) == "installed_version_unknown" else "version_not_older"
                    admin.report(release, name, "blocked", code=code)
                    return
            try:
                updater.assert_idle()
            except ValueError:
                admin.report(release, name, "waiting_for_idle")
                return
            admin.report(release, name, "applying")
            write(state, {"job": job["id"], "version": config["version"],
                          "package_sha256": config["package_sha256"], "started_at": time.time()})
            runner = code_root / "scripts/school_server_update/Invoke-SchoolUpdate.ps1"
            powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            with (transaction / "native.log").open("ab") as log:
                result = invoke([str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-File", str(runner), "-Candidate", config["candidate"],
                                 "-Sha256", config["candidate_sha256"], "-Journal",
                                 str(transaction / "server-journal"), "-Apply"],
                                stdout=log, stderr=subprocess.STDOUT, timeout=1800)
            status = inspect_result(transaction, config, updater)
            if result.returncode and status == "complete":
                status = "recovery_required"
        if status == "complete":
            admin.report(release, name, status, role_verified=True, installed_version=config["version"],
                         package_sha256=config["package_sha256"])
        else:
            admin.report(release, name, status, code="recovery_required" if status == "recovery_required" else None)
        write(transaction / "result.json", {"status": status, "version": config["version"],
                                           "package_sha256": config["package_sha256"], "checked_at": time.time()})
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        admin.report(release, name, "recovery_required", code="recovery_required")
