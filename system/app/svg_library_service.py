"""Unprivileged, read-only SVG service over a fixed local Unix socket.

Run with ``python -m app.svg_library_service``. systemd owns the runtime and
state directories; this process has Internet access, the queue process does
not. No request can select a URL, filesystem path, cache directory or command.

UnixLibraryClient.status() returns the safe status dictionary.
catalogue_response() returns that dictionary plus ``items``.
svg_response(item_id) returns that dictionary plus sanitized ``svg``.
Unavailable/rejected service responses raise ValueError or OSError.
"""
import http.client
import json
import os
import re
import socket
import socketserver
import stat
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from .svg_library import Library, ORIGIN, MAX_BYTES

DEFAULT_SOCKET = '/run/elevhub-svg-library/library.sock'
# DynamicUser + StateDirectory exposes the public path as a symlink. Open the
# private state directory directly inside the unit's namespace so the cache's
# O_NOFOLLOW checks stay intact. This is the same persistent directory.
DEFAULT_CACHE = '/var/lib/private/elevhub-svg-library'
MAX_RESPONSE_BYTES = 8_000_000
# One online SVG request may include an 8s catalogue read and an 8s file read.
TIMEOUT = 20
_ID = re.compile(r'[a-f0-9]{64}')
_STATUS_FIELDS = ('enabled', 'version', 'source_url', 'cache_state', 'last_success')


def _status(value):
    """Allow only public metadata; never relay local paths or exception text."""
    result = {key: value[key] for key in _STATUS_FIELDS}
    if result['enabled'] is not True or type(result['version']) is not int or not 0 <= result['version'] < 2 ** 53:
        raise ValueError('Ugyldig biblioteksstatus.')
    if result['source_url'] != ORIGIN + '/svg-bibliotek/' or result['cache_state'] not in ('ready', 'stale', 'empty'):
        raise ValueError('Ugyldig biblioteksstatus.')
    if result['last_success'] is not None and (not isinstance(result['last_success'], str) or len(result['last_success']) > 64):
        raise ValueError('Ugyldig biblioteksstatus.')
    return result


class _Handler(BaseHTTPRequestHandler):
    server_version = 'SVG-library'
    sys_version = ''

    def setup(self):
        self.request.settimeout(TIMEOUT)
        super().setup()

    def log_message(self, *args):
        # Avoid retaining browser request content or filesystem exception text.
        pass

    def _json(self, status, data):
        raw = json.dumps(data, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        if len(raw) > MAX_RESPONSE_BYTES:
            status, raw = 503, b'{"error":"Bibliotekets svar er for stort."}'
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(raw)
        self.close_connection = True

    def do_GET(self):
        if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length', '0') != '0':
            self._json(400, {'error': 'Ugyldig forespørgsel.'})
            return
        item_id = None
        if self.path not in ('/status', '/catalogue'):
            match = re.fullmatch(r'/file/([a-f0-9]{64})', self.path)
            if match is None:
                self._json(404, {'error': 'Ukendt bibliotekssti.'})
                return
            item_id = match[1]
        library = self.server.library
        try:
            with library.lock:
                data = {}
                if self.path == '/catalogue':
                    data['items'] = library.catalogue()
                elif item_id is not None:
                    data['svg'] = library.svg(item_id)
                data.update(_status(library.status()))
            self._json(200, data)
        except (OSError, ValueError, KeyError, TypeError):
            self._json(503, {'error': 'SVG-biblioteket er ikke tilgængeligt.'})

    def _method_not_allowed(self):
        self._json(405, {'error': 'Kun GET understøttes.'})

    do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = do_CONNECT = do_TRACE = _method_not_allowed


class _Server(socketserver.ThreadingMixIn, getattr(socketserver, 'UnixStreamServer', socketserver.BaseServer)):
    daemon_threads = True
    allow_reuse_address = False

    def server_close(self):
        super().server_close()
        # Remove only the socket created by this instance.
        try:
            current = Path(self.server_address).lstat()
            if getattr(self, '_socket_identity', None) == (current.st_dev, current.st_ino):
                Path(self.server_address).unlink()
        except FileNotFoundError:
            pass


def create_server(socket_path=DEFAULT_SOCKET, library=None):
    """Bind a fresh socket; never remove an existing file/socket on startup.

    The runtime directory must allow traversal by the unprivileged gateway.
    The socket is mode 0666 because its only operations expose public SVGs.
    Existing runtime-directory permissions remain under systemd's control.
    """
    if not hasattr(socketserver, 'UnixStreamServer'):
        raise OSError('Den lokale SVG-tjeneste kræver Unix sockets.')
    path = Path(os.path.abspath(socket_path))
    for parent in reversed((path.parent, *path.parent.parents)):
        if parent.is_symlink():
            raise ValueError('Socketmappen må ikke være et link.')
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if os.path.lexists(path):
        raise ValueError('Socketstien findes allerede.')
    server = _Server(str(path), _Handler)
    try:
        current = path.lstat()
        if not stat.S_ISSOCK(current.st_mode):
            raise ValueError('Ugyldig socket.')
        server._socket_identity = (current.st_dev, current.st_ino)
        server.library = library if library is not None else Library(cache_dir=DEFAULT_CACHE)
        path.chmod(0o666)
    except Exception:
        server.server_close()
        raise
    return server


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, socket_path):
        super().__init__('localhost', timeout=TIMEOUT)
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        try:
            self.sock.connect(self.socket_path)
        except Exception:
            self.sock.close()
            raise


class UnixLibraryClient:
    """Fixed local HTTP transport. Only the constructor accepts a socket path.

    Gateway handlers must use a trusted deployment value for socket_path;
    they must never forward a browser-selected socket, Host or URL.
    """
    def __init__(self, socket_path=DEFAULT_SOCKET):
        self.socket_path = os.fspath(socket_path)

    def _get(self, path):
        conn = _UnixConnection(self.socket_path)
        try:
            conn.request('GET', path, headers={'Accept': 'application/json'})
            response = conn.getresponse()
            if response.status != 200 or response.getheader('Transfer-Encoding'):
                raise ValueError('SVG-biblioteket er ikke tilgængeligt.')
            size = response.getheader('Content-Length')
            if size is None or not size.isdecimal() or int(size) > MAX_RESPONSE_BYTES:
                raise ValueError('Ugyldig størrelse på bibliotekssvar.')
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) != int(size) or len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError('Bibliotekets svar er for stort eller ufuldstændigt.')
            data = json.loads(raw)
            _status(data)
            return data
        except (http.client.HTTPException, UnicodeError, KeyError, TypeError) as error:
            raise ValueError('Ugyldigt svar fra SVG-biblioteket.') from error
        finally:
            conn.close()

    def status(self):
        return _status(self._get('/status'))

    def catalogue_response(self):
        data = self._get('/catalogue')
        items = data.get('items')
        if not isinstance(items, list) or len(items) > 5000:
            raise ValueError('Ugyldigt biblioteksindhold.')
        clean = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or _ID.fullmatch(item['id']) is None:
                raise ValueError('Ugyldigt biblioteks-id.')
            for key, limit in (('title', 120), ('category', 100)):
                if not isinstance(item.get(key), str) or len(item[key]) > limit:
                    raise ValueError('Ugyldig biblioteksbeskrivelse.')
            clean.append({key: item[key] for key in ('id', 'title', 'category')})
        return {**_status(data), 'items': clean}

    def svg_response(self, item_id):
        if not isinstance(item_id, str) or _ID.fullmatch(item_id) is None:
            raise ValueError('Ukendt biblioteksfil.')
        data = self._get('/file/' + item_id)
        svg = data.get('svg')
        if not isinstance(svg, str) or len(svg.encode('utf-8')) > MAX_BYTES:
            raise ValueError('Ugyldigt SVG-svar.')
        return {**_status(data), 'svg': svg}


def main():
    with create_server() as server:
        server.serve_forever()


if __name__ == '__main__':
    main()
