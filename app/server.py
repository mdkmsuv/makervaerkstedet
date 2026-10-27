import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import hashlib
import json
import logging
import mimetypes
import os
from pathlib import Path
import socket
import ssl
import threading
import time
from urllib.parse import unquote, urlparse, quote, parse_qs
import uuid
import re
import secrets

from .bambu import BambuPrinter
from .camera import Cameras
from .core import Queue, SubmissionBlocked
from .instance import InstanceLock
from .backup_guard import assert_not_restored, write_protocol
from .paths import data_root
from .desktop import instance_id
from .discovery import discover
from .firmware import FirmwareChecks
from .profiles import MODELS
from .printer_bridge import PrinterBridge
from .printer_config import ConfigConflict, public_printer
from .slicer import Slicer
from .stl import MAX_BYTES
from .file_library import LibraryInUse, LibraryDeletionPending, LibraryStorageError, LibraryConflict
from .submission_stage import SubmissionStageError
from .worker import Worker, demo_configs, demo_drivers

from .job_diagnostics import read_diagnostic
from .school_appearance import (school_appearance, school_appearance_asset,
                                school_appearance_manifest_present,
                                render_local_html)
from .teacher_auth import TeacherAuth, LoginLimited
from .project_origins import DEFAULT_PROJECT_ORIGINS, validate_project_origins, configured_project_origins
from .demo_workshop import demo_component, demo_printers, demo_driver

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STUDIO = str(ROOT / 'tools' / 'bambu' / 'bambu-studio.exe') if (ROOT / 'tools' / 'bambu' / 'bambu-studio.exe').is_file() else r'C:\Program Files\Bambu Studio\bambu-studio.exe'
UPLOAD_IN_FLIGHT_BYTES = MAX_BYTES * 2
UPLOAD_TOTAL_SECONDS = 60
UPLOAD_READ_CHUNK = 64 * 1024


class UploadOverloaded(Exception):
    """The bounded student-upload memory budget is currently exhausted."""

    status = 503
    retry_after = 1


class UploadDeadlineExceeded(Exception):
    """A student upload did not arrive within its total wall-clock budget."""

    status = 408


class _UploadReservation:
    def __init__(self, budget, size):
        self._budget = budget
        self._size = size
        self._released = False

    def release(self):
        if not self._released:
            self._released = True
            self._budget._release(self._size)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()


class UploadBudget:
    """Reserve bounded in-flight bytes for student model uploads."""

    def __init__(self, capacity=UPLOAD_IN_FLIGHT_BYTES, max_uploads=4):
        if type(capacity) is not int or capacity <= 0 or type(max_uploads) is not int or max_uploads <= 0:
            raise ValueError('Uploadbudgettet skal være positivt.')
        self.capacity = capacity
        self.max_uploads = max_uploads
        self._available = capacity
        self._active = 0
        self._lock = threading.Lock()

    def reserve(self, size):
        if type(size) is not int or not 0 < size <= self.capacity:
            raise UploadOverloaded('Uploaden er for stor til den midlertidige uploadgrænse.')
        with self._lock:
            if self._active >= self.max_uploads or size > self._available:
                raise UploadOverloaded('Uploadkøen er midlertidigt fuld. Prøv igen om lidt.')
            self._available -= size
            self._active += 1
        return _UploadReservation(self, size)

    def _release(self, size):
        with self._lock:
            if self._active <= 0 or size <= 0 or size > self.capacity - self._available:
                raise RuntimeError('Uploadreservationen kunne ikke frigives korrekt.')
            self._available += size
            self._active -= 1


def teacher_code_for_installation(token_file):
    """Keep the installation's code; never replace a locally chosen code."""
    if token_file.is_file():
        code = token_file.read_text(encoding='ascii').strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', code):
            raise ValueError('Lærerkodefilen er ugyldig. Ret den lokalt på styreenheden.')
    else:
        code = secrets.token_urlsafe(12)
        with token_file.open('x', encoding='ascii') as stream:
            stream.write(code)
    if os.name != 'nt':
        token_file.chmod(0o600)
    return code


def configured_print_hosts(path):
    """Read explicit public authorities, including a port-forward's public port."""
    if not path.exists():
        return set()
    if path.stat().st_size > 8192:
        raise ValueError('print-hosts.json er for stor.')
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(config, dict) or set(config) != {'allowed_hosts'}:
        raise ValueError('print-hosts.json skal indeholde allowed_hosts.')
    values = config['allowed_hosts']
    if not isinstance(values, list) or len(values) > 32:
        raise ValueError('allowed_hosts skal være en liste med højst 32 værter.')
    authorities = set()
    for value in values:
        if not isinstance(value, str) or not value or re.search(r'[\s/@?#\\*]', value):
            raise ValueError('Brug et konkret værtsnavn med valgfri port i allowed_hosts.')
        parsed = urlparse('//' + value)
        if (not parsed.hostname or value.endswith(':') or parsed.path or
                parsed.hostname in ('0.0.0.0', '::') or parsed.port == 0):
            raise ValueError('Ugyldig vært i allowed_hosts.')
        authorities.add((parsed.hostname, parsed.port))
    return authorities


class Application:
    def __init__(self, folder, demo=False):
        self.folder = Path(folder).resolve()
        # Check before creating the directory: a restored tree may contain its
        # hold marker in an ancestor that does not yet exist as this path.
        assert_not_restored(self.folder)
        self.instance_lock = None
        self.queue = self.worker = self.firmware = self.cameras = None
        self.drivers = {}
        self._closed = False
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            self.instance_lock = InstanceLock(self.folder)
            assert_not_restored(self.folder)
            write_protocol(self.folder, 'print')
            self._initialize(demo)
        except BaseException:
            self._cleanup()
            raise

    def _initialize(self, demo):
        component = demo_component(self.folder)
        if component is not None and component != 'print':
            raise ValueError('Forkert DEMO-komponent til printtjenesten.')
        demo = demo or component == 'print'
        self.demo = demo
        self.settings_path = self.folder / 'settings.json'
        self.settings_lock = threading.RLock()
        self.discovery_lock = threading.Lock()
        self.discovery_result = None
        self.discovery_finished = 0
        self.settings = json.loads(self.settings_path.read_text(encoding='utf-8')) if self.settings_path.exists() else {'studio_path': DEFAULT_STUDIO, 'printers': []}
        if demo:
            self.settings['printers'] = demo_printers(self.settings)
        token_file = self.folder / 'laererkode.txt'
        self.token = teacher_code_for_installation(token_file)
        self.auth = TeacherAuth(self.token)
        self.queue = Queue(self.folder, self.settings['printers'], demo=demo)
        self.slicer = Slicer(self.settings['studio_path'], Path(self.settings['studio_path']).parent / 'resources' / 'profiles' / 'BBL', self.folder / 'sliced')
        self.drivers = demo_drivers(self.queue) if demo else {}
        self.printer_bridge = PrinterBridge(self, demo_driver if demo else BambuPrinter)
        if not demo:
            for config in self.settings['printers']:
                if config.get('host') and config.get('serial') and config.get('access_code'):
                    self.drivers[config['id']] = BambuPrinter(config, self.queue, self.slicer.profiles)
        for driver in self.drivers.values():
            driver.start()
        self.cameras = None if demo else Cameras(self.folder, self.queue)
        self.worker = Worker(self.queue, self.slicer, self.drivers)
        self.worker.start()
        self.firmware = FirmwareChecks(self.folder, enabled=not demo and any(p.get('model') == 'A1 mini' for p in self.settings['printers']))
        self.firmware.start()

    def _cleanup(self, failures=None):
        failures = list(failures or [])
        for service in (self.firmware, self.worker):
            if service is not None:
                try:
                    service.stop()
                except Exception as error:
                    failures.append(error)
                    logging.exception('Kunne ikke lukke delvist startet service')
        for driver in list(self.drivers.values()):
            try:
                driver.stop()
            except Exception as error:
                failures.append(error)
                logging.exception('Kunne ikke lukke delvist startet printer')
        for service in (self.firmware, self.worker):
            thread = getattr(service, 'thread', None)
            if isinstance(thread, threading.Thread) and thread.ident is not None and thread is not threading.current_thread():
                thread.join()
        if failures:
            # An incomplete shutdown must never advertise a quiescent folder.
            # Keep the OS lock until this process exits or cleanup succeeds.
            raise RuntimeError('Nogle baggrundstjenester kunne ikke lukkes; datalåsen beholdes.') from failures[0]
        if self.queue is not None:
            self.queue.close()
        handler = getattr(self, 'log_handler', None)
        if handler is not None:
            logging.getLogger().removeHandler(handler)
            handler.close()
            self.log_handler = None
        if self.instance_lock is not None:
            self.instance_lock.close()
        self.worker = self.firmware = self.cameras = None
        self.drivers = {}
        self._closed = True

    def shutdown(self):
        if self._closed:
            return
        self._cleanup()

    def public_settings(self):
        return {'printers': [public_printer(p) for p in self.settings['printers']], 'studio_installed': self.slicer.executable.is_file(), 'profiles_found': bool(self.slicer.profiles.items), 'models': list(MODELS), 'demo': self.demo}

    def discover_printers(self):
        if self.demo:
            raise ValueError('Åbn skoleversionen for at finde rigtige printere.')
        # One bounded multicast search at a time, even with multiple teacher tabs.
        with self.discovery_lock:
            if self.discovery_result is None or time.monotonic() - self.discovery_finished >= 30:
                self.discovery_result = discover()
                self.discovery_finished = time.monotonic()
            return self.discovery_result

    def save_printer(self, data):
        return self.printer_bridge.save_legacy(data)


class Handler(BaseHTTPRequestHandler):
    server_version = 'Printvaerksted'

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        # Never log headers, credentials, or private model names.
        if str(args[1] if len(args) > 1 else '') not in ('200', '201', '304'):
            logging.info('HTTP response %s', args[1] if len(args) > 1 else '')

    def setup(self):
        super().setup()
        self.connection.settimeout(45)

    def send(self, status, body, content_type='application/json; charset=utf-8', headers=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, str(value))
        self.send_header('Cache-Control', 'no-store' if content_type.startswith('application/json') or self.path.startswith('/api/camera/') else 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        ancestors = "'none'" if self.path.startswith('/laerer') else (' '.join(self.server.project_origins) or "'none'")
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors " + ancestors + "; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _read_upload(self, length):
        """Read one bounded upload with a total wall-clock deadline."""
        deadline = time.monotonic() + UPLOAD_TOTAL_SECONDS
        chunks = []
        received = 0
        while received < length:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise UploadDeadlineExceeded('Uploaden tog for lang tid og blev afbrudt. Prøv igen.')
            try:
                self.connection.settimeout(min(45, max(0.01, remaining)))
            except (AttributeError, OSError):
                pass
            try:
                reader = getattr(self.rfile, 'read1', self.rfile.read)
                chunk = reader(min(UPLOAD_READ_CHUNK, length - received))
            except TimeoutError:
                # socket.makefile's buffered reader cannot safely resume after
                # a socket timeout. Fail the upload and release its reservation.
                raise UploadDeadlineExceeded('Uploaden tog for lang tid og blev afbrudt. Prøv igen.') from None
            if time.monotonic() >= deadline:
                raise UploadDeadlineExceeded('Uploaden tog for lang tid og blev afbrudt. Prøv igen.')
            if not chunk:
                raise ValueError('Hele filen blev ikke modtaget. Prøv igen.')
            if not isinstance(chunk, bytes):
                raise ValueError('Uploaden havde et ugyldigt indhold.')
            chunks.append(chunk)
            received += len(chunk)
        return b''.join(chunks)

    def _with_upload_budget(self, length, operation):
        reservation = self.server.upload_budget.reserve(length)
        try:
            return operation()
        finally:
            reservation.release()

    def is_admin(self):
        if self.app.auth.session(self.headers.get('Cookie')):
            return True
        authorization = self.headers.get('Authorization', '')
        return (authorization.startswith('Bearer ') and
                self.app.auth.verify(authorization[7:], self.submission_source()))

    def allowed_host(self):
        values = self.headers.get_all('Host') or []
        if len(values) != 1 or re.search(r'[\s/@?#\\]', values[0]):
            return False
        try:
            parsed = urlparse('//' + values[0])
            port = parsed.port
            return ((parsed.hostname in self.server.allowed_hosts and
                     port in (None, self.server.server_port)) or
                    (parsed.hostname, port) in self.server.allowed_authorities)
        except ValueError:
            return False

    def require_admin(self):
        if not self.is_admin():
            raise PermissionError('Indtast lærerkoden for at åbne lærersiden.')

    def require_bridge(self):
        authorizations = self.headers.get_all('Authorization', [])
        if (not ipaddress.ip_address(self.client_address[0]).is_loopback or
                self.headers.get('Cookie') or self.headers.get('Origin') or self.headers.get('X-Print-Source') or
                len(authorizations) != 1 or not secrets.compare_digest(
                    authorizations[0].encode('utf-8'), ('Bearer ' + self.app.printer_bridge.token).encode('ascii'))):
            raise PermissionError('Printeradministrationen kræver lokal tjenesteadgang.')
        if self.command == 'POST' and len(self.headers.get_all('Content-Length', [])) != 1:
            raise ValueError('Ugyldig længde på printerkommandoen.')

    def public_state(self):
        """Return the queue view appropriate for this caller.

        The student surface needs printer availability and print-access
        information, but a shared queue snapshot must not disclose another
        pupil's filename, message, or private job metadata. A teacher
        session retains the complete operational snapshot used by the admin
        view.  The upload response and the browser's local receipt remain the
        source for the submitting student's own filename.
        """
        snapshot = self.app.queue.snapshot()
        if not self.is_admin():
            snapshot = dict(snapshot)
            printer_fields = ('id', 'name', 'model', 'status', 'machine_state',
                              'progress', 'remaining', 'colours', 'bed_clear',
                              'can_release', 'enabled', 'error_code')
            snapshot['printers'] = [
                {key: printer[key] for key in printer_fields if key in printer}
                for printer in snapshot.get('printers', [])
                if isinstance(printer, dict)
            ]
            jobs = []
            for job in snapshot.get('jobs', []):
                safe = {key: job[key] for key in (
                    'id', 'status', 'isolated', 'physical_resolved', 'created', 'printer_id', 'progress',
                    'remaining', 'quality', 'colour', 'actual_colour') if key in job}
                meta = job.get('meta') if isinstance(job, dict) else None
                if isinstance(meta, dict) and isinstance(meta.get('size'), list):
                    safe['meta'] = {'size': list(meta['size'])}
                jobs.append(safe)
            snapshot['jobs'] = jobs
        snapshot['print_access'] = self.app.queue.print_access(self.submission_source())
        return snapshot

    def submission_source(self):
        peer = ipaddress.ip_address(self.client_address[0])
        # Elevhub overwrites this header using the connection's real address.
        # Only the local gateway may supply it; remote clients cannot choose an identity.
        forwarded = self.headers.get('X-Print-Source')
        if peer.is_loopback and forwarded:
            try:
                peer = ipaddress.ip_address(forwarded)
            except ValueError:
                pass
        return 'ip:' + str(peer)

    def do_GET(self):
        try:
            if not self.allowed_host():
                return self.send(403, {'error': 'Denne adresse må ikke åbne Printværkstedet.'})
            path = urlparse(self.path).path
            if path == '/api/makerspace/printers':
                self.require_bridge()
                self.send(200, self.app.printer_bridge.snapshot())
            elif path == '/api/auth/status':
                self.send(200, {'teacher': self.app.auth.session(self.headers.get('Cookie'))})
            elif path == '/api/state':
                self.send(200, self.public_state())
            elif path == '/api/print-access':
                self.require_admin()
                self.send(200, self.app.queue.print_access_overview())
            elif path == '/api/settings':
                self.require_admin()
                self.send(200, self.app.public_settings())
            elif path == '/api/printers/certificates':
                self.require_admin()
                result = self.app.printer_bridge.certificate_snapshot()
                result['queue'] = {'paused': self.app.queue.paused}
                self.send(200, result)
            elif path == '/api/maintenance':
                self.require_admin()
                self.send(200, self.app.queue.maintenance_snapshot())
            elif path == '/api/firmware':
                self.require_admin()
                self.send(200, self.app.firmware.snapshot())
            elif path == '/api/library':
                self.require_admin()
                query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
                if set(query) - {'page', 'page_size', 'query'} or any(len(v) != 1 for v in query.values()):
                    raise ValueError('Ugyldige biblioteksparametre.')
                page = int(query.get('page', ['1'])[0])
                page_size = int(query.get('page_size', ['25'])[0])
                self.send(200, self.app.queue.library_page(page, page_size,
                                                         query.get('query', [''])[0]))
            elif path == '/api/library/storage':
                self.require_admin()
                self.send(200, self.app.queue.library_storage())
            elif path.startswith('/api/library/'):
                self.require_admin()
                parts = path.strip('/').split('/')
                if len(parts) != 4 or parts[3] != 'download':
                    self.send(404, {'error': 'Filen blev ikke fundet.'})
                    return
                file_id = parts[2]
                if not re.fullmatch(r'[0-9a-f]{64}', file_id):
                    raise ValueError('Ugyldigt fil-id.')
                payload, filename = self.app.queue.library_download_entry(file_id)
                safe_name = Path(str(filename or file_id)).name
                self.send(200, payload, 'application/octet-stream', {'Content-Disposition': "attachment; filename=\"model.stl\"; filename*=UTF-8''" + quote(safe_name, safe='')})
            elif path.startswith('/api/jobs/diagnostics/'):
                self.require_admin()
                job_id = path.rsplit('/', 1)[-1]
                if not re.fullmatch(r'[0-9a-f]{32}', job_id):
                    raise ValueError('Ugyldigt job-id.')
                if read_diagnostic is None:
                    raise ValueError('Fejloplysninger er ikke tilgængelige.')
                self.send(200, read_diagnostic(self.app.folder, job_id))
            elif path.startswith('/api/camera/'):
                self.require_admin()
                if not self.app.cameras:
                    raise ValueError('Kameraer findes kun i skoleversionen.')
                parts = path.strip('/').split('/')
                if len(parts) == 4 and parts[2] == 'image':
                    self.send(200, self.app.cameras.image(parts[3]), 'image/jpeg')
                elif len(parts) == 4 and parts[2] == 'status':
                    self.send(200, self.app.cameras.status(parts[3]))
                else:
                    self.send(404, {'error': 'Kameraet blev ikke fundet.'})
            elif path == '/api/health':
                self.send(200, {'ok': True, 'demo': self.app.demo, 'service': 'printvaerksted', 'instance': instance_id(self.app.folder), 'pid': os.getpid()})
            elif path == '/api/school-appearance':
                appearance = school_appearance(self.app.folder)
                if appearance is None and school_appearance_manifest_present(self.app.folder):
                    self.send(503, {'error': 'Den lokale skoleprofil kunne ikke valideres.'})
                else:
                    self.send(200, appearance)
            elif path in ('/school-appearance/website.css', '/school-appearance/logo'):
                asset = path.rsplit('/', 1)[-1]
                result = school_appearance_asset(self.app.folder, asset)
                if result is None:
                    status = 503 if school_appearance_manifest_present(self.app.folder) else 404
                    self.send(status, {'error': 'Den lokale skoleprofil kunne ikke valideres.' if status == 503 else 'Den lokale skoleprofil har ikke denne fil.'})
                else:
                    self.send(200, result[0], result[1])
            elif path == '/project-import-config.js':
                # External same-origin script keeps script-src strict and the
                # postMessage allowlist identical to frame-ancestors.
                body = ('Object.defineProperty(window, "ElevhubProjectOrigins", '
                        '{value: Object.freeze(' + json.dumps(self.server.project_origins) + ')});')
                self.send(200, body.encode('utf-8'), 'text/javascript; charset=utf-8')
            elif path in ('/', '/laerer'):
                appearance = school_appearance(self.app.folder)
                if appearance is None and school_appearance_manifest_present(self.app.folder):
                    return self.send(503, {'error': 'Den lokale skoleprofil kunne ikke valideres.'})
                body = (ROOT / 'web' / 'index.html').read_bytes()
                self.send(200, render_local_html(body, appearance), 'text/html; charset=utf-8')
            else:
                appearance = school_appearance(self.app.folder)
                appearance_asset = school_appearance_asset(self.app.folder, path)
                if appearance_asset is not None:
                    self.send(200, appearance_asset[0], appearance_asset[1])
                    return
                if school_appearance_manifest_present(self.app.folder) and school_appearance(self.app.folder) is None:
                    self.send(503, {'error': 'Den lokale skoleprofil kunne ikke valideres.'})
                    return
                target = (ROOT / 'web' / unquote(path).lstrip('/')).resolve()
                if not target.is_relative_to(ROOT / 'web') or not target.is_file():
                    self.send(404, {'error': 'Siden blev ikke fundet.'})
                    return
                content_type = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
                if target.suffix == '.js':
                    content_type = 'text/javascript'
                body = target.read_bytes()
                if content_type.startswith('text/html'):
                    body = render_local_html(body, appearance)
                self.send(200, body, content_type)
        except LoginLimited as error:
            self.send(429, {'error': str(error)}, headers={'Retry-After': error.retry_after})
        except PermissionError as error:
            self.send(401, {'error': str(error)})
        except ValueError as error:
            self.send(400, {'error': str(error)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            logging.exception('GET failed')
            self.send(500, {'error': 'Siden kunne ikke indlæses.'})

    def do_POST(self):
        try:
            if not self.allowed_host():
                return self.send(403, {'error': 'Denne adresse må ikke ændre Printværkstedet.'})
            host = self.headers.get('Host', '')
            origin = self.headers.get('Origin')
            if origin and origin not in (f'http://{host}', f'https://{host}'):
                raise PermissionError('Denne side må ikke ændre printkøen.')
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                raise PermissionError('Denne side må ikke ændre printkøen.')
            if self.headers.get('Transfer-Encoding'):
                raise ValueError('Upload filen igen med den almindelige upload-knap.')
            path = urlparse(self.path).path
            length = int(self.headers.get('Content-Length', '0'))
            limit = MAX_BYTES if path == '/api/jobs' else 65536
            if not 0 < length <= limit:
                self.send(413, {'error': 'Filen eller forespørgslen er for stor.'})
                return
            if path == '/api/makerspace/printers/commands':
                self.require_bridge()
            elif path not in ('/api/jobs', '/api/auth/login', '/api/auth/logout'):
                self.require_admin()
            if path == '/api/jobs':
                def submit_upload():
                    body = self._read_upload(length)
                    return self.app.queue.submit(body, unquote(self.headers.get('X-Filename', '')), self.headers.get('X-Colour', 'any'), self.headers.get('X-Request-ID', ''), self.headers.get('X-Quality', 'standard'), source=self.submission_source())
                job = self._with_upload_budget(length, submit_upload)
                self.send(201, {'id': job['id'], 'status': job['status']})
                return
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError('Hele filen blev ikke modtaget. Prøv igen.')
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError('Ugyldig forespørgsel.')
            if path == '/api/makerspace/printers/commands':
                if set(data) != {'actor', 'envelope'}:
                    raise ValueError('Ugyldig printerkommando.')
                try:
                    result = self.app.printer_bridge.execute(data['actor'], data['envelope'])
                except PermissionError as error:
                    # The service key is authenticated above. This refusal is
                    # about the actor's authority, not a broken bridge login.
                    return self.send(403, {'error': str(error)})
                return self.send(200, result)
            elif path == '/api/auth/login':
                token = self.app.auth.login(data.get('code'), self.submission_source())
                return self.send(200, {'ok': True}, headers={
                    'Set-Cookie': self.app.auth.cookie(token, secure=isinstance(self.connection, ssl.SSLSocket))})
            elif path == '/api/auth/logout':
                self.app.auth.logout(self.headers.get('Cookie'))
                return self.send(200, {'ok': True}, headers={
                    'Set-Cookie': self.app.auth.cookie(secure=isinstance(self.connection, ssl.SSLSocket))})
            elif path == '/api/queue/pause':
                if type(data.get('paused')) is not bool:
                    raise ValueError('Vælg om køen skal være sat på pause.')
                self.app.queue.pause(data['paused'])
            elif path == '/api/library/policy':
                self.send(200, self.app.queue.library_set_policy(data))
                return
            elif path.startswith('/api/library/'):
                parts = path.strip('/').split('/')
                if len(parts) != 4 or parts[3] not in ('reprint', 'retry', 'delete'):
                    self.send(404, {'error': 'Filhandlingen blev ikke fundet.'})
                    return
                file_id = parts[2]
                if not re.fullmatch(r'[0-9a-f]{64}', file_id):
                    raise ValueError('Ugyldigt fil-id.')
                request_id = str(data.get('request_id') or self.headers.get('X-Request-ID') or '')
                if not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', request_id):
                    raise ValueError('Indtast et gyldigt request_id for at undgå dobbelt print.')
                if parts[3] == 'delete':
                    if set(data) != {'request_id', 'generation', 'confirmed'}:
                        raise ValueError('Ugyldig sletningsbekræftelse.')
                    result = self.app.queue.library_delete(file_id, request_id,
                                                          data['generation'], data['confirmed'])
                    self.send(200, result)
                    return
                colour = data.get('colour')
                quality = data.get('quality')
                job = self.app.queue.library_reprint(file_id, request_id, source='teacher', colour=colour, quality=quality)
                self.send(201, {'id': job['id'], 'status': job['status'], 'request_id': request_id})
                return
            elif path == '/api/print-access/settings':
                self.app.queue.set_print_guard(data.get('trigger_count'))
                self.send(200, self.app.queue.print_access_overview())
                return
            elif path == '/api/print-access/unblock':
                self.app.queue.unblock_print_access(data.get('id'))
                self.send(200, self.app.queue.print_access_overview())
                return
            elif path == '/api/printers/save':
                self.app.save_printer(data)
            elif path == '/api/printers/certificates/confirm':
                required = {'id', 'channel', 'fingerprint', 'confirmed', 'expected_version', 'request_id'}
                if set(data) not in (required, required | {'repair'}):
                    raise ValueError('Ugyldig printercertifikatbekræftelse.')
                if data['confirmed'] is not True:
                    raise ValueError('Bekræft printerens identitet og det observerede fingeraftryk.')
                actor = 'teacher:' + hashlib.sha256(('print:' + self.app.token).encode()).hexdigest()[:32]
                envelope = {'action': 'printer.certificate.confirm',
                            'params': {key: data[key] for key in ('id', 'channel', 'fingerprint')},
                            'expected_version': data['expected_version'], 'request_id': data['request_id']}
                if 'repair' in data:
                    envelope['params']['repair'] = data['repair']
                self.send(200, self.app.printer_bridge.execute(actor, envelope))
                return
            elif path == '/api/printers/certificates/probe':
                if set(data) != {'id', 'expected_version', 'request_id'}:
                    raise ValueError('Ugyldig printercertifikatprøve.')
                actor = 'teacher:' + hashlib.sha256(('print:' + self.app.token).encode()).hexdigest()[:32]
                envelope = {'action': 'printer.certificate.probe', 'params': {'id': data['id']},
                            'expected_version': data['expected_version'], 'request_id': data['request_id']}
                self.send(200, self.app.printer_bridge.execute(actor, envelope))
                return
            elif path == '/api/printers/certificates/cancel':
                required = {'id', 'operation_request_id', 'expected_version', 'request_id'}
                if set(data) not in (required, required | {'operation_actor'}):
                    raise ValueError('Ugyldigt afslutningsforsøg for printercertifikatet.')
                actor = 'teacher:' + hashlib.sha256(('print:' + self.app.token).encode()).hexdigest()[:32]
                params = {key: data[key] for key in ('id', 'operation_request_id')}
                if 'operation_actor' in data:
                    params['operation_actor'] = data['operation_actor']
                envelope = {'action': 'printer.certificate.cancel', 'params': params,
                            'expected_version': data['expected_version'], 'request_id': data['request_id']}
                self.send(200, self.app.printer_bridge.execute(actor, envelope))
                return
            elif path == '/api/maintenance/complete':
                self.app.queue.complete_maintenance(data.get('id'))
                self.send(200, self.app.queue.maintenance_snapshot())
                return
            elif path == '/api/firmware/check':
                self.send(200, self.app.firmware.check(force=True))
                return
            elif path == '/api/firmware/acknowledge':
                self.send(200, self.app.firmware.acknowledge())
                return
            elif path == '/api/printers/discover':
                self.send(200, self.app.discover_printers())
                return
            elif path == '/api/camera/capture' and self.app.cameras:
                pid = data['id']
                self.app.cameras.capture(pid)
                self.send(200, self.app.cameras.status(pid))
                return
            elif path == '/api/printers/release':
                self.app.queue.release_bed(data['id'])
            elif path == '/api/jobs/resolve':
                self.app.queue.resolve(data['id'], data['result'])
            elif path == '/api/demo/finish' and self.app.demo:
                self.app.drivers[data['id']].finish()
            elif path == '/api/service/stop':
                self.send(200, {'ok': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            else:
                self.send(404, {'error': 'Handlingen blev ikke fundet.'})
                return
            self.send(200, {'ok': True})
        except LoginLimited as error:
            self.send(429, {'error': str(error)}, headers={'Retry-After': error.retry_after})
        except ConfigConflict as error:
            self.send(409, {'error': str(error)})
        except (LibraryInUse, LibraryConflict) as error:
            self.send(409, {'error': str(error)})
        except (LibraryDeletionPending, LibraryStorageError) as error:
            self.send(503, {'error': str(error)}, headers={'Retry-After': 1})
        except SubmissionStageError:
            logging.exception('Uploadstaging kunne ikke bekræftes')
            self.send(503, {'error': 'Modellen kunne ikke lægges i køen. Bed læreren kontrollere lagringen.'},
                      headers={'Retry-After': 1})
        except PermissionError as error:
            self.send(401, {'error': str(error)})
        except SubmissionBlocked as error:
            self.send(429, {'error': str(error), 'retry_after': error.retry_after},
                      headers={'Retry-After': error.retry_after} if error.retry_after else None)
        except UploadOverloaded as error:
            self.send(error.status, {'error': str(error)}, headers={'Retry-After': error.retry_after})
        except UploadDeadlineExceeded as error:
            self.send(error.status, {'error': str(error)})
        except (ValueError, KeyError) as error:
            self.send(400, {'error': str(error) if isinstance(error, ValueError) else 'Printeren eller printet blev ikke fundet.'})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            logging.exception('POST failed')
            self.send(500, {'error': 'Handlingen kunne ikke gennemføres. Prøv igen.'})


class Server(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True

    def __init__(self, *args, host_config=None, project_origins=None, project_origin_config=None, **kwargs):
        if project_origins is not None and project_origin_config is not None:
            raise ValueError('Vælg én konfiguration af projektserverens origins.')
        self.project_origins = (validate_project_origins(project_origins) if project_origins is not None
                                else configured_project_origins(Path(project_origin_config)
                                     if project_origin_config is not None else ROOT / 'print-project-origins.json'))
        self.upload_budget = UploadBudget()
        self.allowed_authorities = configured_print_hosts(
            Path(host_config) if host_config is not None else ROOT / 'print-hosts.json')
        super().__init__(*args, **kwargs)
        names = {'localhost', '127.0.0.1', '::1', socket.gethostname().lower(), socket.getfqdn().lower()}
        names.add(str(self.server_address[0]).lower())
        try:
            names.update(item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None))
        except OSError:
            pass
        # Explicit local DNS aliases for installations using a school URL.
        names.update(name.strip().lower() for name in os.environ.get('PRINTVAERKSTED_ALLOWED_HOSTS', '').split(',') if name.strip())
        self.allowed_hosts = names - {'0.0.0.0', '::', ''}


def main():
    parser = argparse.ArgumentParser(description='Skolens Printværksted')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--data', type=Path)
    args = parser.parse_args()
    folder = args.data or data_root() / ('demo' if args.demo else 'school')
    # Application owns the lifetime lock.  Do not create service.log or bind
    # the HTTP server until a restored/duplicate installation is rejected.
    app = Application(folder, args.demo)
    server = None
    try:
        log_handler = logging.FileHandler(app.folder / 'service.log', encoding='utf-8')
        app.log_handler = log_handler
        log_handler.setLevel(logging.INFO)
        log_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logging.getLogger().addHandler(log_handler)
        logging.getLogger().setLevel(logging.INFO)
        server = Server((args.host, args.port), Handler,
                        project_origin_config=app.folder / 'print-project-origins.json')
        server.app = app
        print(f"Printværksted: http://localhost:{args.port}")
        print(f"Lærerside: http://localhost:{args.port}/laerer")
        print(f"Lærerkoden ligger i: {folder / 'laererkode.txt'}")
        print('DEMOVERSION - ingen fysiske printere' if app.demo else 'Skolens printere - tilføj dem på lærersiden')
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # Drain request writers before stopping components and releasing their
        # database lock. A failed drain must not advertise quiescent data.
        if server is not None:
            server.server_close()
        app.shutdown()


if __name__ == '__main__':
    main()
