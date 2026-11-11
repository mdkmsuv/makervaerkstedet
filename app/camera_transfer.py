"""Move labelled camera references with a school's explicitly exported printers.

No latest frame, live clearance, print job or observation streak is transferred.
"""
import base64
import hashlib
import io
import json
import math
from pathlib import Path
import re
import uuid

from PIL import Image

from .camera import MAX_IMAGE, validate_polygon

REFERENCE = re.compile(r'reference-[0-9a-f]{32}\.jpg')


def _normalise(entry, config):
    if not isinstance(entry, dict) or any(entry.get(key) != config[key] for key in ('serial', 'host', 'model')):
        raise ValueError('Kamerabillederne tilhører en anden printer eller adresse.')
    source = entry.get('settings', {})
    refs = source.get('references', [])
    if not isinstance(refs, list) or not 1 <= len(refs) <= 12:
        raise ValueError('Kameraoverførslen skal indeholde 1–12 tomme referencer pr. printer.')
    names = []
    for ref in refs:
        if not isinstance(ref, dict) or not REFERENCE.fullmatch(str(ref.get('file', ''))):
            raise ValueError('Et referencebillede har et ugyldigt filnavn.')
        captured = ref.get('captured')
        if not isinstance(captured, (int, float)) or not math.isfinite(captured) or captured <= 0:
            raise ValueError('Et referencebillede mangler tidspunkt.')
        names.append(ref['file'])
    if len(set(names)) != len(names):
        raise ValueError('Et referencebillede er med flere gange.')
    points = source.get('polygon')
    validate_polygon(points)
    settings = {'serial': config['serial'], 'references': [{'file': ref['file'], 'captured': ref['captured']} for ref in refs],
                'polygon': points, 'view_approved': source.get('view_approved') is True,
                'validated': source.get('validated') is True, 'automatic': False}
    images = entry.get('images', {})
    expected = set(names)
    if settings['validated']:
        expected.add('occupied-validation.jpg')
    if not isinstance(images, dict) or set(images) != expected:
        raise ValueError('Kameraoverførslen mangler billeder eller indeholder uvedkommende filer.')
    decoded = {}
    for name, item in images.items():
        if not isinstance(item, dict) or not isinstance(item.get('jpeg'), str) or len(item['jpeg']) > 4 * ((MAX_IMAGE + 2) // 3):
            raise ValueError('Et kamerabillede er ugyldigt eller for stort.')
        try:
            jpeg = base64.b64decode(item['jpeg'], validate=True)
            if len(jpeg) > MAX_IMAGE or hashlib.sha256(jpeg).hexdigest() != item.get('sha256'):
                raise ValueError('checksum')
            with Image.open(io.BytesIO(jpeg)) as image:
                if image.format != 'JPEG' or not 320 <= image.width <= 4096 or not 200 <= image.height <= 4096:
                    raise ValueError('image')
                image.verify()
        except Exception as error:
            raise ValueError('Et kamerabillede er beskadiget. Eksportér opsætningen igen.') from error
        decoded[name] = jpeg
    # Transfer archived photographs only; old automatic approval is inactive.
    clean = {key: config[key] for key in ('serial', 'host', 'model')}
    clean.update(settings=settings, images={name: {'jpeg': base64.b64encode(jpeg).decode('ascii'), 'sha256': hashlib.sha256(jpeg).hexdigest()} for name, jpeg in decoded.items()})
    return clean


def read_camera_transfer(data, configs):
    entries = data.get('cameras', {})
    by_id = {p['id']: p for p in configs}
    if not isinstance(entries, dict) or not set(entries) <= set(by_id):
        raise ValueError('Kameraoverførslen indeholder en ukendt printer.')
    return {pid: _normalise(entry, by_id[pid]) for pid, entry in entries.items()}


def export_cameras(folder, configs):
    entries = {}
    for config in configs:
        directory = Path(folder) / 'cameras' / config['id']
        path = directory / 'camera.json'
        if not path.is_file():
            continue
        settings = json.loads(path.read_text(encoding='utf-8'))
        if not settings.get('references'):
            continue
        if settings.get('serial') != config['serial']:
            raise ValueError('Gemte kamerabilleder tilhører en anden printer.')
        names = [r['file'] for r in settings['references']]
        if settings.get('validated'):
            names.append('occupied-validation.jpg')
        images = {}
        for name in names:
            if name != 'occupied-validation.jpg' and not REFERENCE.fullmatch(str(name)):
                raise ValueError('Et referencebillede har et ugyldigt filnavn.')
            jpeg = (directory / name).read_bytes()
            images[name] = {'jpeg': base64.b64encode(jpeg).decode('ascii'), 'sha256': hashlib.sha256(jpeg).hexdigest()}
        entry = {key: config[key] for key in ('serial', 'host', 'model')}
        entry.update(settings=settings, images=images)
        entries[config['id']] = _normalise(entry, config)
    return entries


def import_cameras(folder, entries):
    """Install validated entries into absent folders; existing calibration wins."""
    count = 0
    root = Path(folder) / 'cameras'
    for pid, entry in entries.items():
        if not re.fullmatch('[0-9a-f]{32}', pid):
            raise ValueError('Ukendt printer i kameraoverførslen.')
        target = root / pid
        if target.exists():
            continue
        root.mkdir(parents=True, exist_ok=True)
        staging = root / (pid + '.import-' + uuid.uuid4().hex)
        staging.mkdir()
        for name, image in entry['images'].items():
            (staging / name).write_bytes(base64.b64decode(image['jpeg'], validate=True))
        settings = {**entry['settings'], 'automatic': False}
        (staging / 'camera.json').write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
        staging.rename(target)
        count += 1
    return count
