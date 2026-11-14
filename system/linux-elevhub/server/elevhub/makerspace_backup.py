"""Consistent snapshots of the makerspace SQLite database.

This is only a database component. It does not activate a backup schedule,
stop a service, copy a full installation, or provide backup transport.
"""

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import time


SCHEMA_VERSION = 1
_TABLES = {
    "objects": {"type", "id", "version", "body"},
    "workflow_versions": {"id", "revision", "body"},
    "requests": {"actor", "request_id", "digest", "response"},
    "audit": {"sequence", "at", "actor", "action", "target", "request_id", "outcome"},
}
_OPTIONAL_TABLES = {
    "agents": {"id", "name", "scopes", "token_hash", "created_at", "revoked"},
    "agent_requests": {"actor", "request_id", "action", "digest", "agent_id"},
}
_BACKUP_TIMEOUT = 30.0
_PAGE_BATCH = 100


def _path(value, label):
    try:
        raw = os.fspath(value)
        if isinstance(raw, str) and (raw.startswith("\\\\") or raw.startswith("//")):
            raise ValueError("Netværksstier er ikke tilladt.")
        path = Path(value)
    except (TypeError, ValueError, OSError) as error:
        raise ValueError(f"{label} er ugyldig.") from error
    if not path.is_absolute():
        path = path.absolute()
    return path


def _check_source(path):
    if path.is_symlink() or not path.exists() or not path.is_file():
        raise ValueError("Kilden skal være en eksisterende almindelig fil uden symlink.")
    return path


def _reserve_destination(path):
    if path.exists() or path.is_symlink():
        raise FileExistsError("Destinationen skal være en ny fil.")
    if any((sidecar.exists() or sidecar.is_symlink())
           for sidecar in (path.with_name(path.name + suffix)
                          for suffix in ("-wal", "-shm", "-journal"))):
        raise FileExistsError("Destinationens SQLite-sidefiler findes allerede.")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise FileExistsError("Destinationen skal være en ny fil.")
    os.close(descriptor)
    if os.name != "nt":
        os.chmod(path, 0o600)
    stat = path.lstat()
    return path, (stat.st_dev, stat.st_ino)


def _source_connection(path):
    uri = path.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5)


def _columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _validate_database(connection, deadline=None):
    if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        raise ValueError("Uventet makerspace-databaseversion.")
    for table, columns in _TABLES.items():
        actual = _columns(connection, table)
        if not columns <= actual:
            raise ValueError(f"Makerspace-databasen mangler tabellen {table}.")
    for table, columns in _OPTIONAL_TABLES.items():
        if table in {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}:
            if not columns <= _columns(connection, table):
                raise ValueError(f"Makerspace-databasen mangler kolonner i {table}.")
    if deadline is None:
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    else:
        def progress_handler():
            return int(time.monotonic() > deadline)
        connection.set_progress_handler(progress_handler, 1000)
        try:
            try:
                result = connection.execute("PRAGMA integrity_check").fetchone()[0]
            except sqlite3.OperationalError as error:
                if time.monotonic() > deadline:
                    raise TimeoutError("SQLite-integritetskontrollen overskred tidsgrænsen.") from error
                raise
        finally:
            connection.set_progress_handler(None, 0)
    if result != "ok":
        raise ValueError("Makerspace-databasen består ikke integritetskontrollen.")


def _remove_owned(path, identity):
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return
    if (stat.st_dev, stat.st_ino) != identity:
        return
    # SQLite closes its own journals. We have no ownership proof for any
    # sidecar created by another process, even when our base file survives.
    try:
        path.unlink()
    except OSError:
        pass


def _copy(source, destination, revoke_agents):
    source_path = _check_source(_path(source, "Kilde"))
    destination_path, destination_identity = _reserve_destination(_path(destination, "Destination"))
    started = time.monotonic()
    source_db = destination_db = None
    try:
        source_db = _source_connection(source_path)
        _validate_database(source_db, started + _BACKUP_TIMEOUT)
        destination_db = sqlite3.connect(destination_path, timeout=5)
        copied = [0]

        def progress(status, remaining, total):
            copied[0] = max(0, total - remaining)
            if time.monotonic() - started > _BACKUP_TIMEOUT:
                raise TimeoutError("SQLite-backup overskred tidsgrænsen.")

        source_db.backup(destination_db, pages=_PAGE_BATCH, progress=progress, sleep=0.05)
        _validate_database(destination_db, started + _BACKUP_TIMEOUT)
        if revoke_agents and "agents" in {row[0] for row in destination_db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}:
            destination_db.set_progress_handler(lambda: int(time.monotonic() > started + _BACKUP_TIMEOUT), 1000)
            try:
                destination_db.execute("UPDATE agents SET revoked=1")
                destination_db.commit()
            finally:
                destination_db.set_progress_handler(None, 0)
            _validate_database(destination_db, started + _BACKUP_TIMEOUT)
        else:
            destination_db.commit()
        result = {
            "source": str(source_path),
            "destination": str(destination_path),
            "pages": copied[0],
            "integrity": "ok",
            "agents_revoked": bool(revoke_agents),
        }
    except BaseException:
        error = sys.exc_info()
        try:
            if destination_db is not None:
                destination_db.close()
        except Exception:
            pass
        try:
            if source_db is not None:
                source_db.close()
        except Exception:
            pass
        try:
            _remove_owned(destination_path, destination_identity)
        except Exception:
            pass
        raise error[1].with_traceback(error[2])
    else:
        try:
            if destination_db is not None:
                destination_db.close()
        finally:
            if source_db is not None:
                source_db.close()
        return result


def backup(source, destination):
    """Create a consistent new SQLite snapshot without changing source."""
    return _copy(source, destination, revoke_agents=False)


def restore_to_new_file(source_snapshot, destination):
    """Copy a snapshot to a new file and revoke every restored agent key."""
    return _copy(source_snapshot, destination, revoke_agents=True)


def _main(argv=None):
    parser = argparse.ArgumentParser(description="Makerspace SQLite snapshot")
    parser.add_argument("command", choices=("snapshot", "restore"))
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    args = parser.parse_args(argv)
    try:
        result = (backup(args.source, args.destination) if args.command == "snapshot"
                  else restore_to_new_file(args.source, args.destination))
    except (OSError, sqlite3.Error, TimeoutError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(_main())

