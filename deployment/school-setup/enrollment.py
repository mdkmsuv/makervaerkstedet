"""School-local, TLS-only enrollment and pinned SSH for generic pupil media.

Only the local administrator issues tickets. No discovery, passwords, or
automatic acceptance of SSH host keys. This module has no external packages.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import stat
import struct
import subprocess
import tempfile
import threading
import time

NAME = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?')
CODE = re.compile(r'[A-Z2-7]{12}')
NONCE = re.compile(r'[0-9a-f]{64}')
TTL = 30 * 60
SERVICE_LOCK = 'security/enrollment/instance.lock'
OPERATIONS_LOCK = 'security/enrollment/operations/instance.lock'
SERVICE_PROTOCOL = 'security/enrollment/backup-protocol.json'
HOLD_FILE = '.restore-hold.json'
REQUEST_SECONDS = 10
REVOKE_JOURNAL = 'security/enrollment/revoke-device.json'
MAX_REVOKE_INTENT_BYTES = 2 * 1024


def plain_path(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Enrollmentstien må ikke indeholde links eller reparse points.')
    return path


def name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError('Brug 1–32 små bogstaver, tal eller bindestreger til computernavnet.')
    return value


def host_key(value):
    if not isinstance(value, str):
        raise ValueError('En offentlig Ed25519-nøgle kræves.')
    parts = value.strip().split()
    if len(parts) not in (2, 3) or parts[0] != 'ssh-ed25519':
        raise ValueError('En offentlig Ed25519-nøgle kræves.')
    try:
        decoded = base64.b64decode(parts[1], validate=True)
    except ValueError as error:
        raise ValueError('Ugyldig SSH-nøgle.') from error
    prefix = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32)
    if len(decoded) != 51 or not decoded.startswith(prefix):
        raise ValueError('Ugyldig Ed25519-nøgle.')
    return 'ssh-ed25519 ' + parts[1]


def read_json(path, default=None):
    if not path.exists() and default is not None:
        return default
    if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Ugyldig konfigurationsfil.')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write_json(path, value):
    path = plain_path(path)
    pending = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.enrollment-', suffix='.tmp', delete=False) as stream:
            pending = Path(stream.name)
            if os.name != 'nt':
                os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        plain_path(path)
        os.replace(pending, path)
        pending = None
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def _revoke_sources(root):
    """Read and validate the three registries before creating a revoke intent."""
    state_path = plain_path(root / 'security/enrollment.json')
    registry_path = plain_path(root / 'security/enheder.json')
    pins_path = plain_path(root / 'security/ssh-pins.json')
    state = read_json(state_path, {'tickets': {}})
    registry = read_json(registry_path)
    pins = read_json(pins_path, {})
    if not isinstance(state, dict) or not isinstance(state.get('tickets'), dict):
        raise ValueError('Ugyldig tilmeldingsstatus.')
    if not isinstance(registry, dict) or not isinstance(registry.get('enheder'), dict):
        raise ValueError('Ugyldigt enhedsregister.')
    if not isinstance(pins, dict):
        raise ValueError('Ugyldige SSH-identiteter.')
    return state, registry, pins


def _validate_revoke_journal(journal):
    if (not isinstance(journal, dict)
            or set(journal) != {'schemaVersion', 'operation', 'computer'}
            or type(journal.get('schemaVersion')) is not int or journal.get('schemaVersion') != 1
            or journal.get('operation') != 'revoke-device'
            or not isinstance(journal.get('computer'), str)):
        raise ValueError('Ugyldig tilbagekaldelsesjournal.')
    computer = name(journal['computer'])
    return computer


def _recover_revoke_locked(root):
    journal_path = plain_path(root / REVOKE_JOURNAL)
    if not journal_path.exists():
        return
    try:
        if journal_path.stat().st_size > MAX_REVOKE_INTENT_BYTES:
            raise ValueError('Ugyldig tilbagekaldelsesjournal.')
        journal = read_json(journal_path)
        computer = _validate_revoke_journal(journal)
        # Re-read after an interrupted attempt. This preserves legitimate
        # changes made by another enrollment path while this intent was pending.
        state, registry, pins = _revoke_sources(root)
        state['tickets'] = {digest: ticket for digest, ticket in state['tickets'].items()
                            if not isinstance(ticket, dict) or ticket.get('computer') != computer}
        registry['enheder'].pop(computer, None)
        pins.pop(computer, None)
        write_json(root / 'security/enrollment.json', state)
        write_json(root / 'security/enheder.json', registry)
        write_json(root / 'security/ssh-pins.json', pins)
        journal_path.unlink()
    except (OSError, ValueError, TypeError, KeyError):
        raise RuntimeError('Enhedens tilbagekaldelse kunne ikke genoptages. Prøv igen.') from None


_thread_lock = threading.RLock()


def assert_not_restored(root):
    root = Path(root).absolute()
    for parent in (root, *root.parents):
        marker = parent / HOLD_FILE
        if marker.exists() or marker.is_symlink():
            raise RuntimeError('Gendannelsen er isoleret. Kontrollér opsætningen før ibrugtagning.')


class EnrollmentInstanceLock:
    """The same one-byte nonblocking lock protocol as app.InstanceLock."""
    def __init__(self, root, relative=SERVICE_LOCK):
        self.root = plain_path(root)
        if relative not in (SERVICE_LOCK, OPERATIONS_LOCK):
            raise ValueError('Ukendt enrollmentlås.')
        self.path = self.root / relative
        self.stream = None

    def acquire(self, blocking=False):
        if self.stream is not None:
            raise RuntimeError('Enrollmentlåsen er allerede erhvervet.')
        assert_not_restored(self.root)
        plain_path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('a+b')
        try:
            if os.fstat(self.stream.fileno()).st_size == 0:
                self.stream.write(b'1')
                self.stream.flush()
            self.stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
                msvcrt.locking(self.stream.fileno(), mode, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert_not_restored(self.root)
        except BaseException as error:
            self.stream.close()
            self.stream = None
            if isinstance(error, OSError):
                raise RuntimeError('Enrollmenttjenesten er allerede i brug eller under offline vedligeholdelse.') from None
            raise
        return self

    def close(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *args):
        self.close()


def write_service_protocol(root):
    assert_not_restored(root)
    target = plain_path(Path(root) / SERVICE_PROTOCOL)
    target.parent.mkdir(parents=True, exist_ok=True)
    expected = {'protocol': 1, 'role': 'enrollment'}
    if target.exists():
        try:
            value = json.loads(target.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, ValueError):
            value = None
        if (type(value) is dict and set(value) == set(expected) and value == expected
                and type(value['protocol']) is int):
            return
    write_json(target, expected)


@contextmanager
def lock_registry(root):
    # Operations use a separate lock so CLI administration remains available
    # while the enrollment server holds its lifetime lock.
    assert_not_restored(root)
    with _thread_lock:
        with EnrollmentInstanceLock(root, OPERATIONS_LOCK):
            assert_not_restored(root)
            with plain_path(root / 'security/enheder.lock').open('a+b') as stream:
                if os.fstat(stream.fileno()).st_size == 0:
                    stream.write(b'0')
                    stream.flush()
                stream.seek(0)
                try:
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    raise RuntimeError('Enhedsregisteret er optaget. Prøv igen.') from None
                try:
                    _recover_revoke_locked(root)
                    yield
                finally:
                    stream.seek(0)
                    if os.name == 'nt':
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def school(root):
    data = read_json(root / 'school.json')
    if not isinstance(data, dict) or data.get('schemaVersion') != 1:
        raise ValueError('Skoleopsætningen mangler.')
    address = ipaddress.IPv4Address(data['server'])
    subnet = ipaddress.IPv4Network(data['student_subnet'], strict=True)
    if address.is_loopback or address.is_unspecified or address.is_multicast:
        raise ValueError('Vælg styreenhedens netværksadresse.')
    if subnet.prefixlen < 8 or subnet.is_multicast or subnet.is_loopback:
        raise ValueError('Vælg et afgrænset IPv4-elevnetværk.')
    if data.get('role') not in ('vm', 'makerspace') or data.get('theme') not in ('red', 'yellow', 'green'):
        raise ValueError('Vælg rolle og rød, gul eller grøn.')
    title = data.get('school_name', '')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 80 or any(ord(c) < 32 for c in title):
        raise ValueError('Skolenavnet skal være 1–80 tegn.')
    host_key(data['admin_public_key'])
    return data


def connection(root):
    data = school(root)
    return {'schemaVersion': 1, 'kind': 'elevhub-school-connection',
            'school_name': data['school_name'], 'theme': data['theme'],
            'server': data['server'], 'enrollment_port': 8774,
            'caCertificatePem': (root / 'security/ca.crt').read_text(encoding='ascii'),
            'admin_public_key': host_key(data['admin_public_key'])}


def issue_ticket(root, computer, now=None):
    name(computer)
    school(root)
    now = time.time() if now is None else now
    with lock_registry(root):
        registry = read_json(root / 'security/enheder.json')
        if computer in registry['enheder']:
            raise ValueError('Computeren er allerede tilmeldt. Fjern den gamle tilmelding lokalt før geninstallation.')
        if len(registry['enheder']) >= 256:
            raise ValueError('Skolen har allerede 256 enheder.')
        state_path = root / 'security/enrollment.json'
        state = read_json(state_path, {'tickets': {}})
        # A fresh ticket revokes previous unclaimed codes for this name.
        state['tickets'] = {key: value for key, value in state['tickets'].items()
                            if value['expires'] > now and value['computer'] != computer}
        if len(state['tickets']) >= 256:
            raise ValueError('For mange aktive tilmeldingskoder.')
        code = base64.b32encode(secrets.token_bytes(8)).decode('ascii')[:12]
        digest = hashlib.sha256(code.encode('ascii')).hexdigest()
        state['tickets'][digest] = {'computer': computer, 'expires': now + TTL}
        write_json(state_path, state)
    return '-'.join(code[i:i + 4] for i in range(0, 12, 4))


def redeem(root, request, source, now=None):
    cfg = school(root)
    now = time.time() if now is None else now
    if ipaddress.IPv4Address(source) not in ipaddress.IPv4Network(cfg['student_subnet']):
        raise ValueError('Computeren er ikke på det valgte elevnetværk.')
    if not isinstance(request, dict):
        raise ValueError('Ugyldig tilmelding.')
    computer = name(request.get('computer'))
    key = host_key(request.get('ssh_host_key'))
    nonce = request.get('installation_id', '')
    code = str(request.get('code', '')).replace('-', '').replace(' ', '').upper()
    if not CODE.fullmatch(code) or not isinstance(nonce, str) or not NONCE.fullmatch(nonce):
        raise ValueError('Ugyldig tilmeldingskode.')
    digest = hashlib.sha256(code.encode('ascii')).hexdigest()
    with lock_registry(root):
        state_path = root / 'security/enrollment.json'
        state = read_json(state_path, {'tickets': {}})
        ticket = state['tickets'].get(digest)
        if not ticket or ticket['expires'] <= now or ticket['computer'] != computer:
            raise ValueError('Koden er ugyldig eller udløbet, eller computernavnet er forkert.')
        registry_path = root / 'security/enheder.json'
        registry = read_json(registry_path)
        if 'installation_id' in ticket:
            if ticket['installation_id'] != nonce or ticket['ssh_host_key'] != key:
                raise ValueError('Koden er allerede brugt på en anden installation.')
        else:
            if computer in registry['enheder'] or len(registry['enheder']) >= 256:
                raise ValueError('Computernavnet er optaget, eller registeret er fuldt.')
            ticket.update(installation_id=nonce, ssh_host_key=key,
                          token=secrets.token_urlsafe(32), ui_token=secrets.token_urlsafe(32))
            # Journal first: the exact client may retry after power loss/HTTP loss.
            write_json(state_path, state)
        entry = {'token_sha256': hashlib.sha256(ticket['token'].encode('ascii')).hexdigest(),
                 'ui_token_sha256': hashlib.sha256(ticket['ui_token'].encode('ascii')).hexdigest()}
        existing = registry['enheder'].get(computer)
        if existing is not None and existing != entry:
            raise ValueError('Enhedens registrering er ændret. Opret en ny tilmelding lokalt.')
        # Keep the legacy two-hash registry schema compatible with rotation tools.
        pins_path = root / 'security/ssh-pins.json'
        pins = read_json(pins_path, {})
        if existing is not None and computer in pins and pins[computer] != key:
            raise ValueError('Computerens registrerede SSH-identitet er ændret.')
        pins[computer] = key
        write_json(pins_path, pins)
        registry['enheder'][computer] = entry
        write_json(registry_path, registry)
        return {'schemaVersion': 1, 'settings': {'computer': computer,
                'server': cfg['server'], 'school_name': cfg['school_name'], 'theme': cfg['theme'],
                'admin_sources': [cfg['server']]},
                'device': {'computer': computer, 'token': ticket['token'], 'ui_token': ticket['ui_token']},
                'caCertificatePem': connection(root)['caCertificatePem'],
                'admin_public_key': host_key(cfg['admin_public_key'])}


def revoke_device(root, computer):
    """Explicit local admin action; never deletes the pupil's projects or queues."""
    name(computer)
    school(root)
    with lock_registry(root):
        # Commit the intended end state before touching any of the three files.
        # If a later write or process restart interrupts the sequence, the next
        # locked operation repeats the journal idempotently.
        # Validate all current source files before publishing the minimal intent.
        _revoke_sources(root)
        journal = {'schemaVersion': 1, 'operation': 'revoke-device', 'computer': computer}
        if len((json.dumps(journal, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')) > MAX_REVOKE_INTENT_BYTES:
            raise ValueError('Tilbagekaldelsesjournalen er for stor.')
        journal_path = plain_path(root / REVOKE_JOURNAL)
        write_json(journal_path, journal)
        _recover_revoke_locked(root)


class Handler(BaseHTTPRequestHandler):
    server_version = 'ElevhubEnrollment'

    def log_message(self, *args):
        pass  # Never log a ticket, token, pupil address or request body.

    def setup(self):
        self.request.settimeout(REQUEST_SECONDS)
        super().setup()
        # A socket inactivity timeout alone lets a trickle of header/body
        # bytes keep a handler alive indefinitely, blocking offline shutdown.
        def expire():
            try:
                self.request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.deadline = threading.Timer(REQUEST_SECONDS, expire)
        self.deadline.daemon = True
        self.deadline.start()

    def finish(self):
        try:
            super().finish()
        finally:
            self.deadline.cancel()

    def respond(self, status, value):
        payload = json.dumps(value, ensure_ascii=False).encode('utf-8')
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(payload)
        except OSError:
            # The deadline or client can close the socket after redemption.
            # The journal still permits that same client to retry safely.
            self.close_connection = True

    def do_POST(self):
        if self.path != '/enroll':
            return self.respond(404, {'error': 'Ukendt adresse.'})
        if not self.server.allow_attempt(self.client_address[0]):
            return self.respond(429, {'error': 'Vent et minut før næste forsøg.'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 1 <= size <= 4096 or self.headers.get('Transfer-Encoding'):
                return self.respond(400, {'error': 'Ugyldig forespørgsel.'})
            request = json.loads(self.rfile.read(size))
            result = redeem(self.server.data_root, request, self.client_address[0])
        except (ValueError, TypeError, KeyError):
            return self.respond(403, {'error': 'Tilmelding afvist. Kontrollér netværk, computernavn og kode.'})
        except (OSError, RuntimeError):
            return self.respond(503, {'error': 'Serveren kunne ikke gemme tilmeldingen. Prøv igen.'})
        self.respond(200, result)


class EnrollmentServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True
    request_queue_size = 16

    def __init__(self, root):
        root = plain_path(root)
        assert_not_restored(root)
        self.service_lock = EnrollmentInstanceLock(root).acquire()
        self.data_root = root
        self.attempts = {}
        self.attempt_lock = threading.Lock()
        try:
            with EnrollmentInstanceLock(root, OPERATIONS_LOCK):
                write_service_protocol(root)
            self.network = ipaddress.IPv4Network(school(root)['student_subnet'])
            self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self.context.minimum_version = ssl.TLSVersion.TLSv1_2
            self.context.load_cert_chain(root / 'security/server.crt', root / 'security/server.key')
            super().__init__((school(root)['server'], 8774), Handler)
        except BaseException:
            # TCPServer closes a failed bind itself. TLS is loaded before a
            # socket is created, so it cannot leak a port on certificate error.
            self.service_lock.close()
            raise

    def server_close(self):
        super().server_close()  # Join request writers before releasing lock.
        self.service_lock.close()

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(5)
        try:
            if ipaddress.IPv4Address(address[0]) not in self.network:
                raise OSError('Elevnetværk kræves.')
            return self.context.wrap_socket(sock, server_side=True), address
        except (OSError, ValueError):
            sock.close()
            raise

    def allow_attempt(self, source):
        now = time.monotonic()
        with self.attempt_lock:
            self.attempts = {ip: entry for ip, entry in self.attempts.items() if now - entry[0] < 60}
            start, count = self.attempts.get(source, (now, 0))
            if count >= 10 or (source not in self.attempts and len(self.attempts) >= 1024):
                return False
            self.attempts[source] = (start, count + 1)
            return True


def ssh_command(root, computer, now=None):
    with lock_registry(root):
        return _ssh_command_locked(root, computer, now)


def _ssh_command_locked(root, computer, now):
    assert_not_restored(root)
    name(computer)
    cfg = school(root)
    registered = read_json(root / 'security/enheder.json')['enheder'].get(computer)
    if not registered:
        raise ValueError('Computeren er ikke tilmeldt denne skole.')
    status = read_json(root / 'hub/computere.json', {}).get(computer, {})
    now = time.time() if now is None else now
    seen = status.get('sidst_set', 0)
    if not isinstance(seen, (int, float)) or not 0 <= now - seen < 900:
        raise ValueError('Computeren er ikke set inden for de seneste 15 minutter.')
    address = ipaddress.IPv4Address(status.get('ip', '0.0.0.0'))
    if address not in ipaddress.IPv4Network(cfg['student_subnet']):
        raise ValueError('Computeren er uden for det valgte elevnetværk.')
    pin = host_key(read_json(root / 'security/ssh-pins.json', {}).get(computer))
    if host_key(status.get('ssh_host_key')) != pin:
        raise ValueError('Computerens SSH-identitet er ændret. Forbindelsen afvises.')
    # One file per device prevents parallel SSH launches replacing each other's pins.
    known = root / ('admin-ssh/known_hosts-' + computer)
    plain_path(known)
    pending = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='ascii', dir=known.parent,
                                         prefix='.known-hosts-', suffix='.tmp', delete=False) as stream:
            pending = Path(stream.name)
            stream.write(computer + ' ' + pin + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        plain_path(known)
        os.replace(pending, known)
        pending = None
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)
    return ['ssh', '-i', str(root / 'admin-ssh/id_ed25519'), '-o', 'IdentitiesOnly=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + str(known),
            '-o', 'HostKeyAlias=' + computer, '-o', 'ConnectTimeout=10', 'elevhub@' + str(address)]


def main():
    parser = argparse.ArgumentParser(description='Skolens tilmelding og SSH-administration')
    parser.add_argument('action', choices=('serve', 'ticket', 'connection', 'ssh', 'check', 'revoke'))
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--name')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--confirm-name')
    args = parser.parse_args()
    assert_not_restored(args.data_root)
    root = plain_path(args.data_root).resolve()
    school(root)
    if args.action == 'serve':
        with EnrollmentServer(root) as server:
            server.serve_forever()
    elif args.action == 'ticket':
        print('Computernavn: ' + name(args.name))
        print('Engangskode (30 minutter): ' + issue_ticket(root, args.name))
    elif args.action == 'connection':
        if not args.output or args.output.exists():
            raise ValueError('Vælg en ny fil til skolens offentlige forbindelse.')
        write_json(args.output, connection(root))
    elif args.action == 'ssh':
        return subprocess.call(ssh_command(root, args.name))
    elif args.action == 'revoke':
        if not args.name or args.confirm_name != args.name:
            raise ValueError('Bekræft det præcise computernavn med --confirm-name.')
        revoke_device(root, args.name)
        print('Tilmeldingen er fjernet. Opret en ny kode til geninstallation. Elevfiler og køer er bevaret.')
    elif args.action == 'check':
        ssl.create_default_context(cafile=str(root / 'security/ca.crt'))
        print('Skoleopsætning og CA kan læses.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
