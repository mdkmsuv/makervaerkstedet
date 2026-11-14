"""Durable admission pause, never a physical stop or an idle requirement.

Every caller uses the authority's own SQLite transaction/lock. Busy and unknown
jobs are diagnostics: the teacher has already authorized the update timing.
"""
import time
import uuid


class MaintenanceError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def ensure_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS maintenance_hold (
        id INTEGER PRIMARY KEY CHECK(id=1), holder_id TEXT NOT NULL, acquired REAL NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS maintenance_reviews (
        job_id TEXT PRIMARY KEY, holder_id TEXT NOT NULL, reason TEXT NOT NULL)''')


def current_holder(db):
    row = db.execute('SELECT holder_id FROM maintenance_hold WHERE id=1').fetchone()
    return row[0] if row else None


def require_open(db):
    if current_holder(db) is not None:
        raise MaintenanceError('maintenance_dispatch_paused')


def command(db, action, holder_id, *, facts, on_acquire=None, on_release=None):
    """Caller holds its queue lock or BEGIN IMMEDIATE; owns commit/rollback."""
    try:
        if not isinstance(holder_id, str) or str(uuid.UUID(holder_id)) != holder_id:
            raise ValueError()
    except (ValueError, AttributeError):
        raise MaintenanceError('invalid_maintenance_holder') from None
    if action not in ('acquire', 'assert', 'release'):
        raise MaintenanceError('invalid_maintenance_action')
    previous = current_holder(db)
    if previous is not None and previous != holder_id:
        raise MaintenanceError('maintenance_owned_by_another_operation')
    if action == 'acquire':
        if previous is None:
            db.execute('INSERT INTO maintenance_hold VALUES(1,?,?)', (holder_id, time.time()))
        if on_acquire is not None:
            on_acquire(holder_id)
    elif action == 'assert':
        if previous != holder_id:
            raise MaintenanceError('maintenance_hold_missing')
    else:
        if on_release is not None:
            on_release()
        db.execute('DELETE FROM maintenance_hold WHERE id=1')
    held = action != 'release'
    return dict(barrier_version=1, holder_id=holder_id, held=held, admission_closed=held,
                physical_stop_sent=False, **facts())
