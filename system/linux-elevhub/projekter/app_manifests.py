"""Read the strictly bounded manifests installed by local workshop packages."""
import json
import os
from pathlib import Path
import re
import stat

FIELDS = frozenset(('schema', 'id', 'navn', 'version', 'start', 'offline',
                    'aflevering', 'kraever_internet', 'licens'))
ID = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')
START = re.compile(r'http://127\.0\.0\.1:([0-9]{1,5})/[^\s\x00-\x1f\x7f?#\\]*\Z')
MAX_SIZE = 16 * 1024


def validate_manifest(data, expected_id=None):
    if type(data) is dict and data.get('schema') == 2:
        try:
            from app.app_catalog import validate_app_descriptor
        except ModuleNotFoundError:
            import sys
            sys.path.insert(0,str(Path(__file__).resolve().parent))
            from app_catalog import validate_app_descriptor
        validate_app_descriptor(data)
        if expected_id is not None and data['id'] != expected_id:
            raise ValueError('manifest_directory_mismatch')
        return data
    if type(data) is not dict or set(data) != FIELDS:
        raise ValueError('invalid_manifest_fields')
    if type(data['schema']) is not int or data['schema'] != 1:
        raise ValueError('invalid_manifest_schema')
    if type(data['id']) is not str or not ID.fullmatch(data['id']):
        raise ValueError('invalid_manifest_id')
    if expected_id is not None and data['id'] != expected_id:
        raise ValueError('manifest_directory_mismatch')
    for key, limit in (('navn', 256), ('version', 128), ('licens', 1024)):
        value = data[key]
        if (type(value) is not str or not value.strip() or len(value) > limit
                or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise ValueError('invalid_manifest_' + key)
    if type(data['offline']) is not bool or type(data['kraever_internet']) is not bool:
        raise ValueError('invalid_manifest_boolean')
    if data['aflevering'] is not None and (type(data['aflevering']) is not str
            or data['aflevering'] not in ('laser-submit', 'print-job')):
        raise ValueError('invalid_manifest_aflevering')
    start = data['start']
    match = START.fullmatch(start) if type(start) is str and len(start) <= 2048 else None
    if not match or not 1024 <= int(match.group(1)) <= 65535:
        raise ValueError('invalid_manifest_start')
    return data


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_manifest_key')
        result[key] = value
    return result


def _open_root(root):
    # Walk each component without following links, including ancestors of root.
    path = Path(os.path.abspath(root))
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            try:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                return None
            os.close(fd)
            fd = next_fd
        result = fd
        fd = None
        return result
    finally:
        if fd is not None:
            os.close(fd)


def load_manifests(root=Path('/usr/share/makervaerkstedet/apps'),*,on_error=None):
    try:
        root_fd = _open_root(root)
        if root_fd is None:
            return []
        try:
            result = []
            for name in sorted(os.listdir(root_fd)):
                try:
                    info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode):
                        raise ValueError('linked_manifest_directory')
                    if not stat.S_ISDIR(info.st_mode):
                        continue
                    folder_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
                    try:
                        try:
                            file_fd = os.open('app.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder_fd)
                        except FileNotFoundError:
                            continue
                        try:
                            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                                raise ValueError('invalid_manifest_file')
                            with os.fdopen(file_fd, 'rb') as stream:
                                file_fd = None
                                raw = stream.read(MAX_SIZE + 1)
                            if len(raw) > MAX_SIZE:
                                raise ValueError('manifest_too_large')
                            value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs)
                            result.append(validate_manifest(value, name))
                        finally:
                            if file_fd is not None:
                                os.close(file_fd)
                    finally:
                        os.close(folder_fd)
                except (OSError,ValueError,UnicodeError) as error:
                    if on_error is None:raise
                    on_error(name,error)
            return result
        finally:
            os.close(root_fd)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('invalid_manifest_catalog') from error
