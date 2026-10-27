"""Narrow TLS gateway for the station's existing laser-teacher session.

The queue and its authentication remain on loopback. This gateway never supplies
the makerspace service key and never exposes pupil submissions or admin commands.
"""
import http.client
from http.cookies import CookieError, SimpleCookie
import ipaddress
import re
import socket
import ssl
import threading
import time
from urllib.parse import parse_qs, urlsplit

MAX_RESPONSE = 3_000_000
TIMEOUT = 10.0
_JOB = r'[a-f0-9]{32}'
_STATION = r'[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?'


def _allowed(method, raw_path, path):
    target = urlsplit(raw_path)
    if target.scheme or target.netloc or target.fragment or target.path != path:
        return False
    if method == 'GET':
        if path == '/api/laser/jobs':
            query = parse_qs(target.query, keep_blank_values=True)
            return set(query) <= {'summary', 'limit', 'cursor'} and all(len(v) == 1 for v in query.values())
        return not target.query and (path == '/api/laser/health'
            or re.fullmatch(r'/api/laser/jobs/' + _JOB + r'/svg', path)
            or re.fullmatch(r'/api/laser/stations/' + _STATION + r'/configuration', path))
    return method == 'POST' and not target.query and (path in ('/api/laser/login', '/api/laser/logout')
        or re.fullmatch(r'/api/laser/jobs/' + _JOB + r'/(status|image-mode)', path)
        or re.fullmatch(r'/api/laser/stations/' + _STATION + r'/handoff', path))


def handle(handler, path, length):
    """Return whether this namespace was handled, after HTTP framing checks."""
    if not ((isinstance(path, str) and path.startswith('/api/laser/'))
            or urlsplit(handler.path).path.startswith('/api/laser/')):
        return False

    def reject(status, message):
        if length:
            handler.close_connection = True
        handler.svar(status, {'error': message})
        return True

    secure = isinstance(handler.connection, ssl.SSLSocket)
    if not secure and not ipaddress.ip_address(handler.client_address[0]).is_loopback:
        return reject(403, 'Laserstationens serverforbindelse kræver HTTPS.')
    if not isinstance(path, str) or not _allowed(handler.command, handler.path, path):
        return reject(404, 'Laserstationens funktion findes ikke.')
    if handler.headers.get_all('Authorization'):
        return reject(403, 'Laserstationen kræver lærerens laserlogin.')
    origin = handler.headers.get('Origin')
    if origin is not None and origin != ('https://' if secure else 'http://') + handler.headers.get('Host', ''):
        return reject(403, 'Forespørgslen kom ikke fra serverens egen side.')
    if handler.command == 'GET' and length:
        return reject(400, 'En læseforespørgsel må ikke indeholde data.')
    if handler.command == 'POST' and (not length or handler.headers.get_content_type() != 'application/json'):
        return reject(415, 'Brug laserstationens JSON-forespørgsel.')
    cookies = handler.headers.get_all('Cookie', [])
    if len(cookies) > 1 or any(len(value) > 8192 for value in cookies):
        return reject(400, 'Ugyldig lasersession.')
    try:
        cookie = SimpleCookie(cookies[0] if cookies else '')
    except CookieError:
        return reject(400, 'Ugyldig lasersession.')
    service = getattr(handler.server, 'makerspace', None)
    backend = getattr(getattr(service, 'laser_admin', None), 'backend', None)
    # The adapter validates this at startup; retain the boundary here too.
    if (not isinstance(backend, tuple) or len(backend) != 2 or backend[0] != '127.0.0.1'
            or type(backend[1]) is not int or not 1 <= backend[1] <= 65535):
        return reject(503, 'Laserstationens kø er ikke konfigureret.')
    body = handler.laes_krop(length) if length else None
    headers = {'Accept': 'application/json', 'Host': f'{backend[0]}:{backend[1]}'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    if 'laser_teacher' in cookie:
        headers['Cookie'] = cookie['laser_teacher'].OutputString(attrs=[])
    # Only a validated browser Origin is translated for the HTTP loopback hop.
    if origin is not None:
        headers['Origin'] = 'http://' + headers['Host']
    deadline = time.monotonic() + TIMEOUT
    connection = http.client.HTTPConnection(*backend, timeout=TIMEOUT)
    watchdog = None
    try:
        connection.connect()
        sock = connection.sock
        def expire():
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        watchdog = threading.Timer(max(0, deadline - time.monotonic()), expire)
        watchdog.daemon = True
        watchdog.start()
        connection.request(handler.command, handler.path, body=body, headers=headers)
        response = connection.getresponse()
        data = bytearray()
        while len(data) <= MAX_RESPONSE:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            try:
                sock.settimeout(remaining)
            except OSError:
                pass
            chunk = response.read1(min(8192, MAX_RESPONSE + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > MAX_RESPONSE or 300 <= response.status < 400:
            return reject(502, 'Laserkøens svar kunne ikke bekræftes.')
        extra = {}
        if path in ('/api/laser/login', '/api/laser/logout'):
            returned = SimpleCookie(response.getheader('Set-Cookie', ''))
            if 'laser_teacher' in returned:
                morsel = returned['laser_teacher']
                morsel['httponly'], morsel['samesite'], morsel['path'] = True, 'Strict', '/'
                morsel['secure'] = secure
                extra['Set-Cookie'] = morsel.OutputString()
        handler.svar(response.status, bytes(data), response.getheader('Content-Type', 'application/json'), extra)
    except (OSError, http.client.HTTPException, CookieError):
        reject(503, 'Laserkøen svarer ikke lige nu. Prøv igen om lidt.')
    finally:
        if watchdog is not None:
            watchdog.cancel()
        connection.close()
    return True
