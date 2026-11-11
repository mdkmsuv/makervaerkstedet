"""File opening plans. Destinations come from installation, never from a file."""
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, urlencode
from filtyper import association, archive_listing
from makecode_import import editable_project
from stl_check import validate_stl

DEFAULT_WORKSHOPS = {'3dprint': 'http://127.0.0.1:8770/',
                     'laser': 'http://127.0.0.1:8766/laser.html',
                     'folie': 'http://127.0.0.1:8766/folie.html'}


def workshop_urls(path=None):
    values = json.loads(Path(path).read_text(encoding='utf-8-sig')) if path else dict(DEFAULT_WORKSHOPS)
    if not isinstance(values, dict) or set(values) - set(DEFAULT_WORKSHOPS):
        raise ValueError('Ugyldige værkstedsadresser.')
    for value in values.values():
        url = urlsplit(value)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Ugyldig værkstedsadresse.')
    return values


def plan(server, meta, data):
    route = association(meta['filename'], meta['kind'], data)
    program = route['program']
    if program == '3dprint':
        validate_stl(data)
    result = {'project': meta, **route}
    if program in ('scratch', 'kredsloeb', 'datatraener', 'microbit'):
        app = next((a for a in server.projekt_apps if a['id'] == program), None)
        if not app:
            raise ValueError(route['label'] + ' er ikke installeret på denne computer.')
        result.update(mode='local', port=app['port'])
        if program == 'microbit' and route['extension'] == '.hex':
            result['editable'] = {'format': 'elevhub-microbit-project', 'version': 1,
                                  'type': 'microbit', 'project': editable_project(data)}
    elif program == 'cadoodle':
        result.update(mode='download')
    elif program in server.workshop_urls:
        url = urlsplit(server.workshop_urls[program])
        if program in ('laser', 'folie') and url.hostname in ('127.0.0.1', 'localhost'):
            query = urlencode({'elevhub_project': meta['id']})
            result.update(mode='navigate', url=urlunsplit(url._replace(query=query)))
        else:
            result.update(mode='frame', url=server.workshop_urls[program])
    else:
        result.update(mode='viewer')
        if program == 'archive':
            result['entries'] = archive_listing(data)
    return result


def native_request(server, meta, data, request_id):
    raise ValueError('Dette projekt kan downloades som fil. Native 3D er udgået.')


def native_status(server, request_id):
    raise ValueError('Native 3D er udgået. Projektfilen er bevaret.')
