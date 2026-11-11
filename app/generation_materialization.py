"""Materialise a verified generation into a permanently isolated layout.

The materializer copies only a verified :mod:`app.installation_update`
operation.  It never changes the active selector, removes restore holds,
starts code, or changes operating-system permissions.  The resulting tree is
therefore a held, reviewable candidate rather than an install or activation.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import uuid
from contextlib import nullcontext

from . import installation_backup as backup, installation_update as update
from .backup_guard import HOLD_FILE
from .instance import InstanceLock
from .release_compatibility import CREDIT, inspect_program


METADATA = 'materialized-generation.json'
MANIFEST = METADATA
FORMAT = 1
_OWNER = 'installation-owner.json'
_LASER = 'laserstation/laser-installation.json'


def _write_json(path, value):
    temporary = path.with_name('.materialized-' + uuid.uuid4().hex + '.tmp')
    try:
        backup._write(temporary, value)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path):
    return update._read(backup._plain(path, False))


def _json_bytes(value):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    return text.replace('\n', os.linesep).encode()


def _copy_tree(source, target):
    source = backup._plain(source, True)
    target.mkdir(parents=True, mode=0o700)
    files, directories = backup._scan(source, ignore_locks=False)
    for relative in sorted(directories):
        (target / relative).mkdir(parents=True, exist_ok=True, mode=0o700)
    for relative in sorted(files):
        src = backup._plain(source / relative, False)
        out = target / relative
        out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with src.open('rb') as reader, out.open('xb') as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        if backup._hash(src) != backup._hash(out):
            raise OSError('Kopien ændredes under materialisering.')
        if os.name != 'nt':
            out.chmod(0o600)


def _owner(candidate_root):
    path = candidate_root / _OWNER
    if not path.exists():
        raise ValueError('Den materialiserede skole mangler installation-owner.json.')
    owner = _read_json(path)
    owner_id = owner.get('ownerId')
    try:
        parsed_owner_id = uuid.UUID(owner_id) if isinstance(owner_id, str) else None
    except (ValueError, AttributeError):
        parsed_owner_id = None
    canonical_owner_id = (parsed_owner_id is not None and
                          (owner_id == parsed_owner_id.hex or
                           owner_id == str(parsed_owner_id)))
    if (type(owner.get('schemaVersion')) is not int or owner.get('schemaVersion') != 1
            or not canonical_owner_id
            or owner.get('product') != 'ElevhubServer'
            or owner.get('startupProtocol') != 'generation-v1'
            or owner.get('state') != 'installed'
            or owner.get('taskName') != 'ElevhubServer'
            or not isinstance(owner.get('installRoot'), str)
            or not isinstance(owner.get('dataRoot'), str)):
        raise ValueError('Kun installerede generation-v1 skoler kan materialiseres.')
    install_root = backup._plain(Path(owner['installRoot']), True)
    if backup._plain(Path(owner['dataRoot']), True) != candidate_root:
        raise ValueError('Ejerskabsfilens dataRoot matcher ikke kandidatens serverrod.')
    return owner, install_root


def _context(prepared, expected_operation_id):
    prepared = backup._plain(Path(prepared), True)
    journal = update._journal(prepared, expected_operation_id)
    report = update._verify_prepared(prepared, journal)
    profile, roots, components, config = update._copied_profile(prepared)
    if set(roots) != {'server'}:
        raise ValueError('Materialisering understøtter kun én serverrod.')
    candidate = roots['server']
    owner, install_root = _owner(candidate)
    return prepared, report, journal, profile, roots, components, config, candidate, owner, install_root


def _final_profile(profile, data_root):
    result = deepcopy(profile)
    result['roots']['server']['path'] = str(data_root)
    return result


def _school_values(candidate, new_root, profile):
    candidate_roots = {'server': candidate}
    return backup._school_relocations({'server': candidate}, candidate_roots,
                                      {'server': new_root}, profile)


def _copy_rebased_data(candidate, data_root, profile):
    _copy_tree(candidate, data_root)
    config = backup._ref(profile['config'], {'server': data_root})
    candidate_roots = {'server': candidate}
    old_config = backup._ref(profile['config'], candidate_roots)
    value = backup._relocate_config(_read_json(config), candidate_roots,
                                    {'server': data_root}, old_config.parent)
    _write_json(config, value)
    for path, value in _school_values(candidate, data_root, profile).items():
        relative = path.relative_to(data_root)
        _write_json(data_root / relative, value)


def _expected_data(prepared, profile, candidate, data_root):
    files, directories = backup._scan(candidate, ignore_locks=False)
    expected = {}
    for relative in files:
        expected[relative] = candidate / relative
    config = backup._ref(profile['config'], {'server': candidate})
    config_relative = config.relative_to(candidate).as_posix()
    candidate_roots = {'server': candidate}
    old_config = backup._ref(profile['config'], candidate_roots)
    expected_config = backup._relocate_config(_read_json(config), candidate_roots,
                                              {'server': data_root}, old_config.parent)
    expected[config_relative] = _json_bytes(expected_config)
    for path, value in _school_values(candidate, data_root, profile).items():
        expected[path.relative_to(data_root).as_posix()] = (
            _json_bytes(value))
    return expected, directories


def materialize(prepared_folder, destination, *, expected_operation_id):
    prepared_path = backup._plain(Path(prepared_folder), True)
    with InstanceLock(prepared_path):
        (prepared, report, journal, profile, roots, components, config,
         candidate, owner, install_root) = _context(prepared_path, expected_operation_id)
        old_roots, _, _, _ = backup._profile_layout(profile)
        target = backup._destination(destination, [prepared, install_root,
                                                    Path(owner['dataRoot']), *old_roots.values()])
        with InstanceLock(target):
            hold = _read_json(candidate / HOLD_FILE)
            _write_json(target / HOLD_FILE, hold)
            program = target / 'program'
            data = target / 'data'
            update._copy_program(prepared / update.PROGRAM, program,
                                 report['release']['manifest_sha256'])
            _copy_rebased_data(candidate, data, profile)
            files, directories = _inventory(target, {target / 'instance.lock'})
            final_profile = _final_profile(profile, data)
            metadata = {
                'format': FORMAT, 'credits': CREDIT, 'operation_id': journal['operation_id'],
                'prepared_root': str(prepared), 'source_seal_sha256': backup._hash(prepared / update.SEAL),
                'program_root': str(program), 'data_root': str(data),
                'manifest_sha256': report['release']['manifest_sha256'], 'profile': final_profile,
                'files': files, 'directories': directories,
                'holds': [row['path'] for row in files if row['path'].endswith('/' + HOLD_FILE)
                          or row['path'] == HOLD_FILE],
                'result': {'materialized': True, 'activation_ready': False},
            }
            _write_json(target / METADATA, metadata)
            return _verify_materialized_locked(target, expected_operation_id=expected_operation_id,
                                               prepared_lock_held=True)


def _known_operational_locks(folder, profile):
    """Return only lock paths declared by the final profile/layout."""
    roots, components, owners, config = backup._profile_layout(profile)
    paths = {folder / 'instance.lock', *(path / 'instance.lock' for path in owners)}
    for component in components.values():
        for child in ('supervisor', 'service'):
            path = component / child / 'instance.lock'
            if path.exists():
                paths.add(path)
    for enrollment in backup._enrollment_folders(roots):
        paths.update((enrollment / 'instance.lock', enrollment / 'operations/instance.lock'))
    paths.update(path / 'instance.lock' for path in backup._station_folders(roots))
    registry = backup._registry_lock(backup._check_config(config, roots, components), config.parent, roots)
    if registry is not None:
        paths.add(registry)
    return paths


def _inventory(folder, ignored_files=()):
    files, directories = backup._scan(folder, ignore_locks=False, ignored_files=ignored_files)
    entries = [{'path': relative, 'size': signature[2],
                'sha256': backup._hash(folder / relative)}
               for relative, signature in sorted(files.items())
               if relative not in {MANIFEST, 'instance.lock'}]
    directories = sorted(directories)
    return entries, directories


def _verify_materialized_locked(folder, *, expected_operation_id, prepared_lock_held=False):
    folder = backup._plain(Path(folder), True)
    metadata = _read_json(folder / METADATA)
    required = {'format', 'credits', 'operation_id', 'prepared_root', 'source_seal_sha256',
                'program_root', 'data_root', 'manifest_sha256', 'profile', 'files',
                'directories', 'holds', 'result'}
    if (set(metadata) != required or type(metadata['format']) is not int
            or metadata['format'] != FORMAT or metadata['credits'] != CREDIT
            or metadata['operation_id'] != expected_operation_id
            or metadata['result'] != {'materialized': True, 'activation_ready': False}):
        raise ValueError('Ugyldig materialiseringsbinding.')
    prepared = backup._plain(Path(metadata['prepared_root']), True)
    lock = nullcontext() if prepared_lock_held else InstanceLock(prepared)
    with lock:
        return _verify_materialized_body(folder, metadata, prepared,
                                         expected_operation_id=expected_operation_id)


def _verify_materialized_body(folder, metadata, prepared, *, expected_operation_id):
    journal = update._journal(prepared, expected_operation_id)
    report = update._verify_prepared(prepared, journal)
    if metadata['source_seal_sha256'] != backup._hash(prepared / update.SEAL):
        raise ValueError('Klargøringens kontrolsum er ændret.')
    program = backup._plain(Path(metadata['program_root']), True)
    data = backup._plain(Path(metadata['data_root']), True)
    if program != folder / 'program' or data != folder / 'data':
        raise ValueError('Materialiseringens stier matcher ikke layoutet.')
    if (inspect_program(program)['manifest_sha256'] != metadata['manifest_sha256']
            or metadata['manifest_sha256'] != report['release']['manifest_sha256']):
        raise ValueError('Materialiseret program matcher ikke manifestet.')
    profile, roots, components, config = update._copied_profile(prepared)
    final_profile = _final_profile(profile, data)
    if metadata['profile'] != final_profile:
        raise ValueError('Materialiseringsprofilen matcher ikke den endelige datarod.')
    files, directories = _inventory(folder, _known_operational_locks(folder, final_profile))
    if files != metadata['files'] or directories != metadata['directories']:
        raise ValueError('Materialiseringens filoversigt er ændret.')
    expected_holds = [row['path'] for row in files if row['path'].endswith('/' + HOLD_FILE)
                      or row['path'] == HOLD_FILE]
    if metadata['holds'] != expected_holds:
        raise ValueError('Materialiseringens holdoversigt er ændret.')
    candidate = roots['server']
    _owner(candidate)
    expected, directories = _expected_data(prepared, profile, candidate, data)
    actual, actual_directories = backup._scan(
        data, ignore_locks=False, ignored_files=_known_operational_locks(folder, final_profile))
    if actual_directories != directories or set(actual) != set(expected):
        raise ValueError('Materialiseret datafiloversigt er ændret.')
    for relative, value in expected.items():
        path = data / relative
        if isinstance(value, Path):
            if (path.stat().st_size != value.stat().st_size
                    or backup._hash(path) != backup._hash(value)):
                raise ValueError('Materialiseret data matcher ikke kandidaten.')
        elif path.read_bytes() != value:
            raise ValueError('Materialiseret data matcher ikke kandidaten.')
    if _read_json(folder / HOLD_FILE) != _read_json(candidate / HOLD_FILE):
        raise ValueError('Materialiseringsspærren mangler.')
    return {'operation_id': expected_operation_id, 'program_root': str(program),
            'data_root': str(data), 'manifest_sha256': metadata['manifest_sha256'],
            'profile': final_profile, 'materialized': True, 'activation_ready': False}


def verify_materialized(folder, *, expected_operation_id):
    """Verify a held materialization while holding its root instance lock."""
    folder = backup._plain(Path(folder), True)
    with InstanceLock(folder):
        return _verify_materialized_locked(folder, expected_operation_id=expected_operation_id)

