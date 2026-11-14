"""Bounded, role-aware SQLite snapshots for the three service databases.

This module copies databases only.  Installation files, payloads, keys and
service quiescing are deliberately owned by the installation coordinator.
"""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import stat
import time


_ROLES = {"hub", "print", "laser"}
_SCHEMAS = {
    "hub": {
        "versions": (1,),
        "tables": {
            "objects": {"type", "id", "version", "body"},
            "workflow_versions": {"id", "revision", "body"},
            "requests": {"actor", "request_id", "digest", "response"},
            "audit": {"sequence", "at", "actor", "action", "target", "request_id", "outcome"},
        },
        "optional_tables": {
            "agents": {"id", "name", "scopes", "token_hash", "created_at", "revoked"},
            "agent_requests": {"actor", "request_id", "action", "digest", "agent_id"},
        },
    },
    "print": {
        "versions": (0, 1),
        "tables": {
            "jobs": {"id", "request_id", "digest", "filename", "created", "updated", "status", "printer_id",
                     "workflow", "workflow_step", "workflow_version", "workflow_authorized", "workflow_complete"},
            "settings": {"key", "value"},
            "library_models": {"digest", "filename", "meta", "created", "object_name", "aliases"},
            "workflow_commands": {"actor", "request_id", "digest", "response"},
            "workflow_audit": {"sequence", "at", "actor", "request_id", "job_id", "step_id"},
            "submission_access": {"source", "reset_after", "blocked_until", "block_id", "blocked_at", "trigger_count"},
        },
        "optional_tables": {
            "library_deletion_journal": {"operation_id", "digest", "object_name", "state", "generation", "created"},
            "library_delete_receipts": {"request_id", "digest", "state", "generation", "created", "completed"},
            "library_policy_receipts": {"request_id", "version", "policy", "created"},
            "library_settings": {"key", "value"},
        },
    },
    "laser": {
        "versions": (0, 1),
        "tables": {
            "jobs": {"id", "request_id", "created_at", "status", "svg", "details", "payload_available",
                     "workflow", "workflow_step", "workflow_version", "workflow_authorized", "workflow_complete",
                     "station_id", "workflow_model", "physical_done", "handoff_claimed"},
            "image_settings": {"job_id", "modes"},
            "laser_stations": {"id", "name", "location", "model", "enabled", "version", "workflow"},
            "laser_workflow_commands": {"actor", "request_id", "digest", "response"},
            "laser_workflow_audit": {"at", "actor", "action", "target", "request_id", "outcome"},
        },
    },
}
_SIDE_SUFFIXES = ("-wal", "-shm", "-journal")


def _role(role):
    if not isinstance(role, str) or role not in _ROLES:
        raise ValueError("Ugyldig databaserolle.")
    return role


def _path(value, label):
    try:
        raw = os.fspath(value)
        if isinstance(raw, str) and (raw.startswith("\\\\") or raw.startswith("//")):
            raise ValueError
        path = Path(value)
    except (TypeError, ValueError, OSError) as error:
        raise ValueError(f"{label} er ugyldig.") from error
    return path if path.is_absolute() else path.absolute()


def _source(path):
    _no_links(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Kilden skal være en almindelig fil uden symlink.")
    return path


def _no_links(path):
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Links og reparse points er ikke tilladt.')


def _reserve(path):
    _no_links(path)
    if path.exists() or path.is_symlink():
        raise FileExistsError("Destinationen skal være en ny fil.")
    if any(path.with_name(path.name + suffix).exists() or path.with_name(path.name + suffix).is_symlink()
           for suffix in _SIDE_SUFFIXES):
        raise FileExistsError("Destinationens SQLite-sidefiler findes allerede.")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise FileExistsError("Destinationen skal være en ny fil.") from error
    os.close(fd)
    if os.name != "nt":
        os.chmod(path, 0o600)
    stat = path.lstat()
    return (stat.st_dev, stat.st_ino)


def _sidecar_paths(path):
    return {suffix: path.with_name(path.name + suffix) for suffix in _SIDE_SUFFIXES}


def _remember_sidecars(path, remembered):
    """Record regular sidecars observed while this reserved DB is open."""
    for suffix, sidecar in _sidecar_paths(path).items():
        try:
            info = sidecar.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            continue
        if (stat.S_ISREG(info.st_mode)
                and not (getattr(info, 'st_file_attributes', 0) & 0x400)):
            remembered[suffix] = (info.st_dev, info.st_ino)


def _cleanup_failed_snapshot(path, identity, sidecars):
    """Remove only files owned by this failed reservation, after close."""
    targets = [(path, identity)]
    targets.extend((sidecar, sidecars.get(suffix))
                   for suffix, sidecar in _sidecar_paths(path).items())
    for target, expected in targets:
        if expected is None:
            continue
        try:
            info = target.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            continue
        if (stat.S_ISREG(info.st_mode)
                and not (getattr(info, 'st_file_attributes', 0) & 0x400)
                and (info.st_dev, info.st_ino) == expected):
            try:
                target.unlink()
            except OSError:
                pass


def _tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def _columns(db, table):
    return {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}


def _deadline_handler(db, deadline):
    def stop():
        return int(time.monotonic() >= deadline)
    db.set_progress_handler(stop, 1000)
    return stop


def _integrity(db, deadline):
    _deadline_handler(db, deadline)
    try:
        try:
            result = db.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.OperationalError as error:
            if time.monotonic() >= deadline:
                raise TimeoutError("SQLite-integritetskontrollen overskred tidsgrænsen.") from error
            raise
    finally:
        db.set_progress_handler(None, 0)
    if result != "ok":
        raise ValueError("SQLite-databasen består ikke integritetskontrollen.")


def validate_database(path, role=None):
    path = _source(_path(path, "Kilde"))
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        if role is None:
            candidates = []
            for candidate in _ROLES:
                try:
                    _validate_with_deadline(db, candidate, time.monotonic() + 30)
                    candidates.append(candidate)
                except (ValueError, sqlite3.Error, TimeoutError):
                    pass
            if len(candidates) != 1:
                raise ValueError("SQLite-databasen har ingen entydig kendt rolle.")
            role = candidates[0]
        role = _role(role)
        expected = _SCHEMAS[role]
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in expected["versions"]:
            raise ValueError("Uventet SQLite-databaseversion.")
        actual_tables = _tables(db)
        missing_tables = set(expected["tables"]) - actual_tables
        if missing_tables:
            raise ValueError("SQLite-databasen mangler nødvendige tabeller.")
        for table, required in expected["tables"].items():
            if not required <= _columns(db, table):
                raise ValueError(f"SQLite-tabellen {table} mangler kolonner.")
        optional = _tables(db) & set(expected.get("optional_tables", {}))
        for table in optional:
            if not expected["optional_tables"][table] <= _columns(db, table):
                raise ValueError(f"SQLite-tabellen {table} mangler kolonner.")
        _integrity(db, time.monotonic() + 30)
        return {"role": role, "user_version": version,
                "tables": {table: sorted(_columns(db, table)) for table in sorted(set(expected["tables"]) | optional)}}


def snapshot_database(source, destination, role=None, timeout=30):
    role = validate_database(source)['role'] if role is None else _role(role)
    if type(timeout) not in (int, float) or isinstance(timeout, bool) or not 0 < timeout <= 300:
        raise ValueError("Timeout skal være mellem 0 og 300 sekunder.")
    source = _source(_path(source, "Kilde"))
    destination = _path(destination, "Destination")
    identity = _reserve(destination)
    started = time.monotonic()
    source_db = target_db = None
    sidecars = {}
    try:
        source_db = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        source_db.row_factory = sqlite3.Row
        _validate_with_deadline(source_db, role, started + timeout)
        target_db = sqlite3.connect(destination, timeout=5)
        _remember_sidecars(destination, sidecars)
        pages = [0]
        def progress(status, remaining, total):
            pages[0] = total - remaining
            if time.monotonic() >= started + timeout:
                raise TimeoutError("SQLite-backup overskred tidsgrænsen.")
        source_db.backup(target_db, pages=100, progress=progress, sleep=0.05)
        _remember_sidecars(destination, sidecars)
        target_db.commit()
        # A standalone snapshot must not depend on WAL sidecars. Switching the
        # new copy does not change the live source's journal mode or WAL.
        target_db.execute('PRAGMA journal_mode=DELETE').fetchone()
        _remember_sidecars(destination, sidecars)
        _validate_with_deadline(target_db, role, started + timeout)
        _remember_sidecars(destination, sidecars)
        target_db.commit()
        return {"source": str(source), "destination": str(destination), "role": role,
                "user_version": target_db.execute("PRAGMA user_version").fetchone()[0],
                "pages": pages[0], "integrity": "ok"}
    except BaseException:
        try:
            if target_db is not None:
                try:
                    target_db.close()
                except Exception:
                    pass
                target_db = None
        finally:
            if source_db is not None:
                try:
                    source_db.close()
                except Exception:
                    pass
                source_db = None
        # SQLite may leave a rollback/WAL sidecar on a failed write. Cleanup
        # happens only after both handles are closed and only for exact file
        # identities observed while this reservation was active.
        _remember_sidecars(destination, sidecars)
        _cleanup_failed_snapshot(destination, identity, sidecars)
        raise
    finally:
        if target_db is not None:
            try: target_db.close()
            except Exception: pass
        if source_db is not None:
            try: source_db.close()
            except Exception: pass


def _validate_with_deadline(db, role, deadline):
    expected = _SCHEMAS[role]
    if db.execute("PRAGMA user_version").fetchone()[0] not in expected["versions"]:
        raise ValueError("Uventet SQLite-databaseversion.")
    for table, required in expected["tables"].items():
        if not required <= _columns(db, table):
            raise ValueError("SQLite-databasen mangler nødvendige kolonner.")
    optional = _tables(db) & set(expected.get("optional_tables", {}))
    for table in optional:
        if not expected["optional_tables"][table] <= _columns(db, table):
            raise ValueError("SQLite-tabellen mangler nødvendige kolonner.")
    _integrity(db, deadline)


def protect_restored_database(path, role):
    role = _role(role)
    path = _source(_path(path, "Database"))
    db = sqlite3.connect(path)
    try:
        with db:
            _validate_with_deadline(db, role, time.monotonic() + 30)
            if role == "hub":
                if "agents" in _tables(db):
                    db.execute("UPDATE agents SET revoked=1")
            elif role == "print":
                db.execute("INSERT INTO settings(key,value) VALUES('paused','true') ON CONFLICT(key) DO UPDATE SET value='true'")
    finally:
        db.close()
