"""Prepared FLUXGhost upload adapter. Disabled until commissioned on the actual laser.

Uses only authentication, deviceinfo, play report, file upload and file md5.
No start, select, movement, homing, firmware, settings or arbitrary command API.
The existing simulated FLUXGhost compiler remains separate from this adapter.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time

from app.laser_ghost import WebSocket, WebSocketClosed


class TransferError(ValueError):
    pass


class _ManagedGhost:
    """One private FLUXGhost process for one probe or transfer, then terminate it."""

    def __init__(self, executable):
        self.executable = Path(executable)
        self.process = None

    def start(self):
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        try:
            self.process = subprocess.Popen(
                [str(self.executable), '--ip', '127.0.0.1', '--port', str(port),
                 '--trace-pid', str(os.getpid())],
                cwd=str(self.executable.parent), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=flags, close_fds=True,
            )
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise TransferError('Det private FLUX-program stoppede under opstart.')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=0.25):
                        return port
                except OSError:
                    time.sleep(0.1)
            raise TransferError('Det private FLUX-program startede ikke i tide.')
        except BaseException:
            self.stop()
            raise

    def stop(self):
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                return
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=3)
                except OSError:
                    pass


class FluxTransfer:
    """Connect to an explicitly configured local FLUXGhost after on-site commissioning.

    Configuration is local, never supplied by an HTTP caller: enabled,
    commissioned, device_uuid (32 hex), model, and either an existing private
    port or managed_ghost=true + flux_api. Key source is an explicit PEM file,
    or client_key_source='beam_studio' from the same user's Electron Store.
    An already trusted client RSA key is used only over loopback, never logged.
    This preparation does not install a live daemon or enroll a new device key.
    """
    def __init__(self, config=None, websocket_factory=WebSocket):
        self.config = config if isinstance(config, dict) else {}
        self.factory = websocket_factory
        self.lock = threading.Lock()

    def status(self):
        config = self.config
        ready = (self._configured()
                 and config.get('enabled') is True and config.get('commissioned') is True)
        return {'available': bool(ready), 'physicalStart': False,
                'message': 'Overførsel er konfigureret. Forbindelsen kontrolleres ved afsendelse.' if ready else
                'Direkte overførsel afventer opsætning og kontrol ved laseren.'}

    def _store_path(self):
        configured = self.config.get('beam_studio_store')
        if configured is not None:
            return Path(configured) if isinstance(configured, str) and configured else None
        appdata = os.environ.get('APPDATA')
        return Path(appdata) / 'Beam Studio' / 'config.json' if appdata else None

    def _configured(self):
        config = self.config
        managed = config.get('managed_ghost') is True
        endpoint_ready = (isinstance(config.get('flux_api'), str) and Path(config['flux_api']).is_file()) if managed else (
            type(config.get('port')) is int and 1 <= config['port'] <= 65535)
        source = config.get('client_key_source', 'file')
        key_ready = ((isinstance(config.get('client_key_file'), str)
                      and Path(config['client_key_file']).is_file()) if source == 'file' else
                     (self._store_path() is not None and self._store_path().is_file()) if source == 'beam_studio' else False)
        return (endpoint_ready and key_ready
                 and isinstance(config.get('device_uuid'), str)
                 and re.fullmatch(r'[a-f0-9]{32}', config['device_uuid']) is not None
                 and config.get('model') in ('fbb2', 'fhexa1'))

    def _key(self):
        try:
            if self.config.get('client_key_source', 'file') == 'beam_studio':
                path = self._store_path()
                if path is None or path.stat().st_size > 2_000_000:
                    raise TransferError('Beam Studios lokale nøglelager er ikke tilgængeligt.')
                value = json.loads(path.read_text(encoding='utf-8')).get('flux-rsa-key')
            else:
                path = Path(self.config['client_key_file'])
                if path.stat().st_size > 16_384:
                    raise TransferError('Lasernøglen har et ugyldigt format.')
                value = path.read_text(encoding='ascii')
        except (OSError, UnicodeError, ValueError, AttributeError, TypeError, KeyError):
            raise TransferError('Den allerede parrede lokale lasernøgle kunne ikke læses.') from None
        if (not isinstance(value, str) or len(value) > 16_384 or
                not value.startswith(('-----BEGIN RSA PRIVATE KEY-----', '-----BEGIN PRIVATE KEY-----'))):
            raise TransferError('En godkendt lokal RSA-lasernøgle mangler.')
        return value

    def _open(self):
        ghost = _ManagedGhost(self.config['flux_api']) if self.config.get('managed_ghost') is True else None
        try:
            port = ghost.start() if ghost else self.config['port']
            if ghost:
                self._discover(port)
            ws = self.factory('127.0.0.1', port, '/ws/control/' + self.config['device_uuid'], timeout=10)
            return ws, ghost
        except BaseException:
            if ghost:
                ghost.stop()
            raise

    def _discover(self, port):
        """Wait for the exact UUID in FLUXGhost's read-only discovery inventory."""
        ws = self.factory('127.0.0.1', port, '/ws/discover', timeout=5)
        try:
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                if hasattr(ws, 'sock'):
                    ws.sock.settimeout(min(2, max(0.1, deadline - time.monotonic())))
                try:
                    kind, raw = ws.recv()
                except socket.timeout:
                    continue
                if kind != 'text':
                    raise TransferError('FLUX discovery svarede i et uventet format.')
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise TransferError('FLUX discovery svarede i et uventet format.')
                found = str(message.get('uuid', '')).replace('-', '').lower()
                if found == self.config['device_uuid']:
                    return
            raise TransferError('Den godkendte laser blev ikke fundet af det private FLUX-program.')
        finally:
            ws.close()

    def _check(self, ws, key, deadline):
        ws.send_text(key)
        self._read(ws, {'connected'}, deadline)
        ws.send_text('deviceinfo')
        info = self._read(ws, {'ok'}, deadline)
        actual_uuid = str(info.get('uuid', '')).replace('-', '').lower()
        if actual_uuid != self.config['device_uuid'] or info.get('model') != self.config['model']:
            raise TransferError('Den tilsluttede laser matcher ikke den godkendte maskine.')
        ws.send_text('play report')
        state = self._read(ws, {'ok'}, deadline).get('device_status')
        if not isinstance(state, dict) or type(state.get('st_id')) is not int:
            raise TransferError('Laseren gav ingen gyldig status.')
        return {'model': self.config['model'], 'uuid': actual_uuid, 'idle': state['st_id'] == 0}

    def probe(self):
        """Read-only identity and idle-state check; works before commissioning."""
        if not self._configured():
            raise TransferError('Direkte overførsel mangler lokal opsætning.')
        if not self.lock.acquire(blocking=False):
            raise TransferError('En kontrol eller overførsel er allerede i gang.')
        ws = ghost = None
        try:
            key = self._key()
            ws, ghost = self._open()
            return self._check(ws, key, time.monotonic() + 30)
        except TransferError:
            raise
        except (OSError, WebSocketClosed, ValueError) as error:
            raise TransferError('Laserens identitet og status kunne ikke kontrolleres '
                                f'({type(error).__name__}).') from None
        finally:
            if ws is not None:
                try:
                    ws.close()
                except OSError:
                    pass
            if ghost is not None:
                ghost.stop()
            self.lock.release()

    @staticmethod
    def _read(ws, expected, deadline):
        for _ in range(1024):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TransferError('Laseren svarede ikke i tide. Kontrollér maskinen før et nyt forsøg.')
            # Bound the whole exchange, not just an individual receive.
            if hasattr(ws, 'sock'):
                ws.sock.settimeout(min(10, remaining))
            response = ws.recv_json_or_bytes()
            if not isinstance(response, dict):
                raise TransferError('Laseren svarede i et uventet format.')
            status = response.get('status')
            if status in ('error', 'fatal', 'Error'):
                symbol = response.get('error')
                suffix = f' ({symbol})' if isinstance(symbol, str) and re.fullmatch(r'[A-Z0-9_]{1,50}', symbol) else ''
                raise TransferError('Laseren afviste forbindelsen eller overførslen' + suffix +
                                    '. Kontrollér maskinens skærm.')
            if status in expected:
                return response
            if status not in ('connecting', 'uploading', 'progress', 'pong'):
                raise TransferError('FLUX svarede i en uventet rækkefølge.')
        raise TransferError('For mange mellemsvar fra FLUX.')

    def upload(self, content, *, job_id, model='fbb2'):
        if not self.status()['available']:
            raise TransferError(self.status()['message'])
        if model != self.config['model']:
            raise TransferError('Laserfilens model passer ikke til den konfigurerede maskine.')
        if not isinstance(content, bytes) or not 1 <= len(content) <= 64 * 1024 * 1024:
            raise TransferError('Laserfilen mangler eller er for stor.')
        if not isinstance(job_id, str) or not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise TransferError('Overførslens nummer er ugyldigt.')
        if not self.lock.acquire(blocking=False):
            raise TransferError('En overførsel er allerede i gang.')
        ws = ghost = None
        upload_started = False
        path = f'/SD/laserstation-{job_id}.fc'
        deadline = time.monotonic() + 120
        try:
            key = self._key()
            ws, ghost = self._open()
            check = self._check(ws, key, deadline)
            if not check['idle']:
                raise TransferError('Laseren er ikke i tomgang. Afslut den aktuelle opgave ved maskinen først.')
            ws.send_text(f'file upload application/fcode {len(content)} {path}')
            self._read(ws, {'continue'}, deadline)
            upload_started = True
            for offset in range(0, len(content), 128 * 1024):
                if time.monotonic() >= deadline:
                    raise TransferError('Overførslen tog for lang tid.')
                ws.send_binary(content[offset:offset + 128 * 1024])
            self._read(ws, {'ok'}, deadline)
            ws.send_text('file md5 ' + path)
            result = self._read(ws, {'ok'}, deadline)
            # MD5 is the machine's transfer-integrity API, not an authentication hash.
            checksum = result.get('md5')
            if not isinstance(checksum, str) or checksum.lower() != hashlib.md5(content, usedforsecurity=False).hexdigest():
                raise TransferError('Laseren kunne ikke bekræfte hele filen.')
            return {'path': path, 'bytes': len(content), 'verified': True, 'started': False}
        except (OSError, WebSocketClosed, ValueError) as error:
            if upload_started:
                raise TransferError('Overførslen kunne ikke bekræftes. Filen kan ligge på laseren; '
                                    'kontrollér maskinens skærm før et nyt forsøg. Laseren er ikke startet herfra.') from None
            if isinstance(error, TransferError):
                raise
            raise TransferError('Forbindelsen til laseren kunne ikke oprettes. Ingen startkommando er sendt.') from None
        finally:
            if ws is not None:
                try:
                    ws.close()
                except OSError:
                    pass
            if ghost is not None:
                ghost.stop()
            self.lock.release()
