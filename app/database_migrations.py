"""Offline-only SQLite contract marker migrations.

This module deliberately does not construct a service.  It is intended for an
isolated restored copy, before any startup reconciliation, cleanup or key work.
The coordinator must hold its restore/maintenance guard for the whole call.
"""
import sqlite3
import time
import hashlib

from .backup_databases import (_path, _source, _validate_with_deadline,
                               validate_database)


CURRENT_VERSION = 1
_ROLES = frozenset(('hub', 'print', 'laser'))


def _quote(identifier):
    return '"' + str(identifier).replace('"', '""') + '"'


def _digest_value(value):
    if value is None:
        tag, payload = b'n', b''
    elif isinstance(value, bool):
        tag, payload = b'b', (b'1' if value else b'0')
    elif isinstance(value, int):
        tag, payload = b'i', str(value).encode('ascii')
    elif isinstance(value, float):
        tag, payload = b'f', value.hex().encode('ascii')
    elif isinstance(value, bytes):
        tag, payload = b'x', value
    elif isinstance(value, str):
        tag, payload = b's', value.encode('utf-8')
    else:
        raise ValueError('SQLite indeholder en ukendt værditype.')
    return tag + str(len(payload)).encode('ascii') + b':' + payload


def _digest_rows(rows, deadline):
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        if time.monotonic() >= deadline:
            raise TimeoutError('Restorekontrollen overskred tidsgrænsen.')
        count += 1
        digest.update(b'R')
        for value in row:
            digest.update(_digest_value(value))
            digest.update(b'|')
    return count, digest.hexdigest()


def _schema_rows(db):
    return db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name,tbl_name,sql"
    )


def _table_names(db):
    return [row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]


def _columns(db, table):
    return [row[1] for row in db.execute('PRAGMA table_info(' + _quote(table) + ')')]


def _row_query(db, role, table, transform=False):
    columns = _columns(db, table)
    if table == 'settings' and role == 'print':
        if columns != ['key', 'value']:
            raise ValueError('Print settings-tabellen har ukendte kolonner.')
    if table == 'settings' and role == 'print' and transform:
        # Restore intentionally pauses print. Treat a missing row as the
        # documented transform and normalize an existing value as well.
        key = _quote('key')
        value = _quote('value')
        return (f'SELECT {key}, CASE WHEN {key}=? THEN ? ELSE {value} END FROM {_quote(table)} '
                f'UNION ALL SELECT ?, ? WHERE NOT EXISTS '
                f'(SELECT 1 FROM {_quote(table)} WHERE {key}=?) ORDER BY 1,2',
                ('paused', 'true', 'paused', 'true', 'paused'))
    # Print uses the implicit rowid for FIFO tie-breaking and submission
    # watermarks. Equal visible rows with different rowids are not equivalent.
    expressions = ['rowid'] if role == 'print' and table == 'jobs' else []
    params = []
    for column in columns:
        expression = _quote(column)
        if transform and role == 'hub' and table == 'agents' and column == 'revoked':
            expression = '1'
        expressions.append(expression)
    selected = ','.join(expressions)
    order = ','.join(str(index + 1) for index in range(len(expressions))) or '1'
    return f'SELECT {selected} FROM {_quote(table)} ORDER BY {order}', tuple(params)


def _open_read(path):
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
    except BaseException:
        db.close()
        raise
    return db


def _digest_query(db, query, params, deadline):
    """The caller bounds SQL execution; also bound Python row iteration."""
    return _digest_rows(db.execute(query, params), deadline)


def migrate_database(path, role):
    """Migrate only a supported legacy contract marker to version 1.

    Hub must already be version 1. Print and laser may be version 0 only when
    their complete current workflow schema passes ``validate_database``. No
    pre-workflow schema migration is attempted here.
    """
    if not isinstance(role, str) or role not in _ROLES:
        raise ValueError('Ugyldig databaserolle.')
    database = _source(_path(path, 'Kilde'))
    validated = validate_database(database, role)
    version = validated.get('user_version')
    if type(version) is not int:
        raise ValueError('SQLite-versionen er ugyldig.')
    if version == CURRENT_VERSION:
        return {'role': role, 'from_version': version, 'to_version': version, 'changed': False}
    if role == 'hub' or version != 0:
        raise ValueError('Denne databaserolle har ingen understøttet legacy-migration.')

    db = sqlite3.connect(database, timeout=5)
    try:
        db.execute('BEGIN IMMEDIATE')
        deadline = time.monotonic() + 30
        _validate_with_deadline(db, role, deadline)
        if db.execute('PRAGMA user_version').fetchone()[0] != 0:
            raise ValueError('Databasen ændrede version under migrationen.')
        # The validator has already established the complete current schema;
        # this transaction changes only SQLite's compatibility marker.
        db.execute('PRAGMA user_version=1')
        _validate_with_deadline(db, role, deadline)
        db.commit()
    except BaseException:
        try:
            db.rollback()
        finally:
            db.close()
        raise
    else:
        db.close()
    return {'role': role, 'from_version': 0, 'to_version': 1, 'changed': True}


def verify_restored_database(source, destination, role):
    """Verify a restored candidate against its source without writing either.

    The candidate may have the intentional restore transforms performed by
    ``protect_restored_database``: all hub agents are revoked, print is paused,
    and print/laser version 0 may become version 1. The caller must hold the
    restore/maintenance guard for the whole operation.
    """
    if not isinstance(role, str) or role not in _ROLES:
        raise ValueError('Ugyldig databaserolle.')
    source = _source(_path(source, 'Kilde'))
    destination = _source(_path(destination, 'Destination'))
    source_info = validate_database(source, role)
    destination_info = validate_database(destination, role)
    if destination_info['user_version'] not in (source_info['user_version'], 1):
        raise ValueError('Restorekandidaten har en uventet SQLite-version.')
    if role == 'hub' and destination_info['user_version'] != source_info['user_version']:
        raise ValueError('Hub-databasen må ikke skifte SQLite-version.')
    deadline = time.monotonic() + 30
    source_db = destination_db = None
    try:
        source_db = _open_read(source)
        destination_db = _open_read(destination)
        for connection in (source_db, destination_db):
            connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        source_pinned_version = source_db.execute('PRAGMA user_version').fetchone()[0]
        destination_pinned_version = destination_db.execute('PRAGMA user_version').fetchone()[0]
        if source_pinned_version != source_info['user_version'] or destination_pinned_version != destination_info['user_version']:
            raise ValueError('Restorekandidatens SQLite-version ændrede sig under kontrollen.')
        schema_a = list(_schema_rows(source_db))
        schema_b = list(_schema_rows(destination_db))
        if schema_a != schema_b:
            raise ValueError('Restorekandidatens SQLite-schema matcher ikke kilden.')
        tables_a = _table_names(source_db)
        tables_b = _table_names(destination_db)
        if tables_a != tables_b:
            raise ValueError('Restorekandidatens tabeller matcher ikke kilden.')
        for table in tables_a:
            if time.monotonic() >= deadline:
                raise TimeoutError('Restorekontrollen overskred tidsgrænsen.')
            if _columns(source_db, table) != _columns(destination_db, table):
                raise ValueError('Restorekandidatens kolonner matcher ikke kilden.')
            query_a, params_a = _row_query(source_db, role, table, transform=True)
            query_b, params_b = _row_query(destination_db, role, table, transform=False)
            count_a, hash_a = _digest_query(source_db, query_a, params_a, deadline)
            count_b, hash_b = _digest_query(destination_db, query_b, params_b, deadline)
            if (count_a, hash_a) != (count_b, hash_b):
                raise ValueError('Restorekandidatens data matcher ikke kilden: ' + table)
    finally:
        if source_db is not None:
            source_db.rollback()
            source_db.close()
        if destination_db is not None:
            destination_db.rollback()
            destination_db.close()
    return {'role': role, 'source_user_version': source_info['user_version'],
            'destination_user_version': destination_info['user_version'], 'verified': True}
