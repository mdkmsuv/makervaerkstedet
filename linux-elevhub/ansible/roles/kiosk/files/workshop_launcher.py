"""Authenticated loopback launch of CaDoodle from the local workshop page."""
import hmac
import http.client
import json
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8790


def session_request(method, path, data=None):
    """Bounded native-only request; never follows a redirect or the proxy."""
    connection = http.client.HTTPConnection('127.0.0.1', 8791, timeout=4)
    try:
        body = None if data is None else json.dumps(data).encode('utf-8')
        connection.request(method, '/_elevhub/session/' + path, body,
                           {'X-Elevhub-Navigation': '1', 'Content-Type': 'application/json'})
        response = connection.getresponse()
        raw = response.read(8193)
        if response.status not in (200, 202) or len(raw) > 8192:
            raise ValueError('Ugyldigt svar fra værkstedet.')
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError('Ugyldigt svar fra værkstedet.')
        return result
    finally:
        connection.close()


def finish_web_sessions(reason='home', timeout=600):
    """Only a positive acknowledgement permits the kiosk to leave the page."""
    request_id = None
    try:
        result = session_request('POST', 'request', {'reason': reason})
        request_id = result.get('id')
        if not isinstance(request_id, str) or len(request_id) != 32 or any(c not in '0123456789abcdef' for c in request_id):
            raise ValueError('Ugyldig afslutning.')
        deadline = time.monotonic() + timeout
        while result.get('state') == 'pending':
            if time.monotonic() >= deadline:
                raise TimeoutError('Afslutningen tog for lang tid.')
            time.sleep(0.25)
            result = session_request('GET', 'status?id=' + request_id)
            if result.get('id') != request_id:
                raise ValueError('Ugyldig afslutning.')
        if result.get('state') in ('completed', 'cancelled'):
            return result
        raise ValueError(result.get('message') or 'Værkstedet kunne ikke afsluttes.')
    except (OSError, ValueError, TypeError, http.client.HTTPException):
        if request_id:
            try:
                session_request('POST', 'cancel', {'id': request_id})
            except (OSError, ValueError, TypeError, http.client.HTTPException):
                pass
        return {'state': 'error', 'message': 'Værkstedet svarer ikke. Dit arbejde er stadig åbent. Prøv igen.'}


def navigation_request_path():
    return Path.home() / '.local/state/elevhub/navigation/finish.request'


def request_navigation():
    """Coalesce keyboard/WM close attempts into one local toolbar action."""
    path = navigation_request_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        temporary.write_text(json.dumps({'created': time.time()}), encoding='ascii')
        os.replace(temporary, path)
        return True
    except OSError:
        return False


def consume_navigation_request():
    path = navigation_request_path()
    try:
        if path.stat().st_size > 256:
            path.unlink()
            return False
        data = json.loads(path.read_text(encoding='ascii'))
        path.unlink()
        return 0 <= time.time() - data['created'] < 5
    except (OSError, ValueError, KeyError, TypeError):
        return False


class LocalServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(3)

    def log_message(self, *_args):
        pass  # Never log authorization headers or pupil activity.

    def reply(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if self.headers.get('Origin') == 'null':
            self.send_header('Access-Control-Allow-Origin', 'null')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Authorization, X-Elevhub-Computer, X-Elevhub-Page')
            self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.end_headers()
        self.wfile.write(body)

    def allowed_origin(self):
        return (self.headers.get_all('Host', []) == [f'127.0.0.1:{self.server.server_port}']
                and self.headers.get_all('Origin', []) == ['null'])

    def authenticated(self):
        return (self.allowed_origin()
                and self.headers.get_all('X-Elevhub-Computer', []) == [self.server.computer]
                and len(self.headers.get_all('Authorization', [])) == 1
                and hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + self.server.token))

    def do_OPTIONS(self):
        requested = {h.strip().lower() for h in self.headers.get('Access-Control-Request-Headers', '').split(',') if h.strip()}
        good = (self.allowed_origin() and urlsplit(self.path).path in ('/open', '/status', '/finish', '/finish/status')
                and self.headers.get('Access-Control-Request-Method') in ('GET', 'POST')
                and requested <= {'authorization', 'x-elevhub-computer', 'x-elevhub-page'})
        self.reply(200 if good else 403, {})

    def do_GET(self):
        if not self.authenticated():
            self.reply(403, {'error': 'Åbn fra Vælg værksted på denne computer.'})
        elif urlsplit(self.path).path == '/finish/status':
            request_id = parse_qs(urlsplit(self.path).query).get('id', [''])[0]
            with self.server.state_lock:
                status = self.server.results.get(request_id)
            self.reply(200 if status else 404, status or {})
        elif self.path != '/status':
            self.reply(404, {})
        else:
            navigation = None
            if self.headers.get('X-Elevhub-Page') == 'workshops-v1':
                self.server.page_ready = True
                with self.server.state_lock:
                    target = self.server.navigation_target
                    if target and 0 <= time.time() - target['created'] < 120:
                        navigation = target['page']
                    self.server.navigation_target = None
            self.reply(200, {'available': bool(self.server.available()), 'page_ready': self.server.page_ready,
                             'navigation': navigation})

    def do_POST(self):
        if not self.authenticated():
            self.reply(403, {'error': 'Åbn fra Vælg værksted på denne computer.'})
            return
        if self.path not in ('/open', '/finish'):
            self.reply(404, {})
            return
        if self.headers.get('Transfer-Encoding') or self.headers.get_all('Content-Length', []) not in ([], ['0']):
            self.reply(400, {'error': 'Ugyldig åbning.'})
            return
        if self.path == '/open' and not self.server.available():
            self.reply(403, {'error': '3D-værkstedet er lukket lige nu. Spørg din lærer.'})
            return
        with self.server.state_lock:
            if self.server.pending_id:
                if self.server.pending_action != self.path:
                    self.reply(409, {'error': 'Afslut først valget i det åbne værksted.'})
                    return
                request_id = self.server.pending_id
            else:
                request_id = uuid.uuid4().hex
                self.server.pending_id = request_id
                self.server.pending_action = self.path
                self.server.results[request_id] = {'id': request_id, 'state': 'pending'}
                self.server.requests.put_nowait({'id': request_id, 'action': self.path})
        self.server.page_ready = True
        self.reply(202, {'opening': self.path == '/open', 'id': request_id, 'state': 'pending'})


def make_server(computer, token, available, port=PORT):
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError('Missing local UI identity')
    server = LocalServer(('127.0.0.1', port), Handler)
    server.computer, server.token, server.available = computer, token, available
    server.requests = queue.Queue(maxsize=1)
    server.results, server.pending_id, server.pending_action = {}, None, None
    server.state_lock = threading.Lock()
    server.navigation_target = None
    server.page_ready = False
    return server


def attach(workshop, toolbar):
    def keyboard_requests():
        if consume_navigation_request():
            toolbar.til_start()
        toolbar.rod.after(150, keyboard_requests)
    toolbar.rod.after(150, keyboard_requests)
    try:
        server = make_server(toolbar.konf['computer'], toolbar.konf['ui_token'],
                             lambda: bool(workshop and workshop.aaben() and not workshop.skaermpause()))
    except OSError:
        # Preserve the existing toolbar entry if the local port is unavailable.
        return None
    threading.Thread(target=server.serve_forever, daemon=True, name='workshop-launcher').start()
    previous_shortcut = workshop.vis_genvej if workshop else None
    def hide_shortcut(on_home):
        if workshop is None:
            return
        # Keep the old entry usable until the already-open browser reloads
        # the updated page. A fresh login loads the new page immediately.
        if not server.page_ready:
            previous_shortcut(on_home)
            return
        workshop.paa_startside = on_home
        if workshop.knap.winfo_manager():
            workshop.knap.pack_forget()
    if workshop:
        workshop.vis_genvej = hide_shortcut
    hide_shortcut(False)
    def drain():
        try:
            request = server.requests.get_nowait()
        except queue.Empty:
            pass
        else:
            def finished(result):
                with server.state_lock:
                    server.results[request['id']] = dict(result, id=request['id'])
                    server.pending_id = None
                    server.pending_action = None
                    # Keep recent acknowledgements available to the page,
                    # with a strict bound on this process's in-memory history.
                    while len(server.results) > 32:
                        server.results.pop(next(iter(server.results)))
            if request['action'] == '/open' and not server.available():
                finished({'state': 'error', 'message': '3D-værkstedet er lukket lige nu. Spørg din lærer.'})
            else:
                def action():
                    if request['action'] == '/open' and server.available():
                        workshop.start_cad()
                toolbar.afslut_foer(action, reason='switch', completed=finished)
        toolbar.rod.after(150, drain)
    toolbar.rod.after(150, drain)
    return server
