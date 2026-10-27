"""Local computer shelf. Same-origin access only, never installed on the hub."""
import json
import ssl
import threading
from collections import OrderedDict
from urllib.parse import parse_qs, quote, urlsplit
from projektarkiv import MAX_FILE, Konflikt
from projektarkiv import TYPES
from filtyper import association, project_kind
from project_open import plan, native_request, native_status
from previews import build_preview

PREFIX = '/_elevhub/projekter'
FILES = {'/': 'projekter.html', '/projekter.js': 'projekter.js', '/projekter.css': 'projekter.css',
         '/bridge.js': 'projekt-bridge.js', '/bridge.css': 'projekt-bridge.css',
         '/open': 'open.html', '/open.js': 'open.js', '/open.css': 'open.css'}

# Only derived images in memory, bounded independently of project sizes. Share
# the cache across the shelf and embedded pickers; never cache project files.
PREVIEW_CACHE = OrderedDict()
PREVIEW_LOCK = threading.Lock()
PREVIEW_CACHE_BYTES = 8 * 1024 * 1024


def preview(store, project, revision):
    with store.lock:
        meta = store.meta(project)
        revision = meta['revision'] if revision is None else revision
        if type(revision) is not int or revision <= 0:
            raise ValueError('Ugyldig version.')
        if not any(v['revision'] == revision for v in meta['versions']):
            raise KeyError('Versionen findes ikke.')
        key = (str(store.root.resolve()), project, revision, meta['modified'])
        with PREVIEW_LOCK:
            if key in PREVIEW_CACHE:
                PREVIEW_CACHE.move_to_end(key)
                return PREVIEW_CACHE[key]
        meta, data = store.read(project, revision)
    result = build_preview(meta, data)
    with PREVIEW_LOCK:
        PREVIEW_CACHE[key] = result
        while (len(PREVIEW_CACHE) > 128 or
               sum(len(value[0]) for value in PREVIEW_CACHE.values() if value) > PREVIEW_CACHE_BYTES):
            PREVIEW_CACHE.popitem(last=False)
    return result


def opening(server, project):
    cache = getattr(server, 'opening_cache', {})
    key = (project['id'], project['revision'], project['modified'])
    if key not in cache:
        route = association(project['filename'], project['kind'])
        if route['extension'] in ('.svg', '.txt', '.json', '.zip'):
            try:
                _, data = server.projekter.read(project['id'])
                route = association(project['filename'], project['kind'], data)
            except (OSError, ValueError, KeyError):
                pass  # Opening the file will show its concrete error.
        if len(cache) > 4000:
            cache.clear()
        cache[key] = route
        server.opening_cache = cache
    return cache[key]


def handle(handler):
    h = handler
    if not h.path.startswith(PREFIX) or not hasattr(h.server, 'projekter'):
        return False
    try:
        parts = urlsplit(h.path)
        path = parts.path[len(PREFIX):]
        size = h.kropslaengde(MAX_FILE)
        if h.command in ('GET', 'HEAD') and size:
            h.close_connection = True
            raise ValueError('GET må ikke indeholde en fil.')
        if (h.headers.get('Sec-Fetch-Site') == 'cross-site' and
                h.headers.get('Sec-Fetch-Mode') != 'navigate'):
            raise PermissionError('Åbn projektarkivet fra Elevhub.')
        if path in FILES and h.command in ('GET', 'HEAD'):
            h.fil(FILES[path])
            return True
        if path == '' and h.command in ('GET', 'HEAD'):
            h.svar(302, b'', 'text/plain', {'Location': PREFIX+'/'})
            return True
        qs = parse_qs(parts.query, keep_blank_values=True)
        def arg(key, default=''):
            values = qs.get(key, [default])
            if len(values) != 1:
                raise ValueError('Gentaget parameter.')
            return values[0]
        store = h.server.projekter
        if h.command in ('GET', 'HEAD'):
            if path == '/api/list':
                workshop = arg('workshop')
                if workshop and workshop not in TYPES:
                    raise ValueError('Værkstedet kunne ikke genkendes.')
                projects = []
                for p in store.list(arg('q')):
                    route = opening(h.server, p)
                    program = route['program']
                    category = ('cadoodle' if program == '3dprint' else program if program in TYPES
                                else 'datatraener' if p['kind'] == 'datatraener' else 'andet')
                    if not workshop or category == workshop:
                        projects.append({**p, 'opening': route})
                h.svar(200, {'projects': projects, 'local': True,
                             'scan_error': h.server.scanner.error if h.server.scanner else ''})
            elif path == '/api/config':
                h.svar(200, {'apps': [{'id': a['id'], 'port': a.get('port'), 'type': a.get('type', 'vaerksted')}
                                     for a in h.server.projekt_apps]})
            elif path == '/api/meta':
                h.svar(200, store.meta(arg('id')))
            elif path == '/api/preview':
                revision = int(arg('revision')) if arg('revision') else None
                result = preview(store, arg('id'), revision)
                if result is None:
                    h.svar(204, b'')
                else:
                    data, mime = result
                    h.svar(200, data, mime, {
                        'Content-Security-Policy': "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"})
            elif path == '/api/open-info':
                revision = int(arg('revision')) if arg('revision') else None
                meta, data = store.read(arg('id'), revision)
                h.svar(200, plan(h.server, meta, data))
            elif path == '/api/open-status':
                h.svar(200, native_status(h.server, arg('request')))
            elif path == '/api/file':
                revision = int(arg('revision')) if arg('revision') else None
                meta, data = store.read(arg('id'), revision)
                h.svar(200, data, 'application/octet-stream', {
                    'Content-Disposition': "attachment; filename*=UTF-8''"+quote(meta['filename']),
                    'Content-Security-Policy': "sandbox; default-src 'none'"})
            else:
                h.svar(404, {'error': 'Findes ikke.'})
        elif h.command == 'POST' and path in ('/api/save', '/api/open-native'):
            origin = ('https://' if isinstance(h.connection, ssl.SSLSocket) else 'http://') + h.headers.get('Host', '')
            # Exact origin plus non-simple header. No LAN access or CORS.
            source = 'browser'
            if h.headers.get('Origin') != origin or h.headers.get('X-Elevhub-Project') != '1':
                raise PermissionError('Gem fra projektarkivets egen side.')
            if path == '/api/open-native':
                if size:
                    raise ValueError('Åbn en gemt projektfil.')
                revision = int(arg('revision')) if arg('revision') else None
                meta, data = store.read(arg('id'), revision)
                h.svar(200, native_request(h.server, meta, data, arg('request')))
                return True
            if h.headers.get_content_type() != 'application/octet-stream':
                h.close_connection = True
                h.svar(415, {'error': 'Forkert filtype i forespørgslen.'})
                return True
            data = h.laes_krop(size)
            kind = project_kind(arg('filename'), arg('kind'), data)
            try:
                kind = store.meta(arg('id'))['kind']  # Keep existing history, including old labels.
            except KeyError:
                pass
            result = store.save(data, project=arg('id'), name=arg('name'), folder=arg('folder'),
                                kind=kind, filename=arg('filename'), expected=int(arg('expected', '0')), source=source)
            h.svar(200, result)
        else:
            h.close_connection = True
            h.svar(405, {'error': 'Denne handling findes ikke.'})
    except Konflikt as e:
        h.svar(409, {'error': str(e)})
    except PermissionError as e:
        h.close_connection = True
        h.svar(403, {'error': str(e)})
    except KeyError:
        h.svar(404, {'error': 'Projektet eller versionen findes ikke.'})
    except (ValueError, TypeError) as e:
        h.close_connection = True
        h.svar(getattr(e, 'status', 400), {'error': getattr(e, 'besked', str(e))})
    except OSError:
        h.svar(503, {'error': 'Projektet kunne ikke læses eller gemmes. Din åbne fil er ikke ændret. Prøv igen.'})
    return True
