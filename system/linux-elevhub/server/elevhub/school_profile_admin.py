"""Private, bounded school identity editor using the existing appearance manifest.

Wire contract: save({command_id: UUID, expected_revision: int, profile: {
school_name: str, colour: '#rrggbb', font: standard|manrope|metrophobic,
logo_mode: keep|replace|remove, logo_png: bare base64 for replace, else null}}).
All objects have exactly these keys. No paths, URLs or browser CSS are accepted.

Publication is serialized with an OS lock and a durable SQLite intent. Immutable
assets are written first, manifest.json is atomically replaced last, and then
the receipt is committed. On recovery, matching new manifest finalizes the
receipt; matching old manifest abandons the unpublished intent. An unrelated
manifest during recovery is a conflict, never an excuse to overwrite it.
Inactive assets and existing private profile files are deliberately retained.
"""
from contextlib import contextmanager
import base64
import binascii
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import unicodedata
import uuid
import warnings

from school_appearance import school_appearance, school_appearance_asset


MAX_LOGO_BYTES = 2 * 1024 * 1024
MAX_DIMENSION = 2048
FONT_LABELS = {'standard': 'Standard', 'manrope': 'Manrope', 'metrophobic': 'Metrophobic'}
_LOCAL_LOCK = threading.RLock()


class SchoolProfileError(ValueError):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False)


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def _plain(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise SchoolProfileError('linked_profile_path')
    return path


def _read(path, limit):
    path = _plain(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise SchoolProfileError('invalid_profile_file')
    with path.open('rb') as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise SchoolProfileError('invalid_profile_file')
    return value


def _sync_directory(path):
    # Windows has no directory fsync via Python. Atomic replace remains the
    # publication boundary there; SQLite FULL protects the publication intent.
    if os.name != 'nt':
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _png(value):
    try:
        from PIL import Image
    except ImportError:
        raise SchoolProfileError('logo_processing_unavailable', 503) from None
    if not isinstance(value, str) or len(value) > 4 * ((MAX_LOGO_BYTES + 2) // 3):
        raise SchoolProfileError('logo_upload_too_large')
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise SchoolProfileError('invalid_logo_png') from None
    if len(raw) > MAX_LOGO_BYTES:
        raise SchoolProfileError('logo_upload_too_large')
    if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
        raise SchoolProfileError('invalid_logo_png')
    offset = 8
    while offset + 8 <= len(raw):
        length = int.from_bytes(raw[offset:offset + 4], 'big')
        if raw[offset + 4:offset + 8] == b'acTL':
            raise SchoolProfileError('invalid_logo_png')
        offset += 12 + length
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                if source.format != 'PNG' or getattr(source, 'n_frames', 1) != 1:
                    raise SchoolProfileError('invalid_logo_png')
                if not (1 <= source.width <= MAX_DIMENSION and 1 <= source.height <= MAX_DIMENSION):
                    raise SchoolProfileError('logo_dimensions_exceeded')
                source.verify()
            with Image.open(io.BytesIO(raw)) as source:
                source.load()
                # A fresh pixel image omits text, EXIF, colour profile and other
                # user-controlled ancillary metadata, while retaining alpha.
                pixels = source.convert('RGBA')
                image = Image.frombytes('RGBA', pixels.size, pixels.tobytes())
                output = io.BytesIO()
                image.save(output, format='PNG', optimize=True)
                result = output.getvalue()
                if len(result) > 4 * 1024 * 1024:
                    raise SchoolProfileError('logo_upload_too_large')
                return result
    except SchoolProfileError:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        raise SchoolProfileError('invalid_logo_png') from None


def _dark_colour(colour):
    rgb = [int(colour[index:index + 2], 16) for index in (1, 3, 5)]
    # Chosen for white button labels and accent text on a white surface. This
    # calculation is a limited contrast guarantee, not a rendered WCAG audit.
    def luminance(values):
        linear = [(v / 255 / 12.92 if v / 255 <= .04045 else
                   ((v / 255 + .055) / 1.055) ** 2.4) for v in values]
        return sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))
    while luminance(rgb) > .175:
        rgb = [int(value * .9) for value in rgb]
    return '#' + ''.join(f'{value:02x}' for value in rgb)


class SchoolProfileAdmin:
    def __init__(self, data_root, *, font_files=None):
        self.root = Path(os.path.abspath(data_root))
        self.base = self.root / 'local-appearance'
        self.db_path = self.root / 'school-profile-admin.sqlite3'
        self.lock_path = self.root / 'school-profile-admin.lock'
        assets = Path(__file__).parent / 'web/assets'
        self.font_files = dict(font_files) if font_files is not None else {
            'manrope': assets / 'Manrope.ttf', 'metrophobic': assets / 'metrophobic-latin.ttf'}

    @contextmanager
    def _locked(self):
        with _LOCAL_LOCK:
            _plain(self.root).mkdir(parents=True, exist_ok=True)
            for path in (self.db_path, self.lock_path):
                _plain(path)
                if path.exists() and not path.is_file():
                    raise SchoolProfileError('invalid_profile_file')
            # The lock covers the durable intent commit, manifest rename, and
            # receipt commit, across processes and separate service instances.
            with self.lock_path.open('a+b') as lock:
                lock.seek(0, os.SEEK_END)
                if lock.tell() == 0:
                    lock.write(b'0')
                    lock.flush()
                lock.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                db = None
                try:
                    # SQLite sidecars must not follow an administrator-created
                    # link either. This directory is owned by the hub account.
                    for suffix in ('', '-journal', '-wal', '-shm'):
                        _plain(str(self.db_path) + suffix)
                    db = sqlite3.connect(self.db_path, timeout=10)
                    db.row_factory = sqlite3.Row
                    db.execute('PRAGMA synchronous=FULL')
                    db.executescript('''
                        CREATE TABLE IF NOT EXISTS meta(id INTEGER PRIMARY KEY CHECK(id=1),
                            revision INTEGER NOT NULL, profile TEXT, manifest_hash TEXT,
                            observed INTEGER NOT NULL);
                        INSERT OR IGNORE INTO meta VALUES(1,0,NULL,NULL,0);
                        CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,
                            body_hash TEXT NOT NULL, receipt TEXT NOT NULL);
                        CREATE TABLE IF NOT EXISTS pending(id INTEGER PRIMARY KEY CHECK(id=1),
                            command_id TEXT NOT NULL, body_hash TEXT NOT NULL, receipt TEXT NOT NULL,
                            profile TEXT NOT NULL, old_hash TEXT, new_hash TEXT NOT NULL);
                    ''')
                    self._recover(db)
                    yield db
                finally:
                    if db is not None:
                        db.close()
                    lock.seek(0)
                    if os.name == 'nt':
                        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _manifest_hash(self):
        path = _plain(self.base / 'manifest.json')
        try:
            return _digest(_read(path, 65536))
        except FileNotFoundError:
            return None

    def _finish(self, db, pending):
        receipt = json.loads(pending['receipt'])
        with db:
            db.execute('UPDATE meta SET revision=?, profile=?, manifest_hash=?, observed=1 WHERE id=1',
                       (receipt['revision'], pending['profile'], pending['new_hash']))
            db.execute('INSERT INTO commands VALUES(?,?,?)',
                       (pending['command_id'], pending['body_hash'], pending['receipt']))
            db.execute('DELETE FROM pending WHERE id=1')

    def _recover(self, db):
        pending = db.execute('SELECT * FROM pending WHERE id=1').fetchone()
        if pending is None:
            return
        actual = self._manifest_hash()
        if actual == pending['new_hash']:
            self._finish(db, pending)
        elif actual == pending['old_hash']:
            with db:
                db.execute('DELETE FROM pending WHERE id=1')
        else:
            raise SchoolProfileError('profile_recovery_conflict', 409)

    def _font(self, selected):
        if selected == 'standard':
            return None
        path = self.font_files.get(selected)
        if path is None:
            raise SchoolProfileError('profile_font_unavailable')
        try:
            content = _read(path, 4 * 1024 * 1024)
            if not content or Path(path).suffix.lower() != '.ttf':
                raise SchoolProfileError('profile_font_unavailable')
            return content
        except (OSError, SchoolProfileError):
            raise SchoolProfileError('profile_font_unavailable') from None

    def _overview(self, db):
        meta = db.execute('SELECT * FROM meta WHERE id=1').fetchone()
        digest = self._manifest_hash()
        if not meta['observed']:
            with db:
                db.execute('UPDATE meta SET manifest_hash=?, observed=1 WHERE id=1', (digest,))
        elif digest != meta['manifest_hash']:
            # The existing file workflow can still replace a profile. A form
            # loaded before that change must receive a fresh revision conflict.
            with db:
                db.execute('UPDATE meta SET revision=revision+1, profile=NULL, manifest_hash=? WHERE id=1',
                           (digest,))
        meta = db.execute('SELECT * FROM meta WHERE id=1').fetchone()
        appearance = school_appearance(self.root)
        managed = meta['profile'] is not None and digest == meta['manifest_hash']
        if managed:
            profile = json.loads(meta['profile'])
        else:
            profile = {'school_name': (appearance or {}).get('school_name', 'Makerværkstedet'),
                       'colour': (appearance or {}).get('brand', {
                           'red': '#c62b2f', 'green': '#26734d', 'yellow': '#e8b300'
                       }.get((appearance or {}).get('theme'), '#c62b2f')),
                       'font': 'standard'}
        fonts = []
        for selected, label in FONT_LABELS.items():
            try:
                self._font(selected)
                available = True
            except SchoolProfileError:
                available = False
            fonts.append({'id': selected, 'label': label, 'available': available})
        legacy = digest is not None or appearance is not None
        return {'revision': meta['revision'], 'managed': managed,
                'state': 'managed' if managed else ('legacy' if legacy else 'default'),
                'profile': profile, 'fonts': fonts,
                'logoPreviewPath': '/school-appearance/logo' if
                appearance and appearance.get('style', {}).get('logo') else None}

    def overview(self):
        with self._locked() as db:
            return self._overview(db)

    def distribution(self):
        """Public, hash-bound managed identity for the enrolled kiosk updater."""
        from school_appearance import _manifest
        with self._locked() as db:
            state = self._overview(db)
            if not state['managed']:
                return {'schema': 1, 'enabled': False}
            raw = _read(self.base / 'manifest.json', 65536)
            value = json.loads(raw)
            if _manifest(self.root) is None or _canonical(value).encode('utf-8') != raw:
                raise SchoolProfileError('invalid_profile_file', 503)
            style = value['style']
            names = [style[key] for key in ('website_css', 'logo') if key in style]
            names += [item['path'] for item in style.get('static', {}).values()]
            if len(set(names)) != len(names) or len(names) > 128:
                raise SchoolProfileError('invalid_profile_file', 503)
            assets = []
            for name in names:
                match = re.fullmatch(r'managed-([a-f0-9]{64})(\.[a-z0-9]+)', name)
                if match is None:
                    raise SchoolProfileError('invalid_profile_file', 503)
                content = _read(self.base / name, 4 * 1024**2)
                if _digest(content) != match[1]:
                    raise SchoolProfileError('invalid_profile_file', 503)
                assets.append({'name': name, 'sha256': match[1], 'size': len(content),
                               'path': '/api/device/school-profile/assets/' + match[1] + match[2]})
            return {'schema': 1, 'enabled': True, 'revision': state['revision'],
                    'manifest': {'sha256': _digest(raw), 'size': len(raw), 'value': value},
                    'assets': assets}

    def distributed_asset(self, digest, suffix):
        # Immutable names allow a kiosk to finish a verified older download
        # while an administrator publishes a new profile. No path is accepted.
        if not re.fullmatch('[a-f0-9]{64}', digest) or suffix not in ('.png', '.ttf', '.css'):
            raise SchoolProfileError('profile_asset_not_found', 404)
        with self._locked():
            try:
                content = _read(self.base / ('managed-' + digest + suffix), 4 * 1024**2)
            except FileNotFoundError:
                raise SchoolProfileError('profile_asset_not_found', 404) from None
            if _digest(content) != digest:
                raise SchoolProfileError('invalid_profile_file', 503)
            return content

    def _validate(self, body):
        if not isinstance(body, dict) or set(body) != {'command_id', 'expected_revision', 'profile'}:
            raise SchoolProfileError('invalid_profile_fields')
        try:
            if not isinstance(body['command_id'], str) or str(uuid.UUID(body['command_id'])) != body['command_id']:
                raise ValueError()
        except (ValueError, AttributeError):
            raise SchoolProfileError('invalid_profile_command') from None
        revision = body['expected_revision']
        if type(revision) is not int or not 0 <= revision < 2 ** 63 - 1:
            raise SchoolProfileError('invalid_profile_revision')
        profile = body['profile']
        if not isinstance(profile, dict) or set(profile) != {
                'school_name', 'colour', 'font', 'logo_mode', 'logo_png'}:
            raise SchoolProfileError('invalid_profile_fields')
        name = profile['school_name']
        if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 120
                or any(unicodedata.category(char).startswith('C') for char in name)):
            raise SchoolProfileError('invalid_school_name')
        if not isinstance(profile['colour'], str) or not re.fullmatch('#[0-9a-fA-F]{6}', profile['colour']):
            raise SchoolProfileError('invalid_profile_colour')
        if not isinstance(profile['font'], str) or profile['font'] not in FONT_LABELS:
            raise SchoolProfileError('invalid_profile_font')
        mode = profile['logo_mode']
        if mode not in ('keep', 'replace', 'remove') or (mode != 'replace' and profile['logo_png'] is not None):
            raise SchoolProfileError('invalid_logo_mode')
        if mode == 'replace' and not isinstance(profile['logo_png'], str):
            raise SchoolProfileError('invalid_logo_png')
        return {'school_name': name.strip(), 'colour': profile['colour'].lower(), 'font': profile['font']}

    def _asset(self, content, suffix):
        digest = _digest(content)
        relative = 'managed-' + digest + suffix
        path = _plain(self.base / relative)
        if path.exists():
            if _read(path, 4 * 1024 * 1024) != content:
                raise SchoolProfileError('profile_asset_conflict', 409)
        else:
            temporary = _plain(self.base / ('.asset-' + str(uuid.uuid4()) + '.tmp'))
            try:
                with temporary.open('xb') as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                _sync_directory(self.base)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return relative, digest

    def _manifest(self, profile, logo, font):
        accent = profile['colour']
        brand = _dark_colour(accent)
        dark = '#' + ''.join(f'{int(int(brand[i:i + 2], 16) * .75):02x}' for i in (1, 3, 5))
        rgb = [int(accent[i:i + 2], 16) for i in (1, 3, 5)]
        soft = '#' + ''.join(f'{round(v * .1 + 255 * .9):02x}' for v in rgb)
        targets = ':root,body,body:not(.picker),body:not(.picker)[data-theme]'
        css = (f'{targets}{{--school-accent:{accent};--brand:{brand};--brand-dark:{dark};'
               f'--school-brand:{brand};--school-brand-dark:{dark};--school-accent-soft:{soft};'
               f'--brand-soft:{soft};--school-brand-soft:{soft};--deep:{dark};--tint:{soft};'
               f'--highlight:{soft};--accent:{brand};--accent-ink:#ffffff;--accent-soft:{soft};}}\n')
        style = {}
        if font is not None:
            relative, digest = self._asset(font, '.ttf')
            url = '/school-profile-assets/' + relative
            style['static'] = {url: {'path': relative, 'sha256': digest}}
            weight = '200 800' if profile['font'] == 'manrope' else '400'
            css += (f'@font-face{{font-family:SchoolProfile;src:url("{url}") format("truetype");'
                    f'font-weight:{weight};font-display:swap;}}\n'
                    f'{targets}{{--f-body:SchoolProfile,sans-serif;--f-display:SchoolProfile,sans-serif;'
                    '--font-body:SchoolProfile,sans-serif;--font-display:SchoolProfile,sans-serif;}\n'
                    'body,body:not(.picker),button,input,select,textarea,#creative-app-header,#creative-app-header *{font-family:SchoolProfile,sans-serif;}\n')
        relative, digest = self._asset(css.encode('utf-8'), '.css')
        style.update(website_css=relative, website_css_sha256=digest)
        if logo is not None:
            content, suffix = logo
            relative, digest = self._asset(content, suffix)
            style.update(logo=relative, logo_sha256=digest)
        return _canonical({'schemaVersion': 1, 'school_name': profile['school_name'],
                           'theme': 'red', 'brand': brand, 'brand_dark': dark,
                           'font': profile['font'],
                           'style': style}).encode('utf-8')

    def save(self, body):
        profile = self._validate(body)
        body_hash = _digest(_canonical(body).encode('utf-8'))
        try:
            with self._locked() as db:
                previous = db.execute('SELECT * FROM commands WHERE id=?', (body['command_id'],)).fetchone()
                if previous is not None:
                    if previous['body_hash'] != body_hash:
                        raise SchoolProfileError('command_id_reused', 409)
                    return json.loads(previous['receipt'])
                state = self._overview(db)
                if body['expected_revision'] != state['revision']:
                    raise SchoolProfileError('stale_profile_revision', 409)
                old_hash = self._manifest_hash()
                font = self._font(profile['font'])
                logo = None
                mode = body['profile']['logo_mode']
                if mode == 'replace':
                    logo = (_png(body['profile']['logo_png']), '.png')
                elif mode == 'keep':
                    appearance = school_appearance(self.root)
                    if old_hash and appearance is None:
                        raise SchoolProfileError('existing_profile_unreadable', 409)
                    if appearance and appearance.get('style', {}).get('logo'):
                        asset = school_appearance_asset(self.root, 'logo')
                        if asset is None:
                            raise SchoolProfileError('existing_logo_unavailable', 409)
                        suffix = { 'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp',
                                   'image/svg+xml': '.svg', 'image/x-icon': '.ico',
                                   'image/vnd.microsoft.icon': '.ico'}.get(asset[1])
                        if suffix is None:
                            raise SchoolProfileError('existing_logo_unavailable', 409)
                        logo = (asset[0], suffix)
                _plain(self.base).mkdir(parents=True, exist_ok=True)
                manifest = self._manifest(profile, logo, font)
                receipt = {'command_id': body['command_id'], 'revision': state['revision'] + 1,
                           'status': 'saved'}
                with db:
                    db.execute('INSERT INTO pending VALUES(1,?,?,?,?,?,?)', (
                        body['command_id'], body_hash, _canonical(receipt), _canonical(profile),
                        old_hash, _digest(manifest)))
                temporary = _plain(self.base / ('.manifest-' + str(uuid.uuid4()) + '.tmp'))
                try:
                    with temporary.open('xb') as stream:
                        stream.write(manifest)
                        stream.flush()
                        os.fsync(stream.fileno())
                    _sync_directory(self.base)
                    os.replace(temporary, _plain(self.base / 'manifest.json'))
                    _sync_directory(self.base)
                finally:
                    if temporary.exists():
                        temporary.unlink()
                self._finish(db, db.execute('SELECT * FROM pending WHERE id=1').fetchone())
                return receipt
        except (OSError, sqlite3.Error):
            raise SchoolProfileError('profile_write_failed', 500) from None
