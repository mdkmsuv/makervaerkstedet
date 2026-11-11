"""Prepare the preceding program with a fresh copy of current school data.

This is a new forward transaction, not restoration of an old data directory.
It preserves current and historical generations, never starts services, and
accepts only the existing, explicitly supported data contract. Resume/cancel
use generation_transaction; materialization/activation use their usual APIs.
"""
import argparse
from contextlib import ExitStack, contextmanager
from pathlib import Path
import json
import sqlite3
import subprocess
import sys

from . import generation_activation as activation
from . import generation_transaction as preparation
from . import installation_backup as backup
from .generation_lock import GenerationLock
from .generation_runtime import validate_generation
from .generation_selection import LOCK, TRANSITION, _present
from .instance import InstanceLock
from .release_compatibility import inspect_program


def _completed(runtime, folder):
    """Caller holds both the exclusive generation lock and history-folder lock."""
    plan, marker = activation._read_plan(folder, runtime)
    if activation._state(folder, plan)['phase'] != 'active':
        raise ValueError('Tilbageførsel kræver en afsluttet aktivering.')
    previous = backup._plain(folder / preparation.PREVIOUS, True)
    release = inspect_program(previous)
    expected = plan['source_record']['manifest_sha256']
    if release['manifest_sha256'] != expected:
        raise ValueError('Den bevarede tidligere programversion er ændret.')
    validate_generation(previous, runtime, expected, 'server')
    binding = {'operation_id': plan['operation_id'], 'folder': str(folder),
               'plan_sha256': marker['plan_sha256'],
               'active_selection_sha256': plan['target_sha256'],
               'previous_manifest_sha256': expected}
    return plan, binding, previous


def _settled(runtime, folder, base_plan):
    """An explicit closed abort can explain a higher revision of the same pair.

    It cannot authorize a different data directory or program. Its protected
    receipt proves the exact selected bytes after recovery; a bare revision
    change is never accepted as evidence of an aborted transaction.
    """
    plan, marker = activation._read_plan(folder, runtime)
    state = activation._state(folder, plan)
    if state['phase'] != 'aborted':
        raise ValueError('Den supplerende operation er ikke afsluttet som annulleret.')
    if state['recovery_selection'] == 'original':
        selected, digest = plan['source_record'], plan['source_sha256']
    else:
        selected, digest = plan['compensating_record'], plan['compensating_sha256']
    base = base_plan['target_record']
    if (dict(selected, revision=base['revision']) != base or selected['revision'] < base['revision']):
        raise ValueError('Annulleringen genåbnede ikke den ønskede aktiverings program og data.')
    proof = {'operation_id': plan['operation_id'], 'folder': str(folder), 'plan_sha256': marker['plan_sha256']}
    return proof, digest, Path(plan['materialized_folder'])


def _with_settlement(runtime, base_plan, binding, settled_folder, stack):
    if settled_folder is None:
        return binding, ()
    folder = backup._plain(Path(settled_folder), True)
    if folder == Path(binding['folder']):
        raise ValueError('Aktivering og annullering skal være forskellige operationer.')
    stack.enter_context(InstanceLock(folder))
    proof, digest, material = _settled(runtime, folder, base_plan)
    result = dict(binding, active_selection_sha256=digest, settled=proof)
    preparation._rollback_binding(result)
    return result, (folder, material)


@contextmanager
def _reference(runtime, binding):
    """Retain and recheck the immediate history during resume or activation.

    Does not read the active pointer: activation can already have published its
    new pointer. Its own transaction checks the permitted pointer states.
    """
    preparation._rollback_binding(binding)
    folder = backup._plain(Path(binding['folder']), True)
    with InstanceLock(folder), ExitStack() as stack:
        plan, expected, previous = _completed(runtime, folder)
        expected, _ = _with_settlement(runtime, plan, expected,
                                       binding.get('settled', {}).get('folder'), stack)
        if binding != expected:
            raise ValueError('Tilbageførslens aktiveringshistorik er ændret.')
        yield plan, previous


def prepare(runtime_root, completed_folder, destination, *, settled_folder=None):
    """Keep the predecessor's code, but snapshot only the active generation's data."""
    runtime = backup._plain(Path(runtime_root), True)
    folder = backup._plain(Path(completed_folder), True)
    with GenerationLock(runtime / 'control' / LOCK, exclusive=True), InstanceLock(folder), ExitStack() as stack:
        if _present(runtime / 'control' / TRANSITION):
            raise RuntimeError('En vedligeholdelsesoperation er allerede uafsluttet.')
        plan, binding, previous = _completed(runtime, folder)
        binding, settled_roots = _with_settlement(runtime, plan, binding, settled_folder, stack)
        return preparation._prepare_locked(
            runtime, previous, destination, plan['target_record']['installation_id'],
            rollback_of=binding,
            forbidden_roots=(folder, Path(plan['materialized_folder']), *settled_roots,
                             Path(plan['source_record']['program_root']), Path(plan['source_record']['data_root'])))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--completed-folder', type=Path, required=True)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--settled-folder', type=Path,
                        help='Afsluttet annullering, hvis den har genåbnet samme program/data med et højere revisionsnummer.')
    args = parser.parse_args(argv)
    try:
        result = prepare(args.runtime_root, args.completed_folder, args.folder, settled_folder=args.settled_folder)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError, SyntaxError) as error:
        print('Tilbageførsel afvist: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
