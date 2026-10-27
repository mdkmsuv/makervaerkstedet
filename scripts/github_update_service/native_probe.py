"""Bounded Windows identity/ACL fixture. Never invokes transport or updates.

Used only by test-native-controller.ps1 in an isolated QA data directory.
Kreditering: Klostermarksskolen.
"""
import argparse
import csv
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time


def identity():
    process = subprocess.run(["whoami.exe", "/user", "/fo", "csv", "/nh"],
                             capture_output=True, text=True, check=True)
    return next(csv.reader(io.StringIO(process.stdout)))[1]


def run(args):
    fixture = Path(args.fixture)
    config = fixture / "controller/config.json"
    data = fixture / "data"
    updates = data / "updates"
    checks = {}
    checks["expected_identity"] = identity() == args.expected_sid
    checks["token_admin_expected"] = bool(ctypes.windll.shell32.IsUserAnAdmin()) == (args.mode == "system")
    if args.mode == "system":
        code = fixture / "controller/code/scripts/github_update_service"
        sys.path.insert(0, str(code))
        from runtime import Controller, shared_boundary
        from common import write_public_config
        controller = Controller(config)
        checks["controller_native_boundary"] = True
        catalog = {"schema": 1, "releases": []}
        targets = {"schema": 1, "targets": []}
        for name, value in (("catalog.json", catalog), ("targets.json", targets)):
            write_public_config(updates / name, value)
            write_public_config(updates / name, value)
            checks[name + "_atomic_replacement"] = json.loads((updates / name).read_text()) == value
        shared_boundary(data)
        checks["published_boundary_verified"] = True
        with sqlite3.connect(updates / "queue.sqlite3") as db:
            db.execute("CREATE TABLE IF NOT EXISTS native_acl_probe(actor TEXT PRIMARY KEY)")
            db.execute("INSERT OR REPLACE INTO native_acl_probe VALUES('SYSTEM')")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if controller.feed.snapshot().get("importer_online"):
                checks["independent_system_task_heartbeat"] = True
                break
            time.sleep(0.5)
        else:
            checks["independent_system_task_heartbeat"] = False
        checks["no_real_targets"] = not controller.admin.snapshot()["targets"]
        checks["no_feed_configured"] = not (updates / "feed.json").exists()
    else:
        def denied(name, action):
            try:
                action()
            except PermissionError:
                checks[name] = True
            except OSError as error:
                checks[name] = error.winerror == 5
            else:
                checks[name] = False
        if args.mode == "localservice":
            checks["catalog_readable"] = json.loads((updates / "catalog.json").read_text())["schema"] == 1
            checks["targets_readable"] = json.loads((updates / "targets.json").read_text())["schema"] == 1
            with sqlite3.connect(updates / "queue.sqlite3") as db:
                db.execute("INSERT OR REPLACE INTO native_acl_probe VALUES('LocalService')")
                checks["queue_database_writable"] = db.execute(
                    "SELECT actor FROM native_acl_probe WHERE actor='LocalService'").fetchone()[0] == "LocalService"
            journal = updates / "mutable-native-probe.tmp"
            journal.write_text("fixture")
            journal.unlink()
            checks["sqlite_journal_create_delete"] = True
        denied("controller_config_private", lambda: config.read_bytes())
        denied("catalog_write_denied", lambda: (updates / "catalog.json").open("ab").close())
        denied("targets_write_denied", lambda: (updates / "targets.json").open("wb").close())
        denied("catalog_delete_denied", lambda: (updates / "catalog.json").unlink())
        denied("targets_delete_denied", lambda: (updates / "targets.json").unlink())
        denied("controller_work_write_denied", lambda: (fixture / "controller/workers/unauthorized.txt").write_text("fixture"))
        denied("controller_store_write_denied", lambda: (fixture / "controller/store/unauthorized.txt").write_text("fixture"))
        denied("updates_directory_replace_denied", lambda: updates.rename(data / "updates-unauthorized"))
        denied("data_directory_replace_denied", lambda: data.rename(fixture / "data-unauthorized"))
        if args.mode == "standard":
            denied("queue_database_private", lambda: (updates / "queue.sqlite3").read_bytes())
    return {"schema": 1, "mode": args.mode, "sid": identity(), "checks": checks,
            "passed": bool(checks) and all(checks.values()), "transport_used": False,
            "real_installation_touched": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("system", "localservice", "standard"), required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--expected-sid", required=True)
    args = parser.parse_args()
    try:
        result = run(args)
    except BaseException as error:
        result = {"schema": 1, "mode": args.mode, "passed": False,
                  "error_type": type(error).__name__, "error": str(error)}
    temporary = args.result.with_suffix(".pending")
    temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, args.result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
