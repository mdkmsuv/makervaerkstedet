"""A reviewed camera photograph and material polygons. Never controls a machine."""
import base64
import binascii
import copy
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import math
from pathlib import Path
import re
import secrets
import threading
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError
from shapely.geometry import Polygon

from app.laser_material import normalize_regions

MAX_IMAGE_BYTES = 8_000_000
MAX_IMAGE_PIXELS = 24_000_000
PHOTO_SIZE = (1200, 750)
BED = (600.0, 375.0)
_UNSET = object()


def number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} skal være et endeligt tal.')
    return float(value)


def _solve(matrix, values):
    rows = [[float(x) for x in row] + [float(value)] for row, value in zip(matrix, values)]
    for column in range(len(rows)):
        pivot = max(range(column, len(rows)), key=lambda row: abs(rows[row][column]))
        if abs(rows[pivot][column]) < 1e-9:
            raise ValueError('De fire hjørner kan ikke bruges til at rette billedet op.')
        rows[column], rows[pivot] = rows[pivot], rows[column]
        scale = rows[column][column]
        rows[column] = [value / scale for value in rows[column]]
        for row in range(len(rows)):
            if row != column:
                scale = rows[row][column]
                rows[row] = [a - scale * b for a, b in zip(rows[row], rows[column])]
    return [row[-1] for row in rows]


def rectify_image(data, bed=BED):
    """The teacher identifies the four bed corners at the material surface.

    Perspective correction is not lens calibration or a claim of cutting accuracy.
    Final camera alignment is still checked in Beam Studio.
    """
    if data.get('confirmed') is not True:
        raise ValueError('Bekræft billedets hjørner og materialets højde først.')
    height = number(data.get('heightMm'), 'Materialehøjden')
    if not 0 <= height <= 100:
        raise ValueError('Angiv materialehøjden mellem 0 og 100 mm.')
    image_url = data.get('image')
    if not isinstance(image_url, str) or len(image_url) > MAX_IMAGE_BYTES * 4 // 3 + 100:
        raise ValueError('Billedet er for stort. Brug et PNG- eller JPEG-billede under 8 MB.')
    match = re.fullmatch(r'data:image/(png|jpeg);base64,([A-Za-z0-9+/=\r\n]+)', image_url)
    if not match:
        raise ValueError('Brug et PNG- eller JPEG-billede.')
    try:
        content = base64.b64decode(match[2], validate=True)
        if len(content) > MAX_IMAGE_BYTES:
            raise ValueError('Billedet er for stort.')
        with Image.open(BytesIO(content)) as original:
            if original.format not in ('PNG', 'JPEG') or getattr(original, 'n_frames', 1) != 1:
                raise ValueError('Brug et enkelt PNG- eller JPEG-billede.')
            w, h = original.size
            if min(w, h) < 64 or max(w, h) > 8000 or w * h > MAX_IMAGE_PIXELS:
                raise ValueError('Billedets opløsning skal være mellem 64 og 8000 pixels og højst 24 megapixels.')
            photo = ImageOps.exif_transpose(original).convert('RGB')
    except (binascii.Error, UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError('Billedet kunne ikke læses som PNG eller JPEG.') from error
    w, h = photo.size
    corners = data.get('corners')
    if not isinstance(corners, list) or len(corners) != 4:
        raise ValueError('Markér fire hjørner: øverst venstre, øverst højre, nederst højre, nederst venstre.')
    points = []
    for point in corners:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError('Et billedhjørne er ugyldigt.')
        x, y = number(point[0], 'Hjørnet'), number(point[1], 'Hjørnet')
        if not 0 <= x <= w or not 0 <= y <= h:
            raise ValueError('Alle fire hjørner skal ligge inde i billedet.')
        points.append((x, y))
    shape = Polygon(points)
    signed_area = sum(x * points[(i + 1) % 4][1] - points[(i + 1) % 4][0] * y for i, (x, y) in enumerate(points))
    if not shape.is_valid or shape.area < w * h * 0.05 or signed_area <= 0 or not shape.equals(shape.convex_hull):
        raise ValueError('Markér et stort, sammenhængende firkantet område med hjørnerne i den viste rækkefølge.')
    if any(math.dist(points[i], points[(i + 1) % 4]) < 10 for i in range(4)):
        raise ValueError('Billedhjørnerne ligger for tæt sammen.')
    pw, ph = PHOTO_SIZE[0], round(PHOTO_SIZE[0] * bed[1] / bed[0])
    destination = ((0, 0), (pw, 0), (pw, ph), (0, ph))
    matrix, values = [], []
    for (u, v), (x, y) in zip(destination, points):
        matrix.extend(([u, v, 1, 0, 0, 0, -x * u, -x * v], [0, 0, 0, u, v, 1, -y * u, -y * v]))
        values.extend((x, y))
    coefficients = _solve(matrix, values)
    result = photo.transform((pw, ph), Image.Transform.PERSPECTIVE, coefficients, Image.Resampling.BICUBIC)
    return result, height, points


class MaterialScene:
    def __init__(self, folder, bed=BED):
        self.folder = Path(folder)
        if len(bed) != 2 or any(number(v, 'Arbejdsområdet') <= 0 or v > 2000 for v in bed):
            raise ValueError('Arbejdsområdets mål er ugyldige.')
        self.bed = tuple(bed)
        self.lock = threading.RLock()
        self.current = None
        self._approvals = {}

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.current)

    def _match(self, scene_id, revision=_UNSET):
        if not self.current or scene_id != self.current['id']:
            raise ValueError('Billedet er ændret. Hent det nyeste billede og kontrollér placeringen igen.')
        if revision is not _UNSET and (type(revision) is not int or revision != self.current['revision']):
            raise ValueError('Materialeområderne er ændret. Kontrollér den nyeste version igen.')
        return self.current

    def require(self, data, approved=False):
        with self.lock:
            if self.current is None:
                if data.get('sceneId') is not None:
                    raise ValueError('Materialebilledet er fjernet. Vælg det aktuelle billede igen.')
                return None
            if 'sceneRevision' not in data:
                raise ValueError('Materialebilledets version mangler. Genindlæs siden.')
            scene = self._match(data.get('sceneId'), data.get('sceneRevision'))
            if approved and not scene['approved']:
                raise ValueError('Kontrollér og godkend materialeområderne, inklusive huller, før placering.')
            return copy.deepcopy(scene)

    def _forget_previous(self):
        if self.current:
            path = self.folder / (self.current['id'] + '.png')
            if path.is_file():
                path.unlink()
        self._approvals.clear()

    def new_image(self, data):
        photo, height, corners = rectify_image(data, self.bed)
        scene_id = uuid.uuid4().hex
        self.folder.mkdir(parents=True, exist_ok=True)
        image_path = self.folder / (scene_id + '.png')
        photo.save(image_path, format='PNG')
        with self.lock:
            try:
                self._forget_previous()
            except OSError:
                image_path.unlink(missing_ok=True)
                raise ValueError('Det tidligere billede kunne ikke fjernes. Prøv igen.')
            self.current = {
                'id': scene_id, 'revision': 1, 'imageUrl': f'/api/station/material/image/{scene_id}',
                'width': self.bed[0], 'height': self.bed[1], 'imageWidth': photo.width, 'imageHeight': photo.height,
                'regions': [], 'approved': False, 'requiresReview': True, 'warnings': [],
                'heightMm': height, 'margin': 3.0, 'corners': corners,
                'createdAt': datetime.now(timezone.utc).isoformat(),
                'imageSha256': hashlib.sha256(image_path.read_bytes()).hexdigest(),
            }
            return self.snapshot()

    def image(self, scene_id):
        with self.lock:
            self._match(scene_id)
            return (self.folder / (scene_id + '.png')).read_bytes()

    def update_regions(self, data):
        with self.lock:
            scene = self._match(data.get('sceneId'), data.get('revision'))
            if 'revision' not in data:
                raise ValueError('Materialeområdernes version mangler.')
            regions = normalize_regions(data.get('regions'), bed=self.bed)
            self.current = dict(scene, regions=regions, approved=False, requiresReview=True,
                                revision=scene['revision'] + 1,
                                warnings=[])
            self._approvals.clear()
            return self.snapshot()

    def approve_regions(self, data):
        with self.lock:
            scene = self._match(data.get('sceneId'), data.get('revision'))
            if 'revision' not in data or data.get('confirmed') is not True or not scene['regions']:
                raise ValueError('Markér brugbart materiale, kontrollér kanter og huller, og bekræft områderne.')
            self.current = dict(scene, approved=True, requiresReview=False, revision=scene['revision'] + 1)
            self._approvals.clear()
            return self.snapshot()

    def clear(self):
        with self.lock:
            self._forget_previous()
            self.current = None

    def approve_placements(self, data, fingerprint):
        with self.lock:
            scene = self.require(data, approved=True)
            if data.get('confirmed') is not True:
                raise ValueError('Kontrollér placeringerne på det aktuelle billede og godkend dem.')
            token = secrets.token_urlsafe(32)
            # Only the current approval is needed; never keep a list of past jobs.
            self._approvals = {token: fingerprint}
            return token

    def validate_approval(self, data, fingerprint, mandatory=False):
        with self.lock:
            scene = self.require(data, approved=True)
            token = data.get('approvalToken')
            if (scene is not None or mandatory) and (not isinstance(token, str) or self._approvals.get(token) != fingerprint):
                raise ValueError('Godkend den aktuelle placering igen. Billede, områder, materiale eller tegninger er ændret.')
            return scene
