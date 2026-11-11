"""Prepare and verify an isolated program/data generation; never activate it.

No candidate code, service or hardware runs. A verified backup remains intact
beside the restored, protected data copy. The journal permits resuming after
the program and backup are complete; incomplete copies are never overwritten.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import uuid

from . import installation_backup as backup
from .backup_databases import validate_database
from .backup_guard import HOLD_FILE
from .data_contract import inspect_component, require_contract
from .database_migrations import migrate_database, verify_restored_database
from .instance import InstanceLock
from .release_compatibility import CREDIT, MANIFEST, inspect_program, _unique

JOURNAL = 'update-journal.json'
SEAL = 'prepared-manifest.json'
PROGRAM = 'program'
BACKUP = 'backup'
CANDIDATE = 'candidate-data'
PHASES = {'copy_program', 'snapshot', 'restore', 'migrate', 'seal', 'ready'}
HOLD = {'format': 1, 'reason': 'isolated_update', 'credits': CREDIT}


def _read(path):
    path = backup._plain(path, False)
    if path.stat().st_size > backup.MAX_MANIFEST:
        raise ValueError('Metadatafilen er for stor.')
    value = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique)
    if not isinstance(value, dict):
        raise ValueError('Metadata skal være et objekt.')
    return value


def _journal(target, expected_operation_id=None):
    value = _read(target / JOURNAL)
    if (set(value) != {'format', 'state', 'phase', 'operation_id', 'destination', 'created_utc',
                       'credits', 'release_manifest_sha256', 'source_manifest_sha256', 'seal_sha256'}
            or type(value['format']) is not int or value['format'] != 1
            or value['state'] not in ('preparing', 'failed', 'ready')
            or not isinstance(value['phase'], str) or value['phase'] not in PHASES or value['credits'] != CREDIT
            or value['destination'] != str(target)
            or not isinstance(value['operation_id'], str) or len(value['operation_id']) != 32
            or any(c not in '0123456789abcdef' for c in value['operation_id'])
            or not isinstance(value['created_utc'], str)):
        raise ValueError('Ukendt klargøringsjournal eller flyttet klargøring.')
    for name in ('release_manifest_sha256', 'source_manifest_sha256', 'seal_sha256'):
        digest = value[name]
        if digest is None and name != 'release_manifest_sha256':
            continue
        if not isinstance(digest, str) or not backup._HASH.fullmatch(digest):
            raise ValueError('Ugyldig kontrolsum i klargøringsjournalen.')
    if _read(target / HOLD_FILE) != HOLD:
        raise ValueError('Klargøringens driftsspærring mangler.')
    if expected_operation_id is not None and value['operation_id'] != expected_operation_id:
        raise ValueError('Dataklargøringen tilhører en anden operation.')
    return value


def _save(target, journal):
    # Never replace a caller-supplied temp file or follow a redirected journal.
    if (target / JOURNAL).exists():
        backup._plain(target / JOURNAL, False)
    temporary = target / ('.update-journal-' + uuid.uuid4().hex + '.tmp')
    try:
        backup._write(temporary, journal)
        os.replace(temporary, target / JOURNAL)
    finally:
        temporary.unlink(missing_ok=True)


def _phase(target, journal, phase):
    journal.update(state='preparing', phase=phase)
    _save(target, journal)


def _copy_program(source, target, expected):
    target.mkdir(mode=0o700)
    manifest = _read(source / MANIFEST)
    if backup._hash(source / MANIFEST) != expected:
        raise ValueError('Programmanifestet ændredes før kopiering.')

    # The release manifest describes files, while the directory layout is
    # meaningful to an embedded Python runtime as well (for example an empty
    # ``Lib/site-packages`` directory referenced by a ``._pth`` file).  Take a
    # guarded inventory before copying and materialise every verified source
    # directory, including empty ones.  Scanning also applies the same
    # no-links/path checks as the data backup code.
    _source_files, source_directories = backup._scan(source, ignore_locks=False)
    expected_files = set(manifest['files']) | {MANIFEST}
    if set(_source_files) != expected_files:
        raise ValueError('Programmappens filer matcher ikke manifestet.')
    for name in sorted(source_directories):
        backup._relative(name)
        (target / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, record in manifest['files'].items():
        backup._relative(name)
        path = backup._plain(source / name, False)
        out = target / name
        out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open('rb') as reader, out.open('xb') as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        if out.stat().st_size != record['size'] or backup._hash(out) != record['sha256']:
            raise ValueError('Programmet ændredes under kopiering.')
        if os.name != 'nt':
            out.chmod(0o600)
    raw = (source / MANIFEST).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Programmanifestet ændredes under kopiering.')
    # A directory can be created or removed while the files are being copied.
    # Refuse such a moving source rather than silently publishing a partial
    # runtime layout.
    _after_files, after_directories = backup._scan(source, ignore_locks=False)
    if set(_after_files) != expected_files or after_directories != source_directories:
        raise ValueError('Programmappens mappeoversigt ændredes under kopiering.')
    with (target / MANIFEST).open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if inspect_program(target)['manifest_sha256'] != expected:
        raise ValueError('Programkopiens identitet passer ikke.')


def _copied_profile(target):
    manifest = backup._manifest(target / BACKUP)
    profile = manifest['profile']
    roots = {name: target / CANDIDATE / 'roots' / name for name in profile['roots']}
    components = {role: backup._ref(ref, roots) for role, ref in profile['components'].items() if ref is not None}
    config = backup._ref(profile['config'], roots)
    return profile, roots, components, config


def _protected_candidate(target, journal):
    report = _read(target / CANDIDATE / backup.RESTORE)
    profile, roots, components, config = _copied_profile(target)
    expected_report = {'format': 1,
                       'source_created_utc': backup._manifest(target / BACKUP)['created_utc'],
                       'source_manifest_sha256': journal['source_manifest_sha256'],
                       'credits': CREDIT, 'isolated': True, 'agents_revoked': True,
                       'print_paused': 'print' in components,
                       'roots': {name: str(path) for name, path in roots.items()}, 'config': str(config)}
    if report != expected_report or type(report['format']) is not int:
        raise ValueError('Datakopien tilhører ikke denne klargøring.')
    expected = {'format': 1, 'reason': 'isolated_restore', 'credits': CREDIT}
    for folder in {target / CANDIDATE, *roots.values(), *components.values()}:
        if _read(folder / HOLD_FILE) != expected:
            raise ValueError('Datakopiens driftsspærring mangler.')
    backup._check_config(config, roots, components)
    _compare_candidate(target, profile, roots, components, config)
    # Inspect actual access/queue protections, not only the restore report.
    for role, component in components.items():
        path = backup._plain(component / backup.DATABASES[role], False)
        db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            # Teacher-only databases have no agent tables. Retain historical
            # rows, but require revocation on restore to protect old copies.
            has_legacy_agents = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agents'").fetchone()
            if role == 'hub' and has_legacy_agents and db.execute('SELECT COUNT(*) FROM agents WHERE revoked=0').fetchone()[0]:
                raise ValueError('Datakopien har ikke tilbagekaldt de historiske agentadgange.')
            if role == 'print':
                row = db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
                if row is None or row[0] != 'true':
                    raise ValueError('Datakopiens printkø er ikke pauset.')
        finally:
            db.close()
    return components


def _compare_candidate(target, profile, roots, components, config):
    """Prove preservation against the intact backup even after interruption.

    This contract permits only path relocation, agent revocation, queue pause
    and the supported SQLite version marker migration. A future migration
    that changes schema/data needs its own explicit comparison contract.
    """
    candidate = target / CANDIDATE
    manifest = backup._manifest(target / BACKUP)
    expected = {row['path'] for row in manifest['files']} | {backup.RESTORE, HOLD_FILE}
    expected |= {(path / HOLD_FILE).relative_to(candidate).as_posix()
                 for path in {*roots.values(), *components.values()}}
    directories = {'roots', *(f'roots/{name}' for name in roots), *manifest['directories']}
    actual, actual_directories = backup._scan(candidate, ignore_locks=False)
    if set(actual) != expected or actual_directories != directories:
        raise ValueError('Datakopiens filoversigt er ændret.')
    database_roles = {(folder / backup.DATABASES[role]).relative_to(candidate).as_posix(): role
                      for role, folder in components.items()}
    config_name = config.relative_to(candidate).as_posix()
    old_roots, _, _, old_config = backup._profile_layout(profile)
    source_roots = {name: target / BACKUP / 'roots' / name for name in roots}
    school_values = backup._school_relocations(source_roots, old_roots, roots, profile)
    for row in manifest['files']:
        name = row['path']
        source, copy = target / BACKUP / name, candidate / name
        if name == config_name:
            expected_config = backup._relocate_config(_read(source), old_roots, roots, old_config.parent)
            if _read(copy) != expected_config:
                raise ValueError('Datakopiens konfiguration er ændret.')
        elif name in database_roles:
            verify_restored_database(source, copy, database_roles[name])
        elif copy in school_values:
            if _read(copy) != school_values[copy]:
                raise ValueError('Datakopiens skolekonfiguration er ændret.')
        elif copy.stat().st_size != row['size'] or backup._hash(copy) != row['sha256']:
            raise ValueError('En bevaret datafil er ændret: ' + name)


def _inventory(target):
    files, directories = backup._scan(target, ignore_locks=False)
    for name in {*files, *directories}:
        first = name.split('/')[0]
        if first not in {PROGRAM, BACKUP, CANDIDATE} and name not in {JOURNAL, SEAL, HOLD_FILE, 'instance.lock'}:
            raise ValueError('Klargøringen indeholder en ukendt fil eller mappe.')
    # The operation lock and journal change during the operation. All other
    # files, including guards and restoration report, belong to the seal.
    for name in (JOURNAL, SEAL, 'instance.lock'):
        files.pop(name, None)
    if len(files) + len(directories) > backup.MAX_FILES:
        raise ValueError('Klargøringen har for mange filer og mapper.')
    return {name: {'size': signature[2], 'sha256': backup._hash(target / name)}
            for name, signature in sorted(files.items())}, sorted(directories)


def _result(seal):
    return {'format': 1, 'state': 'ready', 'activation_ready': False, 'credits': CREDIT,
            'release': seal['release'], 'data': seal['data'], 'migrations': seal['migrations'],
            'source_manifest_sha256': seal['source_manifest_sha256']}


def _verify_prepared(target, journal):
    if journal['state'] != 'ready' or journal['phase'] != 'ready' or journal['seal_sha256'] is None:
        raise ValueError('Klargøringen er ikke fuldført; undersøg journalen.')
    seal = _read(target / SEAL)
    if backup._hash(target / SEAL) != journal['seal_sha256']:
        raise ValueError('Klargøringens manifest er ændret.')
    if (set(seal) != {'format', 'credits', 'operation_id', 'release', 'data', 'migrations',
                      'source_manifest_sha256', 'files', 'directories'}
            or type(seal['format']) is not int or seal['format'] != 1
            or seal['credits'] != CREDIT or seal['operation_id'] != journal['operation_id']
            or seal['source_manifest_sha256'] != journal['source_manifest_sha256']):
        raise ValueError('Ugyldigt klargøringsmanifest.')
    files, directories = _inventory(target)
    if seal['files'] != files or seal['directories'] != directories:
        raise ValueError('Klargøringens filer er ændret siden kontrollen.')
    backup.verify(target / BACKUP)
    if backup._hash(target / BACKUP / backup.MANIFEST) != journal['source_manifest_sha256']:
        raise ValueError('Databackupens identitet passer ikke.')
    release = inspect_program(target / PROGRAM)
    if release != seal['release'] or release['manifest_sha256'] != journal['release_manifest_sha256']:
        raise ValueError('Programversionens identitet passer ikke.')
    components = _protected_candidate(target, journal)
    data = {role: inspect_component(path / backup.DATABASES[role], role) for role, path in components.items()}
    require_contract(release['data_contract'], data)
    if data != seal['data']:
        raise ValueError('Datakopiens tilstand er ændret.')
    return _result(seal)


def verify_prepared(folder, *, expected_operation_id=None):
    target = backup._plain(Path(folder), True)
    with InstanceLock(target):
        return _verify_prepared(target, _journal(target, expected_operation_id))


def _finish(target, journal):
    release = inspect_program(target / PROGRAM)
    if release['manifest_sha256'] != journal['release_manifest_sha256']:
        raise ValueError('Programversionen matcher ikke journalen.')
    backup.verify(target / BACKUP)
    digest = backup._hash(target / BACKUP / backup.MANIFEST)
    if journal['source_manifest_sha256'] not in (None, digest):
        raise ValueError('Databackupen matcher ikke journalen.')
    journal['source_manifest_sha256'] = digest
    _phase(target, journal, 'restore')
    if not (target / CANDIDATE).exists():
        backup.restore(target / BACKUP, target / CANDIDATE)
    # An incomplete restore has no success report: do not reuse or overwrite
    # it. Preserve it for inspection and prepare again in a new destination.
    components = _protected_candidate(target, journal)
    _phase(target, journal, 'migrate')
    migrations = {}
    for role, path in components.items():
        database = path / backup.DATABASES[role]
        source = target / BACKUP / database.relative_to(target / CANDIDATE)
        before = validate_database(source, role)['user_version']
        result = migrate_database(database, role)
        # Report the whole preparation, including successful role migrations
        # before an interruption, instead of just the last resumed attempt.
        result.update(from_version=before, changed=before != result['to_version'])
        migrations[role] = result
    data = {role: inspect_component(path / backup.DATABASES[role], role) for role, path in components.items()}
    require_contract(release['data_contract'], data)
    _phase(target, journal, 'seal')
    files, directories = _inventory(target)
    seal = {'format': 1, 'credits': CREDIT, 'operation_id': journal['operation_id'],
            'release': release, 'source_manifest_sha256': digest,
            'data': data, 'migrations': migrations, 'files': files, 'directories': directories}
    # A prior seal can only come from an interrupted attempt before ready.
    # Verify its identity before replacing it with the new, verified result.
    if (target / SEAL).exists():
        old = _read(target / SEAL)
        if old.get('operation_id') != journal['operation_id']:
            raise ValueError('Klargøringsmanifestet tilhører en anden operation.')
        (target / SEAL).unlink()
    backup._write(target / SEAL, seal)
    journal.update(state='ready', phase='ready', seal_sha256=backup._hash(target / SEAL))
    _save(target, journal)
    return _verify_prepared(target, journal)


def _failed(target, journal):
    journal['state'] = 'failed'
    # No exception text, job contents, keys or candidate payloads in journal.
    _save(target, journal)


def prepare(profile, program_root, destination):
    program = backup._plain(Path(program_root), True)
    release = inspect_program(program)
    with backup.offline_profile(profile) as locked:
        return _prepare_locked(locked, program, destination, release)


def _prepare_locked(locked, program, destination, release, *, operation_id=None):
    """Internal: caller retains all source service locks until this returns."""
    profile, roots, components, config = locked
    if any(program == root or program.is_relative_to(root) or root.is_relative_to(program) for root in roots.values()):
        raise ValueError('Program og driftsdata skal ligge adskilt.')
    require_contract(release['data_contract'], {role: 1 for role in components})
    for role, path in components.items():
        validate_database(path / backup.DATABASES[role], role)
    target = backup._destination(destination, [program, *roots.values()])
    with InstanceLock(target):
        backup._write(target / HOLD_FILE, HOLD)
        journal = {'format': 1, 'state': 'preparing', 'phase': 'copy_program',
                   'operation_id': operation_id or uuid.uuid4().hex, 'destination': str(target),
                   'created_utc': datetime.now(timezone.utc).isoformat(), 'credits': CREDIT,
                   'release_manifest_sha256': release['manifest_sha256'],
                   'source_manifest_sha256': None, 'seal_sha256': None}
        _save(target, journal)
        try:
            _copy_program(program, target / PROGRAM, release['manifest_sha256'])
            _phase(target, journal, 'snapshot')
            backup._snapshot_locked(profile, roots, components, config, target / BACKUP)
            return _finish(target, journal)
        except BaseException:
            _failed(target, journal)
            raise


def resume_preparation(folder, *, expected_operation_id=None):
    target = backup._plain(Path(folder), True)
    with InstanceLock(target):
        journal = _journal(target, expected_operation_id)
        if journal['state'] == 'ready':
            return _verify_prepared(target, journal)
        try:
            # Both complete inputs must exist. Never reread live data, execute
            # candidate code or repair a partially copied input by guessing.
            return _finish(target, journal)
        except BaseException:
            _failed(target, journal)
            raise


def prepare_from_backup(source, program_root, destination, *, operation_id, manifest_sha256):
    """Prepare historical data without changing or executing its source.

    The caller binds this deliberate restore to the installation and holds its
    offline locks. The ordinary prepared-data checks remain in force.
    """
    source = backup._plain(Path(source), True)
    program = backup._plain(Path(program_root), True)
    backup.verify(source)
    if backup._hash(source / backup.MANIFEST) != manifest_sha256:
        raise ValueError('Den valgte backup er ændret.')
    release = inspect_program(program)
    manifest = backup._manifest(source)
    require_contract(release['data_contract'],
                     {role: 1 for role, ref in manifest['profile']['components'].items() if ref is not None})
    target = backup._destination(destination, [program, source])
    with InstanceLock(target):
        backup._write(target / HOLD_FILE, HOLD)
        journal = {'format': 1, 'state': 'preparing', 'phase': 'copy_program',
                   'operation_id': operation_id, 'destination': str(target),
                   'created_utc': datetime.now(timezone.utc).isoformat(), 'credits': CREDIT,
                   'release_manifest_sha256': release['manifest_sha256'],
                   'source_manifest_sha256': manifest_sha256, 'seal_sha256': None}
        _save(target, journal)
        try:
            _copy_program(program, target / PROGRAM, release['manifest_sha256'])
            _phase(target, journal, 'snapshot')
            copy = target / BACKUP
            copy.mkdir(mode=0o700)
            files, directories = backup._scan(source, ignore_locks=False)
            for relative in sorted(directories):
                (copy / relative).mkdir(parents=True, exist_ok=True, mode=0o700)
            for relative in sorted(files):
                original = backup._plain(source / relative, False)
                with original.open('rb') as reader, (copy / relative).open('xb') as writer:
                    shutil.copyfileobj(reader, writer, length=1024 * 1024)
                    writer.flush(); os.fsync(writer.fileno())
                if os.name != 'nt':
                    (copy / relative).chmod(0o600)
            backup.verify(copy)
            if backup._hash(copy / backup.MANIFEST) != manifest_sha256:
                raise ValueError('Backupen ændredes under kopiering.')
            return _finish(target, journal)
        except BaseException:
            _failed(target, journal)
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'verify', 'resume'))
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--program-root', type=Path)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--source', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare' and args.profile and args.program_root and args.destination:
            result = prepare(_read(args.profile), args.program_root, args.destination)
        elif args.command == 'verify' and args.source:
            result = verify_prepared(args.source)
        elif args.command == 'resume' and args.source:
            result = resume_preparation(args.source)
        else:
            raise ValueError('Angiv profil, program og ny destination eller klargøringens kildemappe.')
    except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError, SyntaxError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
