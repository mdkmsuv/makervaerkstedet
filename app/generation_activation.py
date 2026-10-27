"""Offline publication of a verified, materialized program/data pair.

This coordinator does not grant permissions, stop/start services or operate
hardware. Its host guard must accept the final Windows layout before commit.
An interrupted unpublished switch can be aborted; rollback after reopening
requires a new preserved data generation and is not implemented here.
"""
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from . import generation_transaction as preparation
from . import installation_backup as backup
from . import generation_materialization as materialization
from .generation_lock import GenerationLock
from .generation_runtime import validate_generation
from .generation_selection import ACTIVE, LOCK, TRANSITION, SelectionLease, _present, validate_record
from .instance import InstanceLock
from .release_compatibility import CREDIT, inspect_program

PLAN = 'activation-plan.json'
STATE = 'activation-state.json'
_PHASES = {'staged', 'publishing', 'releasing', 'active', 'aborting', 'aborted'}


def _bytes(record):
    return (json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _publish(path, raw):
    """Publish complete bytes; a crash never exposes a half-written selector."""
    if _present(path):
        backup._plain(path, False)
    temp = path.with_name('.activation-' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        preparation._replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _receipt_path(runtime, operation_id):
    if not isinstance(operation_id, str) or not re.fullmatch('[a-f0-9]{32}', operation_id):
        raise ValueError('Ugyldigt aktiverings-id.')
    return runtime / 'control' / ('activation-' + operation_id + '.json')


def _marker(folder, plan_hash, operation_id):
    return {'format': 1, 'operation': 'activate_update', 'operation_id': operation_id,
            'folder': str(folder), 'plan_sha256': plan_hash}


def _save_state(folder, plan, phase, recovery_selection=None):
    preparation._atomic(folder / STATE, {'format': 1, 'operation_id': plan['operation_id'],
                                        'phase': phase, 'recovery_selection': recovery_selection})


def _state(folder, plan):
    value = preparation._json(folder / STATE)
    if (set(value) != {'format', 'operation_id', 'phase', 'recovery_selection'}
            or type(value['format']) is not int or value['format'] != 1
            or value['operation_id'] != plan['operation_id']
            or not isinstance(value['phase'], str) or value['phase'] not in _PHASES
            or value['recovery_selection'] not in (None, 'original', 'compensating')
            or (value['phase'] in ('aborting', 'aborted')) != (value['recovery_selection'] is not None)):
        raise ValueError('Aktiveringens fasejournal er ugyldig.')
    return value


def _known_locks(folder, locked):
    profile, roots, components, config = locked
    _, _, owners, _ = backup._profile_layout(profile)
    paths = {folder / 'instance.lock', *(path / 'instance.lock' for path in owners)}
    for component in components.values():
        for child in ('supervisor', 'service'):
            path = component / child / 'instance.lock'
            if _present(path):
                paths.add(path)
    for enrollment in backup._enrollment_folders(roots):
        paths.update((enrollment / 'instance.lock', enrollment / 'operations/instance.lock'))
    paths.update(path / 'instance.lock' for path in backup._station_folders(roots))
    registry = backup._registry_lock(backup._check_config(config, roots, components), config.parent, roots)
    if registry is not None:
        paths.add(registry)
    return paths


def _inventory(folder, locks):
    files, directories = backup._scan(folder, ignore_locks=False, ignored_files=locks)
    return {'files': {name: {'size': signature[2], 'sha256': backup._hash(folder / name)}
                      for name, signature in sorted(files.items())}, 'directories': sorted(directories)}


def _verify_target(plan, locked, *, allow_released=False):
    folder = Path(plan['materialized_folder'])
    actual = _inventory(folder, _known_locks(folder, locked))
    expected = {name: dict(row) for name, row in plan['inventory']['files'].items()}
    for relative in plan['holds']:
        if relative not in actual['files'] and allow_released:
            expected.pop(relative)
    if actual['files'] != expected or actual['directories'] != plan['inventory']['directories']:
        raise ValueError('Den endelige program-/datakopi er ændret; aktivering er spærret.')
    record = validate_record(plan['target_record'], Path(plan['runtime_root']))
    validate_generation(record['program_root'], record['runtime_root'], record['manifest_sha256'], 'server')


def _read_plan(folder, runtime):
    plan = preparation._json(folder / PLAN)
    fields = {'format', 'credits', 'operation_id', 'runtime_root', 'folder', 'materialized_folder',
              'source_sha256', 'source_data_sha256', 'target_sha256', 'compensating_sha256', 'source_record',
              'target_record', 'compensating_record', 'inventory', 'holds'}
    if (set(plan) not in (fields, fields | {'rollback_of'}, fields | {'restore_of'}) or type(plan['format']) is not int or plan['format'] != 1
            or plan['credits'] != CREDIT or plan['folder'] != str(folder)
            or plan['runtime_root'] != str(runtime)):
        raise ValueError('Aktiveringsplanen er ukendt eller flyttet.')
    receipt = preparation._json(_receipt_path(runtime, plan['operation_id']))
    marker = _marker(folder, backup._hash(backup._plain(folder / PLAN, False)), plan['operation_id'])
    if receipt != marker:
        raise ValueError('Aktiveringsplanen matcher ikke installationens beskyttede kvittering.')
    journal = preparation._load(folder, runtime)
    source_record = validate_record(preparation._json(folder / preparation.SOURCE), runtime)
    target_folder = backup._plain(Path(plan['materialized_folder']), True)
    if (plan['operation_id'] != journal['operation_id']
            or plan.get('rollback_of') != journal.get('rollback_of')
            or plan.get('restore_of') != journal.get('restore_of')
            or plan['source_sha256'] != journal['source_selection_sha256']
            or plan['source_data_sha256'] != journal['source_data_sha256']
            or plan['source_record'] != source_record
            or plan['target_record']['installation_id'] != source_record['installation_id']
            or plan['target_record']['manifest_sha256'] != journal['next_manifest_sha256']
            or plan['target_record']['program_root'] != str(target_folder / 'program')
            or plan['target_record']['data_root'] != str(target_folder / 'data')):
        raise ValueError('Aktiveringen tilhører en anden klargøring eller installation.')
    for name in ('source_record', 'target_record', 'compensating_record'):
        if validate_record(plan[name], runtime) != plan[name]:
            raise ValueError('Versionsvalgets stier er ikke kanoniske.')
    if (plan['target_record']['revision'] != plan['source_record']['revision'] + 1
            or plan['compensating_record'] != dict(plan['source_record'], revision=plan['source_record']['revision'] + 2)
            or _digest(_bytes(plan['target_record'])) != plan['target_sha256']
            or _digest(_bytes(plan['compensating_record'])) != plan['compensating_sha256']):
        raise ValueError('Aktiveringens versionsbinding er ugyldig.')
    inventory, holds = plan['inventory'], plan['holds']
    if (not isinstance(inventory, dict) or set(inventory) != {'files', 'directories'}
            or not isinstance(inventory['files'], dict) or not isinstance(inventory['directories'], list)
            or not isinstance(holds, dict) or not holds):
        raise ValueError('Aktiveringens filoversigt er ugyldig.')
    for relative in [*inventory['files'], *inventory['directories'], *holds]:
        if (not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative
                or any(ord(c) < 32 for c in relative)
                or any(p in ('', '.', '..') for p in relative.split('/'))):
            raise ValueError('Aktiveringens filsti er ugyldig.')
    for relative, row in inventory['files'].items():
        if (not isinstance(row, dict) or set(row) != {'size', 'sha256'}
                or type(row['size']) is not int or row['size'] < 0
                or not isinstance(row['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', row['sha256'])):
            raise ValueError('Aktiveringens filkontrolsum er ugyldig.')
    if set(holds) != {name for name in inventory['files'] if Path(name).name == backup.HOLD_FILE}:
        raise ValueError('Aktiveringens gendannelsesspærrer er ufuldstændige.')
    for relative, text in holds.items():
        if (not isinstance(text, str)
                or inventory['files'][relative] != {'size': len(text.encode('utf-8')), 'sha256': _digest(text.encode('utf-8'))}):
            raise ValueError('Aktiveringens gendannelsesspærre er ændret.')
    return plan, marker


def _result(plan, phase):
    result = {'operation_id': plan['operation_id'], 'state': phase,
            'maintenance': False, 'services_started': False, 'credits': CREDIT}
    if plan.get('rollback_of') is not None:
        result['rollback_of'] = plan['rollback_of']['operation_id']
    if plan.get('restore_of') is not None:
        result['restore_of'] = plan['restore_of']['manifest_sha256']
    return result


def _completed(folder, runtime, target=None):
    # A completed generation can already have live services and newer data.
    # Read under a shared lease; never reapply its old data seal or take a
    # writer lock merely to answer an idempotent retry.
    plan, _ = _read_plan(folder, runtime)
    if target is not None and backup._plain(Path(target), True) != Path(plan['materialized_folder']):
        raise ValueError('Den angivne generation tilhører en anden aktivering.')
    state = _state(folder, plan)
    if state['phase'] not in ('active', 'aborted'):
        raise ValueError('Aktiveringens vedligeholdelsesspærre mangler.')
    expected = (plan['target_sha256'] if state['phase'] == 'active' else
                plan['source_sha256'] if state['recovery_selection'] == 'original' else plan['compensating_sha256'])
    with SelectionLease(runtime, expected_sha256=expected) as lease:
        validate_generation(lease.record['program_root'], runtime, lease.record['manifest_sha256'], 'server')
        return _result(plan, state['phase'])


def _host(value):
    if value is None:
        from .generation_host import WindowsGenerationHost
        value = WindowsGenerationHost()
    if not callable(getattr(value, 'check', None)):
        raise TypeError('Aktivering kræver en hostkontrol.')
    return value


def _source(locked, folder, runtime, journal):
    record = validate_record(preparation._json(folder / preparation.SOURCE), runtime)
    if (backup._hash(folder / preparation.SOURCE) != journal['source_selection_sha256']
            or preparation._fingerprint(locked) != preparation._json(folder / preparation.FINGERPRINT)):
        raise ValueError('De oprindelige data er ændret; aktiveringen genoptages ikke.')
    validate_generation(record['program_root'], runtime, record['manifest_sha256'], 'server')
    return record


def _begin(folder, runtime, target, journal, stack, host):
    if journal['state'] != 'prepared':
        raise ValueError('Klargøringen skal være fuldført før aktivering.')
    record, raw = preparation._selection(runtime, journal['installation_id'])
    current = inspect_program(Path(record['program_root']))
    source = stack.enter_context(backup.offline_profile(preparation._profile(record, current['data_contract'])))
    preparation._verify_pair(folder, runtime, journal, source)
    stack.enter_context(InstanceLock(target))
    material = materialization._verify_materialized_locked(target, expected_operation_id=journal['operation_id'])
    metadata = preparation._json(target / materialization.MANIFEST)
    if (backup._plain(Path(metadata['prepared_root']), True) != folder / preparation.PREPARED
            or metadata['source_seal_sha256'] != journal['prepared_seal_sha256']):
        raise ValueError('Materialiseringen tilhører en anden klargøringskilde.')
    program, data = backup._plain(Path(material['program_root']), True), backup._plain(Path(material['data_root']), True)
    if material['manifest_sha256'] != journal['next_manifest_sha256']:
        raise ValueError('Materialiseringen har et andet programmanifest.')
    selected = validate_record(dict(record, revision=record['revision'] + 1,
                                    program_root=str(program), data_root=str(data),
                                    manifest_sha256=material['manifest_sha256']), runtime)
    target_locked = stack.enter_context(backup._locked_profile(material['profile'], allow_restored=True))
    host.check(runtime, record, selected)
    inventory = _inventory(target, _known_locks(target, target_locked))
    roots, components, _, _ = backup._profile_layout(material['profile'])
    holds = {(path / backup.HOLD_FILE).relative_to(target).as_posix():
             (path / backup.HOLD_FILE).read_bytes().decode('utf-8')
             for path in {target, *roots.values(), *components.values()}}
    actual_holds = {name for name in inventory['files'] if Path(name).name == backup.HOLD_FILE}
    if actual_holds != set(holds):
        raise ValueError('Materialiseringens gendannelsesspærringer er uventede.')
    compensating = validate_record(dict(record, revision=record['revision'] + 2), runtime)
    plan = {'format': 1, 'credits': CREDIT, 'operation_id': journal['operation_id'],
            'runtime_root': str(runtime), 'folder': str(folder), 'materialized_folder': str(target),
            'source_sha256': _digest(raw), 'source_data_sha256': journal['source_data_sha256'],
            'target_sha256': _digest(_bytes(selected)),
            'compensating_sha256': _digest(_bytes(compensating)), 'source_record': record,
            'target_record': selected, 'compensating_record': compensating,
            'inventory': inventory, 'holds': holds}
    if journal.get('rollback_of') is not None:
        plan['rollback_of'] = dict(journal['rollback_of'])
    if journal.get('restore_of') is not None:
        plan['restore_of'] = dict(journal['restore_of'])
    if _present(folder / PLAN):
        if preparation._json(folder / PLAN) != plan:
            raise ValueError('En anden aktiveringsplan findes allerede.')
    else:
        backup._write(folder / PLAN, plan)
    if _present(folder / STATE):
        if _state(folder, plan)['phase'] != 'staged':
            raise ValueError('Aktiveringens fase matcher ikke den oprindelige spærring.')
    else:
        _save_state(folder, plan, 'staged')
    marker = _marker(folder, backup._hash(folder / PLAN), journal['operation_id'])
    receipt = _receipt_path(runtime, journal['operation_id'])
    if _present(receipt):
        if preparation._json(receipt) != marker:
            raise ValueError('En anden aktivering ejer installationens kvittering.')
    else:
        preparation._atomic(receipt, marker)
    preparation._atomic(runtime / 'control' / TRANSITION, marker)
    return plan, source, target_locked


@contextmanager
def _operation(runtime_root, folder, target=None, host=None, *, check_history=True):
    runtime, folder = backup._plain(Path(runtime_root), True), backup._plain(Path(folder), True)
    host = _host(host)
    with GenerationLock(runtime / 'control' / LOCK, exclusive=True), InstanceLock(folder), ExitStack() as stack:
        journal = preparation._load(folder, runtime)
        marker = preparation._json(runtime / 'control' / TRANSITION)
        if check_history and journal.get('rollback_of') is not None:
            from .generation_rollback import _reference
            stack.enter_context(_reference(runtime, journal['rollback_of']))
        if marker == preparation._transition(folder, journal):
            if target is None:
                raise ValueError('Angiv den nye materialiserede generation.')
            target = backup._plain(Path(target), True)
            plan, source, target_locked = _begin(folder, runtime, target, journal, stack, host)
        else:
            plan, expected = _read_plan(folder, runtime)
            if marker != expected or (target is not None and backup._plain(Path(target), True) != Path(plan['materialized_folder'])):
                raise ValueError('Vedligeholdelsesspærren tilhører en anden aktivering.')
            source_record = plan['source_record']
            contract = inspect_program(Path(source_record['program_root']))['data_contract']
            source = stack.enter_context(backup.offline_profile(preparation._profile(source_record, contract)))
            target_root = Path(plan['materialized_folder'])
            stack.enter_context(InstanceLock(target_root))
            target_locked = stack.enter_context(backup._locked_profile(preparation._profile(plan['target_record'], contract), allow_restored=True))
        _source(source, folder, runtime, journal)
        yield folder, runtime, plan, target_locked, host


def _current(runtime, plan):
    raw = backup._plain(runtime / 'control' / ACTIVE, False).read_bytes()
    value = _digest(raw)
    if value not in (plan['source_sha256'], plan['target_sha256'], plan['compensating_sha256']):
        raise ValueError('Det aktive versionsvalg tilhører en anden operation.')
    return value


def activate(runtime_root, folder, materialized_folder=None, *, host=None):
    runtime, folder = backup._plain(Path(runtime_root), True), backup._plain(Path(folder), True)
    if not _present(runtime / 'control' / TRANSITION):
        result = _completed(folder, runtime, materialized_folder)
        if result['state'] != 'active':
            raise ValueError('Aktiveringen er annulleret.')
        return result
    with _operation(runtime, folder, materialized_folder, host) as (folder, runtime, plan, locked, host):
        state = _state(folder, plan)
        if state['phase'] in ('aborting', 'aborted'):
            raise ValueError('Aktiveringen er under annullering.')
        _verify_target(plan, locked, allow_released=state['phase'] in ('releasing', 'active'))
        host.check(runtime, plan['source_record'], plan['target_record'])
        current = _current(runtime, plan)
        if state['phase'] == 'staged':
            if current != plan['source_sha256']:
                raise ValueError('Kilden er ændret før aktiveringens commit.')
            _save_state(folder, plan, 'publishing')
            state['phase'] = 'publishing'
        if state['phase'] == 'publishing':
            if current == plan['source_sha256']:
                _publish(runtime / 'control' / ACTIVE, _bytes(plan['target_record']))
            elif current != plan['target_sha256']:
                raise ValueError('Aktiveringen er allerede ført tilbage.')
            _save_state(folder, plan, 'releasing')
            state['phase'] = 'releasing'
        if state['phase'] == 'releasing':
            if _current(runtime, plan) != plan['target_sha256']:
                raise ValueError('Versionsvalget matcher ikke datakopien.')
            target = Path(plan['materialized_folder'])
            for relative in sorted(plan['holds'], key=lambda p: len(Path(p).parts), reverse=True):
                (target / relative).unlink(missing_ok=True)
            _save_state(folder, plan, 'active')
        elif state['phase'] == 'active':
            if current != plan['target_sha256'] or any(_present(Path(plan['materialized_folder']) / p) for p in plan['holds']):
                raise ValueError('En fuldført aktiveringsspærring er inkonsistent.')
        (runtime / 'control' / TRANSITION).unlink()
        return _result(plan, 'active')


def abort_unpublished(runtime_root, folder, *, host=None):
    """Recover the original only while the activation barrier never opened."""
    runtime, folder = backup._plain(Path(runtime_root), True), backup._plain(Path(folder), True)
    if not _present(runtime / 'control' / TRANSITION):
        result = _completed(folder, runtime)
        if result['state'] != 'aborted':
            raise ValueError('Generationens drift er åbnet; tilbageførsel kræver en ny datakopi.')
        return result
    with _operation(runtime, folder, host=host, check_history=False) as (folder, runtime, plan, locked, host):
        state = _state(folder, plan)
        _verify_target(plan, locked, allow_released=True)
        host.check(runtime, plan['source_record'], plan['target_record'])
        current = _current(runtime, plan)
        if state['phase'] not in ('aborting', 'aborted'):
            mode = 'original' if current == plan['source_sha256'] else 'compensating'
            _save_state(folder, plan, 'aborting', mode)
        else:
            mode = state['recovery_selection']
        expected = plan['source_sha256'] if mode == 'original' else plan['compensating_sha256']
        if current != expected:
            if mode != 'compensating' or current != plan['target_sha256']:
                raise ValueError('Annulleringens versionsvalg er ændret.')
            _publish(runtime / 'control' / ACTIVE, _bytes(plan['compensating_record']))
        target = Path(plan['materialized_folder'])
        for relative, text in plan['holds'].items():
            path = target / relative
            if not _present(path):
                with path.open('xb') as stream:
                    stream.write(text.encode('utf-8'))
                    stream.flush()
                    os.fsync(stream.fileno())
        _verify_target(plan, locked)
        journal = preparation._load(folder, runtime)
        journal.update(state='cancelled', phase='cancelled')
        preparation._atomic(folder / preparation.JOURNAL, journal)
        _save_state(folder, plan, 'aborted', mode)
        (runtime / 'control' / TRANSITION).unlink()
        return _result(plan, 'aborted')
