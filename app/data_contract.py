"""Read-only compatibility facts for the three service databases.

``CONTRACT`` is the coordinated release contract consumed by maintenance
tools. Contract 1 requires persisted SQLite schema version 1 for every role;
legacy version 0 can be backed up but cannot pass this compatibility check.
"""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import time

from .backup_databases import validate_database


CONTRACT = 1
_ROLES = frozenset(('hub', 'print', 'laser'))
_PENDING_PRINT = frozenset(('queued', 'preparing', 'workflow_wait', 'dispatching',
                            'starting', 'printing', 'recovering', 'attention'))
_UNCERTAIN_PRINT = frozenset(('dispatching', 'starting', 'printing', 'recovering', 'attention'))


def _connection(path):
    """Open a validated database read-only, including an existing WAL."""
    source = Path(os.fspath(path)).absolute()
    return sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True,
                           timeout=5)


def _count(db, sql, params=()):
    return int(db.execute(sql, params).fetchone()[0])


def _facts(db, role):
    if role == 'hub':
        facts = {
            'objects': _count(db, 'SELECT COUNT(*) FROM objects'),
            'workflow_versions': _count(db, 'SELECT COUNT(*) FROM workflow_versions'),
            'requests': _count(db, 'SELECT COUNT(*) FROM requests'),
        }
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        # Agent credentials belonged to an older hub schema.  Keep the
        # aggregate field for historical databases, while a fresh minimal
        # hub reports zero without requiring those tables.
        facts['agents'] = (_count(db, 'SELECT COUNT(*) FROM agents')
                           if 'agents' in tables else 0)
        return facts
    if role == 'print':
        marks = tuple(sorted(_PENDING_PRINT))
        uncertain = tuple(sorted(_UNCERTAIN_PRINT))
        return {
            'jobs': _count(db, 'SELECT COUNT(*) FROM jobs'),
            'pending_print_jobs': _count(
                db, 'SELECT COUNT(*) FROM jobs WHERE status IN (' + ','.join('?' for _ in marks) + ')', marks),
            'uncertain_print_jobs': _count(
                db, 'SELECT COUNT(*) FROM jobs WHERE status IN (' + ','.join('?' for _ in uncertain) + ')', uncertain),
            'unfinished_print_workflows': _count(
                db, 'SELECT COUNT(*) FROM jobs WHERE workflow IS NOT NULL AND workflow_complete=0'),
        }
    return {
        'jobs': _count(db, 'SELECT COUNT(*) FROM jobs'),
        'stations': _count(db, 'SELECT COUNT(*) FROM laser_stations'),
        'unfinished_laser_handoffs': _count(
            db, 'SELECT COUNT(*) FROM jobs WHERE handoff_claimed=1 AND physical_done=0'),
        'unfinished_laser_workflows': _count(
            db, 'SELECT COUNT(*) FROM jobs WHERE workflow IS NOT NULL AND workflow_complete=0'),
    }


def inspect_component(path, role):
    """Validate and summarize one installed component without writing to it."""
    if not isinstance(role, str) or role not in _ROLES:
        raise ValueError('Ugyldig komponentrolle.')
    validated = validate_database(path, role)
    if validated['user_version'] != 1:
        raise ValueError('Komponentens dataformat kræver migration før kompatibilitetskontrol.')
    with closing(_connection(path)) as db:
        db.execute('PRAGMA query_only=ON')
        # Pin version and aggregates to the same read snapshot. Validation
        # above is intentionally read-only and never performs a migration.
        db.execute('BEGIN')
        if db.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise ValueError('Komponentens dataformat ændredes under kontrollen.')
        deadline = time.monotonic() + 30
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        try:
            facts = _facts(db, role)
        except sqlite3.OperationalError as error:
            if time.monotonic() >= deadline:
                raise TimeoutError('Komponentkontrollen overskred tidsgrænsen.') from error
            raise
    return {'role': role, 'contract': CONTRACT,
            'sqlite_user_version': validated['user_version'], 'facts': facts}


def require_contract(declared, actual):
    """Require an exact installed component set and contract level.

    ``declared`` maps component roles to integer contract levels. ``actual``
    maps the same roles to the dictionaries returned by
    :func:`inspect_component` (or to integer levels for compact callers).
    Hub is mandatory; print and laser are optional, but undeclared components
    are rejected so a release cannot silently omit a discovered service.
    """
    if not isinstance(declared, dict) or not isinstance(actual, dict):
        raise ValueError('Komponentkontrakten skal være objekter.')
    if set(declared) - _ROLES or set(actual) - _ROLES or 'hub' not in declared or 'hub' not in actual:
        raise ValueError('Hub-kontrakten er påkrævet, og rollerne skal være kendte.')
    if set(declared) != set(actual):
        raise ValueError('Installerede komponenter matcher ikke kontrakten.')
    for role in declared:
        level = declared[role]
        if type(level) is not int or level != CONTRACT:
            raise ValueError('Ukendt komponentkontrakt.')
        value = actual[role]
        if isinstance(value, dict) and value.get('role', role) != role:
            raise ValueError('Komponentens rolle matcher ikke kontrakten.')
        actual_level = value.get('contract') if isinstance(value, dict) else value
        if type(actual_level) is not int or actual_level != CONTRACT:
            raise ValueError('Komponentens kontrakt matcher ikke.')
    return True
