"""Lokal gateway for installerede apps på elevens egen maskine (kun loopback).

Appens sider serveres fra dens installerede pakke. Kun de faste SDK-ruter når
skolens server: laseraflevering, printaflevering og SVG-biblioteket. Serveren
validerer stadig med clean_svg, dedublerer på requestId og kan lukke værkstedet
(423). Kan serveren ikke nås, lægges en gyldig aflevering i en lokal udbakke og
sendes senere med samme requestId, så eleven kan aflevere uden forbindelse.
"""
import argparse
import base64
import http.client
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import ssl
import threading
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlparse, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from app.laser_server import Handler, InboxBusy, clean_svg, field
from app.school_appearance import (render_local_html, school_appearance,
                                  school_appearance_asset, school_appearance_manifest_present)
from app.stl import MAX_BYTES, inspect_stl

OFFLINE = 'Forbindelsen til læreren blev afbrudt. Prøv igen, eller gem SVG til USB.'
OUTBOX_FULL = 'Der venter allerede mange afleveringer på forbindelse. Gem projektet, og prøv igen senere.'
OUTBOX_LIMIT = 50
OUTBOX_RETRY_SECONDS = 30
OUTBOX_KEEP_REJECTED = 200
REQUEST_ID = re.compile(r'[a-zA-Z0-9-]{16,80}')
FORWARD_TIMEOUT = 10  # skal blive under laser.js' egen timeout på 15 s
JOB_ID = re.compile(r'[a-f0-9]{32}')


def school_profile_error_page(projects_port):
    """A usable document even when every school-profile asset is unavailable."""
    home = f'http://127.0.0.1:{int(projects_port)}/_elevhub/projekter/home'
    return '''<!doctype html>
<html lang="da"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Værkstedet kan ikke åbnes</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f5f7f6;color:#202923;
font:1.125rem/1.6 system-ui,sans-serif}main{max-width:42rem;margin:10vh auto;
padding:clamp(1.25rem,5vw,3rem)}h1{font-size:clamp(1.75rem,5vw,2.5rem);line-height:1.2}
nav{display:flex;flex-wrap:wrap;gap:1rem;margin-top:2rem}
a{display:inline-flex;align-items:center;justify-content:center;min-height:48px;
padding:.65rem 1rem;border:2px solid #23533b;border-radius:.5rem;color:#173c2a;
font-weight:650;text-decoration:underline;text-underline-offset:.2em}
a:first-child{background:#23533b;color:#fff}a:hover{border-color:#102b1d;background:#e2eae5}
a:first-child:hover{background:#173c2a}a:focus-visible{outline:3px solid #101b15;outline-offset:4px}
</style></head><body><main>
<h1>Værkstedet kan ikke åbnes</h1>
<p>Computerens skoleopsætning skal rettes. Få hjælp af din lærer.</p>
<p>Når opsætningen er rettet, kan du prøve at åbne værkstedet igen.</p>
<nav aria-label="Kom videre"><a href="">Prøv igen</a><a href="''' + home + '''">Til værkstederne</a></nav>
</main></body></html>'''


def student_identity(service):
    import importlib.util
    import os
    helper=Path("/usr/lib/makervaerkstedet/runtime_health.py")
    unknown={"release_id":None,"service":service,"pid":os.getpid(),"runtime_instance":None}
    if not helper.is_file() or helper.is_symlink():return unknown
    spec=importlib.util.spec_from_file_location("mkv_student_runtime",helper)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.runtime_snapshot("/usr/share/makervaerkstedet/runtime-release.json",expected_role="student",service=service)


class Offline(ValueError):
    """The school server could not be reached or answered nonsense."""


class Unexpected(Offline):
    """The server answered with a status this client does not expect (403, 404,
    415, an unreadable 400 ...). Usually a gateway or version problem, not the
    pupil's drawing, so the submission is kept and retried."""


class Closed(ValueError):
    """The teacher has closed the workshop (423). Worth retrying later."""


def validate_submission(data):
    """The central inbox's field rules, so the outbox never keeps a doomed job."""
    if not isinstance(data, dict):
        raise ValueError('Ugyldig aflevering.')
    for key, limit in (('elevnavn', 60), ('klasse', 40), ('projekt', 60)):
        field(data, key, limit)
    if not isinstance(data.get('requestId'), str) or not REQUEST_ID.fullmatch(data['requestId']):
        raise ValueError('Ugyldigt afleveringsnummer.')
    clean_svg(data.get('svg'))
    return {key: data[key] for key in ('elevnavn', 'klasse', 'projekt', 'svg', 'requestId')}


class Outbox:
    """Valid submissions waiting for the school server, one file per requestId.

    Files live in the service's own state directory (not the pupil's home) and
    are only removed after the server has accepted them or rejected them as
    invalid. Retrying with the same requestId never creates a second job.
    """

    def __init__(self, directory, limit=OUTBOX_LIMIT, validator=validate_submission):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.limit = limit
        self.validator = validator
        self.lock = threading.Lock()

    def _path(self, request_id):
        if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
            raise ValueError('Ugyldigt afleveringsnummer.')
        return self.directory / f'{request_id}.json'

    def put(self, data):
        path = self._path(data['requestId'])
        with self.lock:
            if self._check_pending(data):
                return
            if not path.exists() and len(self.waiting()) >= self.limit:
                raise ValueError(OUTBOX_FULL)
            temporary = path.with_suffix('.tmp')
            # Durable before the pupil is told the drawing is saved.
            with open(temporary, 'w', encoding='utf-8') as handle:
                handle.write(json.dumps(data, ensure_ascii=False))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            self._sync_directory()

    def _check_pending(self, data):
        path = self._path(data['requestId'])
        try:
            pending = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return False
        if pending != data:
            raise ValueError('Denne indsendelse er allerede brugt til en anden fil.')
        return True

    def check_pending(self, data):
        with self.lock:
            self._check_pending(data)

    def _sync_directory(self):
        try:
            descriptor = os.open(self.directory, os.O_RDONLY)
        except OSError:
            return  # Windows cannot open a directory; Linux devices can.
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def waiting(self):
        entries = []
        for item in self.directory.glob('*.json'):
            try:
                entries.append((item.stat().st_mtime, item.name, item))
            except FileNotFoundError:
                continue  # sent by the other thread meanwhile
        return [item for _, _, item in sorted(entries)]

    def _set_aside(self, path, reason):
        """Keep a rejected submission for an adult instead of deleting it."""
        rejected = self.directory / 'afvist'
        rejected.mkdir(exist_ok=True, mode=0o700)
        try:
            os.replace(path, rejected / path.name)
        except FileNotFoundError:
            return
        print(f'Udbakke: {path.stem} lagt i afvist/: {reason}', flush=True)
        kept = sorted(rejected.glob('*.json'), key=lambda item: item.stat().st_mtime)
        for old in kept[:-OUTBOX_KEEP_REJECTED]:
            old.unlink(missing_ok=True)

    def flush(self, send):
        """Send waiting jobs oldest first.

        Stops while the server is unreachable or the workshop is closed. A
        submission the server cannot take right now (quota, odd status) is
        skipped and retried; one it rejects as invalid is set aside.
        """
        for stale in self.directory.glob('*.tmp'):
            try:
                if stale.stat().st_mtime < time.time() - 3600:
                    stale.unlink()
            except FileNotFoundError:
                pass
        sent = 0
        for path in self.waiting():
            try:
                raw = path.read_text(encoding='utf-8')
            except FileNotFoundError:
                continue
            except OSError as error:
                print(f'Udbakke: {path.name} kunne ikke læses nu: {type(error).__name__}', flush=True)
                continue
            try:
                data = self.validator(json.loads(raw))
            except ValueError as error:
                self._set_aside(path, f'ugyldig fil ({error})')
                continue
            try:
                send(data)
            except Unexpected:
                print(f'Udbakke: {data["requestId"]} fik et uventet svar; prøver igen senere.', flush=True)
                continue
            except (Offline, Closed):
                return sent
            except InboxBusy as busy:
                if getattr(busy, 'retry_after', None):
                    return sent  # Køen tager ikke imod lige nu; prøv hele udbakken senere.
                continue  # Kun denne elevs kvote er fuld.
            except ValueError as error:
                self._set_aside(path, f'afvist af serveren: {error}')
                continue
            path.unlink(missing_ok=True)
            sent += 1
            print(f'Udbakke: {data["requestId"]} sendt til læreren.', flush=True)
        return sent

    def run(self, send, stop, interval=OUTBOX_RETRY_SECONDS):
        while not stop.is_set():
            try:
                self.flush(send)
            except Exception as error:  # the worker must outlive any surprise
                print(f'Udbakke: fejl under afsendelse: {type(error).__name__}', flush=True)
            stop.wait(interval)


class _NoRedirect(HTTPRedirectHandler):
    """Følger aldrig omdirigeringer - en 3xx fra "serveren" behandles som fejl."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ForwardingInbox:
    """Sender submit og de faste SVG-biblioteksruter videre."""

    validate = staticmethod(validate_submission)

    def __init__(self, server_url, ca_file=None, timeout=FORWARD_TIMEOUT, outbox=None):
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
        self.outbox = outbox
        context = ssl.create_default_context(cafile=ca_file)
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), _NoRedirect())
        # Samme attributter som Inbox, så Handler.session()/login kan bruge dem uændret.
        self.lock = threading.Lock()
        self.sessions = {}
        self.attempts = {}

    def submit(self, data):
        # Serialize submissions so two requests cannot race between the pending
        # identity check, forwarding and saving a newly offline submission.
        with self.lock:
            if self.outbox is not None:
                data = self.validate(data)
                self.outbox.check_pending(data)
            try:
                return self.send(data)
            except Offline:
                if self.outbox is None:
                    raise
                self.outbox.put(data)
                return {'queued': True, 'requestId': data['requestId']}

    def send(self, data):
        try:
            return self._submit(data)
        except ValueError:
            raise
        except Exception:
            # Intet uventet må vælte requesten - eleven skal altid se en dansk fejltekst.
            raise Offline(OFFLINE) from None

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
        payload = self._forward('/api/laser/submit', body, {'Content-Type': 'application/json'})
        job_id, duplicate = payload.get('id'), payload.get('duplicate')
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id) or not isinstance(duplicate, bool):
            raise Offline(OFFLINE)
        return {'id': job_id, 'duplicate': duplicate}

    def _forward(self, path, body, headers, accepted=(200,)):
        req = Request(self.server_url + path, data=body, headers=headers, method='POST')
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                status = response.status
                raw = response.read(65536)
        except HTTPError as error:
            with error:  # lukker forbindelsen; tjenesten kører hele skoledagen
                raw = error.read(65536)
            message = self._error_message(raw)
            if error.code >= 500:
                raise Offline(OFFLINE) from None
            if message is not None and error.code in (400, 413):
                raise ValueError(message) from None
            if message is not None and error.code == 423:
                raise Closed(message) from None
            if message is not None and error.code == 429:
                busy = InboxBusy(message)
                # A Retry-After means the whole queue (or this device) is
                # paused; one without it is a per-pupil quota.
                busy.retry_after = error.headers.get('Retry-After') if error.headers else None
                raise busy from None
            raise Unexpected(OFFLINE) from None
        except (URLError, socket.timeout, ssl.SSLError, OSError):
            raise Offline(OFFLINE) from None
        if status not in accepted:
            raise Offline(OFFLINE)
        try:
            payload = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            raise Offline(OFFLINE) from None
        if not isinstance(payload, dict):
            raise Offline(OFFLINE)
        return payload

    @staticmethod
    def _error_message(raw):
        try:
            payload = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return None
        message = payload.get('error') if isinstance(payload, dict) else None
        return message if isinstance(message, str) and 0 < len(message) <= 300 else None


class LocalDesignHandler(Handler):
    """Queue, library and project traffic for one workshop; never an editor page.

    Server.vaerksted er 'laser' eller 'folie'; server.port er den bundne port.
    """

    def reply(self, status, body, mime='application/json; charset=utf-8', headers=None):
        if status == 200 and isinstance(body, dict) and body.get('queued') is True:
            status = 202  # Gemt i udbakken; sendes, når serveren kan nås.
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
        if route == '/api/runtime/health':
            return self.reply(200, {"local":True,"surface":"student",**self.server.runtime_health})
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
        # Queue, library and school-profile traffic only. The editors ship in
        # app packages; InstalledHandler serves their pages from the package.
        if not route.startswith(('/api/', '/school-appearance/', '/school-profile-assets/')):
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


def create_local_servers(server_url, ca_file=None, host='127.0.0.1', laser_port=8795, folie_port=8796, projects_port=8791, runtime=None, outbox_dir=None):
    if host not in ('127.0.0.1', 'localhost'):
        raise ValueError('Denne tjeneste må kun binde til loopback (127.0.0.1/localhost).')
    inbox = ForwardingInbox(server_url, ca_file, outbox=Outbox(outbox_dir) if outbox_dir else None)
    runtime = dict(runtime) if runtime is not None else student_identity("elevhub-design")
    servers = []
    for vaerksted, port in (('laser', laser_port), ('folie', folie_port)):
        server = ThreadingHTTPServer((host, port), LocalDesignHandler)
        server.inbox = inbox
        server.runtime_health = dict(runtime)
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


def print_submission(body, filename, request_id):
    if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
        raise ValueError('Prøv at sende modellen igen.')
    if (not isinstance(filename, str) or not filename.lower().endswith('.stl')
            or len(filename) > 500 or any(ord(c) < 32 for c in filename)):
        raise ValueError('Vælg en fil, der slutter med .stl.')
    inspect_stl(body)
    return {'type': 'print-job', 'requestId': request_id, 'filename': filename,
            'stl': base64.b64encode(body).decode('ascii')}


def validate_print_submission(data):
    if (not isinstance(data, dict) or set(data) != {'type', 'requestId', 'filename', 'stl'}
            or data['type'] != 'print-job' or not isinstance(data['stl'], str)
            or len(data['stl']) > 4 * ((MAX_BYTES + 2) // 3)):
        raise ValueError('Ugyldig 3D-aflevering.')
    try:
        body = base64.b64decode(data['stl'], validate=True)
    except (ValueError, UnicodeError):
        raise ValueError('Ugyldig 3D-aflevering.') from None
    return print_submission(body, data['filename'], data['requestId'])


class PrintForwardingInbox(ForwardingInbox):
    validate = staticmethod(validate_print_submission)

    def _submit(self, data):
        payload = self._forward('/api/jobs', base64.b64decode(data['stl']), {
            'Content-Type': 'application/octet-stream',
            'X-Filename': quote(data['filename'], safe=''), 'X-Colour': 'any',
            'X-Quality': 'standard', 'X-Request-ID': data['requestId'],
        }, accepted=(201,))
        if (not isinstance(payload.get('id'), str) or not JOB_ID.fullmatch(payload['id'])
                or not isinstance(payload.get('status'), str)):
            raise Unexpected('Printkøen svarede ikke som forventet.')
        return {'id': payload['id'], 'status': payload['status']}


class LocalPrintHandler(LocalDesignHandler):
    """Only the project gateway and the fixed print submission route."""

    def send_header(self, keyword, value):
        if keyword.lower() == 'content-security-policy':
            value = value.replace("script-src 'self';", "script-src 'self' 'wasm-unsafe-eval'; worker-src 'self';")
        super().send_header(keyword, value)

    def do_GET(self):
        if not self._host_ok():
            return self.reply(403, {'error': 'Ukendt adresse.'})
        if self._projects():
            return
        route = unquote(urlsplit(self.path).path)
        if route == '/api/runtime/health':
            return self.reply(200, {'local': True, 'surface': 'student', **self.server.runtime_health})
        if route == '/api/school-appearance':
            return Handler.do_GET(self)
        # The 3D editor ships in its app package; InstalledHandler serves it.
        return self.reply(404, {'error': 'Siden findes ikke.'})

    def do_POST(self):
        if not self._host_ok():
            return self.reply(403, {'error': 'Ukendt adresse.'})
        if self._projects():
            return
        if self.path != '/api/jobs':
            return self.reply(404, {'error': 'Funktionen findes ikke.'})
        origin = 'http://' + self.headers.get('Host', '')
        if self.headers.get('Origin') != origin or self.headers.get('Sec-Fetch-Site') == 'cross-site':
            return self.reply(403, {'error': 'Send modellen fra 3D-værkstedets egen side.'})
        try:
            sizes = self.headers.get_all('Content-Length', [])
            if len(sizes) != 1 or self.headers.get('Transfer-Encoding'):
                raise ValueError('Upload modellen igen.')
            size = int(sizes[0])
            if not 0 < size <= MAX_BYTES:
                return self.reply(413, {'error': 'Vælg en STL-fil på højst 25 MB.'})
            if self.headers.get('X-Colour', 'any') != 'any' or self.headers.get('X-Quality', 'standard') != 'standard':
                raise ValueError('Send modellen fra 3D-værkstedet.')
            deadline = time.monotonic() + 30
            chunks, received = [], 0
            while received < size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError('Uploaden tog for lang tid. Prøv igen.')
                self.connection.settimeout(remaining)
                part = self.rfile.read1(min(size - received, 1024 * 1024))
                if not part:
                    raise ValueError('Hele modellen blev ikke modtaget. Prøv igen.')
                chunks.append(part)
                received += len(part)
            data = print_submission(b''.join(chunks), unquote(self.headers.get('X-Filename', '')),
                                    self.headers.get('X-Request-ID', ''))
            payload = getattr(self.server,'print_inbox',self.server.inbox).submit(data)
            return self.reply(202 if payload.get('queued') else 201, payload)
        except Closed as error:
            return self.reply(423, {'error': str(error)})
        except InboxBusy as error:
            return self.reply(429, {'error': str(error)})
        except Offline:
            return self.reply(503, {'error': 'Modellen kunne ikke gemmes til afsendelse. Gem din fil, og prøv igen.'})
        except ValueError as error:
            return self.reply(400, {'error': str(error)})
        except OSError:
            return self.reply(503, {'error': 'Modellen kunne ikke gemmes. Gem din fil, og prøv igen.'})


def create_print_server(server_url, ca_file=None, host='127.0.0.1', port=8798,
                        projects_port=8791, runtime=None, outbox_dir=None):
    if host not in ('127.0.0.1', 'localhost'):
        raise ValueError('Denne tjeneste må kun binde til loopback (127.0.0.1/localhost).')
    outbox = Outbox(outbox_dir, validator=validate_print_submission) if outbox_dir else None
    inbox = PrintForwardingInbox(server_url, ca_file, outbox=outbox)
    server = ThreadingHTTPServer((host, port), LocalPrintHandler)
    server.inbox = inbox
    server.runtime_health = dict(runtime) if runtime is not None else student_identity('elevhub-design')
    server.surface = 'student'
    server.frame_ancestors = f"'self' http://127.0.0.1:{projects_port} http://localhost:{projects_port}"
    server.port = server.server_address[1]
    server.projects_port = projects_port
    server.data_root = Path('/etc/elevhub')
    return server


def _app_host_module():
    source=Path(__file__).resolve()
    project_modules=source.parents[2]/'projekter' if source.parents[2].name=='elevhub' else source.parents[1]/'linux-elevhub/projekter'
    sys.path.insert(0,str(project_modules))
    import app_host
    return app_host


def create_installed_servers(apps,laser,printing,*,runtime=None,projects_port=8791,
                             data_root=Path('/etc/elevhub'),shared_root=None,library_client=None):
    """Bind declarative apps; only fixed SDK endpoints get school-network access."""
    host=_app_host_module();servers=[]
    from .svg_library_service import UnixLibraryClient
    library_client=library_client if library_client is not None else UnixLibraryClient()
    identity=dict(runtime) if runtime is not None else student_identity('elevhub-design')
    shared=Path(shared_root) if shared_root is not None else Path(__file__).resolve().parents[1]/'web'
    for app in apps:
        if not host.uses_gateway(app):continue
        class InstalledHandler(LocalPrintHandler):
            def capabilities(self):
                caps=self.server.descriptor['capabilities']
                return set(caps['required']+caps['optional'])
            def reply(self,status,body,mime='application/json; charset=utf-8',headers=None):
                if status==200 and isinstance(body,dict) and body.get('queued') is True:status=202
                return Handler.reply(self,status,body,mime,headers)
            def do_GET(self):
                if not self._host_ok():return self.reply(403,{'error':'Ukendt adresse.'})
                if self._projects():return
                path=urlsplit(self.path).path
                if path=='/api/runtime/health':
                    return self.reply(200,{'local':True,'app':self.server.descriptor['id'],
                        'app_version':self.server.descriptor['version'],**self.server.runtime_health})
                library=re.fullmatch(r'/api/(laser|folie)/library(?:/status|/file/[a-f0-9]{64})?',path)
                if library:
                    if 'svg-library-v1' not in self.capabilities() or self.path!=path:
                        return self.reply(403,{'error':'Appen har ikke adgang til SVG-biblioteket.'})
                    try:
                        client=self.server.svg_library
                        payload=client.status() if path.endswith('/status') else client.svg_response(path.rsplit('/',1)[-1]) if '/file/' in path else client.catalogue_response()
                        return self.reply(200,payload)
                    except (ValueError,OSError):
                        return self.reply(503,{'error':'SVG-biblioteket kunne ikke hentes. Prøv igen, når forbindelsen er tilbage.'})
                if path.startswith('/api/laser/') and 'laser-submit-v1' not in self.capabilities():
                    return self.reply(404,{'error':'Appen bruger ikke laserkøen.'})
                if path.startswith('/api/') or path.startswith('/school-appearance/') or path.startswith('/school-profile-assets/'):
                    return LocalDesignHandler.do_GET(self)
                # Same rule as the shelf: a profile that is present but fails
                # validation is never silently replaced by the default look.
                appearance=school_appearance(self.server.data_root)
                if appearance is None and school_appearance_manifest_present(self.server.data_root):
                    document = (path == '/' or path.endswith('.html') or
                                (self.headers.get('Sec-Fetch-Dest') == 'document' and not Path(path).suffix))
                    if document:
                        return self.reply(503, school_profile_error_page(self.server.projects_port),
                                          'text/html; charset=utf-8')
                    return self.reply(503,{'error':'Den lokale skoleprofil kunne ikke valideres.'})
                overlay=school_appearance_asset(self.server.data_root,path)
                if overlay is not None:return self.reply(200,*overlay)
                value=host.asset(self.server.descriptor,path,self.server.shared_assets)
                if value is None:return self.reply(404,{'error':'Siden findes ikke.'})
                data,mime=value
                if mime=='text/html':data=render_local_html(data,appearance)
                return self.reply(200,data,mime)
            def do_POST(self):
                if self.path=='/api/jobs':
                    if 'print-job-v1' not in self.capabilities():return self.reply(404,{'error':'Appen bruger ikke printkøen.'})
                    return LocalPrintHandler.do_POST(self)
                if self.path.startswith('/api/') and 'laser-submit-v1' not in self.capabilities():
                    return self.reply(404,{'error':'Appen bruger ikke laserkøen.'})
                return LocalDesignHandler.do_POST(self)
        try:server=ThreadingHTTPServer(('127.0.0.1',app['port']),InstalledHandler)
        except OSError:
            print('App '+app['id']+' kunne ikke starte; udbakkerne fortsætter.',flush=True)
            continue
        server.descriptor=app;server.inbox=laser;server.print_inbox=printing
        server.runtime_health=identity;server.surface='student'
        server.frame_ancestors="'self' http://127.0.0.1:"+str(projects_port)
        server.vaerksted=app['id'];server.port=server.server_port;server.projects_port=projects_port
        server.data_root=Path(data_root);server.shared_assets=shared
        server.svg_library=library_client
        servers.append(server)
    return servers


def run_installed_apps(args):
    """Always run pending queues, including with zero installed editors."""
    host=_app_host_module();apps=host.discover_apps(args.manifest_root,strict=False)
    laser=ForwardingInbox(args.server_url,args.ca_file,outbox=Outbox(args.outbox_dir) if args.outbox_dir else None)
    printing=PrintForwardingInbox(args.print_server_url or args.server_url,args.ca_file,
        outbox=Outbox(Path(args.outbox_dir)/'print',validator=validate_print_submission) if args.outbox_dir else None)
    stop=threading.Event();servers=[]
    for inbox in (laser,printing):
        if inbox.outbox is not None:threading.Thread(target=inbox.outbox.run,args=(inbox.send,stop),daemon=True).start()
    try:
        servers=create_installed_servers(apps,laser,printing)
        for server in servers:threading.Thread(target=server.serve_forever,daemon=True).start()
        print('Installerede netværksapps klar: '+', '.join(s.descriptor['id'] for s in servers)+'; udbakker kører uafhængigt.',flush=True)
        while not stop.wait(1):pass
    except KeyboardInterrupt:pass
    finally:
        stop.set()
        for server in servers:server.shutdown();server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-url', required=True)
    parser.add_argument('--ca-file')
    parser.add_argument('--print-server-url', help='Hubbens printindgang, fx https://server:8770')
    parser.add_argument('--outbox-dir', help='Lokal udbakke til afleveringer uden forbindelse.')
    parser.add_argument('--manifest-root', type=Path, default=Path('/usr/share/makervaerkstedet/apps'),
                        help='Mappen med installerede apps. Hver app binder porten fra sit manifest.')
    # Accepted for older service units and ignored: ports come from app manifests.
    for legacy in ('--host', '--laser-port', '--folie-port', '--model3d-port'):
        parser.add_argument(legacy, help=argparse.SUPPRESS)
    run_installed_apps(parser.parse_args())


if __name__ == '__main__':
    main()
