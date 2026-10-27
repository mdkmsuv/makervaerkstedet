"""Bind offline update preparation to an installed program/data pair.

This begins a durable maintenance session; it does not activate a release,
stop services, delete data, remove restore holds or start hardware. New
generation-aware starts remain blocked until this operation is cancelled or
a future activation transaction completes. Old and new program/data copies
are retained, including on interruption.
"""
import argparse
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import uuid

from . import installation_backup as backup, installation_update as update
from .generation_lock import GenerationLock
from .generation_runtime import validate_generation
from .generation_selection import ACTIVE, LOCK, TRANSITION, _present, _read, _unique, validate_record
from .instance import InstanceLock
from .release_compatibility import CREDIT, inspect_program
from .data_contract import inspect_component, require_contract

JOURNAL = 'generation-operation.json'
SOURCE = 'source-selection.json'
FINGERPRINT = 'source-data.json'
PREVIOUS = 'previous-program'
PREPARED = 'prepared'
_DIGEST = re.compile(r'[a-f0-9]{64}\Z')


def _json(path):
    return update._read(backup._plain(path, False))


def _atomic(path, value):
    if _present(path):
        backup._plain(path, False)
    temp = path.with_name('.generation-write-' + uuid.uuid4().hex + '.tmp')
    try:
        backup._write(temp, value)
        _replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _replace(source, destination):
    """Same-directory publication with the platform's write-through support.

    The caller has already fsynced complete file contents. This does not assert
    power-loss guarantees for a storage device that ignores flush requests.
    Windows: https://learn.microsoft.com/windows/win32/api/winbase/nf-winbase-movefileexw
    """
    if source.parent != destination.parent:
        raise ValueError('Atomisk publicering kræver samme mappe.')
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        move = ctypes.WinDLL('kernel32', use_last_error=True).MoveFileExW
        move.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        move.restype = wintypes.BOOL
        # REPLACE_EXISTING | WRITE_THROUGH, never COPY_ALLOWED.
        if not move(str(source), str(destination), 0x1 | 0x8):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        os.replace(source, destination)
        descriptor = os.open(str(destination.parent), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _selection(runtime, expected_id):
    path = backup._plain(runtime / 'control' / ACTIVE, False)
    raw = _read(path)
    record = validate_record(json.loads(raw.decode('utf-8'), object_pairs_hook=_unique), runtime)
    if record['installation_id'] != expected_id:
        raise ValueError('Versionsvalget tilhører en anden installation.')
    owner = _json(Path(record['data_root']) / 'installation-owner.json')
    if (type(owner.get('schemaVersion')) is not int or owner['schemaVersion'] != 1
            or owner.get('product') != 'ElevhubServer' or owner.get('state') != 'installed'
            or owner.get('startupProtocol') != 'generation-v1'
            or owner.get('taskName') != 'ElevhubServer'
            or any(not isinstance(owner.get(key), str) or not owner[key]
                   for key in ('ownerId', 'installRoot', 'dataRoot'))
            or uuid.UUID(owner.get('ownerId', '')).hex != expected_id
            or backup._plain(Path(owner.get('installRoot', '')), True) != runtime
            or backup._plain(Path(owner.get('dataRoot', '')), True) != Path(record['data_root'])):
        raise ValueError('Versionsskift kræver en færdig, matchende generation-v1 installation.')
    validate_generation(record['program_root'], runtime, record['manifest_sha256'], 'server')
    return record, raw


def _profile(record, contract):
    folders = {'hub': 'hub', 'print': 'printvaerksted', 'laser': 'designvaerksted'}
    return {'format': 1, 'roots': {'server': {'path': record['data_root'], 'role': 'server'}},
            'components': {role: {'root': 'server', 'path': folder} if role in contract else None
                           for role, folder in folders.items()},
            'config': {'root': 'server', 'path': 'konfiguration.json'}}


def _fingerprint(locked):
    profile, roots, components, config = locked
    registry_lock = backup._registry_lock(backup._check_config(config, roots, components), config.parent, roots)
    ignored = {registry_lock} if registry_lock is not None else set()
    result = {}
    for name, root in roots.items():
        before = backup._scan(root, ignored_files=ignored)
        # A read-only SQLite probe can change the reader index and create an
        # empty WAL. Normalize only those exact sidecars of a header-verified
        # database, dynamically on each scan so a WAL that grows is detected.
        sqlite_databases = set()
        for relative in before[0]:
            with (root / relative).open('rb') as stream:
                if stream.read(16) == b'SQLite format 3\x00':
                    sqlite_databases.add(relative)
        before = backup._stable_sqlite_scan(before, sqlite_databases)
        files = {relative: {'size': signature[2], 'sha256': backup._hash(root / relative)}
                 for relative, signature in sorted(before[0].items())}
        after = backup._stable_sqlite_scan(backup._scan(root, ignored_files=ignored), sqlite_databases)
        if after != before:
            raise ValueError('Kildedata ændredes under versionskontrollen.')
        result[name] = {'files': files, 'directories': sorted(before[1])}
    return result


def _transition(folder, journal):
    value = {'format': 1, 'operation': 'prepare_update', 'operation_id': journal['operation_id'],
            'folder': str(folder), 'installation_id': journal['installation_id'],
            'source_selection_sha256': journal['source_selection_sha256'],
            'source_data_sha256': journal['source_data_sha256'],
            'next_program': journal['next_program'], 'next_manifest_sha256': journal['next_manifest_sha256']}
    if journal.get('rollback_of') is not None:
        value['rollback_of'] = journal['rollback_of']
    if journal.get('restore_of') is not None:
        value['restore_of'] = journal['restore_of']
    return value


def _restore_binding(value):
    if (not isinstance(value, dict) or set(value) != {'folder', 'manifest_sha256'}
            or not isinstance(value['folder'], str) or not Path(value['folder']).is_absolute()
            or value['folder'].startswith(('\\\\', '//'))
            or any(ord(c) < 32 for c in value['folder'])
            or not isinstance(value['manifest_sha256'], str)
            or not _DIGEST.fullmatch(value['manifest_sha256'])):
        raise ValueError('Gendannelsens backupbinding er ugyldig.')
    return value


def _restore_school(folder, record, profile):
    """Accept only this school's standard layout, identity and host contract."""
    manifest = backup._manifest(folder)
    saved = manifest['profile']
    if (set(saved['roots']) != {'server'} or saved['roots']['server']['role'] != 'server'
            or saved['components'] != profile['components'] or saved['config'] != profile['config']):
        raise ValueError('Backupen har et andet skolelayout.')
    copied = folder / 'roots/server'
    current = Path(record['data_root'])
    owner = _json(copied / 'installation-owner.json')
    now = _json(current / 'installation-owner.json')
    try:
        owner_id = uuid.UUID(owner.get('ownerId', '')).hex
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Backupen mangler en gyldig skoleidentitet.') from None
    if (owner.get('schemaVersion') != 1 or type(owner.get('schemaVersion')) is not int
            or owner.get('state') != 'installed' or owner.get('product') != 'ElevhubServer'
            or owner.get('startupProtocol') != 'generation-v1'
            or owner_id != record['installation_id']
            or not isinstance(owner.get('installRoot'), str)
            or not isinstance(owner.get('dataRoot'), str)
            or Path(owner.get('installRoot', '')) != Path(record['runtime_root'])
            or Path(owner.get('dataRoot', '')) != Path(saved['roots']['server']['path'])
            or any(owner.get(key) != now.get(key) for key in
                   ('taskName', 'roleIp', 'teacherAddress', 'teacherPort', 'studentSubnet',
                    'externalBackends', 'backendPorts', 'printBackendPort',
                    'designBackendPort', 'teacherBackendPort'))):
        raise ValueError('Backupen tilhører en anden skole eller Windows-opsætning.')
    old_config, current_config = _json(copied / 'konfiguration.json'), _json(current / 'konfiguration.json')
    if any(old_config.get(key) != current_config.get(key) for key in
           ('elev_adresse', 'laerer_adresse', 'laerer_port', 'backend_ports', 'external_backends')):
        raise ValueError('Backupens netværksopsætning matcher ikke installationen.')
    # The installed trust anchor must remain valid; this operation never changes
    # machine trust or silently substitutes another school's TLS identity.
    if backup._hash(backup._plain(copied / 'security/ca.crt', False)) != backup._hash(
            backup._plain(current / 'security/ca.crt', False)):
        raise ValueError('Backupens CA-identitet matcher ikke installationen.')
    for name in ('school.json',):
        if _present(copied / name) != _present(current / name):
            raise ValueError('Backupens skoleprofil matcher ikke installationen.')
        if _present(copied / name):
            old_school, current_school = _json(copied / name), _json(current / name)
            if any(old_school.get(key) != current_school.get(key) for key in
                   ('schemaVersion', 'role', 'server', 'student_subnet')):
                raise ValueError('Backupens skoleprofil matcher ikke installationen.')


def _rollback_binding(value):
    fields = {'operation_id', 'folder', 'plan_sha256', 'active_selection_sha256', 'previous_manifest_sha256'}
    if (not isinstance(value, dict) or set(value) not in (fields, fields | {'settled'})
            or not isinstance(value['operation_id'], str) or not re.fullmatch('[a-f0-9]{32}', value['operation_id'])
            or not isinstance(value['folder'], str) or not Path(value['folder']).is_absolute()
            or value['folder'].startswith(('\\\\', '//')) or any(ord(c) < 32 for c in value['folder'])
            or any(not isinstance(value[key], str) or not _DIGEST.fullmatch(value[key])
                   for key in ('plan_sha256', 'active_selection_sha256', 'previous_manifest_sha256'))):
        raise ValueError('Tilbageførslens historikbinding er ugyldig.')
    if 'settled' in value:
        settled = value['settled']
        if (not isinstance(settled, dict) or set(settled) != {'operation_id', 'folder', 'plan_sha256'}
                or not isinstance(settled['operation_id'], str) or not re.fullmatch('[a-f0-9]{32}', settled['operation_id'])
                or not isinstance(settled['folder'], str) or not Path(settled['folder']).is_absolute()
                or settled['folder'].startswith(('\\\\', '//')) or any(ord(c) < 32 for c in settled['folder'])
                or not isinstance(settled['plan_sha256'], str) or not _DIGEST.fullmatch(settled['plan_sha256'])
                or settled['folder'] == value['folder'] or settled['operation_id'] == value['operation_id']):
            raise ValueError('Tilbageførslens annulleringskvittering er ugyldig.')
    return value


def _load(folder, runtime):
    journal = _json(folder / JOURNAL)
    fields = {'format', 'credits', 'operation_id', 'installation_id', 'runtime_root', 'folder',
              'state', 'phase', 'source_selection_sha256', 'source_data_sha256',
              'next_program', 'next_manifest_sha256', 'backup_manifest_sha256', 'prepared_seal_sha256'}
    if (set(journal) not in (fields, fields | {'rollback_of'}, fields | {'restore_of'}) or type(journal['format']) is not int or journal['format'] != 1
            or journal['credits'] != CREDIT or journal['folder'] != str(folder)
            or journal['runtime_root'] != str(runtime)
            or not isinstance(journal['operation_id'], str)
            or not re.fullmatch('[a-f0-9]{32}', journal['operation_id'])
            or not isinstance(journal['installation_id'], str)
            or not re.fullmatch('[a-f0-9]{32}', journal['installation_id'])
            or journal['state'] not in ('preparing', 'prepared', 'cancelled')
            or journal['phase'] not in ('previous', 'data', 'verify', 'prepared', 'cancelled')
            or not isinstance(journal['next_program'], str)
            or not Path(journal['next_program']).is_absolute()
            or journal['next_program'].startswith(('\\\\', '//'))
            or any(ord(char) < 32 for char in journal['next_program'])):
        raise ValueError('Ukendt eller flyttet generationsoperation.')
    if 'rollback_of' in journal:
        binding = _rollback_binding(journal['rollback_of'])
        if (journal['source_selection_sha256'] != binding['active_selection_sha256']
                or journal['next_manifest_sha256'] != binding['previous_manifest_sha256']
                or Path(journal['next_program']) != Path(binding['folder']) / PREVIOUS
                or journal['operation_id'] == binding['operation_id']):
            raise ValueError('Tilbageførslen matcher ikke den registrerede historik.')
    if 'restore_of' in journal:
        binding = _restore_binding(journal['restore_of'])
        if journal['backup_manifest_sha256'] not in (None, binding['manifest_sha256']):
            raise ValueError('Gendannelsen matcher ikke den valgte backup.')
    phases = {'preparing': ('previous', 'data', 'verify'), 'prepared': ('prepared',),
              'cancelled': ('cancelled',)}
    if journal['phase'] not in phases[journal['state']]:
        raise ValueError('Generationsoperationens fase passer ikke til tilstanden.')
    for key in ('source_selection_sha256', 'source_data_sha256', 'next_manifest_sha256',
                'backup_manifest_sha256', 'prepared_seal_sha256'):
        if (journal[key] is None and key in ('backup_manifest_sha256', 'prepared_seal_sha256')
                and journal['state'] != 'prepared' and journal['phase'] != 'verify'):
            continue
        if not isinstance(journal[key], str) or not _DIGEST.fullmatch(journal[key]):
            raise ValueError('Generationsoperationens kontrolsum er ugyldig.')
    if backup._hash(backup._plain(folder / SOURCE, False)) != journal['source_selection_sha256']:
        raise ValueError('Den gemte program-/databinding er ændret.')
    if backup._hash(backup._plain(folder / FINGERPRINT, False)) != journal['source_data_sha256']:
        raise ValueError('Kildedatas generationsbinding er ændret.')
    return journal


def _assert_source(folder, runtime, journal, locked):
    _, raw = _selection(runtime, journal['installation_id'])
    if hashlib.sha256(raw).hexdigest() != journal['source_selection_sha256']:
        raise ValueError('Det aktive program-/datavalg er ændret.')
    if _fingerprint(locked) != _json(folder / FINGERPRINT):
        raise ValueError('Kildedata er ændret siden klargøringens start. Aktivér ikke denne kopi.')


def _result(journal):
    result = {'state': journal['state'], 'operation_id': journal['operation_id'],
            'maintenance': journal['state'] != 'cancelled', 'activation_ready': False,
            'source_selection_sha256': journal['source_selection_sha256'], 'credits': CREDIT}
    if journal.get('rollback_of') is not None:
        result['rollback_of'] = journal['rollback_of']['operation_id']
    if journal.get('restore_of') is not None:
        result['restore_of'] = journal['restore_of']['manifest_sha256']
    return result


def _verify_pair(folder, runtime, journal, locked):
    _assert_source(folder, runtime, journal, locked)
    old_record = validate_record(_json(folder / SOURCE), runtime)
    old = inspect_program(folder / PREVIOUS)
    if old['manifest_sha256'] != old_record['manifest_sha256']:
        raise ValueError('Den tidligere programkopi matcher ikke kildegenerationen.')
    report = update.verify_prepared(folder / PREPARED, expected_operation_id=journal['operation_id'])
    if (report['release']['manifest_sha256'] != journal['next_manifest_sha256']
            or report['source_manifest_sha256'] != journal['backup_manifest_sha256']
            or backup._hash(folder / PREPARED / update.SEAL) != journal['prepared_seal_sha256']):
        raise ValueError('Den nye generation eller den parrede datakopi er ændret.')
    saved_profile = backup._manifest(folder / PREPARED / update.BACKUP)['profile']
    if journal.get('restore_of') is not None:
        if report['source_manifest_sha256'] != journal['restore_of']['manifest_sha256']:
            raise ValueError('Gendannelsens parrede backup er ændret.')
        _restore_school(folder / PREPARED / update.BACKUP, old_record, locked[0])
    elif saved_profile != locked[0]:
        raise ValueError('Databackupen tilhører en anden kildegeneration.')
    return _result(journal)


def _finish(folder, runtime, journal, locked):
    _assert_source(folder, runtime, journal, locked)
    old_record = validate_record(_json(folder / SOURCE), runtime)
    if not _present(folder / PREVIOUS):
        update._copy_program(Path(old_record['program_root']), folder / PREVIOUS, old_record['manifest_sha256'])
    if inspect_program(folder / PREVIOUS)['manifest_sha256'] != old_record['manifest_sha256']:
        raise ValueError('Tidligere programkopi er ufuldstændig; den overskrives ikke.')
    journal['phase'] = 'data'
    _atomic(folder / JOURNAL, journal)
    if _present(folder / PREPARED):
        report = update.resume_preparation(folder / PREPARED, expected_operation_id=journal['operation_id'])
    else:
        program = backup._plain(Path(journal['next_program']), True)
        release = inspect_program(program)
        if release['manifest_sha256'] != journal['next_manifest_sha256']:
            raise ValueError('Den bestilte programudgivelse er ændret.')
        validate_generation(program, runtime, release['manifest_sha256'], 'server')
        if journal.get('restore_of') is not None:
            binding = journal['restore_of']
            source = backup._plain(Path(binding['folder']), True)
            backup.verify(source)
            if backup._hash(source / backup.MANIFEST) != binding['manifest_sha256']:
                raise ValueError('Den valgte backup er ændret.')
            _restore_school(source, old_record, locked[0])
            report = update.prepare_from_backup(source, program, folder / PREPARED,
                operation_id=journal['operation_id'], manifest_sha256=binding['manifest_sha256'])
        else:
            report = update._prepare_locked(locked, program, folder / PREPARED, release,
                                            operation_id=journal['operation_id'])
    digests = {'backup_manifest_sha256': report['source_manifest_sha256'],
               'prepared_seal_sha256': backup._hash(folder / PREPARED / update.SEAL)}
    if any(journal[key] not in (None, digest) for key, digest in digests.items()):
        raise ValueError('Den tidligere verificerede dataklargøring er ændret.')
    journal.update(phase='verify', **digests)
    _atomic(folder / JOURNAL, journal)
    _verify_pair(folder, runtime, journal, locked)
    journal.update(state='prepared', phase='prepared')
    _atomic(folder / JOURNAL, journal)
    return _result(journal)


def prepare(runtime_root, next_program_root, destination, installation_id):
    runtime = backup._plain(Path(runtime_root), True)
    control = backup._plain(runtime / 'control', True)
    with GenerationLock(control / LOCK, exclusive=True):
        return _prepare_locked(runtime, next_program_root, destination, installation_id)


def restore(runtime_root, backup_folder, next_program_root, destination, installation_id):
    """Deliberately restore a selected backup; retain the current generation.

    The same school's existing generation-v1 host and CA are required. This
    does not reinstall a lost host or change its identity/network contract.
    """
    runtime = backup._plain(Path(runtime_root), True)
    source = backup._plain(Path(backup_folder), True)
    with GenerationLock(runtime / 'control' / LOCK, exclusive=True):
        backup.verify(source)
        binding = {'folder': str(source), 'manifest_sha256': backup._hash(source / backup.MANIFEST)}
        return _prepare_locked(runtime, next_program_root, destination, installation_id,
                               restore_of=binding, forbidden_roots=(source,))


def _prepare_locked(runtime, next_program_root, destination, installation_id, *, rollback_of=None,
                    restore_of=None, forbidden_roots=()):
    """Caller retains the exclusive selector lock and any rollback history lock."""
    control = backup._plain(runtime / 'control', True)
    if _present(control / TRANSITION):
        raise RuntimeError('En vedligeholdelsesoperation er allerede uafsluttet.')
    record, raw = _selection(runtime, installation_id)
    current = inspect_program(Path(record['program_root']))
    program = backup._plain(Path(next_program_root), True)
    release = inspect_program(program)
    if release['data_contract'] != current['data_contract']:
        raise ValueError('Et versionsskift må ikke ændre installationens roller.')
    validate_generation(program, runtime, release['manifest_sha256'], 'server')
    if rollback_of is not None:
        _rollback_binding(rollback_of)
        if (hashlib.sha256(raw).hexdigest() != rollback_of['active_selection_sha256']
                or release['manifest_sha256'] != rollback_of['previous_manifest_sha256']
                or program != Path(rollback_of['folder']) / PREVIOUS):
            raise ValueError('Tilbageførslens aktive version eller tidligere program er ændret.')
    profile = _profile(record, current['data_contract'])
    with backup.offline_profile(profile) as locked:
        if restore_of is not None:
            if rollback_of is not None:
                raise ValueError('Gendannelse og programtilbageførsel er forskellige operationer.')
            _restore_binding(restore_of)
            _restore_school(Path(restore_of['folder']), record, profile)
        if rollback_of is not None:
            # A rollback may reuse only the current supported data contract.
            # Never apply a reverse migration or silently reopen a stale backup.
            facts = {role: inspect_component(path / backup.DATABASES[role], role)
                     for role, path in locked[2].items()}
            require_contract(release['data_contract'], facts)
        fingerprint = _fingerprint(locked)
        target = backup._destination(destination, [runtime, program, *forbidden_roots,
                                     Path(record['program_root']), Path(record['data_root'])])
        with InstanceLock(target):
            with (target / SOURCE).open('xb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            backup._write(target / FINGERPRINT, fingerprint)
            journal = {'format': 1, 'credits': CREDIT, 'operation_id': uuid.uuid4().hex,
                       'installation_id': record['installation_id'], 'runtime_root': str(runtime),
                       'folder': str(target), 'state': 'preparing', 'phase': 'previous',
                       'source_selection_sha256': hashlib.sha256(raw).hexdigest(),
                       'source_data_sha256': backup._hash(target / FINGERPRINT),
                       'next_program': str(program), 'next_manifest_sha256': release['manifest_sha256'],
                       'backup_manifest_sha256': None, 'prepared_seal_sha256': None}
            if rollback_of is not None:
                journal['rollback_of'] = dict(rollback_of)
            if restore_of is not None:
                journal['restore_of'] = dict(restore_of)
            _atomic(target / JOURNAL, journal)
            # Persist the startup barrier before copying or snapshotting.
            _atomic(control / TRANSITION, _transition(target, journal))
            return _finish(target, runtime, journal, locked)


@contextmanager
def _operation(runtime_root, folder, allow_cancelled=False):
    runtime = backup._plain(Path(runtime_root), True)
    folder = backup._plain(Path(folder), True)
    control = backup._plain(runtime / 'control', True)
    with GenerationLock(control / LOCK, exclusive=True), InstanceLock(folder), ExitStack() as stack:
        journal = _load(folder, runtime)
        transition = control / TRANSITION
        if journal['state'] == 'cancelled' and allow_cancelled and not _present(transition):
            yield folder, runtime, journal, None
            return
        unregistered = not _present(transition)
        if unregistered:
            # A crash may happen after the private journal was saved but before
            # the startup barrier was published. Only this untouched first phase
            # can be registered later; existing copies never justify guessing.
            if (journal['state'] != 'preparing' or journal['phase'] != 'previous'
                    or _present(folder / PREVIOUS) or _present(folder / PREPARED)):
                raise ValueError('Vedligeholdelsesspærren mangler; operationen kan ikke overtages.')
        elif _json(transition) != _transition(folder, journal):
            raise ValueError('Vedligeholdelsesspærren tilhører en anden operation.')
        if journal.get('rollback_of') is not None and not allow_cancelled:
            from .generation_rollback import _reference
            stack.enter_context(_reference(runtime, journal['rollback_of']))
        record, raw = _selection(runtime, journal['installation_id'])
        if hashlib.sha256(raw).hexdigest() != journal['source_selection_sha256']:
            raise ValueError('Det aktive generationsvalg er ændret; spærren bevares.')
        current = inspect_program(Path(record['program_root']))
        with backup.offline_profile(_profile(record, current['data_contract'])) as locked:
            if unregistered:
                if not allow_cancelled:
                    _assert_source(folder, runtime, journal, locked)
                _atomic(transition, _transition(folder, journal))
            yield folder, runtime, journal, locked


def resume(runtime_root, folder):
    with _operation(runtime_root, folder) as (folder, runtime, journal, locked):
        if journal['state'] == 'cancelled':
            raise ValueError('Operationen er annulleret.')
        if journal['state'] == 'prepared':
            return _verify_pair(folder, runtime, journal, locked)
        return _finish(folder, runtime, journal, locked)


def cancel(runtime_root, folder):
    with _operation(runtime_root, folder, allow_cancelled=True) as (folder, runtime, journal, locked):
        if locked is None:
            return _result(journal)
        # Cancellation never restores stale data or changes the active pointer.
        # It permits reopening the still-selected original after a failed stage.
        journal.update(state='cancelled', phase='cancelled')
        _atomic(folder / JOURNAL, journal)
        (runtime / 'control' / TRANSITION).unlink()
        return _result(journal)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('command', choices=('prepare', 'resume', 'cancel'))
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--program-root', type=Path)
    parser.add_argument('--installation-id')
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            if args.program_root is None or args.installation_id is None:
                raise ValueError('Angiv ny programmappe og installationsejer.')
            result = prepare(args.runtime_root, args.program_root, args.folder, args.installation_id)
        else:
            if args.program_root is not None or args.installation_id is not None:
                raise ValueError('Genoptagelse og annullering bruger den gemte operation.')
            result = (resume if args.command == 'resume' else cancel)(args.runtime_root, args.folder)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError, SyntaxError) as error:
        print('Generationsvedligeholdelse afvist: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
