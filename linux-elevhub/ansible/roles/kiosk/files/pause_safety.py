"""Independent fail-open watchdog for the X11 pause owner.

Only the toolbar process is disposable: editors and Chromium have separate
processes. A pidfd prevents a recycled PID from ever targeting another app.
The small ledger prevents a restarted toolbar from repeating a failed pause.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import time


def safety_clock():
    # Unlike monotonic, Linux BOOTTIME includes time spent suspended.
    if hasattr(time, 'CLOCK_BOOTTIME'):
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    return time.monotonic()


def _write(path, record):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            os.chmod(temporary, 0o600)
            json.dump(record, stream)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class PauseSafety:
    def __init__(self, path=None, *, heartbeat_timeout=15, grace=2):
        display = re.sub(r'[^a-zA-Z0-9_-]', '_', os.environ.get('DISPLAY', 'default'))
        self.path = Path(path) if path is not None else (
            Path.home() / '.cache' / 'elevhub' / f'pause-safety-{display}.json')
        self.heartbeat_timeout, self.grace = heartbeat_timeout, grace
        self.process = None
        self.record = None
        self._last_ack = None

    def arm(self, key, deadline):
        """Return the persisted deadline, or None if safe locking is unavailable."""
        self.stop()
        try:
            boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            previous = json.loads(self.path.read_text()) if self.path.exists() else {}
            if previous.get('key') == key:
                if previous.get('blocked'):
                    return None
                if previous.get('boot') == boot:
                    deadline = min(deadline, float(previous['deadline']))
            if not math.isfinite(deadline) or deadline <= safety_clock():
                return None
            # Persist the crash marker BEFORE acquiring any X11 grab. Clearing
            # it requires a normal release, so a full disk at crash time cannot
            # cause the kiosk supervisor to repeat the same failed pause.
            self.record = {'key': key, 'boot': boot, 'deadline': deadline,
                           'blocked': True, 'reason': 'armed'}
            _write(self.path, self.record)
            pidfd = os.pidfd_open(os.getpid())
            try:
                self.process = subprocess.Popen(
                    [sys.executable, '-u', str(Path(__file__).resolve()), '--guard',
                     str(pidfd), str(self.path), json.dumps(self.record),
                     str(self.heartbeat_timeout), str(self.grace)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, pass_fds=(pidfd,), start_new_session=True)
            finally:
                os.close(pidfd)
            ready, _, _ = select.select([self.process.stdout], [], [], 2)
            if not ready or os.read(self.process.stdout.fileno(), 1) != b'R':
                raise OSError('watchdog did not become ready')
            os.set_blocking(self.process.stdin.fileno(), False)
            os.set_blocking(self.process.stdout.fileno(), False)
            self._last_ack = safety_clock()
            if not self.heartbeat():
                raise OSError('watchdog did not accept heartbeat')
            return deadline
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self.block()
            self.stop()
            return None

    def heartbeat(self):
        if self.process is None or self.process.poll() is not None:
            return False
        try:
            os.write(self.process.stdin.fileno(), b'.')
            try:
                acknowledgement = os.read(self.process.stdout.fileno(), 4096)
                if acknowledgement:
                    self._last_ack = safety_clock()
                elif acknowledgement == b'':
                    return False
            except BlockingIOError:
                pass
            return safety_clock() - self._last_ack < 3
        except (OSError, ValueError):
            return False

    def block(self):
        if self.record is not None:
            self.record['blocked'] = True
            self.record['reason'] = 'watchdog-unavailable'
            try:
                _write(self.path, self.record)
            except OSError:
                pass

    def stop(self):
        process, self.process = self.process, None
        if process is not None:
            # Call only AFTER the X11 grab/window has been released. Terminating
            # the guard is deliberately not a command to terminate its owner.
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
            process.stdin.close()
            process.stdout.close()
            if self.record is not None and self.record.get('reason') == 'armed':
                self.record['blocked'] = False
                self.record['reason'] = 'released'
                try:
                    _write(self.path, self.record)
                except OSError:
                    pass  # Leave the crash marker in place: fail open on restart.


def _guard(pidfd, path, record, heartbeat_timeout, grace):
    # Refuse to announce readiness on a runtime lacking the safe signal API.
    if not callable(getattr(signal, 'pidfd_send_signal', None)):
        return
    try:
        signal.pidfd_send_signal(pidfd, 0)  # Check permission before READY/grab.
    except OSError:
        return
    deadline = record['deadline']
    last = safety_clock()
    reason = 'owner-pipe-closed'
    os.write(1, b'R')
    while True:
        now = safety_clock()
        if now >= deadline + grace:
            reason = 'deadline'
            break
        if now - last >= heartbeat_timeout:
            reason = 'event-loop-timeout'
            break
        readable, _, _ = select.select([0, pidfd], [], [], min(
            .5, max(0, deadline + grace - now), max(0, heartbeat_timeout - (now - last))))
        if pidfd in readable:
            return  # The original owner exited; never target another PID.
        if 0 in readable:
            data = os.read(0, 4096)
            if not data:
                break
            last = safety_clock()
            try:
                os.write(1, b'.')
            except BrokenPipeError:
                break
    record['blocked'] = True
    record['reason'] = reason
    try:
        _write(path, record)
    except OSError:
        pass
    try:
        signal.pidfd_send_signal(pidfd, signal.SIGKILL)
    except ProcessLookupError:
        pass


if __name__ == '__main__' and len(sys.argv) == 7 and sys.argv[1] == '--guard':
    _guard(int(sys.argv[2]), Path(sys.argv[3]), json.loads(sys.argv[4]),
           float(sys.argv[5]), float(sys.argv[6]))
