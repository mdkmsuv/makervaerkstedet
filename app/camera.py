"""Teacher-requested printer camera viewing; photographs never release a plate.

A1/P1 transport: https://github.com/Doridian/OpenBambuAPI/blob/main/video.md
"""
import hashlib
import io
import json
from pathlib import Path
import re
import socket
import struct
import threading
import time
import uuid

from PIL import Image

from .bambu import CertificatePins, tls_context

MAX_IMAGE = 3 * 1024 * 1024
CAPTURE_SETTLE_FRAMES = 5


def capture_jpeg(config, certificates, timeout=18):
    if config['model'] not in ('A1 mini', 'P1S'):
        raise ValueError('H2D-kameraet kræver en særskilt videoforbindelse. Kamerakontrol er ikke aktiveret på denne printer endnu.')
    deadline = time.monotonic() + timeout
    def receive(connection, size):
        data = bytearray()
        while len(data) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Kameraet svarede ikke i tide.')
            connection.settimeout(remaining)
            chunk = connection.recv(size - len(data))
            if not chunk:
                raise ConnectionError('Kameraforbindelsen blev lukket.')
            data.extend(chunk)
        return bytes(data)
    with socket.create_connection((config['host'], 6000), timeout=min(5, timeout)) as raw:
        raw.settimeout(max(.1, deadline - time.monotonic()))
        with tls_context().wrap_socket(raw, server_hostname=config['host']) as connection:
            fingerprint = hashlib.sha256(connection.getpeercert(True)).hexdigest()
            mqtt_pin = Path(certificates) / f"{config['id']}-mqtt.sha256"
            if not mqtt_pin.exists() or mqtt_pin.read_text().strip() != fingerprint:
                raise ValueError('Kameraet kunne ikke bekræftes som den tilsluttede printer.')
            CertificatePins(certificates, config['id']).check(connection, 'camera')
            password = config['access_code'].encode('ascii')
            if not 1 <= len(password) <= 32:
                raise ValueError('Printerens kamera-adgangskode kunne ikke bruges.')
            connection.sendall(struct.pack('<IIII32s32s', 64, 0x3000, 0, 0, b'bblp', password))
            jpeg = None
            for _ in range(CAPTURE_SETTLE_FRAMES):
                length, _, _, _ = struct.unpack('<IIII', receive(connection, 16))
                if not 100 <= length <= MAX_IMAGE:
                    raise ValueError('Kameraet sendte et ugyldigt billede.')
                candidate = receive(connection, length)
                if not candidate.startswith(b'\xff\xd8') or not candidate.endswith(b'\xff\xd9'):
                    raise ValueError('Hele kamerabilledet blev ikke modtaget.')
                try:
                    with Image.open(io.BytesIO(candidate)) as picture:
                        if picture.format != 'JPEG' or not 320 <= picture.width <= 4096 or not 200 <= picture.height <= 4096:
                            raise ValueError('Kamerabilledet har et ukendt format.')
                        picture.verify()
                except ValueError:
                    raise
                except (OSError, SyntaxError) as error:
                    raise ValueError('Kamerabilledet kunne ikke læses.') from error
                jpeg = candidate
    return jpeg


def validate_polygon(points):
    if not isinstance(points, list) or not 3 <= len(points) <= 8:
        raise ValueError('Markér 3–8 punkter i rækkefølge rundt langs den synlige pladekant.')
    try:
        points = [(float(p[0]), float(p[1])) for p in points]
    except (TypeError, ValueError, IndexError):
        raise ValueError('Pladens fire hjørner kunne ikke læses.') from None
    if any(not 0 <= x <= 1 or not 0 <= y <= 1 for x, y in points):
        raise ValueError('Markér det synlige pladeområde inde i billedet. Det lille godkendte blinde hjørne kan udelades.')
    cross = []
    count = len(points)
    for i in range(count):
        a, b, c = points[i], points[(i+1)%count], points[(i+2)%count]
        cross.append((b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]))
    area = abs(sum(points[i][0]*points[(i+1)%count][1]-points[(i+1)%count][0]*points[i][1] for i in range(count))) / 2
    if area < .06 or not (all(v > 0 for v in cross) or all(v < 0 for v in cross)):
        raise ValueError('Markér alle fire pladehjørner i rækkefølge. Pladen skal fylde tydeligt i billedet.')
    return points


class Cameras:
    def __init__(self, folder, queue, capture=capture_jpeg, clock=time.time):
        self.folder = Path(folder) / 'cameras'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.queue, self.capture_image, self.clock = queue, capture, clock
        self.locks, self.guard = {}, threading.Lock()

    def lock(self, pid):
        with self.guard:
            return self.locks.setdefault(pid, threading.RLock())

    def config(self, pid):
        with self.queue.lock:
            return dict(self.queue.configs[pid])

    def paths(self, pid):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', pid):
            raise ValueError('Ukendt printer.')
        self.config(pid)
        folder = self.folder / pid
        folder.mkdir(exist_ok=True)
        return folder, folder / 'camera.json'

    def load(self, pid):
        _, path = self.paths(pid)
        config = self.config(pid)
        data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        if data and data.get('serial') != config['serial']:
            raise ValueError('Kamerabillederne tilhører en anden printer.')
        return {'serial': config['serial'], **data}

    def capture(self, pid):
        with self.lock(pid):
            config = self.config(pid)
            data = self.load(pid)
            started = self.clock()
            jpeg = self.capture_image(config, self.queue.folder / 'certificates')
            folder, path = self.paths(pid)
            (folder / 'latest.tmp').write_bytes(jpeg)
            (folder / 'latest.tmp').replace(folder / 'latest.jpg')
            data['latest'] = {'token': uuid.uuid4().hex, 'captured': started,
                              'digest': hashlib.sha256(jpeg).hexdigest()}
            # Preserve archived references and private settings as inert data.
            # Only the teacher's explicit queue action can release a plate.
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(path)
            return data['latest']

    def status(self, pid):
        with self.lock(pid):
            return {'id': pid, 'latest': self.load(pid).get('latest'),
                    'message': 'Kontrollér pladen, og vælg Pladen er ryddet for at frigive den.'}

    def image(self, pid):
        with self.lock(pid):
            folder, _ = self.paths(pid)
            return (folder / 'latest.jpg').read_bytes()
