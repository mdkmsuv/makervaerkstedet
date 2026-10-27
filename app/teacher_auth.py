"""Bounded teacher sessions and login attempts for the print service."""
from collections import defaultdict, deque
import hmac
from http.cookies import CookieError, SimpleCookie
import secrets
import threading
import time


class LoginLimited(Exception):
    retry_after = 60


class TeacherAuth:
    lifetime = 8 * 3600

    def __init__(self, code):
        self.code = code.encode('utf-8')
        self.sessions = {}
        self.failures = defaultdict(deque)
        self.lock = threading.Lock()

    def _prune(self, now):
        self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}
        for source, attempts in list(self.failures.items()):
            while attempts and attempts[0] <= now - 60:
                attempts.popleft()
            if not attempts:
                del self.failures[source]

    def _verify(self, code, source, now):
        self._prune(now)
        if len(self.failures.get(source, ())) >= 10 or sum(map(len, self.failures.values())) >= 1000:
            raise LoginLimited('For mange forsøg. Vent et minut, og prøv igen.')
        if not isinstance(code, str) or not hmac.compare_digest(code.encode('utf-8'), self.code):
            self.failures[source].append(now)
            return False
        return True

    def verify(self, code, source):
        # Compatibility for installed local launch/stop tools. Browser code
        # uses sessions; failed bearer attempts share the login rate limit.
        with self.lock:
            return self._verify(code, source, time.monotonic())

    def login(self, code, source):
        with self.lock:
            now = time.monotonic()
            if not self._verify(code, source, now):
                raise PermissionError('Forkert lærerkode.')
            if len(self.sessions) >= 1024:
                del self.sessions[next(iter(self.sessions))]
            token = secrets.token_urlsafe(32)
            self.sessions[token] = now + self.lifetime
            return token

    @staticmethod
    def cookie_token(header):
        try:
            cookies = SimpleCookie(header or '')
            return cookies['print_teacher'].value if 'print_teacher' in cookies else ''
        except CookieError:
            return ''

    def session(self, header):
        with self.lock:
            now = time.monotonic()
            self._prune(now)
            return self.sessions.get(self.cookie_token(header), 0) > now

    def logout(self, header):
        with self.lock:
            self.sessions.pop(self.cookie_token(header), None)

    @classmethod
    def cookie(cls, token='', secure=False):
        return (f'print_teacher={token}; HttpOnly; SameSite=Strict; Path=/; '
                f'Max-Age={cls.lifetime if token else 0}' + ('; Secure' if secure else ''))
