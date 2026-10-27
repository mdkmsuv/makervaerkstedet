"""Crash-resumable administrator operation for generation-v1 TLS renewal.

This module runs from the fixed installation and holds no generation lifetime
lease.  It validates the selected generation, uses the existing Windows task
controller for ownership/process checks, and journals every offline phase so a
failed console can be resumed without guessing task state.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys

from . import generation_transaction as preparation
from . import school_maintenance as maintenance
from . import school_maintenance_host
from . import installation_backup as backup
from .release_compatibility import inspect_program
from .generation_selection import validate_record
from .instance import InstanceLock


JOURNAL = 'school-tls-renewal.json'
PHASES = {'requested', 'captured', 'stopping', 'stopped', 'renewing',
          'renewed', 'starting', 'complete'}
RECEIPT_FIELDS = {'schemaVersion', 'state', 'operationId', 'serverIp',
                  'oldServerSha256', 'newServerSha256', 'caSha256', 'keySha256'}


def _ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise ValueError('TLS-fornyelsen kræver en gyldig IPv4-serveradresse.') from error
    if not isinstance(address, ipaddress.IPv4Address) or address.is_unspecified or address.is_multicast or address.is_loopback:
        raise ValueError('TLS-fornyelsen kræver en aktiv IPv4-serveradresse.')
    return str(address)


def _path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError('TLS-operatoren kræver en absolut runtime-sti.')
    return path


def _journal_path(runtime: Path) -> Path:
    return runtime / 'control' / JOURNAL


def _validate_receipt(value, operation_id, server_ip):
    if (not isinstance(value, dict) or set(value) != RECEIPT_FIELDS
            or value.get('schemaVersion') != 1 or value.get('state') != 'complete'
            or value.get('operationId') != operation_id or value.get('serverIp') != server_ip
            or any(not isinstance(value.get(name), str) or not re.fullmatch(r'[0-9a-f]{64}', value[name])
                   for name in ('oldServerSha256', 'newServerSha256', 'caSha256', 'keySha256'))):
        raise ValueError('TLS-fornyelsesreceipten matcher ikke den journalførte operation.')
    return value


def _save(path: Path, journal: dict, phase: str) -> None:
    journal['phase'] = phase
    preparation._atomic(path, journal)


def _load(path: Path, runtime: Path) -> dict:
    value = preparation._json(path)
    fields = {'format', 'runtime_root', 'source_record', 'server_ip',
              'security_root', 'operation_id', 'phase', 'task_receipt',
              'renewal_receipt'}
    if (not isinstance(value, dict) or set(value) != fields
            or type(value['format']) is not int or value['format'] != 1
            or value['runtime_root'] != str(runtime)
            or not isinstance(value['server_ip'], str)
            or not isinstance(value['operation_id'], str)
            or not re.fullmatch(r'[0-9a-f]{32}', value['operation_id'])
            or value['phase'] not in PHASES
            or value['task_receipt'] is not None and not isinstance(value['task_receipt'], dict)
            or value['renewal_receipt'] is not None and not isinstance(value['renewal_receipt'], dict)):
        raise ValueError('TLS-operatorjournalen er ugyldig eller flyttet.')
    record = validate_record(value['source_record'], runtime)
    if value['security_root'] != str(Path(record['data_root']) / 'security'):
        raise ValueError('TLS-operatorjournalens security-sti matcher ikke generationsvalget.')
    if _ip(value['server_ip']) != value['server_ip']:
        raise ValueError('TLS-operatorjournalens serveradresse er ugyldig.')
    if value['phase'] in {'captured', 'stopping', 'stopped', 'renewing',
                           'renewed', 'starting', 'complete'} and value['task_receipt'] is None:
        raise ValueError('TLS-operatorjournalen mangler taskkvitteringen.')
    if value['phase'] in {'renewed', 'starting', 'complete'} and value['renewal_receipt'] is None:
        raise ValueError('TLS-operatorjournalen mangler fornyelsesreceipt.')
    if value['renewal_receipt'] is not None:
        _validate_receipt(value['renewal_receipt'], value['operation_id'], value['server_ip'])
    return dict(value, source_record=record)


def _server_ip(record: dict, requested: str | None = None) -> str:
    config = preparation._json(Path(record['data_root']) / 'konfiguration.json')
    configured = _ip(config.get('elev_adresse'))
    if requested is not None and _ip(requested) != configured:
        raise ValueError('Den valgte serveradresse matcher ikke generationsdata.')
    return configured


def _renew(security: Path, server_ip: str, runtime: Path, operation_id: str) -> dict:
    provision = backup._plain(runtime / 'server' / 'provision.py', False)
    security = backup._plain(security, True)
    if not provision.is_file():
        raise ValueError('Den faste runtime mangler server/provision.py.')
    completed = subprocess.run(
        [sys.executable, '-B', str(provision), 'renew-tls',
         '--output-dir', str(security), '--server-ip', server_ip,
         '--operation-id', operation_id, '--lock-held'],
        cwd=str(runtime), check=False, capture_output=True, text=True)
    if completed.returncode:
        detail = (completed.stdout or '').strip() or (completed.stderr or '').strip()
        raise RuntimeError('Servercertifikatfornyelsen fejlede.' + (f' {detail}' if detail else ''))
    # The fixed helper prints one strict JSON result.  Running it as a child
    # keeps the receipt durable in the data directory; no in-process import
    # can accidentally select generation code.
    try:
        value = json.loads((completed.stdout or '').strip())
    except (json.JSONDecodeError, TypeError) as error:
        raise RuntimeError('Servercertifikatfornyelsen returnerede ugyldig receipt.') from error
    if not isinstance(value, dict) or value.get('state') != 'complete' or value.get('operationId') != operation_id:
        raise RuntimeError('Servercertifikatfornyelsen returnerede en forkert receipt.')
    return value


def _profile_lock(record):
    contract = inspect_program(Path(record['program_root']))['data_contract']
    return backup.offline_profile(maintenance.preparation._profile(record, contract))


def execute(*, runtime_root: str | Path, server_ip: str | None = None,
            controller=None, renewer=None, selector=None, locker=None,
            resume: bool = False) -> dict:
    runtime = backup._plain(_path(runtime_root), True)
    control = backup._plain(runtime / 'control', True)
    journal_path = _journal_path(runtime)
    if controller is None:
        controller = school_maintenance_host.WindowsMaintenanceController()
    if selector is None:
        selector = maintenance._selected
    if renewer is None:
        renewer = _renew
    if locker is None:
        locker = lambda record: _profile_lock(record)

    with InstanceLock(control):
        if journal_path.exists():
            journal = _load(journal_path, runtime)
            if journal['phase'] == 'complete' and not resume:
                current = selector(runtime)
                if current['installation_id'] != journal['source_record']['installation_id']:
                    raise ValueError('Installationen er ændret; ny TLS-fornyelse er spærret.')
                archive = control / ('school-tls-renewal-' + journal['operation_id'] + '.json')
                if archive.exists():
                    if backup._plain(archive, False).read_bytes() != journal_path.read_bytes():
                        raise ValueError('Den tidligere TLS-fornyelsesreceipt kan ikke arkiveres sikkert.')
                elif archive.is_symlink():
                    raise ValueError('Den tidligere TLS-fornyelsesreceipt peger på et link.')
                else:
                    journal_path.replace(archive)
                journal = None
        else:
            if resume:
                raise ValueError('Der findes ingen TLS-fornyelse at genoptage.')
            journal = None
        if journal is None:
            record = selector(runtime)
            selected_ip = _server_ip(record, server_ip)
            journal = {
                'format': 1, 'runtime_root': str(runtime),
                'source_record': record, 'server_ip': selected_ip,
                'security_root': str(Path(record['data_root']) / 'security'),
                'operation_id': secrets.token_hex(16), 'phase': 'requested',
                'task_receipt': None, 'renewal_receipt': None,
            }
            _save(journal_path, journal, 'requested')

        # Never stop a task until the current active selection still equals
        # the one captured in the journal. Legacy/no-selection installs fail
        # here, before any Windows task action.
        current = selector(runtime)
        if current != journal['source_record']:
            raise ValueError('Det aktive generationsvalg er ændret; TLS-fornyelsen er spærret.')

        phase = journal['phase']
        if phase == 'complete':
            return {'state': 'complete', 'server_ip': journal['server_ip']}
        if phase == 'requested':
            journal['task_receipt'] = controller.capture(runtime, journal['source_record'])
            _save(journal_path, journal, 'captured')
            phase = 'captured'
        if phase in {'captured', 'stopping'}:
            if selector(runtime) != journal['source_record']:
                raise ValueError('Generationsvalget ændrede sig før nedlukningen.')
            _save(journal_path, journal, 'stopping')
            controller.stop(runtime, journal['source_record'], journal['task_receipt'])
            if selector(runtime) != journal['source_record']:
                raise ValueError('Generationsvalget ændrede sig under nedlukningen.')
            _save(journal_path, journal, 'stopped')
            phase = 'stopped'
        if phase in {'stopped', 'renewing'}:
            _save(journal_path, journal, 'renewing')
            with locker(journal['source_record']):
                result = renewer(Path(journal['security_root']), journal['server_ip'], runtime,
                                 journal['operation_id'])
            journal['renewal_receipt'] = _validate_receipt(result, journal['operation_id'], journal['server_ip'])
            _save(journal_path, journal, 'renewed')
            phase = 'renewed'
        if phase in {'renewed', 'starting'}:
            if selector(runtime) != journal['source_record']:
                raise ValueError('Generationsvalget ændrede sig før genstart.')
            _save(journal_path, journal, 'starting')
            controller.start(runtime, journal['source_record'], journal['task_receipt'])
            _save(journal_path, journal, 'complete')
        return {'state': 'complete', 'server_ip': journal['server_ip']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('action', choices=('renew', 'resume'))
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--server-ip')
    args = parser.parse_args(argv)
    try:
        result = execute(runtime_root=args.runtime_root, server_ip=args.server_ip,
                         resume=args.action == 'resume')
        print(json.dumps({'ok': True, **result}, ensure_ascii=True))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=True))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
