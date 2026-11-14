"""File opening plans. Destinations come from installation, never from a file."""
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, urlencode
from filtyper import association, archive_listing
from makecode_import import editable_project
from stl_check import validate_stl

DEFAULT_WORKSHOPS = {'3dprint': 'http://127.0.0.1:8798/model3d.html',
                     'laser': 'http://127.0.0.1:8766/laser.html',
                     'folie': 'http://127.0.0.1:8766/folie.html'}


def workshop_urls(path=None,apps=None):
    allowed=DEFAULT_WORKSHOPS if apps is None else {'projekter':'http://127.0.0.1:8791/',**{a['id']:a['start'] for a in apps}}
    values = json.loads(Path(path).read_text(encoding='utf-8-sig')) if path else dict(allowed)
    if not isinstance(values, dict) or set(values) - set(allowed):
        raise ValueError('Ugyldige værkstedsadresser.')
    for value in values.values():
        url = urlsplit(value)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Ugyldig værkstedsadresse.')
    if apps is not None and any(value!=allowed[key] for key,value in values.items()):
        raise ValueError('Ugyldige lokale værkstedsadresser.')
    return values


def validate_mkv3d(data):
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError('Ugyldigt 3D-projekt.') from error
    if (type(value) is not dict or value.get('format') != 'makervaerkstedet-3d-project'
            or type(value.get('version')) is not int or value['version'] != 1
            or type(value.get('scene')) is not dict
            or value['scene'].get('format') != 'makervaerkstedet-3d'
            or type(value['scene'].get('version')) is not int or value['scene']['version'] != 2):
        raise ValueError('Ugyldigt 3D-projekt.')
    return value


def plan(server, meta, data, revision=None):
    route = association(meta['filename'], meta['kind'], data,server.projekt_apps)
    program = route['program']
    if program == '3dprint':
        if route['extension'] == '.mkv3d':
            validate_mkv3d(data)
        else:
            validate_stl(data)
    result = {'project': meta, **route}
    dynamic=next((a for a in server.projekt_apps if a['id']==program and a.get('project')),None)
    if dynamic and program not in ('scratch','microbit','laser','folie','3dprint'):
        result.update(mode='local',port=dynamic['port'])
    elif program in ('scratch', 'kredsloeb', 'datatraener', 'microbit'):
        app = next((a for a in server.projekt_apps if a['id'] == program), None)
        if not app:
            raise ValueError(route['label'] + ' er ikke installeret på denne computer.')
        result.update(mode='local', port=app['port'])
        if program == 'microbit' and route['extension'] == '.hex':
            result['editable'] = {'format': 'elevhub-microbit-project', 'version': 1,
                                  'type': 'microbit', 'project': editable_project(data)}
    elif program == 'andet':
        result.update(mode='download')
    elif program in server.workshop_urls:
        url = urlsplit(server.workshop_urls[program])
        if (program in ('laser', 'folie') or (program == '3dprint' and route['extension'] == '.mkv3d')) and url.hostname in ('127.0.0.1', 'localhost'):
            params = {'elevhub_project': meta['id']}
            if revision is not None:
                params['elevhub_revision'] = revision
            query = urlencode(params)
            result.update(mode='navigate', url=urlunsplit(url._replace(query=query)))
        else:
            if program == '3dprint' and route['extension'] == '.mkv3d':
                raise ValueError('3D-værkstedet skal være lokalt.')
            result.update(mode='frame', url=server.workshop_urls[program])
    else:
        result.update(mode='viewer')
        if program == 'archive':
            result['entries'] = archive_listing(data)
    return result
