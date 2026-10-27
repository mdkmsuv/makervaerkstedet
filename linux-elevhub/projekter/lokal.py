#!/usr/bin/env python3
"""Pupil loopback apps and project history. Python/SQLite only, no network client.

Never bind these services to LAN. App assets are installed on the pupil computer;
only print/laser submissions belong in the separate central queue.
"""
import argparse
import json
import logging
import mimetypes
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from projektarkiv import Projektarkiv
from projekt_http import handle, PREFIX
from scanner import Scanner
from project_open import workshop_urls
from workshop_sessions import WorkshopSessions, handle_session

HERE = Path(__file__).resolve().parent
APPS = {'scratch': 8792, 'datatraener': 8793, 'kredsloeb': 8794, 'microbit': 8797}
BRIDGE = '<link rel="stylesheet" href="/_elevhub/projekter/bridge.css"><script src="/_elevhub/projekter/bridge.js" defer></script>'
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
       "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob: data:; "
       "connect-src 'self' blob:; font-src 'self' data:; worker-src 'self' blob:; "
       "frame-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; "
       "frame-ancestors 'self'")


class Navigation:
    """One local navigation event, not project content or browsing history."""
    def __init__(self):
        self.lock = threading.Lock()
        self.current = None

    def visit(self, app):
        with self.lock:
            self.current = {'id': app, 'version': uuid.uuid4().hex}

    def read(self):
        with self.lock:
            return dict(self.current) if self.current else None


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, fmt, *args):
        logging.info('%s %s', self.server.app, fmt % args)

    def kropslaengde(self, limit):
        values = self.headers.get_all('Content-Length', [])
        if self.headers.get('Transfer-Encoding') or len(values) > 1:
            raise ValueError('Ugyldig forespørgsel.')
        size = int(values[0]) if values else 0
        if size < 0 or size > limit:
            raise ValueError('Filen er for stor.')
        return size

    def laes_krop(self, size):
        data = self.rfile.read(size)
        if len(data) != size:
            raise ValueError('Filen blev ikke modtaget helt.')
        return data

    def svar(self, status, data, content_type=None, extra=None):
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False).encode('utf-8')
            content_type = 'application/json; charset=utf-8'
        elif isinstance(data, str):
            data = data.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type or 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        destinations = ' '.join(self.server.workshop_urls.values())
        if self.server.app == 'microbit':
            destinations += ' https://makecode.microbit.org'
        headers = {'Content-Security-Policy': CSP.replace("frame-src 'self' blob:", "frame-src 'self' blob: " + destinations)}
        headers.update(extra or {})
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def fil(self, filename):
        path = HERE / 'web' / filename
        self.svar(200, path.read_bytes(), mimetypes.guess_type(filename)[0] or 'application/octet-stream')

    def dispatch(self):
        try:
            hosts = self.headers.get_all('Host', [])
            expected = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if len(hosts) != 1 or hosts[0] not in expected:
                self.close_connection = True
                self.svar(403, {'error': 'Kun adgang fra denne computer.'})
                return
            if (self.headers.get('Sec-Fetch-Site') == 'cross-site' and
                    self.headers.get('Sec-Fetch-Mode') != 'navigate'):
                self.svar(403, {'error': 'Åbn fra den lokale projektoversigt.'})
                return
            path = unquote(urlsplit(self.path).path)
            document = (self.command == 'GET' and self.headers.get('Sec-Fetch-Dest') == 'document'
                        and self.headers.get('Sec-Fetch-Mode') == 'navigate')
            if path == PREFIX + '/session.js' and self.command in ('GET', 'HEAD'):
                self.fil('project-session.js')
                return
            if document and (path in (PREFIX, PREFIX+'/') or (self.server.app == 'projekter' and path == '/')):
                self.server.navigation.visit('start')
            if handle_session(self) or handle(self):
                return
            if self.command not in ('GET', 'HEAD'):
                self.close_connection = True
                self.svar(405, {'error': 'Denne handling findes ikke.'})
            elif path == '/_elevhub/metrophobic.ttf':
                self.fil('metrophobic.ttf')
            elif path == '/_elevhub/health':
                self.svar(200, {'local': True, 'app': self.server.app,
                               'apps': self.server.projekt_apps,
                               'navigation': self.server.navigation.read()})
            elif path == '/favicon.ico':
                self.svar(204, b'')
            elif self.server.app == 'projekter':
                self.svar(302, b'', extra={'Location': PREFIX+'/'})
            else:
                # No directory listing, source escaping, symlinks or project-root mounts.
                root = self.server.assets
                relative = path.lstrip('/') or 'index.html'
                target = (root / relative).resolve()
                if not target.is_relative_to(root) or not target.is_file():
                    self.svar(404, {'error': 'Filen findes ikke på denne computer.'})
                    return
                data = target.read_bytes()
                if document and path in ('/', '/index.html', '/standalone.html'):
                    self.server.navigation.visit(self.server.app)
                if target.suffix == '.html' and target.parent == root:
                    data = data.replace(b'</head>', (BRIDGE+'</head>').encode())
                self.svar(200, data, mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
        except (ValueError, OSError) as e:
            self.close_connection = True
            try:
                self.svar(400, {'error': 'Forespørgslen kunne ikke gennemføres.'})
            except OSError:
                pass

    do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = dispatch


def make_server(port, store, scanner=None, app='projekter', assets=None, apps=None, navigation=None, workshops=None, native=False, sessions=None):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.projekter, server.scanner, server.app = store, scanner, app
    server.assets = Path(assets).resolve() if assets else None
    server.projekt_apps = apps or []
    server.navigation = navigation or Navigation()
    server.workshop_urls = workshops if workshops is not None else workshop_urls()
    server.native_projects = native
    server.workshop_sessions = sessions or WorkshopSessions()
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--projects', type=Path, default=Path.home()/'Projekter')
    parser.add_argument('--state', type=Path)
    parser.add_argument('--apps-root', type=Path, default=Path('/usr/local/share/elevhub/apps'))
    parser.add_argument('--port', type=int, default=8791)
    parser.add_argument('--only-projects', action='store_true')
    parser.add_argument('--workshop-config', type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    # One bad workshop address must not take Scratch, Kredsløb, Datatræner and
    # the project shelf down with it; only "open in workshop" is then disabled.
    try:
        workshops = workshop_urls(args.workshop_config)
    except (OSError, ValueError) as error:
        logging.error('Værkstedsadresser kunne ikke læses (%s); projekter åbnes kun som visning.', error)
        workshops = {}
    native = Path('/usr/local/bin/elevhub-cadoodle').is_file()
    store = Projektarkiv(args.state or args.projects/'.elevhub')
    scanner = Scanner(args.projects, store)
    apps = [] if args.only_projects else [{'id': app, 'port': port} for app, port in APPS.items()
                                         if (args.apps_root/app/'index.html').is_file()]
    servers, stop = [], threading.Event()
    navigation = Navigation()
    sessions = WorkshopSessions()
    try:
        servers.append(make_server(args.port, store, scanner, apps=apps, navigation=navigation, workshops=workshops, native=native, sessions=sessions))
        for app in apps:
            servers.append(make_server(app['port'], store, scanner, app['id'], args.apps_root/app['id'], apps, navigation, workshops, native, sessions))
        for server in servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        threading.Thread(target=scanner.run, args=(stop,), daemon=True).start()
        logging.info('Lokale projekter: http://127.0.0.1:%s%s/', args.port, PREFIX)
        while not stop.wait(1):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        for server in servers:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    main()
