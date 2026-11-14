"""Exact built-in package trees served by the declarative installed-app runtime.

All state, school appearance and ports are disposable. No school/hardware client.
The approved external Scratch build is an input, never modified by this fixture.
"""
from pathlib import Path as _LayoutPath
import sys as _layout_sys
_LAYOUT_ROOT = next(p for p in _LayoutPath(__file__).resolve().parents if (p / 'REPOSITORY-LAYOUT.json').is_file())
_layout_sys.path[:0] = [str(_LAYOUT_ROOT), str(_LAYOUT_ROOT / 'system')]
from repository import source_path, repo_root
import json
import hashlib
import os
from pathlib import Path
import shutil
import signal
import struct
import sys
import tempfile
import threading
import uuid
import zlib
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(source_path('linux-elevhub/projekter', ROOT))]
from app_host import SHARED, discover_apps, uses_gateway
from projektarkiv import Projektarkiv
from lokal import make_server, Handler
from workshop_sessions import WorkshopSessions
from app.laser_local import create_installed_servers


def interrupted(*_):
    raise KeyboardInterrupt


signal.signal(signal.SIGTERM, interrupted)
signal.signal(signal.SIGHUP, interrupted)


class NoSchool:
    def health(self):
        return 503, {'error': 'Syntetisk offline-test; ingen skoleforbindelse.'}

    def __getattr__(self, name):
        raise AssertionError('No submissions or school access in installed-app QA: ' + name)


class PublicLibrary:
    loaded = False
    item_id = 'a' * 64
    def status(self):
        return {'enabled': True, 'version': 1 if self.loaded else 0, 'source_url': 'https://xn--skolevrkstedet-5ib.dk/svg-bibliotek/',
                'cache_state': 'stale' if self.loaded else 'empty', 'last_success': '2026-11-14T12:00:00Z' if self.loaded else None}

    def catalogue_response(self):
        self.loaded = True
        items=[{'id': self.item_id, 'title': 'Syntetisk QA-firkant', 'category': 'QA'}]
        if os.environ.get('MKV_LIBRARY_TEST_CASES')=='1':
            items += [{'id':f'{index:064x}','title':{1:'Transformerede former',2:'Ugyldig fil',3:'For mange dele'}.get(index,'QA-form '+str(index)),'category':'QA'} for index in range(1,13)]
        return {**self.status(), 'items': items}

    def svg_response(self, item_id):
        if os.environ.get('MKV_LIBRARY_TEST_CASES')=='1' and item_id!=self.item_id:
            index=int(item_id,16)
            if index==1:svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><g transform="translate(10 12) rotate(12)"><rect x="0" y="0" width="20" height="15"/><circle cx="45" cy="20" r="8"/><polygon points="10,50 20,30 30,50"/><line x1="0" y1="65" x2="50" y2="65" stroke="black"/></g></svg>'
            elif index==2:svg='<svg xmlns="http://www.w3.org/2000/svg"><path d="M invalid"/></svg>'
            elif index==3:svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">'+''.join(f'<path d="M{i} 0h1v1h-1z"/>' for i in range(101))+'</svg>'
            elif index==4:svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 50 30"><line x1="0" y1="0" x2="40" y2="0" stroke="black"/><line x1="0" y1="0" x2="0" y2="20" stroke="black"/></svg>'
            else:svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><path d="M0 0H20V20H0Z"/></svg>'
            return {**self.status(),'svg':svg}
        assert item_id == self.item_id
        return {**self.status(), 'svg': '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20"><path d="M0 0H40V20H0Z" fill="black"/></svg>'}


def stage_inputs(stage):
    for app_id in ('laser', 'folie', '3dprint', 'scratch', 'microbit'):
        if os.environ.get('MKV_INSTALLED_APP_FILTER') and app_id != os.environ['MKV_INSTALLED_APP_FILTER']:
            continue
        folder = stage / 'apps' / app_id
        folder.mkdir(parents=True)
        shutil.copy2(ROOT / 'apps' / app_id / 'app.json', folder / 'app.json')
        if app_id == 'scratch':
            supplied = os.environ.get('MKV_INSTALLED_SCRATCH_BUILD')
            source = Path(supplied) if supplied else next((p / 'byggeinput/linux/scratch-gui-v15.1.1-local-projects-build' for p in ROOT.parents if (p / 'byggeinput').is_dir()), None)
            if source is None or not (source / 'guistandalone.js').is_file():
                raise RuntimeError('Approved Scratch build missing; set MKV_INSTALLED_SCRATCH_BUILD')
            shutil.copytree(source, folder / 'www')
        manifest = json.loads((ROOT / 'apps' / app_id / 'files.json').read_text())
        for entry in manifest['files']:
            prefix = 'usr/share/makervaerkstedet/apps/' + app_id + '/'
            if app_id == 'scratch' and entry['path'].startswith('usr/local/share/elevhub/apps/scratch/'):
                target = folder / 'www' / entry['path'][len('usr/local/share/elevhub/apps/scratch/'):]
            else:
                assert entry['path'].startswith(prefix)
                target = folder / entry['path'][len(prefix):]
            source = ROOT / entry['source']
            if entry['tree']:
                shutil.copytree(source, target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    # Only assets explicitly owned by core, never mount the entire development web/.
    shared = stage / 'shared'
    for asset in SHARED:
        source, target = source_path('web', ROOT) / asset[1:], shared / asset[1:]
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return shared


def stage_profile(stage):
    profile = stage / 'etc/elevhub/local-appearance'
    profile.mkdir(parents=True)
    # Native valid PNG, with no imaging package or existing school artwork.
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    pixels = b''.join(b'\0' + b''.join(bytes((21, 115, 71, 255)) if 6 <= x < 58 and 6 <= y < 42 else bytes((255, 255, 255, 255)) for x in range(64)) for y in range(48))
    logo = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 64, 48, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')
    font = (source_path('linux-elevhub/server/elevhub/web/assets/Manrope.ttf', ROOT)).read_bytes()
    digest = lambda data: hashlib.sha256(data).hexdigest()
    def managed(data, suffix):
        name = 'managed-' + digest(data) + suffix
        (profile / name).write_bytes(data)
        return name
    font_name, logo_name = managed(font, '.ttf'), managed(logo, '.png')
    font_url = '/school-profile-assets/' + font_name
    # Match SchoolProfileAdmin's native font URL, family, weights and UI-only selectors.
    targets = ':root,body,body:not(.picker),body:not(.picker)[data-theme]'
    css = (f'@font-face{{font-family:SchoolProfile;src:url("{font_url}") format("truetype");font-weight:200 800;font-display:swap;}}\n'
        f'{targets}{{--f-body:SchoolProfile,sans-serif;--f-display:SchoolProfile,sans-serif;--font-body:SchoolProfile,sans-serif;--font-display:SchoolProfile,sans-serif;}}\n'
        'body,body:not(.picker),button,input,select,textarea,#creative-app-header,#creative-app-header *{font-family:SchoolProfile,sans-serif;}\n').encode()
    css_name = managed(css, '.css')
    appearance = {'schemaVersion': 1, 'school_name': 'Syntetisk QA-skole', 'theme': 'green',
        'brand': '#157347', 'brand_dark': '#0d5032', 'font': 'manrope',
        'style': {'logo': logo_name, 'logo_sha256': digest(logo),
            'website_css': css_name, 'website_css_sha256': digest(css),
            'static': {font_url: {'path': font_name, 'sha256': digest(font)}}}}
    (profile / 'manifest.json').write_text(json.dumps(appearance))
    config = {'computer': 'installed-browser-qa', 'ui_token': uuid.uuid4().hex * 2, 'server': '127.0.0.1',
        'hub': 'https://127.0.0.1:8773', 'protokol': 'https', 'elev': 'elev', 'elev_porte': [8791],
        'school_name': appearance['school_name'], 'theme': 'green', 'brand': '#157347', 'brand_dark': '#0d5032'}
    (profile.parent / 'kiosk.json').write_text(json.dumps(config))
    return profile.parent, config


def stage_home(stage, apps, config):
    sys.path.insert(0, str(source_path('linux-elevhub/administration', ROOT)))
    from template_activation import context
    from jinja2 import Environment, StrictUndefined
    installed = stage / 'usr/share/makervaerkstedet'
    installed.mkdir(parents=True)
    (installed / 'runtime-release.json').write_text(json.dumps({'release_id': '2.1.0~20261114.1'}))
    # Context discovery needs descriptors/www only; reuse the separately staged actual app bytes.
    for app in apps:
        folder = installed / 'apps' / app['id']
        (folder / 'www').mkdir(parents=True)
        value = {key: value for key, value in app.items() if key not in ('assets', 'port')}
        (folder / 'app.json').write_text(json.dumps(value))
    values = context(config, stage)
    values['kiosk_lokale_app_urler']['projekter'] = config['shelf_url']
    env = Environment(undefined=StrictUndefined, autoescape=False)
    env.filters['to_json'] = lambda value: json.dumps(value, ensure_ascii=True)
    source = source_path('linux-elevhub/ansible/roles/kiosk/templates/start.html.j2', ROOT)
    home = stage / 'usr/local/share/elevhub/start.html'
    home.parent.mkdir(parents=True)
    home.write_text(env.from_string(source.read_text()).render(**values))
    assets = home.parent / 'assets'
    assets.mkdir()
    names = ('creative-hub.css', 'creative-hub.js', 'workshops.js', 'favicon.svg', 'Manrope.ttf',
        'workshop-3d-v2.webp', 'workshop-laser-v2.webp', 'workshop-folie-v2.webp', 'workshop-scratch-v2.png',
        'workshop-microbit-v2.png', 'workshop-internet-v2.png')
    for name in names:
        shutil.copy2(source_path('linux-elevhub/server/elevhub/web/assets', ROOT) / name, assets / name)
    return home


with tempfile.TemporaryDirectory(prefix='mkv-installed-browser-') as temporary:
    stage = Path(temporary)
    shared = stage_inputs(stage)
    profile, config = stage_profile(stage)
    apps = discover_apps(stage / 'apps')
    assert len(apps) == (1 if os.environ.get('MKV_INSTALLED_APP_FILTER') else 5)
    for app in apps:
        app['port'] = 0
    store = Projektarkiv(stage / 'state', apps=apps)
    sessions = WorkshopSessions(kinds={a['project']['kind'] for a in apps if a.get('project')})
    servers = []
    stop = threading.Event()
    token = uuid.uuid4().hex
    shelf = make_server(0, store, apps=apps, sessions=sessions, workshops={}, data_root=profile,shared_root=shared)
    class FixtureHandler(Handler):
        def do_GET(self):
            if self.path == '/qa/catalogue':
                self.svar(200, {'apps': [{key: value for key, value in app.items() if key not in ('assets', 'port')} for app in apps]})
            elif self.path == '/qa/policy':
                self.svar(200, {'apps': [{'id': app['id'], 'navn': app['navn'], 'type': 'lokal', 'aaben': True, 'klar': True} for app in apps]})
            else:
                self.dispatch()
        def do_POST(self):
            if self.path == '/_qa-stop/' + token:
                self.svar(200, {'stopping': True})
                stop.set()
            else:
                self.dispatch()
    shelf.RequestHandlerClass = FixtureHandler
    servers.append(shelf)
    gateways = create_installed_servers(apps, NoSchool(), NoSchool(), projects_port=shelf.server_port,
        data_root=profile, shared_root=shared, library_client=PublicLibrary())
    servers.extend(gateways)
    urls = {}
    for app in apps:
        if uses_gateway(app):
            server = next(s for s in gateways if s.descriptor['id'] == app['id'])
        else:
            server = make_server(0, store, app=app['id'], assets=app['assets'], apps=apps,
                sessions=sessions, descriptor=app, workshops={}, data_root=profile,shared_root=shared)
            servers.append(server)
        app['port'] = server.server_port
        app['start'] = 'http://127.0.0.1:' + str(server.server_port) + (urlsplit(app['start']).path or '/')
        urls[app['id']] = app['start']
    workshops = {a['id']: a['start'] for a in apps}
    config['shelf_url'] = 'http://127.0.0.1:' + str(shelf.server_port) + '/_elevhub/projekter/'
    home = stage_home(stage, apps, config)
    for server in servers:
        if hasattr(server, 'workshop_urls'):
            server.workshop_urls = workshops
            server.kiosk_config = profile / 'kiosk.json'
            server.kiosk_start_file = home
            server.home_url = config['shelf_url'] + 'home'
        server.RequestHandlerClass.log_message = lambda *args: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
    print(json.dumps({'shelf': config['shelf_url'], 'home': config['shelf_url'] + 'home',
        'catalogue': 'http://127.0.0.1:' + str(shelf.server_port) + '/qa/catalogue',
        'policy': 'http://127.0.0.1:' + str(shelf.server_port) + '/qa/policy',
        'cleanup': 'http://127.0.0.1:' + str(shelf.server_port) + '/_qa-stop/' + token, **urls}), flush=True)
    try:
        stop.wait()
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
