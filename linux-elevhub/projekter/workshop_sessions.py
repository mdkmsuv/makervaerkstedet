"""In-memory pupil-workshop handoff. Contains no names or project contents."""
import json
import re
import threading
import time
import uuid
from urllib.parse import parse_qs, urlsplit

PREFIX = '/_elevhub/session/'
KINDS = {'scratch', 'kredsloeb', 'laser', 'folie', 'datatraener', 'microbit'}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{32}', value):
        raise ValueError('Ugyldig arbejdsrunde.')
    return value


class WorkshopSessions:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.clients = {}
        self.requests = {}

    def poll(self, data):
        client = identifier(data.get('client'))
        kind = data.get('kind')
        if kind not in KINDS or type(data.get('dirty')) is not bool:
            raise ValueError('Ugyldigt værksted.')
        with self.lock:
            self.clients[client] = {'kind': kind, 'dirty': data['dirty'],
                                    'active': data.get('active') is not False, 'seen': self.clock()}
            pending = next((r for r in reversed(list(self.requests.values()))
                            if client in r['targets'] and r['targets'][client] == 'pending'), None)
            return {'request': self.public(pending) if pending else None}

    def request(self, reason='home'):
        if reason not in ('home', 'switch', 'close'):
            raise ValueError('Ugyldig afslutning.')
        with self.lock:
            for request in self.requests.values():
                if request['state'] == 'pending':
                    return self.public(request)
            now = self.clock()
            # A silent dirty editor is never interpreted as permission to leave.
            active = {key: c for key, c in self.clients.items() if c['active']}
            stale = any(c['dirty'] and now - c['seen'] > 30 for c in active.values())
            targets = {key: 'pending' for key, c in active.items() if now - c['seen'] <= 30}
            request = {'id': uuid.uuid4().hex, 'state': 'error' if stale else 'pending' if targets else 'completed',
                       'reason': reason, 'targets': targets, 'created': now}
            if stale:
                request['message'] = 'Et værksted svarer ikke. Gå tilbage til projektet og prøv igen.'
            self.requests[request['id']] = request
            for key in list(self.requests)[:-100]:
                del self.requests[key]
            return self.public(request)

    @staticmethod
    def public(request):
        return {key: request[key] for key in ('id', 'state', 'reason', 'message') if key in request}

    def status(self, request_id):
        with self.lock:
            return self.public(self.requests[identifier(request_id)])

    def acknowledge(self, data):
        client, request_id = identifier(data.get('client')), identifier(data.get('id'))
        outcome = data.get('state')
        if outcome not in ('completed', 'cancelled', 'error'):
            raise ValueError('Ugyldigt svar fra værkstedet.')
        with self.lock:
            request = self.requests[request_id]
            if client not in request['targets']:
                raise PermissionError('Svaret hører til et andet værksted.')
            if request['state'] != 'pending':
                return self.public(request)
            request['targets'][client] = outcome
            if outcome == 'completed':
                self.clients[client]['active'] = False
                self.clients[client]['dirty'] = False
                if all(value == 'completed' for value in request['targets'].values()):
                    request['state'] = 'completed'
            else:
                request['state'] = outcome
                if outcome == 'error':
                    request['message'] = 'Projektet kunne ikke afsluttes. Dit arbejde er stadig åbent.'
            return self.public(request)

    def cancel(self, request_id):
        with self.lock:
            request = self.requests[identifier(request_id)]
            if request['state'] == 'pending':
                request['state'] = 'cancelled'
            return self.public(request)


def handle_session(h):
    url = urlsplit(h.path)
    if not url.path.startswith(PREFIX):
        return False
    try:
        action = url.path[len(PREFIX):]
        origin = 'http://' + h.headers.get('Host', '')
        native = (not h.headers.get('Origin') and not h.headers.get('Sec-Fetch-Site')
                  and h.headers.get('X-Elevhub-Navigation') == '1')
        if h.command == 'GET' and action == 'status':
            query = parse_qs(url.query)
            if set(query) != {'id'} or len(query['id']) != 1:
                raise ValueError('Ugyldig afslutning.')
            result = h.server.workshop_sessions.status(query['id'][0])
        elif h.command == 'POST':
            if not (h.headers.get('Origin') == origin and h.headers.get('X-Elevhub-Session') == '1'):
                if not native or action not in ('request', 'cancel'):
                    raise PermissionError('Afslut fra værkstedets egen side.')
            if h.headers.get_content_type() != 'application/json':
                raise ValueError('Ugyldigt format.')
            data = json.loads(h.laes_krop(h.kropslaengde(4096)))
            if not isinstance(data, dict):
                raise ValueError('Ugyldigt format.')
            sessions = h.server.workshop_sessions
            if action == 'poll':
                result = sessions.poll(data)
            elif action == 'request':
                result = sessions.request(data.get('reason', 'home'))
            elif action == 'ack':
                result = sessions.acknowledge(data)
            elif action == 'cancel':
                result = sessions.cancel(data.get('id'))
            else:
                raise ValueError('Ukendt handling.')
        else:
            raise ValueError('Ukendt handling.')
        h.svar(200, result)
    except PermissionError as error:
        h.svar(403, {'error': str(error)})
    except KeyError:
        h.svar(404, {'error': 'Arbejdsrunden findes ikke.'})
    except (ValueError, TypeError, OSError):
        h.svar(400, {'error': 'Arbejdsrunden kunne ikke opdateres.'})
    return True
