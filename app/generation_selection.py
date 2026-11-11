"""Read one program/data selection while holding its lifetime lease.

Stdlib only: the stable launcher loads this file before importing any app.
This module never activates a release, removes restore holds or repairs a
pending transaction. The installer must create and protect the control files.
"""
import hashlib
import json
from pathlib import Path
import re
import runpy
import stat


ACTIVE = 'active-generation.json'
LOCK = 'generation.lock'
TRANSITION = 'selection-transition.json'
MAX_SELECTION = 64 * 1024


def _present(path):
    # lstat also detects broken junctions/reparse points on Windows.
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def _read(path):
    with path.open('rb') as stream:
        raw = stream.read(MAX_SELECTION + 1)
    if len(raw) > MAX_SELECTION:
        raise ValueError('Generationsmetadata er for store.')
    return raw


def _plain(path, directory=True):
    path = Path(path)
    if not path.is_absolute() or str(path).startswith(('\\\\', '//')):
        raise ValueError('Generationsstier skal være absolutte stier på lokale drev.')
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Generationsstier må ikke indeholde links eller reparse points.')
    if directory and not path.is_dir() or not directory and not path.is_file():
        raise ValueError('En generationssti mangler eller har forkert type.')
    return path.resolve(strict=True)


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Dubleret nøgle i generationsvalget.')
        value[key] = item
    return value


def _hash(value, length=64):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{' + str(length) + '}', value) is not None


def validate_record(value, runtime_root):
    keys = {'format', 'installation_id', 'revision', 'runtime_root',
            'program_root', 'data_root', 'manifest_sha256'}
    if (type(value) is not dict or set(value) != keys or type(value['format']) is not int
            or value['format'] != 1 or not _hash(value['installation_id'], 32)
            or type(value['revision']) is not int or not 1 <= value['revision'] <= 2 ** 63 - 1
            or not _hash(value['manifest_sha256'])
            or any(type(value[key]) is not str or not value[key] for key in ('runtime_root', 'program_root', 'data_root'))):
        raise ValueError('Ukendt eller ufuldstændigt generationsvalg.')
    roots = {key: _plain(value[key]) for key in ('runtime_root', 'program_root', 'data_root')}
    if roots['runtime_root'] != _plain(runtime_root):
        raise ValueError('Generationsvalget tilhører en anden runtime.')
    data = roots['data_root']
    for key in ('runtime_root', 'program_root'):
        program = roots[key]
        if data == program or data.is_relative_to(program) or program.is_relative_to(data):
            raise ValueError('Program og runtime skal være adskilt fra generationsdata.')
    # Keep one canonical spelling in the record and in every child command.
    return dict(value, **{key: str(path) for key, path in roots.items()})


def _lock_class():
    # Do not import stable app here: it would poison the later source selection.
    source = _plain(Path(__file__).with_name('generation_lock.py'), False)
    return runpy.run_path(str(source), run_name='_makerspace_generation_lock')['GenerationLock']


class SelectionLease:
    def __init__(self, runtime_root, expected_sha256=None):
        self.runtime_root = _plain(runtime_root)
        self.control = _plain(self.runtime_root / 'control')
        if expected_sha256 is not None and not _hash(expected_sha256):
            raise ValueError('Ugyldig identitet for generationsvalget.')
        self.lock = _lock_class()(self.control / LOCK)
        try:
            transition = self.control / TRANSITION
            if _present(transition):
                raise RuntimeError('Et versionsskift er uafsluttet; generationsstart er spærret.')
            path = _plain(self.control / ACTIVE, False)
            raw = _read(path)
            self.sha256 = hashlib.sha256(raw).hexdigest()
            if expected_sha256 is not None and self.sha256 != expected_sha256:
                raise ValueError('Generationsvalget er ændret siden underprocessen blev bestilt.')
            self.record = validate_record(json.loads(raw.decode('utf-8'), object_pairs_hook=_unique), self.runtime_root)
            data = Path(self.record['data_root'])
            for folder in (data, *data.parents):
                hold = folder / '.restore-hold.json'
                if _present(hold):
                    raise RuntimeError('Datagenerationen er spærret til isoleret gendannelse.')
            if _read(path) != raw or _present(transition):
                raise ValueError('Generationsvalget ændredes under kontrollen.')
        except BaseException:
            self.close()
            raise

    def close(self):
        self.lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def bind_arguments(record, entry, arguments):
    """Bind all data selectors; reject alternate/abbreviated/duplicate overrides."""
    data = Path(record['data_root'])
    paths = {
        'server': {'--data-root': data},
        'enrollment': {'--data-root': data},
        'school-admin': {'--data-root': data},
        'print': {'--data': data / 'printvaerksted'},
        'print-server': {'--data': data / 'printvaerksted'},
        'design': {'--data': data / 'designvaerksted'},
        'hub': {'--data': data / 'hub', '--konfiguration': data / 'konfiguration.json'},
        'laser-station': {'--config': data / 'laserstation/laser-installation.json'},
        'boxes-worker': {},
    }
    if entry not in paths:
        raise ValueError('Ukendt generationsindgang.')
    bound = paths[entry]
    forbidden = {'--package-root', '--runtime-root', '--manifest-sha256'} if entry == 'server' else set()
    if entry == 'laser-station':
        forbidden |= {'--plates', '--beam-studio', '--flux-root', '--server', '--ca'}
        config_path = _plain(bound['--config'], False)
        config = json.loads(_read(config_path).decode('utf-8'), object_pairs_hook=_unique)
        if (type(config) is not dict or type(config.get('plates')) is not str
                or _plain(config['plates']) != _plain(data / 'Laserplader')):
            raise ValueError('Laserstationens plader tilhører en anden datageneration.')
    result, seen = [], set()
    arguments = list(map(str, arguments))
    index = 0
    while index < len(arguments):
        item = arguments[index]
        option, equal, raw = item.partition('=')
        if item == '--':
            raise ValueError('Ekstra argumentseparator er ikke understøttet ved generationsvalg.')
        protected = set(bound) | forbidden
        if option.startswith('--') and any(name.startswith(option) for name in protected):
            if option not in bound or option in forbidden or option in seen:
                raise ValueError('Datavalget fastlægges entydigt af generationsmarkøren.')
            seen.add(option)
            if not equal:
                index += 1
                if index >= len(arguments):
                    raise ValueError('Datastien mangler efter argumentet.')
                raw = arguments[index]
            supplied = Path(raw)
            if (not supplied.is_absolute() or str(supplied).startswith(('\\\\', '//'))
                    or supplied.resolve(strict=False) != bound[option].resolve(strict=False)):
                raise ValueError('En underproces forsøger at bruge en anden datageneration.')
        else:
            result.append(item)
        index += 1
    for option, path in bound.items():
        result.extend([option, str(path)])
    return result
