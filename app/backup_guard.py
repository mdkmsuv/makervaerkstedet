"""Small offline-maintenance protocol shared by service entry points."""
import json
import os
from pathlib import Path
import tempfile

PROTOCOL_FILE = 'backup-protocol.json'
HOLD_FILE = '.restore-hold.json'


def _protocol_matches(value, role):
    """Require the marker's JSON types to match the wire contract exactly."""
    return (type(value) is dict and set(value) == {'protocol', 'role'} and
            type(value['protocol']) is int and value['protocol'] == 1 and
            type(value['role']) is str and value['role'] == role)


def assert_not_restored(folder):
    """A restored tree cannot silently reconnect to its former hardware/LAN."""
    folder = Path(folder).absolute()
    for parent in (folder, *folder.parents):
        marker = parent / HOLD_FILE
        if marker.exists() or marker.is_symlink():
            raise RuntimeError('Gendannelsen er isoleret. Kontrollér opsætning og fysisk køstatus før ibrugtagning.')


def write_protocol(folder, role):
    """Called only after the service has acquired its lifetime instance lock."""
    if role not in ('server', 'hub', 'print', 'laser', 'laser_station'):
        raise ValueError('Ukendt backupportokolrolle.')
    folder = Path(folder)
    target = folder / PROTOCOL_FILE
    if target.is_symlink() or bool(getattr(target, 'is_junction', lambda: False)()):
        raise ValueError('Backupportokollen må ikke være et link.')
    expected = {'protocol': 1, 'role': role}
    if target.exists():
        if not target.is_file():
            raise ValueError('Backupportokollen er ikke en fil.')
        try:
            with target.open('r', encoding='utf-8') as stream:
                current = json.load(stream)
        except (OSError, UnicodeError, ValueError) as exc:
            # A malformed marker is handled by the same atomic replacement
            # path as a stale marker; never edit it in place.
            current = None
        if _protocol_matches(current, role):
            return target
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=str(folder),
                                         prefix='.backup-protocol-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(expected, stream, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
