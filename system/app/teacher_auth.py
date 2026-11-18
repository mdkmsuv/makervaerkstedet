"""Bounded teacher sessions and login attempts for the print service."""
from collections import defaultdict, deque
import hashlib
import hmac
from http.cookies import CookieError, SimpleCookie
import json
import os
from pathlib import Path
import secrets
import threading
import time


class LoginLimited(Exception):
    retry_after = 60


def shared_teacher_code_file(folder):
    """Where the control panel keeps the teacher code on a school server.

    A freshly started school service can precede the hub's first setup.
    Standalone installations continue to use their own code.
    """
    root = Path(folder).resolve().parent
    if (os.environ.get('ELEVHUB_SCHOOL_MODE') == '1' or (root / 'school.json').exists()
            or (root / 'hub').is_dir()):
        return root / 'hub' / 'laerer.json'
    return None


def shared_teacher_code_matches(path, code):
    """True when ``code`` is the teacher code chosen in the control panel.

    One code, chosen once on the server, opens the control panel, the print
    service and the laser queue. Only the control panel's salted hash is read;
    the code itself is never stored here. An absent, unreadable or malformed
    school verifier leaves teacher login closed until the panel is ready.
    """
    try:
        if not isinstance(code, str) or len(code) < 8:
            return False
        path = Path(path)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2048:
            return False
        stored = json.loads(path.read_text(encoding='utf-8'))
        salt, iterations, expected = stored['salt'], stored['iterationer'], stored['hash']
        if (set(stored) != {'salt', 'iterationer', 'hash'} or type(iterations) is not int
                or not 100_000 <= iterations <= 2_000_000
                or not isinstance(salt, str) or len(salt) != 32
                or not isinstance(expected, str) or len(expected) != 64):
            return False
        computed = hashlib.pbkdf2_hmac('sha256', code.encode(), bytes.fromhex(salt), iterations)
        return hmac.compare_digest(computed.hex(), expected)
    except (OSError, ValueError, TypeError, KeyError):
        return False


def teacher_login_matches(shared_file, code, local_code):
    if shared_file is not None:
        return shared_teacher_code_matches(shared_file, code)
    return isinstance(code, str) and hmac.compare_digest(code.encode('utf-8'), local_code.encode('utf-8'))


class TeacherAuth:
    lifetime = 8 * 3600

    def __init__(self, code, shared_file=None):
        self.code = code
        self.shared_file = shared_file
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

    def _verify(self, code, source, now, shared_file=None):
        self._prune(now)
        if len(self.failures.get(source, ())) >= 10 or sum(map(len, self.failures.values())) >= 1000:
            raise LoginLimited('For mange forsøg. Vent et minut, og prøv igen.')
        if not teacher_login_matches(shared_file, code, self.code):
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
            if not self._verify(code, source, now, self.shared_file):
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
