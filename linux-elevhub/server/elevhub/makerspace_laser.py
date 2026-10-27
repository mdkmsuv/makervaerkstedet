"""Read-only snapshots from explicitly configured local FLUX stations."""

import http.client
import json
import math
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait


_ID = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$')
_BACKEND = re.compile(r'^127\.0\.0\.1:([0-9]{1,5})$')
_MAX_RESPONSE = 32 * 1024
_TIMEOUT = 1.0
_CACHE_SECONDS = 2.0
_MAX_STATIONS = 8
_TOTAL_SECONDS = 2.0


def _stations(value):
    if not isinstance(value, list):
        raise ValueError('Laserstationer skal være en liste.')
    result, ids, backends = [], set(), set()
    if len(value) > _MAX_STATIONS:
        raise ValueError('Der må højst være otte laserstationer.')
    for station in value:
        if not isinstance(station, dict) or set(station) != {'id', 'backend'}:
            raise ValueError('En laserstation skal have id og backend.')
        identifier, backend = station['id'], station['backend']
        if not isinstance(identifier, str) or len(identifier) > 40 or not _ID.fullmatch(identifier):
            raise ValueError('Laserstationens id skal være en lowercase slug på højst 40 tegn.')
        match = _BACKEND.fullmatch(backend) if isinstance(backend, str) else None
        if not match or not 1 <= int(match[1]) <= 65535:
            raise ValueError('Laserstationens backend skal være 127.0.0.1:port.')
        backend = '127.0.0.1:' + str(int(match[1]))
        if identifier in ids:
            raise ValueError('Laserstationens id skal være entydigt.')
        if backend in backends:
            raise ValueError('Laserstationens id og backend skal være entydige.')
        ids.add(identifier)
        backends.add(backend)
        result.append((identifier, backend))
    return tuple(result)


def _safe_text(value):
    if not isinstance(value, str):
        return ''
    return ''.join(char for char in value if char.isprintable() and char not in '<>')[:160]


def _safe_time(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


class LaserSnapshots:
    """Poll configured loopback station overview endpoints without commands."""

    def __init__(self, stations, clock=time.time, monotonic=time.monotonic):
        self.stations = _stations(stations)
        self.clock, self.monotonic = clock, monotonic
        self._lock = threading.Lock()
        self._cached = None
        self._cached_until = 0.0
        self._executor = None
        self._inflight = {}

    @staticmethod
    def _unknown(identifier, observed_at):
        return {'id': 'flux:' + identifier, 'kind': 'laser', 'adapter': 'flux',
                'source_id': identifier, 'name': '', 'model': '', 'status': 'unknown',
                'observed_at': observed_at, 'online': False, 'supported': False,
                'software': {'beam_studio_configured': False, 'flux_configured': False},
                'capabilities': {'read': ['status', 'model', 'connection']}}

    def _one(self, identifier, backend, observed_at):
        row = self._unknown(identifier, None)
        host, _, port = backend.rpartition(':')
        connection = http.client.HTTPConnection(host, int(port), timeout=_TIMEOUT)
        deadline = time.monotonic() + _TIMEOUT
        response_socket = None
        watchdog = None
        try:
            connection.request('GET', '/api/station/overview', headers={'Accept': 'application/json'})
            response_socket = connection.sock
            if response_socket is not None:
                response_socket.settimeout(max(0.001, deadline - time.monotonic()))
                def expire():
                    try:
                        response_socket.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                watchdog = threading.Timer(max(0, deadline - time.monotonic()), expire)
                watchdog.daemon = True
                watchdog.start()
            response = connection.getresponse()
            if response.status != 200:
                return row
            raw = getattr(getattr(response, 'fp', None), 'raw', None)
            response_socket = getattr(raw, '_sock', None) or response_socket
            pieces, total = [], 0
            while total <= _MAX_RESPONSE:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return row
                if response_socket is not None:
                    try:
                        response_socket.settimeout(remaining)
                    except OSError:
                        pass
                chunk = response.read1(min(8192, _MAX_RESPONSE + 1 - total))
                if not chunk:
                    break
                pieces.append(chunk)
                total += len(chunk)
            payload = b''.join(pieces)
            if len(payload) > _MAX_RESPONSE:
                return row
            data = json.loads(payload)
            if not isinstance(data, dict) or data.get('status') != 'ok' or not isinstance(data.get('items'), list):
                return row
            item = next((item for item in data['items'] if isinstance(item, dict) and item.get('id') == identifier), None)
            if not isinstance(item, dict):
                return row
            software = item.get('software')
            if not isinstance(software, dict):
                software = {}
            row.update(name=_safe_text(item.get('name')), model=_safe_text(item.get('model')),
                       supported=_safe_text(item.get('model')) in {'fbb2', 'fhexa1'},
                       observed_at=observed_at,
                       software={'beam_studio_configured': software.get('beam_studio_configured') is True,
                                 'flux_configured': software.get('flux_configured') is True})
            return row
        except (OSError, ValueError, TypeError, http.client.HTTPException, UnicodeError):
            return row
        finally:
            if watchdog is not None:
                watchdog.cancel()
            connection.close()

    def snapshot(self):
        now = self.monotonic()
        if self._cached is not None and now < self._cached_until:
            return self._cached
        if not self.stations:
            result = {'status': 'not_configured', 'checked_at': _safe_time(self.clock()), 'items': []}
            self._cached, self._cached_until = result, self.monotonic() + _CACHE_SECONDS
            return result
        if not self._lock.acquire(blocking=False):
            return {'status': 'unknown', 'checked_at': _safe_time(self.clock()),
                    'items': [self._unknown(identifier, None) for identifier, _ in self.stations]}
        try:
            now = self.monotonic()
            if self._cached is not None and now < self._cached_until:
                return self._cached
            if self._inflight:
                if not all(future.done() for future in self._inflight):
                    return {'status': 'unknown', 'checked_at': _safe_time(self.clock()),
                            'items': [self._unknown(identifier, None) for identifier, _ in self.stations]}
                old_executor = self._executor
                self._inflight = {}
                self._executor = None
                if old_executor is not None:
                    old_executor.shutdown(wait=False, cancel_futures=True)
            checked_at = _safe_time(self.clock())
            items = [self._unknown(identifier, None) for identifier, _ in self.stations]
            pool = ThreadPoolExecutor(max_workers=min(_MAX_STATIONS, len(self.stations)))
            futures = {}
            self._executor = pool
            try:
                futures = {pool.submit(self._one, identifier, backend, checked_at): index
                           for index, (identifier, backend) in enumerate(self.stations)}
                self._inflight = futures
                done, _ = wait(futures, timeout=_TOTAL_SECONDS)
                for future in done:
                    try:
                        items[futures[future]] = future.result()
                    except Exception:
                        pass
            finally:
                for future in futures:
                    future.cancel()
                if futures and all(future.done() for future in futures):
                    self._inflight = {}
                    self._executor = None
                    pool.shutdown(wait=False, cancel_futures=True)
            result = {'status': 'ok' if any(item['observed_at'] is not None for item in items) else 'unknown',
                      'checked_at': checked_at, 'items': items}
            self._cached, self._cached_until = result, self.monotonic() + _CACHE_SECONDS
            return result
        finally:
            self._lock.release()
