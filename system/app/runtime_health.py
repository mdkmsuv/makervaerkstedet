"""Startup-only runtime identity from an installed, build-bound descriptor."""
import json
import os
from pathlib import Path
import re
import uuid

_PROCESS_ID = os.getpid()
_PROCESS_INSTANCE = str(uuid.uuid4())
_VERSION = re.compile(r"[0-9][A-Za-z0-9.:+_~\-]{0,127}")
_ROLES = ('server', 'laser_station', 'student')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_runtime_descriptor_key')
        result[key] = value
    return result


def runtime_snapshot(path, *, expected_role, service):
    """Call once at startup and retain the result; unknown is never a release.

    The installer binds descriptor bytes to its verified payload. This reader
    does not grant trust to a version string, environment variable or feed tag.
    """
    if expected_role not in _ROLES or not isinstance(service, str) or not service:
        raise ValueError('invalid_runtime_identity_scope')
    result = {'release_id': None, 'service': service, 'pid': _PROCESS_ID,
              'runtime_instance': _PROCESS_INSTANCE}
    try:
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            return result
        with path.open('rb') as source:
            raw = source.read(4097)
        if len(raw) > 4096:
            return result
        value = json.loads(raw, object_pairs_hook=_unique)
        if (not isinstance(value, dict) or set(value) != {'schema', 'release_id', 'role'}
                or type(value['schema']) is not int or value['schema'] != 1
                or value['role'] != expected_role
                or not isinstance(value['release_id'], str)
                or _VERSION.fullmatch(value['release_id']) is None):
            return result
        result['release_id'] = value['release_id']
    except (OSError, ValueError, TypeError):
        pass
    return result
