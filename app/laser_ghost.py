"""FLUX's own backend (FLUXGhost, bundled with Beam Studio) for the laser station.

A private copy is started on a free loopback port with --simulate. It is only used
to turn a plate into a Beambox II task and read FLUX's time estimate and extent.
No machine routes (discover, control, camera) are used here.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time

DEFAULT_EXECUTABLE = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Beam Studio' / 'resources' / 'backend' / 'flux_api' / 'flux_api.exe'
# 1 x 1 PNG. FLUXGhost requires a thumbnail in front of the document.
THUMBNAIL = b'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=='
GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'


class WebSocketClosed(Exception):
    pass


class WebSocket:
    """Minimal RFC 6455 client: text and binary messages, ping/pong and close."""

    def __init__(self, host, port, path, timeout=30):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode('ascii')
        request = (f'GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                   f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n')
        self.sock.sendall(request.encode('ascii'))
        data = b''
        while b'\r\n\r\n' not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WebSocketClosed('Forbindelsen lukkede under opstart.')
            data += chunk
            if len(data) > 65536:
                raise WebSocketClosed('Ugyldigt svar fra FLUX.')
        head, self.buffer = data.split(b'\r\n\r\n', 1)
        lines = head.decode('latin-1').split('\r\n')
        if ' 101 ' not in lines[0] + ' ':
            raise WebSocketClosed(f'FLUX afviste forbindelsen: {lines[0]}')
        headers = {k.strip().lower(): v.strip() for k, v in (line.split(':', 1) for line in lines[1:] if ':' in line)}
        accept = base64.b64encode(hashlib.sha1((key + GUID).encode('ascii')).digest()).decode('ascii')
        if headers.get('sec-websocket-accept') != accept:
            raise WebSocketClosed('FLUX svarede med en forkert nøgle.')

    def _read_exact(self, n):
        while len(self.buffer) < n:
            chunk = self.sock.recv(max(65536, n - len(self.buffer)))
            if not chunk:
                raise WebSocketClosed('FLUX lukkede forbindelsen.')
            self.buffer += chunk
        data, self.buffer = self.buffer[:n], self.buffer[n:]
        return data

    def _send_frame(self, opcode, payload):
        length = len(payload)
        header = bytearray([0x80 | opcode])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += length.to_bytes(2, 'big')
        else:
            header.append(0x80 | 127)
            header += length.to_bytes(8, 'big')
        mask = os.urandom(4)
        if length:
            repeated = (mask * (length // 4 + 1))[:length]
            masked = (int.from_bytes(payload, 'big') ^ int.from_bytes(repeated, 'big')).to_bytes(length, 'big')
        else:
            masked = b''
        self.sock.sendall(bytes(header) + mask + masked)

    def send_text(self, text):
        self._send_frame(0x1, text.encode('utf-8'))

    def send_binary(self, data):
        self._send_frame(0x2, bytes(data))

    def recv(self):
        """Returns ('text', str) or ('binary', bytes)."""
        message, kind = b'', None
        while True:
            first, second = self._read_exact(2)
            fin, opcode = first & 0x80, first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = int.from_bytes(self._read_exact(2), 'big')
            elif length == 127:
                length = int.from_bytes(self._read_exact(8), 'big')
            if length > 256 * 1024 * 1024:
                raise WebSocketClosed('For stort svar fra FLUX.')
            mask = self._read_exact(4) if second & 0x80 else None
            payload = self._read_exact(length)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise WebSocketClosed('FLUX lukkede forbindelsen.')
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x2):
                kind = 'text' if opcode == 0x1 else 'binary'
                message = payload
            elif opcode == 0x0:
                message += payload
            if fin and kind:
                return kind, message.decode('utf-8') if kind == 'text' else message

    def recv_json_or_bytes(self):
        kind, data = self.recv()
        return json.loads(data) if kind == 'text' else data

    def close(self):
        try:
            self._send_frame(0x8, b'')
        except OSError:
            pass
        self.sock.close()


class FluxGhostError(Exception):
    pass


class FluxGhost:
    """A private FLUXGhost process, started when first needed and stopped with stop()."""

    def __init__(self, executable=None, workdir=None):
        self.executable = Path(executable) if executable else DEFAULT_EXECUTABLE
        self.workdir = Path(workdir) if workdir else None
        self.process = None
        self.port = None
        self.lock = threading.Lock()

    def available(self):
        return self.executable.is_file()

    def _start(self):
        if self.process and self.process.poll() is None:
            return
        if not self.available():
            raise FluxGhostError('Beam Studio blev ikke fundet på denne computer.')
        probe = socket.socket()
        probe.bind(('127.0.0.1', 0))
        self.port = probe.getsockname()[1]
        probe.close()
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        cwd = self.workdir or self.executable.parent
        if self.workdir:
            self.workdir.mkdir(parents=True, exist_ok=True)
        self.process = subprocess.Popen(
            # --trace-pid: FLUXGhost exits by itself if the station process disappears.
            [str(self.executable), '--ip', '127.0.0.1', '--port', str(self.port), '--simulate', '--trace-pid', str(os.getpid())],
            cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise FluxGhostError('FLUX-programmet stoppede under opstart.')
            try:
                socket.create_connection(('127.0.0.1', self.port), timeout=0.5).close()
                return
            except OSError:
                time.sleep(0.25)
        self.stop()
        raise FluxGhostError('FLUX-programmet startede ikke.')

    def stop(self):
        process, self.process = self.process, None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    def _connect(self, route):
        return WebSocket('127.0.0.1', self.port, f'/ws/{route}', timeout=120)

    def version(self):
        with self.lock:
            self._start()
            ws = self._connect('ver')
            try:
                return ws.recv_json_or_bytes()
            finally:
                ws.close()

    def task(self, document, model='fbb2', progress=None):
        """Task from a Beam Studio document for an explicitly supported model.

        Returns {'seconds', 'metadata', 'bytes', 'fcode'}. Raises FluxGhostError.
        """
        from app.laser_machines import get_machine
        machine = get_machine(model)  # Refuse unknown model before starting FLUXGhost.
        data = THUMBNAIL + document.encode('utf-8')
        with self.lock:
            self._start()
            ws = self._connect('svgeditor-laser-parser')
            try:
                ws.send_text(f'svgeditor_upload plate {len(data)} {len(THUMBNAIL)} -{model} -model {model} -mdpi')
                while True:
                    message = ws.recv_json_or_bytes()
                    status = message.get('status') if isinstance(message, dict) else None
                    if status == 'continue':
                        for start in range(0, len(data), 128 * 1024):
                            ws.send_binary(data[start:start + 128 * 1024])
                    elif status == 'ok':
                        break
                    elif status in ('Error', 'error', 'fatal'):
                        raise FluxGhostError(f'FLUX kunne ikke læse pladen: {message.get("message", "")}')
                    elif status == 'computing' and progress:
                        progress(message)
                ws.send_text('go  -f ' + machine['compilerArgs'])
                chunks, total, expected, result = [], 0, None, None
                while expected is None or total < expected:
                    message = ws.recv_json_or_bytes()
                    if isinstance(message, bytes):
                        chunks.append(message)
                        total += len(message)
                        continue
                    status = message.get('status')
                    if status == 'complete':
                        expected = int(message.get('length', 0))
                        result = message
                    elif status in ('Error', 'error', 'fatal'):
                        raise FluxGhostError(f'FLUX kunne ikke lave laserjobbet: {message.get("message", "")}')
                    elif status == 'computing' and progress:
                        progress(message)
                fcode = b''.join(chunks)
                return {'seconds': float(result.get('time', 0)), 'metadata': result.get('metadata') or {},
                        'bytes': len(fcode), 'fcode': fcode}
            except (OSError, WebSocketClosed, ValueError) as error:
                self.stop()
                raise FluxGhostError(f'Forbindelsen til FLUX-programmet fejlede: {error}') from None
            finally:
                ws.close()
