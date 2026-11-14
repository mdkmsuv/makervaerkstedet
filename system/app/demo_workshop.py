"""Explicit, persistent local simulation; never the default school setup."""
import argparse
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from .instance import InstanceLock
from .backup_guard import assert_not_restored

ROOT = Path(__file__).resolve().parents[1]
CREDIT = 'Kreditering: Klostermarksskolen.'
COMPONENTS = {'print': 'printvaerksted', 'laser': 'designvaerksted', 'hub': 'hub'}
MARKER = {'schema': 1, 'mode': 'simulation', 'product': 'Makerværkstedet',
          'credits': CREDIT, 'components': COMPONENTS}


def _read(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8192:
        raise ValueError('Ugyldig DEMO-markering.')
    return json.loads(path.read_text(encoding='utf-8'))


def _write(path, value):
    temporary = path.with_suffix('.pending')
    if path.is_symlink() or temporary.is_symlink():
        raise ValueError('DEMO-filer må ikke være symbolske links.')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def component_marker(component):
    return {k: v for k, v in MARKER.items() if k != 'components'} | {'component': component}


def demo_component(folder):
    path = Path(folder) / 'demo-component.json'
    if not path.exists() and not path.is_symlink():
        return None
    value = _read(path)
    role = value.get('component') if isinstance(value, dict) else None
    if role not in COMPONENTS or value != component_marker(role):
        raise ValueError('Ugyldig DEMO-komponent. Produktionsdrift ændres ikke.')
    return role


def demo_root(folder, required=False):
    root = Path(folder)
    path = root / 'demo-workshop.json'
    if not path.exists() and not path.is_symlink():
        if required:
            raise ValueError('Denne datarod er ikke en separat DEMO.')
        return False
    if root.is_symlink() or _read(path) != MARKER:
        raise ValueError('Ugyldig DEMO-datarod.')
    for role, name in COMPONENTS.items():
        if (root / name).is_symlink() or demo_component(root / name) != role:
            raise ValueError('DEMO-komponenterne er ufuldstændige eller ændrede.')
    return True


def demo_printers(settings):
    from .worker import demo_configs
    known = {p['id']: p for p in demo_configs()}
    rows = settings.get('printers') or list(known.values())
    if not isinstance(rows, list) or not rows:
        raise ValueError('DEMO kræver simulerede printere.')
    result, identifiers = [], set()
    for row in rows:
        if (not isinstance(row, dict) or row.get('id') not in known or row['id'] in identifiers
                or row.get('model') != known[row['id']]['model']
                or any(row.get(field) for field in ('host', 'serial', 'access_code'))):
            raise ValueError('DEMO må kun bruge de kendte simulerede printere uden forbindelsesoplysninger.')
        identifiers.add(row['id'])
        value = deepcopy(row)
        name = str(value.get('name', known[row['id']]['name']))
        value.update(name=name if name.startswith('DEMO · ') else 'DEMO · ' + name,
                     location='SIMULATION – ingen fysisk printer', host='', serial='', access_code='')
        value.setdefault('enabled', True)
        value.setdefault('version', 1)
        value.setdefault('bed_type', 'Textured PEI Plate')
        if value.get('workflow') is not None:
            from .hardware_workflows import validate_binding
            value['workflow'] = validate_binding(value['workflow'])
        result.append(value)
    return result


def demo_driver(config, queue, _profiles):
    from types import SimpleNamespace
    from .worker import demo_drivers
    checked = demo_printers({'printers': [config]})[0]
    return demo_drivers(SimpleNamespace(configs={checked['id']: checked}))[checked['id']]


def _hub_modules():
    # Only the hub shipped beside this source module, never caller-chosen code.
    hub = ROOT / 'hub'
    if not (hub / 'makerspace_store.py').is_file():
        hub = ROOT / 'linux-elevhub/server/elevhub'
    sys.path.insert(0, str(hub))
    from makerspace_store import MakerspaceStore
    from makerspace_workflows import templates
    return MakerspaceStore, templates


def setup(folder):
    root = Path(folder)
    if root.is_symlink():
        raise ValueError('DEMO-dataroden må ikke være et symbolsk link.')
    root = root.resolve()
    assert_not_restored(root)
    existing_marker = root / 'demo-workshop.json'
    if existing_marker.exists():
        if _read(existing_marker) != MARKER:
            raise ValueError('Dataroden har en anden eller ugyldig DEMO-markering.')
    elif root.exists() and any(root.iterdir()):
        raise ValueError('Brug en ny, tom datarod. Eksisterende skole- eller pilotdata må ikke ændres.')
    root.mkdir(parents=True, exist_ok=True)
    with ExitStack() as locks:
        locks.enter_context(InstanceLock(root))
        for role, name in COMPONENTS.items():
            path = root / name
            if path.is_symlink():
                raise ValueError('DEMO-komponenter må ikke være symbolske links.')
            path.mkdir(exist_ok=True)
            marker = path / 'demo-component.json'
            if marker.exists() and demo_component(path) != role:
                raise ValueError('DEMO-komponenten er ændret.')
            locks.enter_context(InstanceLock(path))
            _write(marker, component_marker(role))
        _write(existing_marker, MARKER)
        Store, templates = _hub_modules()
        store = Store(root / 'hub/makerspace.sqlite3')
        actor = {'id': 'teacher:' + hashlib.sha256(b'demo-workshop-setup').hexdigest()[:32],
                 'name': 'DEMO-opsætning', 'role': 'teacher',
                 'scopes': ['makerspace:read', 'workflows:write', 'machines:write', 'jobs:write']}
        def command(action, params, version, key):
            return store.execute(actor, {'action': action, 'params': params,
                'expected_version': version, 'request_id': hashlib.sha256(key.encode()).hexdigest()[:32]})
        bindings = {}
        for kind in ('3d_print', 'laser'):
            definition = templates()[kind]
            definition['name'] = 'DEMO · ' + definition['name']
            for step in definition['steps']:
                step['label'] = 'DEMO · ' + step['label']
            created = command('workflow.create', {'definition': definition}, 0, kind + '-create')['result']
            published = command('workflow.publish', {'id': created['id']}, 1, kind + '-publish')['result']
            bindings[kind] = {'id': created['id'], 'revision': published['active_revision'], 'definition': definition}
        settings_path = root / 'printvaerksted/settings.json'
        if settings_path.exists():
            # Repeat setup verifies the existing simulation, never overwrites jobs
            # or the teacher's later workflow choices.
            demo_printers(json.loads(settings_path.read_text(encoding='utf-8')))
        else:
            printers = demo_printers({'printers': [{'id': 'demo-mini', 'name': 'DEMO · A1 mini',
                                                   'model': 'A1 mini', 'enabled': True,
                                                   'workflow': bindings['3d_print']}]})
            _write(settings_path, {'studio_path': '', 'printers': printers,
                                  'collect_completion_samples': False})
        from .laser_server import Inbox
        inbox = Inbox(root / 'designvaerksted')
        def laser(action, params, version, key):
            return inbox.workflows.execute(actor, {'action': action, 'params': params,
                'expected_version': version, 'request_id': hashlib.sha256(key.encode()).hexdigest()[:32]})
        station = {'id': 'demo-laser', 'name': 'DEMO · Laser – ingen maskine',
                   'location': 'SIMULATION – ingen fysisk laser', 'model': 'fhexa1', 'enabled': False}
        laser('laser.station.create', station, 0, 'laser-create')
        laser('laser.station.update', {**station, 'enabled': True}, 1, 'laser-enable')
        laser('laser.workflow.assign', {'id': station['id'], 'workflow': bindings['laser']}, 2, 'laser-workflow')
        # The laser generates its own bridge key; print does so on runtime start.
        return {'mode': 'simulation', 'credits': CREDIT, 'data_root': str(root),
                'components': {role: str(root / name) for role, name in COMPONENTS.items()},
                'printer_id': 'demo-mini', 'laser_station_id': 'demo-laser'}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Separat DEMO af Makerværkstedet – ingen fysiske maskiner.')
    parser.add_argument('action', choices=('setup', 'check', 'start'))
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--role-ip')
    parser.add_argument('--teacher-port', type=int)
    parser.add_argument('--teacher-address')
    parser.add_argument('--runtime-root', type=Path)
    parser.add_argument('--manifest-sha256')
    args = parser.parse_args(argv)
    if args.action == 'setup':
        print(json.dumps(setup(args.data_root), ensure_ascii=False, indent=2))
        return 0
    demo_root(args.data_root, required=True)
    if args.action == 'check':
        print('DEMO · varige testkøer, ingen fysisk printer eller laser. ' + CREDIT)
        return 0
    bootstrap = ROOT / 'server/bootstrap.py'
    if not bootstrap.is_file():
        raise ValueError('Start DEMO fra den installerede serverpakke.')
    config = _read(args.data_root / 'konfiguration.json')
    role_ip = args.role_ip or config.get('elev_adresse')
    teacher_address = args.teacher_address or config.get('laerer_adresse')
    teacher_port = args.teacher_port if args.teacher_port is not None else config.get('laerer_port')
    if not isinstance(role_ip, str) or not isinstance(teacher_address, str) or type(teacher_port) is not int:
        raise ValueError('DEMO kræver sin egen normale server-/TLS-konfiguration.')
    command = [sys.executable, '-B', str(bootstrap), '--package-root', str(ROOT),
               '--data-root', str(args.data_root.resolve()), '--role-ip', role_ip,
               '--teacher-port', str(teacher_port), '--teacher-address', teacher_address]
    if args.runtime_root is not None:
        command += ['--runtime-root', str(args.runtime_root)]
    if args.manifest_sha256 is not None:
        command += ['--manifest-sha256', args.manifest_sha256]
    return subprocess.call(command)


if __name__ == '__main__':
    raise SystemExit(main())
