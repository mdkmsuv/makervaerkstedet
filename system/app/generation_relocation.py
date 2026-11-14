"""Pure rebasing of selected school JSON values for a new data generation.

This module performs no filesystem access and never validates that an external
program, URL, certificate, or key exists.  Such machine-local references must
be checked by the host when the relocated generation is materialised.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import os
import re


_OWNER = 'installation-owner.json'
_LASER = 'laserstation/laser-installation.json'
_PRIVATE_PATHS = (('ca_file',), ('vision', 'ca_file'), ('direct_transfer', 'client_key_file'))


def _path_text(value, name):
    """Validate lexical path spelling before the platform normalizes it."""
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise ValueError(f'{name} skal være en entydig lokal sti.')
    # Drive-relative (``C:foo``), root-relative (``\\foo``), UNC and mixed
    # dot-segments are ambiguous when a generation is moved between hosts.
    if re.match(r'^[A-Za-z]:($|[^\\/])', value) or (value.startswith('\\') and not value.startswith('\\\\')):
        raise ValueError(f'{name} skal være en entydig lokal sti.')
    if value.startswith(('\\\\', '//')):
        raise ValueError(f'{name} må ikke være en UNC-sti.')
    if any(part in ('.', '..') for part in re.split(r'[\\/]', value)):
        raise ValueError(f'{name} må ikke indeholde . eller ...')
    return value


def _root(value, name):
    value = os.fspath(value) if isinstance(value, (str, os.PathLike)) else value
    raw = _path_text(value, name)
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError(f'{name} skal være en absolut lokal sti.')
    return Path(os.path.abspath(raw))


def _path_key(key):
    if (not isinstance(key, str) or not key or any(ord(char) < 32 for char in key)
            or '\\' in key or ':' in key or key.startswith('/')):
        raise ValueError('Filerne skal have entydige relative POSIX-stier.')
    parts = key.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise ValueError('Filerne skal have entydige relative POSIX-stier.')


def _absolute_reference(value, field):
    value = _path_text(value, field)
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f'{field} skal være en absolut lokal sti.')
    return Path(os.path.abspath(value))


def _rebase_reference(value, field, old_root, new_root):
    path = _absolute_reference(value, field)
    try:
        relative = path.relative_to(old_root)
    except ValueError:
        return value
    if relative == Path('.'):
        raise ValueError(f'{field} må ikke pege tvetydigt på selve dataroden.')
    return str(new_root / relative)


def _owner(value, old_root, new_root):
    if not isinstance(value, dict):
        raise ValueError('installation-owner.json skal være et JSON-objekt.')
    if type(value.get('schemaVersion')) is not int or value['schemaVersion'] != 1:
        raise ValueError('Ejerskabsfilens schemaVersion er ugyldig.')
    if value.get('product') != 'ElevhubServer' or value.get('startupProtocol') != 'generation-v1':
        raise ValueError('Ejerskabsfilens produkt eller opstartsprotokol er ugyldig.')
    data_root = _absolute_reference(value.get('dataRoot'), 'dataRoot')
    if data_root != old_root:
        raise ValueError('Ejerskabsfilens dataRoot passer ikke til den gamle datageneration.')
    result = deepcopy(value)
    result['dataRoot'] = str(new_root)
    return result


def _laser(value, old_root, new_root):
    if not isinstance(value, dict):
        raise ValueError('Laserstationens konfiguration skal være et JSON-objekt.')
    result = deepcopy(value)
    if 'plates' not in result or not isinstance(result['plates'], str):
        raise ValueError('Laserstationens plates-sti mangler.')
    expected = old_root / 'Laserplader'
    plates = _absolute_reference(result['plates'], 'plates')
    if plates != expected:
        raise ValueError('Laserstationens plates-sti skal tilhøre den gamle datageneration.')
    result['plates'] = str(new_root / 'Laserplader')
    if 'port' in result and (type(result['port']) is not int or not 1 <= result['port'] <= 65535):
        raise ValueError('Laserstationens port er ugyldig.')
    for path in _PRIVATE_PATHS:
        cursor = result
        for key in path[:-1]:
            if key not in cursor:
                cursor = None
                break
            cursor = cursor[key]
            if not isinstance(cursor, dict):
                raise ValueError(f'Laserstationens {".".join(path)}-struktur er ugyldig.')
        if cursor is not None and path[-1] in cursor:
            cursor[path[-1]] = _rebase_reference(cursor[path[-1]], '.'.join(path), old_root, new_root)
    return result


def relocate_school_files(old_data_root: Path, new_data_root: Path,
                          files: dict[str, dict]) -> dict[str, dict]:
    """Return rebased copies of school JSON values without performing I/O.

    ``files`` maps relative copied-file names to decoded JSON objects.  Only
    the owner metadata and laser-station data references are interpreted;
    unknown fields are preserved.  External executable, Beam Studio, FLUX,
    URL, and key-store references remain explicit machine-local references and
    require host validation after materialisation.
    """
    old_root = _root(old_data_root, 'old_data_root')
    new_root = _root(new_data_root, 'new_data_root')
    if (old_root == new_root or old_root.is_relative_to(new_root)
            or new_root.is_relative_to(old_root)):
        raise ValueError('De gamle og nye datarødder skal være disjunkte.')
    if not isinstance(files, dict):
        raise ValueError('files skal være et JSON-objekt.')
    result = {}
    for key, value in files.items():
        _path_key(key)
        if not isinstance(value, dict):
            raise ValueError('Hver skolefil skal være et JSON-objekt.')
        result[key] = deepcopy(value)
    if _OWNER in result:
        result[_OWNER] = _owner(result[_OWNER], old_root, new_root)
    if _LASER in result:
        result[_LASER] = _laser(result[_LASER], old_root, new_root)
    return result
