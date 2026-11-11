"""Small, read-only snapshot for the teacher's system control panel."""
import http.client
import json
import datetime as _datetime
import os
import ssl
import stat
import threading
import tempfile
import time
from pathlib import Path

from sikkerhed import lokal_backend
from tilstand import SOFTWARE_VERSION

MAX_PRINT_BYTES = 128 * 1024
PRINT_TIMEOUT = 0.8
PRINT_READ_SECONDS = 1.2
PRINT_CACHE_SECONDS = 2
MAX_CERT_BYTES = 64 * 1024
CERT_CACHE_SECONDS = 30


def _reparse(info, path):
    return (path.is_symlink() or
            bool(getattr(info, 'st_file_attributes', 0) & 0x400))


def _safe_cert_identity(path):
    """Return a bounded file identity, rejecting links in the path chain."""
    path = Path(path).absolute()
    current = path
    try:
        path_info = path.lstat()
    except FileNotFoundError:
        path_info = None
        current = path.parent
    except OSError:
        return None
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError:
            info = None
        except OSError:
            return None
        if info is not None and _reparse(info, current):
            return None
        if current.parent == current:
            break
        current = current.parent
    if path_info is None:
        return None
    if _reparse(path_info, path) or not stat.S_ISREG(path_info.st_mode):
        return None
    return (path_info.st_dev, path_info.st_ino, path_info.st_size,
            getattr(path_info, 'st_mtime_ns', int(path_info.st_mtime * 1e9)))


def _utc_from_ssl(value):
    try:
        return _datetime.datetime.strptime(value, '%b %d %H:%M:%S %Y %Z').replace(
            tzinfo=_datetime.timezone.utc)
    except (TypeError, ValueError):
        return None


def _certificate_dates(path, pem):
    """Decode only an already bounded certificate using installed tooling."""
    try:
        from cryptography import x509
        certificate = x509.load_pem_x509_certificate(pem)
        before = getattr(certificate, 'not_valid_before_utc', None)
        after = getattr(certificate, 'not_valid_after_utc', None)
        if before is None:
            before = certificate.not_valid_before
        if after is None:
            after = certificate.not_valid_after
        if before.tzinfo is None:
            before = before.replace(tzinfo=_datetime.timezone.utc)
        if after.tzinfo is None:
            after = after.replace(tzinfo=_datetime.timezone.utc)
        return before, after
    except ImportError:
        pass
    except (ValueError, TypeError):
        return None
    # Python's standard library exposes the OpenSSL decoder by path rather than
    # bytes.  Decode a bounded private temporary copy so a concurrent source
    # replacement cannot make the decoder read an unbounded certificate.
    try:
        with tempfile.NamedTemporaryFile(prefix='elevhub-cert-', suffix='.pem', delete=False) as handle:
            temporary = handle.name
            handle.write(pem)
        try:
            decoded = ssl._ssl._test_decode_cert(temporary)
        finally:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        before = _utc_from_ssl(decoded.get('notBefore'))
        after = _utc_from_ssl(decoded.get('notAfter'))
        return (before, after) if before and after else None
    except (AttributeError, OSError, ValueError, TypeError):
        return None


def _certificate_state(path, now):
    result = {'status': 'unavailable', 'not_before': None, 'not_after': None,
              'days_remaining': None}
    identity = _safe_cert_identity(path) if path else None
    if identity is None:
        return result
    if identity[2] > MAX_CERT_BYTES:
        return result
    try:
        with Path(path).open('rb') as handle:
            pem = handle.read(MAX_CERT_BYTES + 1)
        if len(pem) > MAX_CERT_BYTES or _safe_cert_identity(path) != identity:
            return result
    except OSError:
        return result
    dates = _certificate_dates(Path(path), pem)
    if dates is None:
        return result
    before, after = dates
    remaining = (after - now).total_seconds()
    result['not_before'] = before.isoformat().replace('+00:00', 'Z')
    result['not_after'] = after.isoformat().replace('+00:00', 'Z')
    result['days_remaining'] = int(remaining // 86400)
    if now < before:
        result['status'] = 'not_yet_valid'
    elif now >= after:
        result['status'] = 'expired'
    elif remaining <= 30 * 86400:
        result['status'] = 'expiring_30_days'
    else:
        result['status'] = 'valid'
    return result


class CertificateSnapshot:
    """Read-only, bounded status for the configured leaf and CA certificates."""

    def __init__(self, paths=None, clock=time.time, monotonic=time.monotonic):
        self.paths = dict(paths or {})
        self.clock, self.monotonic = clock, monotonic
        self.cached = None
        self.cached_signature = None
        self.cached_until = 0
        self.lock = threading.Lock()

    def snapshot(self):
        paths = (self.paths.get('certificate'), self.paths.get('ca'))
        signature = tuple(_safe_cert_identity(path) if path else None for path in paths)
        now_mono = self.monotonic()
        if self.cached is not None and now_mono < self.cached_until and signature == self.cached_signature:
            return json.loads(json.dumps(self.cached))
        with self.lock:
            signature = tuple(_safe_cert_identity(path) if path else None for path in paths)
            now_mono = self.monotonic()
            if self.cached is not None and now_mono < self.cached_until and signature == self.cached_signature:
                return json.loads(json.dumps(self.cached))
            now = _datetime.datetime.fromtimestamp(self.clock(), tz=_datetime.timezone.utc)
            certificate = _certificate_state(paths[0], now)
            ca = _certificate_state(paths[1], now)
            result = {'status': certificate['status'], 'certificate': certificate,
                      'ca': ca, 'checked_at': self.clock()}
            self.cached = result
            self.cached_signature = signature
            self.cached_until = self.monotonic() + CERT_CACHE_SECONDS
            return json.loads(json.dumps(result))


def release_info(path):
    """Missing or damaged release metadata must never imply an updated client."""
    result = {'release_id': None, 'expected_client_release': None}
    try:
        path = Path(path)
        if path.is_symlink() or path.stat().st_size > 4096:
            return result
        data = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            for key in result:
                value = data.get(key)
                if isinstance(value, str) and SOFTWARE_VERSION.fullmatch(value):
                    result[key] = value
    except (OSError, ValueError, TypeError):
        pass
    return result


def certificate_snapshot(paths=None):
    """Return a one-shot certificate status for callers without a server cache."""
    return CertificateSnapshot(paths).snapshot()


def computers(overview, inventory, expected):
    """Join current inventory to historic state without disclosing credentials."""
    registered = set(inventory)
    historic = {c['navn']: c for c in overview.get('computere', [])
                if isinstance(c, dict) and c.get('navn') in registered}
    rows = []
    for name in sorted(registered):
        c = historic.get(name, {})
        version = c.get('software_version')
        if not isinstance(version, str) or not SOFTWARE_VERSION.fullmatch(version):
            version = None
        rows.append({
            'navn': name, 'ip': c.get('ip'), 'online': c.get('online') is True,
            'sidst_set_sekunder': c.get('sidst_set_sekunder'),
            'visning': c.get('visning') if c.get('online') is True else None,
            'bekraeftet': c.get('bekraeftet') is True,
            'software_version': version,
            'version_status': ('unknown' if not expected or not version else
                               'current' if version == expected else 'different'),
            'registered': True,
        })
    return rows


def _short(value):
    if not isinstance(value, str):
        return ''
    return ''.join(ch for ch in value if ch.isprintable() and ch not in '<>')[:80]


def _printer_rows(data):
    if not isinstance(data, dict) or not isinstance(data.get('printers'), list):
        raise ValueError('Invalid print state')
    result = []
    for row in data['printers'][:128]:
        if not isinstance(row, dict):
            continue
        status = row.get('status')
        if status not in ('offline', 'disabled', 'attention', 'printing', 'preparing', 'ready', 'bed'):
            status = 'unknown'
        online = status not in ('unknown', 'offline')
        enabled = row.get('enabled') is True
        result.append({
            'id': _short(row.get('id')), 'name': _short(row.get('name')),
            'model': _short(row.get('model')), 'status': status,
            'online': online, 'enabled': enabled,
            'ready': online and enabled and status == 'ready',
            'bed_clear': online and row.get('bed_clear') is True,
            'error': status == 'attention' or bool(row.get('error_code')),
        })
    return result


class PrinterSnapshot:
    """Probe the configured loopback print service; coalesce overlapping reads."""

    def __init__(self, apps, clock=time.time, monotonic=time.monotonic):
        self.clock, self.monotonic = clock, monotonic
        self.backend = None
        for app in apps:
            if app.get('id') == '3dprint' and app.get('regler') == 'printvaerksted':
                address = app.get('bagved')
                if isinstance(address, str) and lokal_backend(address):
                    host, _, port = address.rpartition(':')
                    self.backend = host, int(port)
                break
        self.lock = threading.Lock()
        self.cached = None
        self.cached_until = 0

    def snapshot(self):
        now = self.monotonic()
        if self.cached is not None and now < self.cached_until:
            return self.cached
        if not self.lock.acquire(blocking=False):
            return {'status': 'unknown', 'checked_at': self.clock(), 'items': []}
        try:
            now = self.monotonic()
            if self.cached is not None and now < self.cached_until:
                return self.cached
            result = {'status': 'unknown', 'checked_at': self.clock(), 'items': []}
            if self.backend:
                connection = http.client.HTTPConnection(*self.backend, timeout=PRINT_TIMEOUT)
                try:
                    connection.request('GET', '/api/state', headers={'Accept': 'application/json'})
                    sock = connection.sock
                    response = connection.getresponse()
                    if response.status == 200:
                        deadline = self.monotonic() + PRINT_READ_SECONDS
                        pieces, total = [], 0
                        while total <= MAX_PRINT_BYTES:
                            remaining = deadline - self.monotonic()
                            if remaining <= 0:
                                raise TimeoutError('Print state took too long')
                            sock.settimeout(remaining)
                            chunk = response.read1(min(8192, MAX_PRINT_BYTES + 1 - total))
                            if not chunk:
                                break
                            pieces.append(chunk)
                            total += len(chunk)
                            if response.length == 0:
                                break
                        raw = b''.join(pieces)
                        if len(raw) <= MAX_PRINT_BYTES:
                            result['items'] = _printer_rows(json.loads(raw))
                            result['status'] = 'ok'
                except (OSError, TimeoutError, ValueError, UnicodeError, http.client.HTTPException):
                    pass
                finally:
                    connection.close()
            self.cached = result
            self.cached_until = self.monotonic() + PRINT_CACHE_SECONDS
            return result
        finally:
            self.lock.release()
