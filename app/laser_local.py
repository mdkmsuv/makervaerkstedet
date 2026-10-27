"""Kører laser- og folieeditoren lokalt på elevens egen maskine (kun loopback).
Kun "send til læreren" videresendes til den centrale skoleserver, som stadig
validerer med clean_svg, dedublerer på requestId og kan lukke værkstedet (423).
"""
import argparse
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import re
import socket
import ssl
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from app.laser_server import Handler

OFFLINE = 'Forbindelsen til læreren blev afbrudt. Prøv igen, eller gem SVG til USB.'
FORWARD_TIMEOUT = 10  # skal blive under laser.js' egen timeout på 15 s
JOB_ID = re.compile(r'[a-f0-9]{32}')
ELEVHUB_STYLE = '<style id="elevhub">#workshop-home { display: none !important; }</style>'
PROJECT_BRIDGE = '<link rel="stylesheet" href="/_elevhub/projekter/bridge.css"><script src="/_elevhub/projekter/bridge.js" defer></script>'


class _NoRedirect(HTTPRedirectHandler):
    """Følger aldrig omdirigeringer - en 3xx fra "serveren" behandles som fejl."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ForwardingInbox:
    """Sender submit og de faste SVG-biblioteksruter videre."""

    def __init__(self, server_url, ca_file=None, timeout=FORWARD_TIMEOUT):
        parsed = urlsplit(server_url)
        if parsed.scheme not in ('http', 'https'):
            raise ValueError('Serveradressen skal starte med http:// eller https://.')
        if not parsed.hostname:
            raise ValueError('Serveradressen mangler værtsnavn.')
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Serveradressen må kun være en ren origin.')
        if parsed.path not in ('', '/'):
            raise ValueError('Serveradressen må ikke have en sti.')
        self.server_url = server_url.rstrip('/')
        self.timeout = timeout
        context = ssl.create_default_context(cafile=ca_file)
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), _NoRedirect())
        # Samme attributter som Inbox, så Handler.session()/login kan bruge dem uændret.
        self.lock = threading.Lock()
        self.sessions = {}
        self.attempts = {}

    def submit(self, data):
        try:
            return self._submit(data)
        except ValueError:
            raise
        except Exception:
            # Intet uventet må vælte requesten - eleven skal altid se en dansk fejltekst.
            raise ValueError(OFFLINE) from None

    def library(self, path):
        """Forward one fixed workshop-library route and recheck its gate."""
        match = re.fullmatch(r'/api/(laser|folie)/library(?:/status|/file/[a-f0-9]{64})?', path)
        if not match:
            return 403, {'error': 'Ugyldig biblioteksforespørgsel.'}
        workshop = match.group(1)
        status_route = f'/api/{workshop}/library/status'
        def read(route):
            try:
                with self.opener.open(Request(self.server_url + route, method='GET'), timeout=8) as response:
                    raw = response.read(2_100_001)
                    if response.status != 200 or len(raw) > 2_100_000:
                        raise ValueError(OFFLINE)
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        raise ValueError(OFFLINE)
                    return 200, payload
            except HTTPError as error:
                with error:
                    message = self._error_message(error.read(65536))
                return error.code if error.code in (403, 404, 423, 502, 503) else 503, {'error': message or OFFLINE}
        try:
            status, payload = read(path)
            if status == 200 and path != status_route:
                check_status, check = read(status_route)
                if check_status != 200 or check.get('enabled') is not True or check.get('version') != payload.get('version'):
                    return 423, {'error': f'Læreren har ændret adgangen til {workshop.capitalize()}.'}
            return status, payload
        except (OSError, ValueError):
            return 503, {'error': 'Biblioteket kræver forbindelse til læreren.'}

    def health(self):
        """Return the live central connection state for the local workshop."""
        try:
            with self.opener.open(Request(self.server_url + '/api/laser/health', method='GET'),
                                  timeout=min(self.timeout, 4)) as response:
                raw = response.read(65536)
                if response.status != 200:
                    raise ValueError('central health status')
                payload = json.loads(raw.decode('utf-8'))
                if not isinstance(payload, dict):
                    raise ValueError('central health payload')
                available = payload.get('surface') == 'student' and payload.get('inbox') is True
                return 200, {'surface': 'student', 'inbox': bool(available)}
        except (HTTPError, URLError, OSError, socket.timeout, ssl.SSLError,
                ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return 503, {'surface': 'student', 'inbox': False}

    def _submit(self, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        req = Request(self.server_url + '/api/laser/submit', data=body,
                       headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                status = response.status
                raw = response.read(65536)
        except HTTPError as error:
            with error:  # lukker forbindelsen; tjenesten kører hele skoledagen
                raw = error.read(65536)
            if error.code in (400, 413, 423):
                message = self._error_message(raw)
                if message is not None:
                    raise ValueError(message) from None
            raise ValueError(OFFLINE) from None
        except (URLError, socket.timeout, ssl.SSLError, OSError):
            raise ValueError(OFFLINE) from None
        if status != 200:
            raise ValueError(OFFLINE)
        try:
            payload = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            raise ValueError(OFFLINE) from None
        if not isinstance(payload, dict):
            raise ValueError(OFFLINE)
        job_id, duplicate = payload.get('id'), payload.get('duplicate')
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id) or not isinstance(duplicate, bool):
            raise ValueError(OFFLINE)
        return {'id': job_id, 'duplicate': duplicate}

    @staticmethod
    def _error_message(raw):
        try:
            payload = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return None
        message = payload.get('error') if isinstance(payload, dict) else None
        return message if isinstance(message, str) and 0 < len(message) <= 300 else None


class LocalDesignHandler(Handler):
    """Server.vaerksted er 'laser' eller 'folie'; server.port er den bundne port."""

    def reply(self, status, body, mime='application/json; charset=utf-8', headers=None):
        if mime.startswith('text/html') and isinstance(body, bytes):
            text = body.decode('utf-8')
            text = (text.replace('</head>', ELEVHUB_STYLE + PROJECT_BRIDGE + '</head>', 1) if '</head>' in text
                    else ELEVHUB_STYLE + text)
            body = text.encode('utf-8')
        super().reply(status, body, mime, headers)

    def _host_ok(self):
        host = self.headers.get('Host', '')
        return host in (f'127.0.0.1:{self.server.port}', f'localhost:{self.server.port}')

    def _projects(self):
        """Same-origin proxy to the pupil's project service, never the school server."""
        route = urlsplit(self.path)
        if not route.path.startswith(('/_elevhub/projekter/', '/_elevhub/session/')):
            return False
        connection = None
        try:
            body = None
            headers = {}
            upstream = f'127.0.0.1:{self.server.projects_port}'
            if self.command == 'POST':
                origin = 'http://' + self.headers.get('Host', '')
                if self.headers.get('Origin') != origin:
                    return self.reply(403, {'error': 'Gem fra værkstedets egen side.'}) or True
                sizes = self.headers.get_all('Content-Length', [])
                if len(sizes) != 1 or self.headers.get('Transfer-Encoding'):
                    raise ValueError('Ugyldig fil.')
                size = int(sizes[0])
                if not 0 <= size <= 50 * 1024 * 1024:
                    raise ValueError('Filen er for stor.')
                body = self.rfile.read(size)
                if len(body) != size:
                    raise ValueError('Filen blev ikke modtaget helt.')
                headers = {'Origin': 'http://' + upstream, 'Content-Type': self.headers.get('Content-Type', '')}
                for name in ('X-Elevhub-Project', 'X-Elevhub-Session'):
                    if self.headers.get(name): headers[name] = self.headers[name]
            connection = http.client.HTTPConnection('127.0.0.1', self.server.projects_port, timeout=10)
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read(51 * 1024 * 1024)
            # Emit one gateway CSP. Intersecting this with the project
            # service's frame-ancestors policy would make the iframe blank.
            extra = {name: value for name, value in response.getheaders()
                     if name.lower() == 'content-disposition'}
            super().reply(response.status, data, response.getheader('Content-Type', 'application/octet-stream'), extra)
        except (OSError, http.client.HTTPException):
            self.reply(503, {'error': 'Projektgemning er ikke klar. Dit arbejde er stadig åbent.'})
        except ValueError as error:
            self.reply(400, {'error': str(error)})
        finally:
            if connection: connection.close()
        return True

    def do_GET(self):
        if not self._host_ok():
            return self.reply(403, {'error': 'Ukendt adresse.'})
        if self._projects():
            return
        route = unquote(urlparse(self.path).path)
        if route == '/api/laser/health':
            status, payload = self.server.inbox.health()
            return self.reply(status, payload)
        library_match = re.fullmatch(r'/api/(laser|folie)/library(?:/status|/file/[a-f0-9]{64})?', route)
        if library_match:
            if self.server.vaerksted != library_match.group(1) or self.path != route:
                return self.reply(403, {'error': 'Biblioteket er kun tilgængeligt i dette værksted.'})
            status, payload = self.server.inbox.library(route)
            return self.reply(status, payload)
        if route.startswith('/api/laser/library') or route.startswith('/api/folie/library'):
            return self.reply(403, {'error': 'Ugyldig biblioteksforespørgsel.'})
        own_page = '/laser.html' if self.server.vaerksted == 'laser' else '/folie.html'
        other_page = '/folie.html' if self.server.vaerksted == 'laser' else '/laser.html'
        if route == '/':
            return self.reply(302, {}, headers={'Location': own_page})
        if route in (other_page, '/workshop.html', '/workshop.css'):
            return self.reply(404, {'error': 'Siden findes ikke.'})
        if (self.server.vaerksted == 'folie' and route.startswith('/api/')
                and route not in ('/api/laser/health', '/api/school-appearance')):
            return self.reply(404, {'error': 'Siden findes ikke.'})
        return super().do_GET()

    def do_POST(self):
        if not self._host_ok():
            return self.reply(403, {'error': 'Ukendt adresse.'})
        if self._projects():
            return
        if self.server.vaerksted == 'folie':
            return self.reply(404, {'error': 'Funktionen findes ikke.'})
        return super().do_POST()


def create_local_servers(server_url, ca_file=None, host='127.0.0.1', laser_port=8795, folie_port=8796, projects_port=8791):
    if host not in ('127.0.0.1', 'localhost'):
        raise ValueError('Denne tjeneste må kun binde til loopback (127.0.0.1/localhost).')
    inbox = ForwardingInbox(server_url, ca_file)
    servers = []
    for vaerksted, port in (('laser', laser_port), ('folie', folie_port)):
        server = ThreadingHTTPServer((host, port), LocalDesignHandler)
        server.inbox = inbox
        server.surface = 'student'
        # The project service is reached through this same-origin gateway.
        server.frame_ancestors = "'self'"
        server.vaerksted = vaerksted
        server.port = server.server_address[1]
        server.projects_port = projects_port
        # The shared appearance helper reads /etc/elevhub/kiosk.json on Linux.
        # Keep the same data-root contract as the central laser server without
        # giving the local workshop access to queue or teacher data.
        server.data_root = Path('/etc/elevhub')
        servers.append(server)
    return servers[0], servers[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-url', required=True)
    parser.add_argument('--ca-file')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--laser-port', type=int, default=8795)
    parser.add_argument('--folie-port', type=int, default=8796)
    args = parser.parse_args()
    laser_server, folie_server = create_local_servers(
        args.server_url, args.ca_file, args.host, args.laser_port, args.folie_port)
    folie_thread = threading.Thread(target=folie_server.serve_forever, daemon=True)
    folie_thread.start()
    print(f'Laserværksted: http://{args.host}:{laser_server.port}/  '
          f'Folieværksted: http://{args.host}:{folie_server.port}/', flush=True)
    try:
        laser_server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        laser_server.server_close()
        folie_server.shutdown()
        folie_server.server_close()


if __name__ == '__main__':
    main()
