"""Explicit preparation of a replacement Windows host from a school backup.

The original owner, LAN, TLS and SSH identities are retained. Preparation is
offline and isolated; only the installer may release its holds after securing
the copied data and verifying the exact original program package. No task,
firewall, certificate store or hardware is controlled by this module.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid
import types

from . import installation_backup as backup
from .generation_relocation import relocate_school_files
from .generation_transaction import _atomic
from .release_compatibility import CREDIT, inspect_program

RECEIPT = 'school-recovery.json'
REQUEST = 'school-recovery-request.json'
FIELDS = ('schemaVersion', 'product', 'ownerId', 'taskName', 'startupProtocol',
          'generationLayout', 'roleIp', 'roleNetwork', 'studentSubnet',
          'teacherAddress', 'teacherPort', 'printBackendPort', 'designBackendPort',
          'teacherBackendPort', 'externalBackends', 'firewallNames', 'caThumbprint',
          'packageManifestSha256', 'releaseManifestSha256', 'installRoot', 'dataRoot')


def _new_path(value):
    path = Path(value)
    if not path.is_absolute() or str(path).startswith(('\\\\', '//')):
        raise ValueError('Gendannelse kræver en absolut lokal sti.')
    return backup._plain(path.parent, True) / backup._relative(path.name)


def _package(package, owner):
    package = backup._plain(Path(package), True)
    release = inspect_program(package)
    if (backup._hash(backup._plain(package / 'integrity.json', False)) != owner['packageManifestSha256']
            or release['manifest_sha256'] != owner['releaseManifestSha256']):
        raise ValueError('Værtsgendannelse kræver den oprindelige, uændrede installationspakke.')
    # This is explicitly selected, manifest-checked installer code, not a
    # discovery/import of a runtime or a school-local Python module.
    path = backup._plain(package / 'server/provision.py', False)
    raw = path.read_bytes()
    manifest_raw = backup._plain(package / 'makerspace-release.json', False).read_bytes()
    if hashlib.sha256(manifest_raw).hexdigest() != owner['releaseManifestSha256']:
        raise ValueError('Pakkens manifest ændredes efter verifikation.')
    manifest = json.loads(manifest_raw.decode('utf-8'))
    if hashlib.sha256(raw).hexdigest() != manifest['files']['server/provision.py']['sha256']:
        raise ValueError('Pakkens identitetskontrol ændredes efter verifikation.')
    provision = types.ModuleType('school_recovery_provision')
    provision.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), provision.__dict__)
    return package, release, provision


def _profile(data):
    return {'format': 1, 'roots': {'server': {'path': str(data), 'role': 'server'}},
            'components': {role: {'root': 'server', 'path': folder} for role, folder in
                           (('hub', 'hub'), ('print', 'printvaerksted'), ('laser', 'designvaerksted'))},
            'config': {'root': 'server', 'path': 'konfiguration.json'}}


def _school_identity(copied, owner):
    from cryptography.hazmat.primitives import serialization
    school = backup._json(backup._plain(copied / 'school.json', False))
    if (type(school.get('schemaVersion')) is not int or school['schemaVersion'] != 1
            or school.get('role') not in ('vm', 'makerspace')
            or school.get('server') != owner['roleIp'] or school.get('student_subnet') != owner['studentSubnet']):
        raise ValueError('Backupens skoleprofil matcher ikke serveridentiteten.')
    private = backup._plain(copied / 'admin-ssh/id_ed25519', False).read_bytes()
    public = backup._plain(copied / 'admin-ssh/id_ed25519.pub', False).read_bytes()
    key = serialization.load_ssh_private_key(private, password=None)
    derived = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
    if (derived.split()[0] != b'ssh-ed25519' or b' '.join(public.split()[:2]) != derived
            or not isinstance(school.get('admin_public_key'), str)
            or b' '.join(school['admin_public_key'].encode('ascii').split()[:2]) != derived):
        raise ValueError('Backupens private SSH-nøgle matcher ikke skolens offentlige identitet.')
    return school


def _inventory(data):
    config = backup._json(data / 'konfiguration.json')
    owner = backup._json(data / 'installation-owner.json')
    registry = Path(config['enhedsregister'])
    relative = registry.relative_to(Path(owner['dataRoot'])) if registry.is_absolute() else registry
    backup._relative(relative.as_posix())
    ignored = {data / relative.with_suffix('.lock'), data / 'instance.lock'}
    for folder in ('hub', 'printvaerksted', 'designvaerksted'):
        ignored.update(data / folder / child / 'instance.lock' for child in ('', 'supervisor', 'service'))
    ignored.update((data / 'security/enrollment/instance.lock',
                    data / 'security/enrollment/operations/instance.lock'))
    station = data / 'laserstation/laser-installation.json'
    if station.exists():
        plates = Path(backup._json(station)['plates']).relative_to(Path(owner['dataRoot']))
        backup._relative(plates.as_posix())
        ignored.add(data / plates / 'instance.lock')
    files, directories = backup._scan(data, ignore_locks=False, ignored_files=ignored)
    return {'files': {name: {'sha256': backup._hash(data / name), 'size': signature[2]}
                     for name, signature in sorted(files.items()) if name not in (RECEIPT, 'installation-owner.json')},
            'directories': sorted(directories)}


def plan(source, package, runtime_root, data_root):
    source = backup._plain(Path(source), True)
    backup.verify(source)
    manifest = backup._manifest(source)
    if manifest['profile'] != dict(_profile(Path(manifest['profile']['roots']['server']['path']))):
        raise ValueError('Værtsgendannelse kræver skolens samlede standardlayout.')
    copied = source / 'roots/server'
    owner = backup._json(backup._plain(copied / 'installation-owner.json', False))
    config = backup._json(backup._plain(copied / 'konfiguration.json', False))
    if (type(owner.get('schemaVersion')) is not int or owner['schemaVersion'] != 1
            or Path(owner.get('dataRoot', '')) != Path(manifest['profile']['roots']['server']['path'])
            or owner.get('state') != 'installed' or owner.get('startupProtocol') != 'generation-v1'
            or owner.get('generationLayout') is not True or owner.get('taskName') != 'ElevhubServer'
            or owner.get('product') != 'ElevhubServer' or owner.get('externalBackends')
            or config.get('external_backends')):
        raise ValueError('Backupen tilhører ikke en fuldført samlet generation-v1 skoleinstallation.')
    if uuid.UUID(owner['ownerId']).int == 0:
        raise ValueError('Backupens skoleidentitet er ugyldig.')
    package, release, provision = _package(package, owner)
    school = _school_identity(copied, owner)
    runtime, data = _new_path(runtime_root), _new_path(data_root)
    if (runtime == data or runtime.is_relative_to(data) or data.is_relative_to(runtime)
            or any(data == path or data.is_relative_to(path) or path.is_relative_to(data)
                   or runtime == path or runtime.is_relative_to(path) or path.is_relative_to(runtime)
                   for path in (source, package))):
        raise ValueError('En erstatningsvært kræver nye, adskilte program- og datarødder.')
    provision._tls_preflight(copied / 'security', owner['roleIp'], owner['ownerId'])
    request = {'format': 1, 'credits': CREDIT, 'source': str(source),
               'backup_sha256': backup._hash(source / backup.MANIFEST),
               'package': str(package), 'package_sha256': owner['packageManifestSha256'],
               'runtime_root': str(runtime), 'data_root': str(data),
               'school': {key: owner[key] for key in FIELDS if key not in ('installRoot', 'dataRoot')},
               'school_profile': school}
    if runtime.exists() and not data.exists():
        raise ValueError('Erstatningsværtens programmappe findes allerede.')
    return request


def prepare(source, package, runtime_root, data_root):
    request = plan(source, package, runtime_root, data_root)
    source, package = Path(request['source']), Path(request['package'])
    runtime, data = Path(request['runtime_root']), Path(request['data_root'])
    if data.exists():
        receipt = backup._json(backup._plain(data / RECEIPT, False))
        if receipt['request'] != request:
            raise ValueError('Den bevarede gendannelse tilhører en anden bestilling.')
        check(package, runtime, data)
        return {'state': receipt['phase'], 'data_root': str(data), 'credits': CREDIT}
    if runtime.exists():
        raise ValueError('Erstatningsværtens programmappe findes allerede.')
    # Never adopt an unfinished staging directory, including one whose first
    # request write was torn by power loss. A retry uses a fresh owned folder
    # and leaves every previous copy/receipt available for inspection.
    stage = data.parent / ('.' + data.name + '.school-recovery-' + uuid.uuid4().hex)
    stage.mkdir(mode=0o700)
    backup._private_directory(stage)
    backup._write(stage / REQUEST, request)
    # A partial restore is preserved for inspection; retry always creates a
    # fresh copy from the verified backup and never blesses partial bytes.
    attempt = stage / ('restore-' + uuid.uuid4().hex)
    restored_report = backup.restore(source, attempt)
    if restored_report['source_manifest_sha256'] != request['backup_sha256']:
        raise ValueError('Backupen er ændret siden værtsgendannelsens bestilling.')
    restored = attempt / 'roots/server'
    config_path = restored / 'konfiguration.json'
    config = backup._relocate_config(backup._json(config_path), {'server': restored}, {'server': data}, restored)
    _atomic(config_path, config)
    values = {'installation-owner.json': backup._json(restored / 'installation-owner.json')}
    station = restored / 'laserstation/laser-installation.json'
    if station.exists():
        values['laserstation/laser-installation.json'] = backup._json(station)
    for name, value in relocate_school_files(restored, data, values).items():
        if name == 'installation-owner.json':
            value.update(installRoot=str(runtime), state='recoveryPrepared')
        _atomic(restored / name, value)
    expected_owner = backup._json(restored / 'installation-owner.json')
    _package(package, expected_owner)
    inventory = _inventory(restored)
    holds = {name: row for name, row in inventory['files'].items() if Path(name).name == backup.HOLD_FILE}
    receipt = {'format': 1, 'credits': CREDIT, 'request': request, 'phase': 'prepared',
               'owner': {key: expected_owner[key] for key in FIELDS},
               'inventory': inventory, 'holds': holds}
    _atomic(restored / RECEIPT, receipt)
    # Both paths share the parent filesystem. Windows rename refuses an
    # existing destination, including one created after the initial check.
    if os.name != 'nt':
        raise ValueError('Værtsgendannelse er kun understøttet på Windows.')
    os.rename(restored, data)
    check(package, runtime, data)
    return {'state': 'prepared', 'data_root': str(data), 'credits': CREDIT}


def check(package, runtime_root, data_root):
    data = backup._plain(Path(data_root), True)
    receipt = backup._json(backup._plain(data / RECEIPT, False))
    if (receipt.get('format') != 1 or receipt.get('credits') != CREDIT
            or receipt.get('phase') not in ('prepared', 'releasing', 'released', 'started', 'complete')
            or receipt['request']['runtime_root'] != str(Path(runtime_root).absolute())
            or receipt['request']['data_root'] != str(data)):
        raise ValueError('Værtsgendannelsens binding eller fase er ugyldig.')
    owner = backup._json(backup._plain(data / 'installation-owner.json', False))
    if {key: owner.get(key) for key in FIELDS} != receipt['owner']:
        raise ValueError('Værtsgendannelsens skoleidentitet eller Windows-layout er ændret.')
    package, release, provision = _package(package, owner)
    provision._tls_preflight(data / 'security', owner['roleIp'], owner['ownerId'])
    if receipt['phase'] in ('started', 'complete'):
        if (owner.get('state') not in ('installing', 'rolledBack', 'rollbackFailed', 'installed')
                or receipt['phase'] == 'complete' and owner.get('state') != 'installed'
                or any((data / name).exists() for name in receipt['holds'])):
            raise ValueError('En afsluttet værtsgendannelse kræver en installeret ejer.')
        return {'state': receipt['phase'], 'credits': CREDIT, 'ownerId': owner['ownerId']}
    with backup._locked_profile(_profile(data), allow_restored=True):
        _verify_inventory(data, receipt)
    return {'state': receipt['phase'], 'credits': CREDIT, 'ownerId': owner['ownerId']}


def _verify_inventory(data, receipt):
    actual = _inventory(data)
    expected = dict(receipt['inventory']['files'])
    if receipt['phase'] != 'prepared':
        for name in receipt['holds']:
            if name not in actual['files']:
                expected.pop(name)
    # The installer can create its canonical empty runtime directories. Every
    # other directory and even an unrelated file named instance.lock remains
    # part of the preserved-data contract.
    permitted = set(receipt['inventory']['directories']) | {'logs', 'local-ai'}
    if (actual['files'] != expected or not set(receipt['inventory']['directories']) <= set(actual['directories'])
            or not set(actual['directories']) <= permitted):
        raise ValueError('Gendannelsens klargjorte data er ændret; startspærrer bevares.')


def release(package, runtime_root, data_root):
    data = backup._plain(Path(data_root), True)
    check(package, runtime_root, data)
    owner = backup._json(data / 'installation-owner.json')
    if owner.get('state') != 'installing':
        raise ValueError('Kun den ejede installer må frigive en klargjort værtsgendannelse.')
    installed = backup._plain(Path(runtime_root) / 'program', True)
    _package(installed, owner)
    if backup._json(data / RECEIPT)['phase'] == 'started':
        return {'state': 'released', 'credits': CREDIT}
    with backup._locked_profile(_profile(data), allow_restored=True):
        receipt = backup._json(data / RECEIPT)
        _verify_inventory(data, receipt)
        if receipt['phase'] != 'released':
            receipt['phase'] = 'releasing'
            _atomic(data / RECEIPT, receipt)
            for name, row in receipt['holds'].items():
                path = data / name
                if path.exists():
                    if backup._hash(backup._plain(path, False)) != row['sha256']:
                        raise ValueError('Gendannelsens startspærre er ændret.')
                    path.unlink()
            receipt['phase'] = 'released'
            _atomic(data / RECEIPT, receipt)
    return {'state': 'released', 'credits': CREDIT}


def started(package, runtime_root, data_root):
    data = backup._plain(Path(data_root), True)
    check(package, runtime_root, data)
    receipt = backup._json(data / RECEIPT)
    owner = backup._json(data / 'installation-owner.json')
    if receipt['phase'] not in ('released', 'started') or owner.get('state') != 'installing':
        raise ValueError('Opstart kræver en frigivet, ejet værtsgendannelse.')
    _package(Path(runtime_root) / 'program', owner)
    receipt['phase'] = 'started'
    _atomic(data / RECEIPT, receipt)
    return {'state': 'started', 'credits': CREDIT}


def complete(package, runtime_root, data_root):
    """Record the installer's successful readiness boundary, retaining history.

    A completed installation may already contain accepted student changes.
    The old prepared inventory is never used to overwrite or reject them.
    """
    data = backup._plain(Path(data_root), True)
    receipt = backup._json(backup._plain(data / RECEIPT, False))
    owner = backup._json(backup._plain(data / 'installation-owner.json', False))
    if (receipt.get('phase') not in ('started', 'complete') or owner.get('state') != 'installed'
            or {key: owner.get(key) for key in FIELDS} != receipt['owner']
            or receipt['request']['data_root'] != str(data)
            or receipt['request']['runtime_root'] != str(Path(runtime_root).absolute())):
        raise ValueError('Værtsgendannelsen har ikke fuldført sin ejede installation.')
    _package(Path(runtime_root) / 'program', owner)
    if any((data / name).exists() for name in receipt['holds']):
        raise ValueError('Værtsgendannelsens startspærrer er ikke frigivet.')
    receipt['phase'] = 'complete'
    _atomic(data / RECEIPT, receipt)
    return {'state': 'complete', 'credits': CREDIT}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('action', choices=('plan', 'prepare', 'check', 'release', 'started', 'complete'))
    parser.add_argument('--source', type=Path)
    parser.add_argument('--package-root', required=True, type=Path)
    parser.add_argument('--runtime-root', required=True, type=Path)
    parser.add_argument('--data-root', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action in ('plan', 'prepare'):
            if args.source is None:
                raise ValueError('Angiv den verificerede skolebackup.')
            result = globals()[args.action](args.source, args.package_root, args.runtime_root, args.data_root)
        else:
            if args.source is not None:
                raise ValueError('Backupkilden hører kun til klargøringen.')
            result = globals()[args.action](args.package_root, args.runtime_root, args.data_root)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        print('Værtsgendannelse afvist: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
