"""File opening plans. Destinations come from installation, never from a file."""
import hashlib
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, urlencode
from filtyper import association, archive_listing
from makecode_import import editable_project
from stl_check import validate_stl

DEFAULT_WORKSHOPS = {'3dprint': 'http://127.0.0.1:8765/',
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
    elif program == 'cadoodle' or (program == '3dprint' and server.native_projects):
        if not server.native_projects:
            raise ValueError('Dette projekt skal åbnes i CaDoodle på elevcomputeren.')
        if route['extension'] == '.zip':
            archive_listing(data)
        result.update(mode='native')
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
    server.projekter.valid_id(request_id)
    route = plan(server, meta, data)
    if route['mode'] != 'native':
        raise ValueError('Filen åbnes i browseren.')
    root = server.projekter.root / 'open-requests'
    root.mkdir(exist_ok=True, mode=0o700)
    request = root / (request_id + '.json')
    if not request.exists() and not request.with_suffix('.ready').exists() and not (root / (request_id + '.status.json')).exists():
        suffix = route['extension']
        if suffix == '.json':
            suffix = '.klosterskitse'
        if suffix not in ('.stl', '.klosterskitse', '.zip'):
            raise ValueError('CaDoodle understøtter ikke denne filtype.')
        file = root / (request_id + suffix)
        file.write_bytes(data)
        payload = {'id': request_id, 'file': file.name, 'name': meta['name'],
                   'sha256': hashlib.sha256(data).hexdigest(), 'created': time.time()}
        temp = request.with_suffix('.tmp')
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        os.replace(temp, request)
    return {'request': request_id, 'label': route['label']}


def native_status(server, request_id):
    server.projekter.valid_id(request_id)
    root = server.projekter.root / 'open-requests'
    status = root / (request_id + '.status.json')
    if status.exists():
        result = json.loads(status.read_text(encoding='utf-8'))
        return {k: result[k] for k in ('state', 'message') if k in result}
    if (root / (request_id + '.json')).exists() or (root / (request_id + '.ready')).exists():
        return {'state': 'pending', 'message': 'Åbner i CaDoodle …'}
    raise KeyError(request_id)
