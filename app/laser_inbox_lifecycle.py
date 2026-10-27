"""Storage policy helpers for the laser teacher inbox.

The inbox keeps a small history row after a delivery leaves the queue.  Its
SVG payload and per-image mode choices are removed only after ``done`` is
durable; startup repeats the same exact, idempotent migration after an
interrupted transition.
"""
import logging
import sqlite3

log = logging.getLogger(__name__)
TERMINAL = frozenset({'done'})


def ensure_payload_column(db):
    """Add the durable payload marker and completion time to an existing inbox database."""
    columns = {row[1] for row in db.execute('PRAGMA table_info(jobs)')}
    if 'payload_available' not in columns:
        db.execute("ALTER TABLE jobs ADD COLUMN payload_available INTEGER NOT NULL DEFAULT 1")
        db.commit()
    if 'finished_at' not in columns:
        # Retention counts from completion; rows finished before this column
        # existed fall back to their creation time.
        db.execute("ALTER TABLE jobs ADD COLUMN finished_at TEXT")
        db.commit()


def enable_incremental_autovacuum(db):
    """Migrate a legacy inbox database to incremental page reclamation.

    SQLite only applies ``auto_vacuum`` after a VACUUM.  This is deliberately
    best effort: VACUUM needs temporary free space and can be blocked by
    another process or fail on a full disk.  The inbox remains usable and a
    later startup can retry; no second full database copy is made.
    """
    mode = db.execute('PRAGMA auto_vacuum').fetchone()[0]
    if mode == 2:  # SQLITE_AUTO_VACUUM_INCREMENTAL
        return True
    try:
        db.commit()
        db.execute('PRAGMA auto_vacuum=INCREMENTAL')
        db.commit()
        db.execute('VACUUM')
        db.commit()
        return db.execute('PRAGMA auto_vacuum').fetchone()[0] == 2
    except sqlite3.DatabaseError as error:
        db.rollback()
        log.warning('Kunne ikke aktivere pladsgenbrug for laserinboxen; prøver igen ved næste opstart: %s', error)
        return False


def reclaim_incremental_space(db):
    """Return free SQLite pages to the file system when incremental mode is on."""
    try:
        if db.execute('PRAGMA auto_vacuum').fetchone()[0] == 2:
            db.commit()
            # The pragma frees one page per result row; it must be stepped to the end.
            db.execute('PRAGMA incremental_vacuum').fetchall()
            db.commit()
    except sqlite3.DatabaseError as error:
        # Payload deletion is already durable. A locked/full database must not
        # make a teacher's completed-status request fail.
        db.rollback()
        log.warning('Laserinboxens frigivelse af SQLite-plads blev udsat: %s', error)


def _cleanup_terminal_payload(db, job_id):
    """Drop one completed SVG payload while retaining teacher-visible history.

    Caller supplies an open SQLite connection. The transaction is acquired
    here so status and payload deletion cannot be observed half-applied.
    """
    db.execute('BEGIN IMMEDIATE')
    row = db.execute('SELECT status, payload_available FROM jobs WHERE id=?', (job_id,)).fetchone()
    if row is None:
        db.rollback()
        return False
    if row[0] not in TERMINAL:
        db.rollback()
        return False
    if not row[1]:
        db.commit()
        return True
    # Keep details: dimensions, operation counts and image names make useful
    # history, but the source SVG itself is the payload that must disappear.
    db.execute('UPDATE jobs SET svg=NULL, payload_available=0 WHERE id=?', (job_id,))
    db.execute('DELETE FROM image_settings WHERE job_id=?', (job_id,))
    db.commit()
    return True


def cleanup_terminal_payload(db, job_id):
    ensure_payload_column(db)
    cleaned = _cleanup_terminal_payload(db, job_id)
    if cleaned:
        reclaim_incremental_space(db)
    return cleaned


def cleanup_terminal_payloads(db):
    """Retry all completed rows after an interrupted server transition."""
    ensure_payload_column(db)
    ids = [row[0] for row in db.execute("SELECT id FROM jobs WHERE status='done' AND payload_available=1")]
    cleaned = sum(_cleanup_terminal_payload(db, job_id) for job_id in ids)
    if cleaned:
        reclaim_incremental_space(db)
    return cleaned


HISTORY_DAYS = 30


def delete_expired_history(db, days=HISTORY_DAYS, now=None):
    """Delete completed deliveries, including the pupil's name and class.

    GDPR data minimisation: once a job is done, the teacher needs its history
    only briefly, so it is kept ``days`` after completion. Rows still waiting
    for the teacher are never touched. The timestamps are UTC ISO strings, so
    string order is time order.
    """
    from datetime import datetime, timedelta, timezone
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
    ids = [row[0] for row in db.execute(
        "SELECT id FROM jobs WHERE status='done' AND coalesce(finished_at, created_at) < ?", (cutoff,))]
    if not ids:
        return 0
    db.execute('BEGIN IMMEDIATE')
    db.executemany('DELETE FROM image_settings WHERE job_id=?', [(i,) for i in ids])
    db.executemany("DELETE FROM jobs WHERE id=? AND status='done'", [(i,) for i in ids])
    db.commit()
    reclaim_incremental_space(db)
    log.info('Slettede %s afsluttede laserafleveringer ældre end %s dage.', len(ids), days)
    return len(ids)


def payload_available(job):
    """Safe UI/API predicate for whether preview/download may be offered."""
    return bool(job.get('payload_available', 1) and job.get('status') not in TERMINAL)
