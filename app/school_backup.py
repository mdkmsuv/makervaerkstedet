"""Local operator entry for the school's existing offline backup protocol.

No service control, live replacement or activation is performed here. A normal
generation school-admin lease may wrap this module: backup only needs data
locks, not the exclusive generation selector lock used by an update.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

from . import installation_backup as backup

CREDIT = 'Kreditering: Klostermarksskolen.'
FOLDERS = {'hub': 'hub', 'print': 'printvaerksted', 'laser': 'designvaerksted'}


def school_profile(data_root, runtime_root, *, active_record=None):
    """Resolve the complete supported school layout, never guess missing queues."""
    data = backup._plain(Path(data_root), True)
    runtime = backup._plain(Path(runtime_root), True)
    owner = backup._json(backup._plain(data / 'installation-owner.json', False), 64 * 1024)
    if (type(owner.get('schemaVersion')) is not int or owner['schemaVersion'] != 1
            or owner.get('product') != 'ElevhubServer' or owner.get('state') != 'installed'
            or owner.get('taskName') != 'ElevhubServer'
            or backup._plain(Path(owner.get('dataRoot', '')), True) != data
            or backup._plain(Path(owner.get('installRoot', '')), True) != runtime):
        raise ValueError('Backup kræver en færdig installation med matchende ejer og datamappe.')
    try:
        identity = uuid.UUID(owner.get('ownerId', '')).hex
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Installationen mangler en gyldig ejeridentitet.') from None
    config = backup._json(backup._plain(data / 'konfiguration.json', False), 1024 * 1024)
    if owner.get('externalBackends') or config.get('external_backends'):
        raise ValueError('Denne menu kræver samlede lokale kødata. Brug en fuld backup-profil til eksterne backends.')
    if active_record is not None:
        if (owner.get('startupProtocol') != 'generation-v1'
                or active_record.get('installation_id') != identity
                or Path(active_record['data_root']) != data
                or Path(active_record['runtime_root']) != runtime):
            raise ValueError('Backupens datamappe matcher ikke den aktive programgeneration.')
    elif owner.get('startupProtocol') is not None or (runtime / 'control').exists():
        raise ValueError('Generationsdata skal åbnes gennem den installerede skoleadministration.')
    return {'format': 1, 'roots': {'server': {'path': str(data), 'role': 'server'}},
            'components': {role: {'root': 'server', 'path': folder} for role, folder in FOLDERS.items()},
            'config': {'root': 'server', 'path': 'konfiguration.json'}}


def execute(action, *, data_root=None, runtime_root=None, source=None, destination=None, active_record=None):
    # Exact argument contracts prevent a typo from silently selecting another operation.
    if action == 'create':
        if data_root is None or runtime_root is None or destination is None or source is not None:
            raise ValueError('Angiv installation og en ny lokal backupmappe.')
        profile = school_profile(data_root, runtime_root, active_record=active_record)
        result = backup.snapshot(profile, destination)
        return {'action': action, 'verified': True, 'folder': str(Path(destination).absolute()),
                'credits': CREDIT, 'result': result}
    if action == 'verify':
        if source is None or destination is not None:
            raise ValueError('Angiv den backupmappe, der skal kontrolleres.')
        return {'action': action, 'verified': True, 'folder': str(Path(source).absolute()),
                'credits': CREDIT, 'result': backup.verify(source)}
    if action == 'restore':
        if source is None or destination is None:
            raise ValueError('Angiv backupen og en ny lokal mappe til gendannelsesprøven.')
        result = backup.restore(source, destination)
        return {'action': action, 'isolated': True, 'activation_ready': False,
                'folder': str(Path(destination).absolute()), 'credits': CREDIT, 'result': result}
    raise ValueError('Ukendt backuphandling.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('action', choices=('create', 'verify', 'restore'))
    parser.add_argument('--data-root', type=Path)
    parser.add_argument('--runtime-root', type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args(argv)
    try:
        result = execute(args.action, data_root=args.data_root, runtime_root=args.runtime_root,
                         source=args.source, destination=args.destination)
        print(json.dumps({'ok': True, **result}, ensure_ascii=True))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=True))
        return 1


if __name__ == '__main__':
    sys.exit(main())
