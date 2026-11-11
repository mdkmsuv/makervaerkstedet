"""Journalled local operator for generation-v1 update, restore and rollback.

Run directly with the fixed installation's Python, NEVER as a generation
runtime entry: its shared lifetime lease would block the exclusive core locks.
All generations, backups and journals are retained. Queues remain paused.
"""
import argparse
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

from . import generation_activation as activation
from . import generation_materialization as materialization
from . import generation_rollback as rollback
from . import generation_transaction as preparation
from . import installation_backup as backup
from .generation_runtime import validate_generation
from .generation_selection import TRANSITION, SelectionLease, _present, validate_record
from .instance import InstanceLock
from .release_compatibility import CREDIT


PHASES = {'requested', 'captured', 'stopping', 'stopped', 'preparing', 'prepared',
          'materializing', 'materialized', 'securing', 'activating', 'active',
          'starting', 'complete', 'cancelling', 'cancelled'}


def _destination(value):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError('Angiv en lokal absolut destinationssti.')
    parent = backup._plain(path.parent, True)
    if _present(path):
        return backup._plain(path, True)
    return parent / path.name


def journal_path(runtime, folder):
    name = hashlib.sha256(str(folder).casefold().encode('utf-8')).hexdigest()
    return runtime / 'control' / ('school-maintenance-' + name + '.json')


def _selected(runtime, installation_id=None):
    # Keep this scope short: no exclusive core/controller call under this lease.
    with SelectionLease(runtime) as lease:
        record = dict(lease.record)
        if installation_id is not None and record['installation_id'] != installation_id:
            raise ValueError('Forkert installationsejer.')
        validate_generation(record['program_root'], runtime, record['manifest_sha256'], 'server')
        return record


def _save(path, journal, phase):
    journal['phase'] = phase
    preparation._atomic(path, journal)


def _load(path, runtime, folder):
    journal = preparation._json(path)
    fields = {'format', 'credits', 'runtime_root', 'folder', 'materialized_folder',
              'action', 'program_root', 'backup_source', 'completed_folder', 'settled_folder',
              'source_record', 'phase', 'task_receipt'}
    if (not isinstance(journal, dict) or set(journal) != fields
            or type(journal['format']) is not int or journal['format'] != 1
            or journal['credits'] != CREDIT or journal['runtime_root'] != str(runtime)
            or journal['folder'] != str(folder) or not isinstance(journal['action'], str)
            or journal['action'] not in {'update', 'restore', 'rollback'}
            or not isinstance(journal['phase'], str) or journal['phase'] not in PHASES
            or not isinstance(journal['materialized_folder'], str)):
        raise ValueError('Operatørjournalen er ugyldig eller flyttet.')
    validate_record(journal['source_record'], runtime)
    if _destination(journal['materialized_folder']) == folder:
        raise ValueError('Klargøring og endelig generation skal være forskellige mapper.')
    for key in ('program_root', 'backup_source', 'completed_folder', 'settled_folder'):
        if journal[key] is not None:
            # Completed copies/history have their own bound integrity checks.
            # The removable candidate/backup medium need not remain mounted
            # after copying, or merely to cancel a failed preparation.
            if not isinstance(journal[key], str) or not Path(journal[key]).is_absolute():
                raise ValueError('Journalens oprindelige kildesti er ugyldig.')
    if journal['action'] == 'update' and (journal['program_root'] is None or journal['backup_source'] is not None or journal['completed_folder'] is not None or journal['settled_folder'] is not None):
        raise ValueError('Opdateringens parametre er ugyldige.')
    if journal['action'] == 'restore' and (journal['program_root'] is None or journal['backup_source'] is None or journal['completed_folder'] is not None or journal['settled_folder'] is not None):
        raise ValueError('Gendannelsens parametre er ugyldige.')
    if journal['action'] == 'rollback' and (journal['completed_folder'] is None or journal['program_root'] is not None or journal['backup_source'] is not None):
        raise ValueError('Tilbageførslens parametre er ugyldige.')
    if journal['phase'] not in {'requested', 'cancelling', 'cancelled'} and journal['task_receipt'] is None:
        raise ValueError('Operatørjournalen mangler de oprindelige tasktilstande.')
    return journal


def _report(runtime, folder, journal):
    return {'action': journal['action'], 'state': journal['phase'], 'folder': str(folder),
            'materialized_folder': journal['materialized_folder'],
            'maintenance': _present(runtime / 'control' / TRANSITION),
            'services_restored': journal['phase'] in {'complete', 'cancelled'},
            'queues_unpaused': False, 'credits': CREDIT}


def _prepare(runtime, folder, journal):
    if _present(folder / preparation.JOURNAL):
        result = preparation.resume(runtime, folder)
    elif journal['action'] == 'rollback':
        result = rollback.prepare(runtime, journal['completed_folder'], folder,
                                  settled_folder=journal['settled_folder'])
    elif journal['action'] == 'restore':
        result = preparation.restore(runtime, journal['backup_source'], journal['program_root'],
                                     folder, journal['source_record']['installation_id'])
    else:
        result = preparation.prepare(runtime, journal['program_root'], folder,
                                     journal['source_record']['installation_id'])
    source = preparation._json(folder / preparation.SOURCE)
    if source != journal['source_record']:
        raise ValueError('Klargøringen valgte en anden kildegeneration; spærren bevares.')
    return result


def _continue(runtime, folder, path, journal, controller):
    if journal['phase'] == 'cancelled':
        raise ValueError('Operationen er annulleret.')
    if journal['phase'] == 'complete':
        # Verify the completed selector without relaunching or stopping live
        # services and without examining an obsolete data fingerprint.
        activation.activate(runtime, folder, journal['materialized_folder'], host=controller.host)
        return _report(runtime, folder, journal)
    if journal['phase'] == 'cancelling':
        return _cancel(runtime, folder, path, journal, controller)
    if journal['phase'] == 'requested':
        if _selected(runtime) != journal['source_record']:
            raise ValueError('Det aktive valg er ændret før taskkvitteringen.')
        journal['task_receipt'] = controller.capture(runtime, journal['source_record'])
        _save(path, journal, 'captured')
    if journal['phase'] in {'captured', 'stopping'}:
        _save(path, journal, 'stopping')
        controller.stop(runtime, journal['source_record'], journal['task_receipt'])
        if _selected(runtime) != journal['source_record']:
            raise ValueError('Det aktive valg er ændret under nedlukning.')
        _save(path, journal, 'stopped')
    target = Path(journal['materialized_folder'])
    # The activation journal determines recovery after selector publication.
    # It is checked by the core together with its protected receipt/transition.
    if not _present(folder / activation.PLAN):
        _save(path, journal, 'preparing')
        prepared = _prepare(runtime, folder, journal)
        _save(path, journal, 'prepared')
        _save(path, journal, 'materializing')
        if _present(target):
            material = materialization.verify_materialized(target, expected_operation_id=prepared['operation_id'])
        else:
            material = materialization.materialize(folder / preparation.PREPARED, target,
                                                    expected_operation_id=prepared['operation_id'])
        _save(path, journal, 'materialized')
        selected = dict(journal['source_record'], revision=journal['source_record']['revision'] + 1,
                        program_root=material['program_root'], data_root=material['data_root'],
                        manifest_sha256=material['manifest_sha256'])
        _save(path, journal, 'securing')
        controller.secure(runtime, target, journal['source_record'], selected)
    _save(path, journal, 'activating')
    activation.activate(runtime, folder, target, host=controller.host)
    _save(path, journal, 'active')
    selected = _selected(runtime, journal['source_record']['installation_id'])
    _save(path, journal, 'starting')
    controller.start(runtime, selected, journal['task_receipt'])
    _save(path, journal, 'complete')
    return _report(runtime, folder, journal)


def _cancel(runtime, folder, path, journal, controller):
    if journal['phase'] == 'cancelled':
        return _report(runtime, folder, journal)
    if journal['phase'] == 'complete':
        raise ValueError('Genåbnet drift kræver en ny tilbageførsel med aktuelle data.')
    if _present(folder / activation.PLAN):
        transition = runtime / 'control' / TRANSITION
        core_journal = preparation._load(folder, runtime)
        if (_present(transition)
                and preparation._json(transition) == preparation._transition(folder, core_journal)):
            # _begin can crash between plan/state/receipt creation and replacing
            # the preparation marker. Publication never began in this state;
            # preparation's checked cancel is authoritative, even with a PLAN.
            preparation.cancel(runtime, folder)
        else:
            # The core rejects any active publication whose barrier is gone.
            activation.abort_unpublished(runtime, folder, host=controller.host)
    elif _present(folder / preparation.JOURNAL):
        preparation.cancel(runtime, folder)
    elif _present(runtime / 'control' / TRANSITION):
        raise ValueError('En fremmed vedligeholdelse ejer spærren.')
    else:
        if _selected(runtime) != journal['source_record']:
            raise ValueError('Det aktive versionsvalg er ændret; originalen genåbnes ikke.')
    # Save after the core abort but before starting. On retry, aborted core
    # receipts prove the compensating selection and preserve the newer revision.
    _save(path, journal, 'cancelling')
    selected = _selected(runtime, journal['source_record']['installation_id'])
    if journal['task_receipt'] is not None:
        controller.start(runtime, selected, journal['task_receipt'])
    _save(path, journal, 'cancelled')
    return _report(runtime, folder, journal)


def execute(action, *, runtime_root, folder, materialized_folder=None, program_root=None,
            installation_id=None, completed_folder=None, settled_folder=None, backup_source=None,
            controller=None):
    runtime = backup._plain(Path(runtime_root), True)
    control = backup._plain(runtime / 'control', True)
    folder = _destination(folder)
    if controller is None:
        from .school_maintenance_host import WindowsMaintenanceController
        controller = WindowsMaintenanceController()
    path = journal_path(runtime, folder)
    # A separate operator lock serializes host actions without nesting the
    # generation selector lock acquired independently by every core operation.
    with (nullcontext() if action == 'status' else InstanceLock(control)):
        tls_journal = control / 'school-tls-renewal.json'
        if action != 'status' and _present(tls_journal):
            from .school_tls import _load as load_tls_journal
            if load_tls_journal(tls_journal, runtime)['phase'] != 'complete':
                raise RuntimeError('Genoptag og afslut servercertifikatfornyelsen før anden vedligeholdelse.')
        if action in {'resume', 'cancel', 'status'}:
            if any(value is not None for value in (materialized_folder, program_root, installation_id,
                                                   completed_folder, settled_folder, backup_source)):
                raise ValueError('Genoptagelse, annullering og status bruger kun den gemte operation.')
            journal = _load(path, runtime, folder)
        elif action in {'update', 'restore', 'rollback'}:
            if _present(path):
                raise ValueError('Der findes allerede en operation for mappen; brug resume eller status.')
            if materialized_folder is None:
                raise ValueError('Angiv en ny endelig generationsmappe.')
            final = _destination(materialized_folder)
            if _present(folder) or _present(final):
                raise ValueError('Klargøring og endelig generation skal bruge nye mapper.')
            if final == folder or final in folder.parents or folder in final.parents:
                raise ValueError('Klargøring og endelig generation må ikke overlappe.')
            source = _selected(runtime, installation_id)
            journal = {'format': 1, 'credits': CREDIT, 'runtime_root': str(runtime), 'folder': str(folder),
                       'materialized_folder': str(final), 'action': action, 'source_record': source,
                       'program_root': str(backup._plain(Path(program_root), True)) if program_root is not None else None,
                       'backup_source': str(backup._plain(Path(backup_source), True)) if backup_source is not None else None,
                       'completed_folder': str(backup._plain(Path(completed_folder), True)) if completed_folder is not None else None,
                       'settled_folder': str(backup._plain(Path(settled_folder), True)) if settled_folder is not None else None,
                       'phase': 'requested', 'task_receipt': None}
            # Validate exact contracts before saving or touching Windows tasks.
            if action == 'update' and (program_root is None or completed_folder is not None or backup_source is not None or settled_folder is not None):
                raise ValueError('Opdatering kræver kun programkandidat og nye mapper.')
            if action == 'restore' and (program_root is None or backup_source is None or completed_folder is not None or settled_folder is not None):
                raise ValueError('Gendannelse kræver backup, programkandidat og nye mapper.')
            if action == 'rollback' and (completed_folder is None or program_root is not None or backup_source is not None):
                raise ValueError('Tilbageførsel kræver en afsluttet opdateringsmappe og nye mapper.')
            _save(path, journal, 'requested')
        else:
            raise ValueError('Ukendt vedligeholdelseshandling.')
        if action == 'status':
            return _report(runtime, folder, journal)
        if action == 'cancel':
            return _cancel(runtime, folder, path, journal, controller)
        return _continue(runtime, folder, path, journal, controller)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('action', choices=('update', 'restore', 'rollback', 'resume', 'cancel', 'status'))
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--materialized-folder', type=Path)
    parser.add_argument('--program-root', type=Path)
    parser.add_argument('--installation-id')
    parser.add_argument('--completed-folder', type=Path)
    parser.add_argument('--settled-folder', type=Path)
    parser.add_argument('--backup-source', type=Path)
    args = parser.parse_args(argv)
    try:
        result = execute(**vars(args))
        print(json.dumps({'ok': True, **result}, ensure_ascii=True))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError, SyntaxError) as error:
        print(json.dumps({'ok': False, 'error': str(error), 'credits': CREDIT}, ensure_ascii=True))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
