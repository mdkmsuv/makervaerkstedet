"""Fælles skærmpause for elevcomputerne.

Modulet indeholder ingen HTTP-kode. ``elevhub.py`` kan derfor koble det på
lærerens cookie-session og på den eksisterende, autentificerede
``/api/status``-melding uden at give pause-funktionen en ekstra port.

Pause er en lease: en aktiv pause har altid en udløbstid og kan højst vare to
timer. Det er bevidst en separat version fra værkstedsversionen. En elevklient
skal først melde ``shown=True`` for den aktuelle version, når overlayet faktisk
er synligt. Et svar fra serveren om, at pause ønskes, er derfor ikke det samme
som en kvittering for, at eleven kan se den.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable


MAKS_PAUSE_SEKUNDER = 2 * 60 * 60
MAKS_BESKED = 280
MAKS_KVITTERINGER = 512


def _tal(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class PauseStore:
    """Thread-safe pause-state with bounded persistence and acknowledgements.

    ``data`` may be a JSON file or a directory. A directory stores the state
    as ``pause.json``. The optional clock makes expiry behaviour deterministic
    in unit tests and avoids coupling the store to ``Tilstand``'s clock.
    """

    def __init__(self, data: str | Path, *, clock: Callable[[], float] = time.time,
                 max_seconds: int = MAKS_PAUSE_SEKUNDER):
        self.clock = clock
        self.max_seconds = max(1, min(int(max_seconds), MAKS_PAUSE_SEKUNDER))
        sti = Path(data)
        self.path = sti / 'pause.json' if sti.suffix != '.json' else sti
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._acks: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._state = {
            'paused': False,
            'version': 0,
            'message': '',
            'until': None,
            'issued_at': None,
        }
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(raw, dict):
            return
        with self._lock:
            version = raw.get('version')
            paused = raw.get('paused')
            until = raw.get('until')
            issued = raw.get('issued_at')
            if type(version) is int and version >= 0:
                self._state['version'] = version
            if type(paused) is bool:
                self._state['paused'] = paused
            if isinstance(raw.get('message'), str):
                self._state['message'] = raw['message'][:MAKS_BESKED]
            if until is None or _tal(until):
                self._state['until'] = until
            if issued is None or _tal(issued):
                self._state['issued_at'] = issued
            # A malformed or old active record must never become permanent.
            if self._state['paused'] and not _tal(self._state['until']):
                self._state['paused'] = False
                self._state['message'] = ''
                self._state['until'] = None
            self._expire_locked(self.clock())

    def _write_locked(self, state: dict[str, Any] | None = None) -> None:
        temp = self.path.with_suffix(self.path.suffix + '.tmp')
        payload = json.dumps(self._state if state is None else state,
                             ensure_ascii=False, sort_keys=True) + '\n'
        temp.write_text(payload, encoding='utf-8')
        temp.replace(self.path)

    def _expire_locked(self, now: float) -> bool:
        if not self._state['paused']:
            return False
        until = self._state.get('until')
        if not _tal(until) or now < until:
            return False
        self._state.update(paused=False, message='', until=None, issued_at=now)
        self._state['version'] += 1
        self._write_locked()
        return True

    def _snapshot_locked(self, now: float | None = None) -> dict[str, Any]:
        now = self.clock() if now is None else now
        self._expire_locked(now)
        state = dict(self._state)
        state['remaining'] = max(0, state['until'] - now) if state['paused'] and _tal(state['until']) else 0
        return state

    def state(self, now: float | None = None) -> dict[str, Any]:
        """Return the current public state, expiring a stale lease first."""
        with self._lock:
            return self._snapshot_locked(now)

    def set(self, paused: bool, *, message: str = '', expires_in: int | float | None = None,
            now: float | None = None) -> dict[str, Any]:
        """Set or clear pause and return the new public state.

        Active pauses default to the maximum lease. ``expires_in`` is clamped
        to that maximum, which keeps a bad tablet request from creating a
        permanent lock. Clearing pause ignores the message and expiry.
        """
        if type(paused) is not bool:
            raise ValueError('Feltet paused skal være true eller false.')
        now = self.clock() if now is None else now
        if not _tal(now):
            raise ValueError('Ugyldigt klokkeslæt.')
        if not isinstance(message, str):
            raise ValueError('Feltet message skal være tekst.')
        message = ''.join(ch for ch in message if ch in '\n\r\t' or ord(ch) >= 32)[:MAKS_BESKED]
        if paused:
            if expires_in is None:
                seconds = self.max_seconds
            elif not _tal(expires_in) or expires_in <= 0:
                raise ValueError('Feltet expires_in skal være større end nul.')
            else:
                seconds = min(float(expires_in), self.max_seconds)
            until = now + seconds
        else:
            until = None
            message = ''
        with self._lock:
            candidate = dict(self._state)
            candidate.update(paused=paused, message=message, until=until, issued_at=now)
            candidate['version'] += 1
            # Persist first. A full or unwritable disk must leave the in-memory
            # state at the last committed value so the error response cannot
            # disagree with the state served by a subsequent request.
            self._write_locked(candidate)
            self._state = candidate
            return self._snapshot_locked(now)

    def acknowledge(self, computer: str, version: int, shown: bool, *, now: float | None = None) -> bool:
        """Record a client's actual overlay state for the current version.

        Old or future versions are ignored. The return value tells the caller
        whether the acknowledgement was accepted. Entries are bounded and
        refreshed in insertion order so a classroom cannot grow this store
        without limit.
        """
        if not isinstance(computer, str) or not computer or len(computer) > 128:
            return False
        if type(version) is not int or type(shown) is not bool:
            return False
        now = self.clock() if now is None else now
        with self._lock:
            self._expire_locked(now)
            current = self._state['version']
            if version != current:
                return False
            self._acks.pop(computer, None)
            self._acks[computer] = {'version': version, 'shown': shown, 'at': now}
            while len(self._acks) > MAKS_KVITTERINGER:
                self._acks.popitem(last=False)
            return True

    def acknowledgement(self, computer: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._acks.get(computer)
            return dict(value) if value else None

    def acknowledgements(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {computer: dict(value) for computer, value in self._acks.items()}

    def for_client(self, computer: str | None = None, now: float | None = None) -> dict[str, Any]:
        """Return pause plus optional per-computer acknowledgement."""
        with self._lock:
            result = {'pause': self._snapshot_locked(now)}
            if computer is not None:
                result['pause_ack'] = self.acknowledgement(computer)
            return result

    def for_teacher(self, now: float | None = None) -> dict[str, Any]:
        with self._lock:
            return {'pause': self._snapshot_locked(now), 'acks': self.acknowledgements()}

