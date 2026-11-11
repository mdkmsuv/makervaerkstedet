"""Offline data backup and isolated restore for a complete makerspace server.

All declared services must use the lifetime instance-lock protocol. This module
never stops services, starts hardware, replaces live data, or deletes old copies.
The protected backup contains private data and is not a distribution artifact.
"""
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import argparse
import copy
import csv
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import subprocess
import sys

from .backup_guard import HOLD_FILE, PROTOCOL_FILE, assert_not_restored
from .backup_databases import snapshot_database, validate_database, protect_restored_database
from .instance import InstanceLock
from .generation_relocation import relocate_school_files

FORMAT = 1
MANIFEST = 'backup-manifest.json'
RESTORE = 'restore-report.json'
ROLES = {'server', 'hub', 'print', 'laser', 'laser_station'}
DATABASES = {'hub': 'makerspace.sqlite3', 'print': 'queue.sqlite3', 'laser': 'afleveringer.sqlite3'}
_ID = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')
_HASH = re.compile(r'[a-f0-9]{64}\Z')
MAX_FILES = 200_000
MAX_MANIFEST = 64 * 1024 * 1024


def _json(path, maximum=MAX_MANIFEST):
    if path.stat().st_size > maximum:
        raise ValueError('Metadatafilen er for stor.')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Metadata skal være et JSON-objekt.')
    return value


def _relative(value, allow_dot=False):
    if value == '.' and allow_dot:
        return value
    if (not isinstance(value, str) or not value or any(c in value for c in '\\:*?"<>|')
            or any(not c.isprintable() for c in value)):
        raise ValueError('Ugyldig relativ sti.')
    parts = value.split('/')
    if any(part in ('', '.', '..') or part.endswith((' ', '.')) for part in parts):
        raise ValueError('Stien skal blive inden for datamappen.')
    # Avoid Windows device aliases and alternate streams on every platform.
    for part in parts:
        stem = part.split('.')[0].upper()
        if stem in {'CON', 'PRN', 'AUX', 'NUL'} or re.fullmatch(r'(COM|LPT)[1-9]', stem):
            raise ValueError('Reserveret filnavn.')
    return PurePosixPath(value).as_posix()


def _plain(path, directory=None):
    """Reject links/reparse points in both the leaf and all ancestors."""
    path = Path(path).absolute()
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Links og reparse points er ikke tilladt i en backupsti.')
    mode = path.stat().st_mode
    if directory is True and not stat.S_ISDIR(mode):
        raise ValueError('Datamappen mangler.')
    if directory is False and not stat.S_ISREG(mode):
        raise ValueError('Kilden skal være en almindelig fil.')
    return path.resolve(strict=True)


def _ref(value, roots):
    if (not isinstance(value, dict) or set(value) != {'root', 'path'}
            or not isinstance(value['root'], str) or value['root'] not in roots):
        raise ValueError('Ugyldig reference til en datamappe.')
    relative = _relative(value['path'], allow_dot=True)
    return roots[value['root']] / relative


def _protocol(folder, role):
    path = _plain(folder / PROTOCOL_FILE, False)
    metadata = _json(path, 1024)
    if type(metadata.get('protocol')) is not int or metadata != {'protocol': 1, 'role': role}:
        raise ValueError('Tjenesten understøtter ikke den krævede offline-backup. Opgrader og kontrollér installationen først.')


def _profile_layout(value):
    """Validate metadata without requiring the original installation to exist."""
    if (not isinstance(value, dict) or set(value) != {'format', 'roots', 'components', 'config'}
            or type(value['format']) is not int or value['format'] != FORMAT):
        raise ValueError('Ukendt installationsprofil.')
    if not isinstance(value['roots'], dict) or not 1 <= len(value['roots']) <= 32:
        raise ValueError('Angiv installationens lokale datamapper.')
    roots = {}
    owners = {}
    for name, entry in value['roots'].items():
        if (not isinstance(name, str) or len(name) > 40 or not _ID.fullmatch(name)
                or not isinstance(entry, dict) or set(entry) != {'path', 'role'}
                or not isinstance(entry['role'], str) or entry['role'] not in ROLES):
            raise ValueError('Ugyldig datamapperolle.')
        raw = entry['path']
        if (not isinstance(raw, str) or not raw or raw.startswith(('\\\\', '//'))
                or not Path(raw).is_absolute()):
            raise ValueError('Brug en absolut lokal datasti.')
        path = Path(os.path.abspath(raw))
        if any(path == old or path.is_relative_to(old) or old.is_relative_to(path) for old in roots.values()):
            raise ValueError('Datamapper må ikke overlappe hinanden.')
        roots[name] = path
        owners[path] = entry['role']
    components = value['components']
    if not isinstance(components, dict) or set(components) != set(DATABASES):
        raise ValueError('Angiv hub, print og laser; ubrugte køer angives som null.')
    if components['hub'] is None:
        raise ValueError('Hubdata skal indgå i serverbackup.')
    paths = {}
    for role, ref in components.items():
        if ref is None:
            continue
        path = _ref(ref, roots)
        if (any(path == other or path.is_relative_to(other) or other.is_relative_to(path)
                for other in paths.values()) or path in owners and owners[path] != role):
            raise ValueError('Datamappen har flere roller.')
        paths[role] = path
        owners[path] = role
    config = _ref(value['config'], roots)
    if config in roots.values() or config.name in {*DATABASES.values(), PROTOCOL_FILE, HOLD_FILE, 'instance.lock'}:
        raise ValueError('Hubkonfigurationen skal være JSON.')
    return roots, paths, owners, config


def _profile(value, *, allow_restored=False):
    roots, paths, owners, config = _profile_layout(value)
    for folder in roots.values():
        _plain(folder, True)
        if not allow_restored:
            assert_not_restored(folder)
    for role, path in paths.items():
        _plain(path, True)
        if not allow_restored:
            assert_not_restored(path)
        _plain(path / DATABASES[role], False)
    _plain(config, False)
    for folder, role in owners.items():
        _protocol(folder, role)
    return roots, paths, owners, config


def _covered_file(raw, base, roots):
    if not isinstance(raw, str) or not raw:
        raise ValueError('En nødvendig datareference mangler.')
    path = _plain(base / raw, False)
    if not any(path.is_relative_to(root) for root in roots.values()):
        raise ValueError('En privat konfigurationsfil ligger uden for backupens datamapper.')
    return path


def _check_config(config_path, roots, components):
    config = _json(config_path, 1024 * 1024)
    return _check_config_value(config, config_path.parent, roots, components)


def _check_config_value(config, base, roots, components):
    if config.get('usikker_test') is not True:
        tls = config.get('tls')
        if not isinstance(tls, dict):
            raise ValueError('TLS-konfigurationen mangler i serverbackup.')
        for field in ('certifikat', 'noegle', 'ca'):
            _covered_file(tls.get(field), base, roots)
        _covered_file(config.get('enhedsregister'), base, roots)
    for setting, role, filename in (('makerspace_printers', 'print', 'makerspace-bridge.key'),
                                     ('makerspace_laser_queue', 'laser', 'makerspace-laser.key')):
        connection = config.get(setting)
        if connection is not None:
            if role not in components or not isinstance(connection, dict):
                raise ValueError('En konfigureret kø mangler i backupen.')
            key = _covered_file(connection.get('key_file'), base, roots)
            if key != (components[role] / filename).resolve():
                raise ValueError('Køens private nøglereference passer ikke til datamappen.')
    return config


def _signature(path):
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _scan(root, ignore_locks=True, ignored_files=()):
    files, folders = {}, set()
    for directory, dirs, names in os.walk(root, followlinks=False):
        directory = _plain(Path(directory), True)
        for name in dirs:
            child = _plain(directory / name, True)
            folders.add(_relative(child.relative_to(root).as_posix()))
            if len(folders) > MAX_FILES:
                raise ValueError('Datamappen har for mange undermapper til denne backup.')
        for name in names:
            path = _plain(directory / name, False)
            relative = _relative(path.relative_to(root).as_posix())
            if name == 'instance.lock' and ignore_locks or path in ignored_files:
                continue
            files[relative] = _signature(path)
            if len(files) > MAX_FILES:
                raise ValueError('Datamappen har for mange filer til denne backup.')
    return files, folders


def _stable_sqlite_scan(scan, databases):
    """Remove only volatile sidecars for known SQLite database files.

    ``databases`` contains relative paths whose contents were already
    header-verified as SQLite.  Reader SHM indexes are volatile; a zero-byte
    WAL can be created by a read-only probe.  Non-empty WALs, journals and
    unrelated sidecars remain part of the inventory.
    """
    files, folders = scan
    databases = set(databases)
    volatile = {relative + '-shm' for relative in databases}
    volatile |= {relative + '-wal' for relative in databases
                 if relative + '-wal' in files and files[relative + '-wal'][2] == 0}
    return ({relative: signature for relative, signature in files.items()
             if relative not in volatile}, folders)


def _hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name != 'nt':
        path.chmod(0o600)


def _private_directory(path):
    if os.name != 'nt':
        path.chmod(0o700)
        return
    system = Path(os.environ['SystemRoot']) / 'System32'
    options = {'capture_output': True, 'text': True, 'check': True, 'timeout': 15,
               'creationflags': subprocess.CREATE_NO_WINDOW}
    identity = subprocess.run([str(system / 'whoami.exe'), '/user', '/fo', 'csv', '/nh'], **options)
    rows = list(csv.reader(identity.stdout.splitlines()))
    sid = rows[0][1] if len(rows) == 1 and len(rows[0]) == 2 else ''
    if not re.fullmatch(r'S-1-[0-9-]+', sid):
        raise OSError('Backupmappens private adgang kunne ikke oprettes.')
    try:
        subprocess.run([str(system / 'icacls.exe'), str(path), '/inheritance:r', '/grant:r',
                        f'*{sid}:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F'], **options)
    except subprocess.SubprocessError:
        raise OSError('Backupmappens private adgang kunne ikke oprettes.') from None


def _destination(value, source_roots):
    if os.fspath(value).startswith(('\\\\', '//')):
        raise ValueError('Brug en lokal destination til backup og gendannelse.')
    path = Path(value).absolute()
    parent = _plain(path.parent, True)
    path = parent / path.name
    _relative(path.name)
    if path.exists() or path.is_symlink():
        raise FileExistsError('Destinationen skal være en ny mappe.')
    if any(path.is_relative_to(root) or root.is_relative_to(path) for root in source_roots):
        raise ValueError('Backupdestination og kilde må ikke overlappe.')
    path.mkdir(mode=0o700)
    _private_directory(path)
    return path


@contextmanager
def offline_profile(profile):
    """Hold every declared service lock during offline maintenance.

    Returns a normalized profile and validated layout; never stops services.
    Other maintenance operations must keep this context open until finished.
    """
    with _locked_profile(profile, allow_restored=False) as locked:
        yield locked


@contextmanager
def _locked_profile(profile, *, allow_restored):
    """Internal activation also leases verified, still-isolated destination data.

    Permission to inspect a restored layout does not remove its startup holds.
    Public snapshot/restore compatibility checks always use offline_profile.
    """
    profile = copy.deepcopy(profile)
    roots, components, owners, config = _profile(profile, allow_restored=allow_restored)
    for name, root in roots.items():
        profile['roots'][name]['path'] = str(root)
    with ExitStack() as locks:
        # No service is stopped by this tool. Nonblocking locks make a live
        # installation fail before any destination is created.
        lock_folders = set(owners)
        initial_config = _check_config(config, roots, components)
        registry_lock = _registry_lock(initial_config, config.parent, roots)
        enrollment = _enrollment_folders(roots)
        stations = _station_folders(roots)
        lock_folders.update(stations)
        for folder in enrollment:
            lock_folders.update((folder, _plain(folder / 'operations', True)))
        for folder in components.values():
            for child in ('supervisor', 'service'):
                if (folder / child / 'instance.lock').exists():
                    lock_folders.add(_plain(folder / child, True))
        for folder in sorted(lock_folders, key=lambda p: str(p).casefold()):
            locks.enter_context(InstanceLock(folder))
        if registry_lock is not None:
            locks.enter_context(InstanceLock(registry_lock.parent, filename=registry_lock.name))
        for folder, role in owners.items():
            _protocol(folder, role)
        if _enrollment_folders(roots) != enrollment:
            raise ValueError('Enrollmentopsætningen ændredes under vedligeholdelseskontrollen.')
        for folder in enrollment:
            _protocol(folder, 'enrollment')
        if _station_folders(roots) != stations:
            raise ValueError('Laserstationens datasti ændredes under vedligeholdelseskontrollen.')
        if _check_config(config, roots, components) != initial_config:
            raise ValueError('Konfigurationen ændredes under vedligeholdelseskontrollen.')
        yield profile, roots, components, config


def _station_folders(roots):
    """The laser station leases its plate directory, not its config directory."""
    result = set()
    for root in roots.values():
        config = root / 'laserstation/laser-installation.json'
        if config.exists() or config.is_symlink():
            value = _json(_plain(config, False), 64 * 1024)
            raw = value.get('plates')
            if not isinstance(raw, str) or not Path(raw).is_absolute():
                raise ValueError('Laserstationens backup kræver en eksplicit absolut plademappe.')
            folder = _plain(Path(raw), True)
            if not any(folder.is_relative_to(covered) for covered in roots.values()):
                raise ValueError('Laserstationens plademappe ligger uden for backupens datarødder.')
            _protocol(folder, 'laser_station')
            result.add(folder)
    return result


def _enrollment_folders(roots):
    """School enrollment is a separate writer, even when the hub is stopped.

    Its server lifetime and short admin/HTTP operations have distinct locks:
    issuing tickets must remain possible while the server is running. A
    school profile without the protocol is an unsupported legacy writer,
    never evidence that no writer exists.
    """
    result = set()
    for root in roots.values():
        school = root / 'school.json'
        folder = root / 'security/enrollment'
        if school.exists() or school.is_symlink() or folder.exists() or folder.is_symlink():
            folder = _plain(folder, True)
            _plain(folder / 'operations', True)
            _protocol(folder, 'enrollment')
            result.add(folder)
    return result


def _registry_lock(config, base, roots):
    raw = config.get('enhedsregister')
    if raw is None and config.get('usikker_test') is True:
        return None
    return _covered_file(raw, base, roots).with_suffix('.lock')


def snapshot(profile, destination):
    with offline_profile(profile) as (profile, roots, components, config):
        return _snapshot_locked(profile, roots, components, config, destination)


def _snapshot_locked(profile, roots, components, config, destination):
    """Internal: caller must retain the complete offline_profile context."""
    registry_lock = _registry_lock(_check_config(config, roots, components), config.parent, roots)
    ignored = {registry_lock} if registry_lock is not None else set()
    # SQLite may create/update its shared-memory reader index while a
    # read-only snapshot is being opened or closed.  It is not database
    # content; WAL and rollback-journal files remain part of the inventory.
    # Determine the exception from the actual SQLite header, rather than
    # ignoring arbitrary ``*.shm`` files.
    raw_scans = {name: _scan(root, ignored_files=ignored) for name, root in roots.items()}
    sqlite_databases = {}
    scans = {}
    for name, (files, folders) in raw_scans.items():
        database_files = set()
        root = roots[name]
        for relative in files:
            with (root / relative).open('rb') as stream:
                if stream.read(16) == b'SQLite format 3\x00':
                    database_files.add(relative)
        # The reader can create this exact sidecar after the initial scan, so
        # predict it even when it was absent.  This remains limited to the
        # sibling name of a header-verified SQLite database.
        sqlite_databases[name] = database_files
        scans[name] = _stable_sqlite_scan((files, folders), database_files)
    if any(Path(path).name == HOLD_FILE for files, _ in scans.values() for path in files):
        raise ValueError('En isoleret gendannelse kan ikke bruges som aktiv backupkilde.')
    if sum(len(files) + len(folders) for files, folders in scans.values()) > MAX_FILES:
        raise ValueError('Installationen har for mange filer til denne backup.')
    target = _destination(destination, roots.values())
    role_paths = {folder / DATABASES[role]: role for role, folder in components.items()}
    entries, directories = [], []
    for name, root in roots.items():
        files, folders = raw_scans[name]
        copied_root = target / 'roots' / name
        copied_root.mkdir(parents=True, mode=0o700)
        for relative in sorted(folders):
            (copied_root / relative).mkdir(parents=True, exist_ok=True, mode=0o700)
            directories.append(f'roots/{name}/{relative}')
        database_files = sqlite_databases[name]
        sidecars = {db + suffix for db in database_files for suffix in ('-wal', '-shm', '-journal')}
        for relative in sorted(files):
            if relative in sidecars:
                continue
            source, out = root / relative, copied_root / relative
            if _signature(source) != files[relative]:
                raise OSError('Kildedata blev ændret under backup.')
            metadata = None
            role = role_paths.get(source)
            if role is not None or relative in database_files:
                metadata = snapshot_database(source, out, role=role)
            else:
                with source.open('rb') as reader, out.open('xb') as writer:
                    shutil.copyfileobj(reader, writer, length=1024 * 1024)
                    writer.flush(); os.fsync(writer.fileno())
                if _signature(source) != files[relative] or _hash(source) != _hash(out):
                    raise OSError('Kildedata blev ændret under backup.')
            if os.name != 'nt': out.chmod(0o600)
            entries.append({'path': f'roots/{name}/{relative}', 'size': out.stat().st_size,
                            'sha256': _hash(out), 'database_role': role,
                            'sqlite': metadata is not None})
    # Includes WAL sidecars: a writer that ignores service locks cannot
    # silently change the database/file inventory during the snapshot.
    def stable_scan(name, root):
        files, folders = _scan(root, ignored_files=ignored)
        return _stable_sqlite_scan((files, folders), sqlite_databases[name])

    if any(stable_scan(name, root) != scans[name] for name, root in roots.items()):
        raise OSError('Kildedata blev ændret under backup. Kopien er ufuldstændig.')
    manifest = {'format': FORMAT, 'product': 'Makerværkstedet',
                'credits': 'Kreditering: Klostermarksskolen.',
                'created_utc': datetime.now(timezone.utc).isoformat(),
                'profile': profile, 'files': entries, 'directories': sorted(directories)}
    _write(target / MANIFEST, manifest)
    return verify(target)


def _manifest(folder):
    value = _json(_plain(folder / MANIFEST, False))
    if (set(value) != {'format', 'product', 'credits', 'created_utc', 'profile', 'files', 'directories'}
            or type(value['format']) is not int or value['format'] != FORMAT
            or value['product'] != 'Makerværkstedet' or value['credits'] != 'Kreditering: Klostermarksskolen.'
            or not isinstance(value['files'], list) or len(value['files']) > MAX_FILES
            or not isinstance(value['directories'], list) or len(value['directories']) > MAX_FILES
            or not isinstance(value['created_utc'], str)):
        raise ValueError('Ukendt eller ugyldig backupmanifest.')
    return value


def verify(folder):
    folder = _plain(Path(folder), True)
    manifest = _manifest(folder)
    return _verify(folder, manifest)


def _verify(folder, manifest):
    profile = manifest['profile']
    old_roots, _, _, old_config = _profile_layout(profile)
    expected, seen = {MANIFEST}, set()
    roots = profile['roots']
    for name, entry in roots.items():
        _plain(folder / 'roots' / name, True)
        _protocol(folder / 'roots' / name, entry['role'])
    for row in manifest['files']:
        if (not isinstance(row, dict) or set(row) != {'path', 'size', 'sha256', 'database_role', 'sqlite'}
                or type(row['size']) is not int or row['size'] < 0
                or not isinstance(row['sha256'], str) or not _HASH.fullmatch(row['sha256'])
                or type(row['sqlite']) is not bool
                or row['database_role'] is not None and (not isinstance(row['database_role'], str) or row['database_role'] not in DATABASES)):
            raise ValueError('Ugyldig filliste.')
        relative = _relative(row['path'])
        parts = relative.split('/')
        if len(parts) < 3 or parts[0] != 'roots' or parts[1] not in roots or relative.casefold() in seen:
            raise ValueError('Ugyldig eller dubleret backupsti.')
        seen.add(relative.casefold()); expected.add(relative)
        path = _plain(folder / relative, False)
        if path.stat().st_size != row['size'] or _hash(path) != row['sha256']:
            raise ValueError('Backupfilens størrelse eller kontrolsum passer ikke.')
        if row['sqlite']:
            validate_database(path, row['database_role'])
        elif row['database_role'] is not None:
            raise ValueError('Databasen mangler SQLite-markering.')
    expected_directories = {'roots', *(f'roots/{name}' for name in roots)}
    for relative in manifest['directories']:
        relative = _relative(relative)
        parts = relative.split('/')
        if (len(parts) < 3 or parts[0] != 'roots' or parts[1] not in roots
                or relative.casefold() in seen):
            raise ValueError('Ugyldig datamappe.')
        seen.add(relative.casefold())
        expected_directories.add(relative)
        _plain(folder / relative, True)
    actual, actual_directories = _scan(folder, ignore_locks=False)
    if set(actual) != expected or actual_directories != expected_directories:
        raise ValueError('Backupen indeholder manglende eller ekstra filer eller mapper.')
    components = profile['components']
    if not isinstance(components, dict) or set(components) != set(DATABASES) or components['hub'] is None:
        raise ValueError('Backupen mangler komponentoversigten.')
    new_roots = {name: folder / 'roots' / name for name in roots}
    for role, ref in components.items():
        if ref is None: continue
        component = _ref(ref, new_roots)
        _protocol(component, role)
        db = component / DATABASES[role]
        relative = db.relative_to(folder).as_posix()
        if not any(row['path'] == relative and row['database_role'] == role for row in manifest['files']):
            raise ValueError('En nødvendig database mangler i backupen.')
    config = _plain(_ref(profile['config'], new_roots), False)
    relocated = _relocate_config(_json(config), old_roots, new_roots, old_config.parent)
    # Validate private references against the actual copied files, without
    # reading or requiring the source installation to still exist.
    _check_config_value(relocated, config.parent, new_roots,
        {role: _ref(ref, new_roots) for role, ref in components.items() if ref is not None})
    return {'ok': True, 'files': len(manifest['files']), 'bytes': sum(row['size'] for row in manifest['files']),
            'components': [role for role, ref in components.items() if ref is not None],
            'created_utc': manifest['created_utc'], 'path': str(folder)}


def _relocate_config(config, old_roots, new_roots, old_base):
    def relocate(raw):
        if not isinstance(raw, str): raise ValueError('Ugyldig konfigurationssti.')
        source = Path(raw) if Path(raw).is_absolute() else old_base / raw
        source = Path(os.path.abspath(source))
        for name, old in old_roots.items():
            if source.is_relative_to(old):
                return str(new_roots[name] / source.relative_to(old))
        raise ValueError('En privat konfigurationssti ligger uden for kopien.')
    if isinstance(config.get('tls'), dict):
        for key in ('certifikat', 'noegle', 'ca'):
            if key in config['tls']: config['tls'][key] = relocate(config['tls'][key])
    if 'enhedsregister' in config: config['enhedsregister'] = relocate(config['enhedsregister'])
    for setting in ('makerspace_printers', 'makerspace_laser_queue'):
        if config.get(setting) is not None:
            config[setting]['key_file'] = relocate(config[setting]['key_file'])
    return config


def _school_relocations(source_roots, old_roots, new_roots, profile):
    """Known generation-v1 school files, read from an intact data copy.

    Legacy owner files retain their original meaning. No machine-local key
    store or executable is copied, opened or rewritten by this step.
    """
    result = {}
    for name, source in source_roots.items():
        if profile['roots'][name]['role'] != 'server':
            continue
        owner = source / 'installation-owner.json'
        if not owner.exists():
            continue
        values = {'installation-owner.json': _json(_plain(owner, False))}
        if 'startupProtocol' not in values['installation-owner.json']:
            continue
        if values['installation-owner.json']['startupProtocol'] != 'generation-v1':
            raise ValueError('Ejerskabsfilen har en ukendt opstartsprotokol.')
        station = source / 'laserstation/laser-installation.json'
        if station.exists():
            values['laserstation/laser-installation.json'] = _json(_plain(station, False))
        changed = relocate_school_files(old_roots[name], new_roots[name], values)
        result.update({new_roots[name] / relative: value for relative, value in changed.items()})
    return result


def restore(folder, destination):
    folder = _plain(Path(folder), True)
    manifest_path = _plain(folder / MANIFEST, False)
    if manifest_path.stat().st_size > MAX_MANIFEST:
        raise ValueError('Metadatafilen er for stor.')
    manifest_hash = _hash(manifest_path)
    manifest = _manifest(folder)
    if _hash(folder / MANIFEST) != manifest_hash:
        raise ValueError('Backupmanifestet blev ændret under læsning.')
    _verify(folder, manifest)
    profile = manifest['profile']
    target = _destination(destination, [folder])
    hold = {'format': 1, 'reason': 'isolated_restore', 'credits': 'Kreditering: Klostermarksskolen.'}
    _write(target / HOLD_FILE, hold)
    old_roots, _, _, old_config = _profile_layout(profile)
    new_roots = {name: target / 'roots' / name for name in old_roots}
    for root in new_roots.values():
        root.mkdir(parents=True, mode=0o700)
    for relative in manifest['directories']:
        (target / relative).mkdir(parents=True, exist_ok=True, mode=0o700)
    for row in manifest['files']:
        source, out = folder / row['path'], target / row['path']
        _plain(source, False)
        with source.open('rb') as reader, out.open('xb') as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush(); os.fsync(writer.fileno())
        if _hash(out) != row['sha256']:
            raise OSError('Backupen blev ændret under gendannelsen.')
        if os.name != 'nt': out.chmod(0o600)
    if _hash(folder / MANIFEST) != manifest_hash:
        raise OSError('Backupmanifestet blev ændret under gendannelsen.')
    # Same keys and receipts; only access revocation and queue pause change.
    for role, ref in profile['components'].items():
        if ref is None: continue
        component = _ref(ref, new_roots)
        _write(component / HOLD_FILE, hold)
        protect_restored_database(component / DATABASES[role], role)
    for root in new_roots.values():
        if not (root / HOLD_FILE).exists(): _write(root / HOLD_FILE, hold)
    config_path = _ref(profile['config'], new_roots)
    config = _relocate_config(_json(config_path), old_roots, new_roots, old_config.parent)
    config_temp = config_path.with_name('.restored-config.tmp')
    _write(config_temp, config)
    os.replace(config_temp, config_path)
    for path, value in _school_relocations(new_roots, old_roots, new_roots, profile).items():
        temporary = path.with_name('.relocated-school.tmp')
        _write(temporary, value)
        os.replace(temporary, path)
    components = {role: _ref(ref, new_roots) for role, ref in profile['components'].items() if ref is not None}
    _check_config(config_path, new_roots, components)
    report = {'format': 1, 'source_created_utc': manifest['created_utc'],
              'source_manifest_sha256': manifest_hash,
              'credits': 'Kreditering: Klostermarksskolen.', 'isolated': True,
              'agents_revoked': True, 'print_paused': 'print' in components,
              'roots': {name: str(path) for name, path in new_roots.items()},
              'config': str(config_path)}
    _write(target / RESTORE, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('snapshot', 'verify', 'restore'))
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'snapshot' and args.profile is not None and args.destination is not None:
            result = snapshot(_json(_plain(args.profile, False)), args.destination)
        elif args.command == 'verify' and args.source is not None:
            result = verify(args.source)
        elif args.command == 'restore' and args.source is not None and args.destination is not None:
            result = restore(args.source, args.destination)
        else:
            raise ValueError('Angiv profil/kilde og en ny destination til den valgte handling.')
    except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
