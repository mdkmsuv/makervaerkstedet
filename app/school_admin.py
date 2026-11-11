"""Local school-menu actions under the selected generation's lifetime lease."""
import argparse
import ipaddress
import json
from pathlib import Path
import sys

from .generation_runtime import active_details, active_selection_sha256
from .generation_selection import _plain, _read, _unique


def _json(path):
    value = json.loads(_read(_plain(path, False)).decode('utf-8-sig'), object_pairs_hook=_unique)
    if type(value) is not dict:
        raise ValueError('Skolekonfigurationen skal være et JSON-objekt.')
    return value


def _port(value):
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError('Skolepanelets port er ugyldig.')
    return value


def codes(data):
    result = []
    for folder, label in (('printvaerksted', 'Printværksted'), ('designvaerksted', 'Laserværksted')):
        path = data / folder / 'laererkode.txt'
        try:
            path.lstat()
        except FileNotFoundError:
            code = None
        else:
            code = _read(_plain(path, False)).decode('utf-8-sig').strip()
            if not code or any(not char.isprintable() for char in code):
                raise ValueError('Lærerkoden har et ugyldigt format.')
        result.append({'label': label, 'code': code})
    return result


def urls(data):
    config = _json(data / 'konfiguration.json')
    teacher = ipaddress.IPv4Address(config['laerer_adresse'])
    server = ipaddress.IPv4Address(config['elev_adresse'])
    if (teacher.is_unspecified or teacher.is_multicast
            or str(teacher) not in ('127.0.0.1', str(server))):
        raise ValueError('Lærerpanel skal høre til den lokale serverinstallation.')
    base = 'https://' + str(teacher) + ':' + str(_port(config['laerer_port']))
    backend = config.get('backend_ports', {})
    if type(backend) is not dict:
        raise ValueError('Backendportene er ugyldige.')
    value = {'teacher': base, 'design': 'http://127.0.0.1:' + str(_port(backend.get('teacher', 8767))),
             'controlpanel': base + '/kontrolpanel', 'laser': None}
    profile = _json(data / 'school.json')
    if profile.get('role') == 'makerspace':
        value['laser'] = laser_url(data)
    elif profile.get('role') != 'vm':
        raise ValueError('Skolens rolle er ugyldig.')
    return value


def laser_url(data):
    # A logged-in station user can read its public config without needing
    # administrator-only school metadata or the server's configuration file.
    station = _json(data / 'laserstation/laser-installation.json')
    return 'http://127.0.0.1:' + str(_port(station.get('port', 8790))) + '/'


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('action', choices=('inspect', 'codes', 'urls', 'laser-url', 'lease',
                                         'backup-create', 'backup-verify', 'backup-restore'))
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args()
    record = active_details()
    data = _plain(args.data_root)
    if data != Path(record['data_root']):
        raise ValueError('Skoleadministrationen tilhører en anden datageneration.')
    if not args.action.startswith('backup-') and (args.source is not None or args.destination is not None):
        raise ValueError('Filstierne hører ikke til denne skolehandling.')
    if args.action.startswith('backup-'):
        from .school_backup import execute
        value = execute(args.action.removeprefix('backup-'), data_root=data,
                        runtime_root=record['runtime_root'], active_record=record,
                        source=args.source, destination=args.destination)
        value['ok'] = True
    elif args.action in ('inspect', 'lease'):
        value = {key: record[key] for key in ('runtime_root', 'program_root', 'data_root', 'installation_id', 'revision')}
        if args.action == 'lease':
            value['selection_sha256'] = active_selection_sha256()
    elif args.action == 'codes':
        value = codes(data)
    elif args.action == 'laser-url':
        value = {'laser': laser_url(data)}
    else:
        value = urls(data)
    # ASCII JSON also survives Windows PowerShell's legacy pipe encoding.
    print(json.dumps(value, ensure_ascii=True))
    sys.stdout.flush()
    if args.action == 'lease':
        # The active-generation lease belongs to this process.  Keep it alive
        # until the supervising wrapper closes stdin or the process is killed.
        sys.stdin.buffer.read()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
