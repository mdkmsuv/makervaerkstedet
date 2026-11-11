"""One file association table for saves, scanning and reopening projects."""
import io
import json
import re
import zipfile
from pathlib import PurePosixPath

PROGRAMS = {
    'scratch': 'Scratch', 'microbit': 'MakeCode', '3dprint': '3D-værkstedet',
    'cadoodle': 'CaDoodle', 'laser': 'Laserværkstedet', 'folie': 'Folieværkstedet',
    'kredsloeb': 'Kredsløb', 'image': 'Billedviseren', 'audio': 'Lydafspilleren',
    'table': 'Tabelviseren', 'text': 'Tekstviseren', 'json': 'Dataviseren',
    'archive': 'Arkivviseren', 'piskel': 'Pixelviseren', 'datatraener': 'Datatræner',
}
UNAMBIGUOUS = {'.sb3': 'scratch', '.hex': 'microbit', '.stl': '3dprint',
               '.klosterskitse': 'cadoodle', '.png': 'image', '.wav': 'audio',
               '.mp3': 'audio', '.csv': 'table', '.piskel': 'piskel'}


def association(filename, kind='andet', data=None):
    ext = PurePosixPath(filename.lower()).suffix
    program = UNAMBIGUOUS.get(ext)
    if not program and ext == '.svg':
        # Embedded workshop metadata wins over folders and the upload dropdown.
        marker = re.search(rb'\bdata-workshop=[\"\'](laser|vinyl)[\"\']', (data or b'')[:65536])
        program = ('folie' if marker[1] == b'vinyl' else 'laser') if marker else ('folie' if kind == 'folie' else 'laser')
    elif not program and ext == '.txt':
        circuit = bool(data and re.match(rb'(?:\$\s+\d+\s|<cir\s)', data.lstrip()))
        program = 'kredsloeb' if circuit or kind == 'kredsloeb' else 'text'
    elif not program and ext == '.zip':
        program = 'archive'
        if data:
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    names = archive.namelist()
                    if any('/' not in n and n.lower().endswith('.doodle') for n in names):
                        program = 'cadoodle'
                    elif 'project.json' in names:
                        program = 'scratch'
            except zipfile.BadZipFile:
                pass
        elif kind == 'cadoodle':
            program = 'cadoodle'
    elif not program and ext == '.json':
        program = 'json'
        if data and len(data) <= 50 * 1024 * 1024:
            try:
                obj = json.loads(data)
                if isinstance(obj, dict) and isinstance(obj.get('targets'), list) and 'meta' in obj:
                    program = 'scratch'
                elif isinstance(obj, dict) and isinstance(obj.get('marks'), list) and 'height' in obj:
                    program = 'cadoodle'
                elif isinstance(obj, dict) and obj.get('format') == 'elevhub-datatraener-project' and obj.get('version') == 1 and isinstance(obj.get('gestureData'), list):
                    program = 'datatraener'
                elif isinstance(obj, dict) and obj.get('format') == 'elevhub-microbit-project' and obj.get('version') == 1 and isinstance(obj.get('project'), dict):
                    program = 'microbit'
            except (ValueError, UnicodeError):
                pass
    program = program or 'text'
    return {'program': program, 'label': PROGRAMS[program], 'extension': ext}


def project_kind(filename, hint='andet', data=None):
    program = association(filename, hint, data)['program']
    if program == '3dprint':
        return 'cadoodle'
    if program in {'scratch', 'microbit', 'cadoodle', 'laser', 'folie', 'kredsloeb', 'datatraener'}:
        return program
    return hint if hint in {'datatraener', 'scratch', 'folie', 'laser'} else 'andet'


def archive_listing(data):
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise ValueError('ZIP-filen kunne ikke læses.') from error
    with archive:
        entries = archive.infolist()
        if len(entries) > 5000 or sum(i.file_size for i in entries) > 200 * 1024 * 1024:
            raise ValueError('ZIP-filen indeholder for mange eller for store filer.')
        for entry in entries:
            path = PurePosixPath(entry.filename.replace('\\', '/'))
            if path.is_absolute() or '..' in path.parts or ':' in entry.filename or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('ZIP-filen indeholder en ugyldig filsti.')
        return [{'name': i.filename, 'size': i.file_size} for i in entries if not i.is_dir()]
