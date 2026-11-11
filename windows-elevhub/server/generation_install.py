"""Initialize the first generation of a newly owned installation only.

This is not an update, recovery or activation command. A partial control folder
is deliberately left blocked for the installer's ownership-aware rollback.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import uuid

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.generation_lock import GenerationLock
from app.generation_runtime import validate_generation
from app.generation_selection import ACTIVE, LOCK, TRANSITION, _plain, _present, _read, _unique, validate_record
from app.instance import InstanceLock
from app.release_compatibility import inspect_program


def check_package(package_root, runtime_root=None):
    package = _plain(package_root)
    release = inspect_program(package)
    required = {'app/school_admin.py', 'server/generation_install.py',
                'server/generation-startup.ps1', 'deployment/school-setup/enrollment.py'}
    for name in required:
        _plain(package / name, False)
    validate_generation(package, runtime_root or package, release['manifest_sha256'], 'server')
    return release


def _owner(data, runtime, installation_id):
    value = json.loads(_read(_plain(data / 'installation-owner.json', False)).decode('utf-8-sig'),
                       object_pairs_hook=_unique)
    if (type(value) is not dict or type(value.get('schemaVersion')) is not int
            or value.get('schemaVersion') != 1 or value.get('product') != 'ElevhubServer'
            or value.get('state') != 'installing' or value.get('startupProtocol') != 'generation-v1'
            or value.get('taskName') != 'ElevhubServer'):
        raise ValueError('Kun en ny, ejet installation kan få sit første generationsvalg.')
    if (uuid.UUID(value['ownerId']).hex != installation_id
            or _plain(value['installRoot']) != runtime or _plain(value['dataRoot']) != data):
        raise ValueError('Første generationsvalg passer ikke til installationsejeren.')
    for parent in (data, *data.parents):
        if _present(parent / '.restore-hold.json'):
            raise ValueError('En gendannet datamappe må ikke aktiveres af førsteinstallationen.')
    return value


def _write_new(path, value):
    raw = (json.dumps(value, ensure_ascii=True, sort_keys=True) + '\n').encode('ascii')
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def initialize(package_root, runtime_root, data_root, installation_id):
    package, runtime, data = map(_plain, (package_root, runtime_root, data_root))
    if package != runtime / 'program':
        raise ValueError('Første programgeneration skal ligge i installationens programmappe.')
    owner = _owner(data, runtime, installation_id)
    release = check_package(package, runtime)
    record = {'format': 1, 'installation_id': installation_id, 'revision': 1,
              'runtime_root': str(runtime), 'program_root': str(package),
              'data_root': str(data), 'manifest_sha256': release['manifest_sha256']}
    validate_record(record, runtime)
    _plain(data / 'instance.lock', False)
    with InstanceLock(data):
        if _owner(data, runtime, installation_id) != owner:
            raise ValueError('Installationsejeren ændredes under kontrollen.')
        control = runtime / 'control'
        # Exclusive mkdir also refuses broken links and abandoned initialization.
        control.mkdir()
        _write_new(control / TRANSITION, {'format': 1, 'operation': 'initial_install',
                                          'installation_id': installation_id})
        with (control / LOCK).open('xb') as stream:
            stream.write(b'1')
            stream.flush()
            os.fsync(stream.fileno())
        with GenerationLock(control / LOCK, exclusive=True):
            validate_generation(package, runtime, record['manifest_sha256'], 'server')
            pending = control / (ACTIVE + '.pending')
            _write_new(pending, record)
            os.replace(pending, control / ACTIVE)
            (control / TRANSITION).unlink()
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--package-root', type=Path, required=True)
    parser.add_argument('--runtime-root', type=Path)
    parser.add_argument('--data-root', type=Path)
    parser.add_argument('--installation-id')
    parser.add_argument('--check-package', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.check_package:
            if args.runtime_root or args.data_root or args.installation_id:
                raise ValueError('Pakkekontrol kan ikke kombineres med installation.')
            value = check_package(args.package_root)
        else:
            if not all((args.runtime_root, args.data_root, args.installation_id)):
                raise ValueError('Angiv runtime, data og installationsejer.')
            value = initialize(args.package_root, args.runtime_root, args.data_root, args.installation_id)
        print(json.dumps(value, ensure_ascii=True))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        print('Første generationsvalg afvist: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
