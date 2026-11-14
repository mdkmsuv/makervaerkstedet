"""Read-only, fixed-origin SVG library. Never a general URL proxy."""
import json
import hashlib
import math
import os
import re
import ssl
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from html.parser import HTMLParser
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit, quote
from urllib.request import Request, ProxyHandler, HTTPSHandler, HTTPRedirectHandler, build_opener

ORIGIN = 'https://xn--skolevrkstedet-5ib.dk'
MAX_BYTES = 2_000_000
CACHE_BYTES = 64 * 1024 * 1024
CATALOGUE_BYTES = 8_000_000
CACHE_SECONDS = 300
NS = 'http://www.w3.org/2000/svg'
VECTOR_TAGS = {'svg', 'g', 'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon', 'title', 'desc', 'defs'}
ATTRS = {'width', 'height', 'viewBox', 'preserveAspectRatio', 'x', 'y', 'x1', 'y1', 'x2', 'y2', 'cx', 'cy', 'r', 'rx', 'ry', 'd', 'points', 'transform', 'fill', 'fill-rule', 'fill-opacity', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'stroke-miterlimit', 'stroke-opacity', 'opacity', 'style', 'version'}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def safe_svg(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError('SVG-filen er for stor.')
    text = raw.decode('utf-8-sig')
    if re.search(r'<!DOCTYPE|<!ENTITY|<\?', text, re.I):
        # XML declaration is harmless; processing instructions are not.
        text = re.sub(r'^\s*<\?xml\s[^?]*\?>', '', text, count=1)
        # Potrace files carry an obsolete external DTD. Remove the declaration
        # without opening its URL; internal subsets/entities remain forbidden.
        text = re.sub(r'<!DOCTYPE\s+svg\s+(?:PUBLIC\s+"[^"\[\]]*"\s+"[^"\[\]]*"|SYSTEM\s+"[^"\[\]]*")\s*>', '', text, count=1, flags=re.I)
        if re.search(r'<!DOCTYPE|<!ENTITY|<\?', text, re.I):
            raise ValueError('SVG med eksternt eller aktivt indhold afvises.')
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise ValueError('Ugyldig SVG.') from error
    if root.tag != '{' + NS + '}svg':
        raise ValueError('Filen skal være en almindelig SVG.')
    stack = [(root, 0)]
    while stack:
        element, depth = stack.pop()
        if depth > 64:
            raise ValueError('SVG har for mange indlejrede grupper.')
        stack.extend((child, depth + 1) for child in element)
    elements = list(root.iter())
    if len(elements) > 3000:
        raise ValueError('SVG har for mange elementer.')
    # Metadata never enters a browser or the editor's project importer.
    for parent in elements:
        for child in list(parent):
            if child.tag == '{' + NS + '}metadata':
                parent.remove(child)
    elements = list(root.iter())
    for element in elements:
        tag = element.tag.removeprefix('{' + NS + '}')
        if tag not in VECTOR_TAGS or (tag == 'svg' and element is not root) or (tag == 'defs' and len(element)):
            raise ValueError('Biblioteket understøtter kun almindelige vektorfigurer.')
        for name, value in list(element.attrib.items()):
            if name.lower().startswith('on') or name.lower().endswith('href') or re.search(r'url\s*\(|javascript:|@|[\\<>]', value, re.I):
                raise ValueError('SVG med scripts eller eksterne henvisninger afvises.')
            if name == 'style':
                for part in value.split(';'):
                    if part.strip() and part.split(':', 1)[0].strip() not in ATTRS - {'style'}:
                        raise ValueError('SVG bruger en effekt, som ikke understøttes.')
            if name not in ATTRS:
                # Strip editor/project metadata, IDs and foreign namespaces.
                del element.attrib[name]
    try:
        vb = [float(v) for v in re.split(r'[\s,]+', root.get('viewBox', '').strip())]
        if len(vb) != 4 or not all(math.isfinite(v) and abs(v) < 1e8 for v in vb) or min(vb[2:]) <= 0:
            raise ValueError()
        w, h = vb[2], vb[3]
        factor = 80 / max(w, h)
        if min(w, h) * factor < 1:
            raise ValueError()
        root.set('width', f'{w * factor:g}mm')
        root.set('height', f'{h * factor:g}mm')
    except ValueError as error:
        raise ValueError('SVG mangler et gyldigt viewBox.') from error
    ET.register_namespace('', NS)
    result = ET.tostring(root, encoding='unicode')
    if len(result.encode('utf-8')) > MAX_BYTES:
        raise ValueError('SVG-filen er for stor.')
    return result


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('utf-8')


class CacheSecurityError(ValueError):
    """A linked or nonregular cache is never followed or replaced."""


class _Cache:
    def __init__(self, directory):
        self.path = Path(os.path.abspath(directory))
        with self._directory():
            pass

    @contextmanager
    def _directory(self):
        # Walk using directory descriptors on Linux so a concurrent ancestor
        # replacement cannot redirect reads/writes into a different directory.
        if os.name == 'posix':
            fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
            try:
                for part in self.path.parts[1:]:
                    try:
                        child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    except FileNotFoundError:
                        os.mkdir(part, 0o700, dir_fd=fd)
                        child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    except OSError as error:
                        raise CacheSecurityError('Cachemappen er ikke en almindelig mappe.') from error
                    os.close(fd)
                    fd = child
                yield fd
            finally:
                os.close(fd)
        else:
            for parent in reversed((self.path, *self.path.parents)):
                if parent.is_symlink():
                    raise CacheSecurityError('Cachemappen må ikke være et link.')
            self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
            yield None

    def _stat(self, name, directory):
        try:
            value = os.stat(name, dir_fd=directory, follow_symlinks=False) if directory is not None else (self.path / name).lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
            raise CacheSecurityError('Cachefilen er ikke en almindelig fil.')
        return value

    def read(self, name, limit):
        with self._directory() as directory:
            info = self._stat(name, directory)
            if info is None:
                return None
            if info.st_size > limit:
                raise ValueError('Cachefilen er for stor.')
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
            fd = os.open(name, flags, dir_fd=directory) if directory is not None else os.open(self.path / name, flags)
            with os.fdopen(fd, 'rb') as stream:
                actual = os.fstat(stream.fileno())
                if not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1:
                    raise CacheSecurityError('Cachefilen er ikke en almindelig fil.')
                raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise ValueError('Cachefilen er for stor.')
            envelope = json.loads(raw)
            data = envelope['data']
            if envelope['sha256'] != hashlib.sha256(_json_bytes(data)).hexdigest():
                raise ValueError('Cachefilen er beskadiget.')
            return data

    def write(self, name, data):
        raw = _json_bytes({'data': data, 'sha256': hashlib.sha256(_json_bytes(data)).hexdigest()})
        with self._directory() as directory:
            self._stat(name, directory)
            temporary = '.svg-' + uuid.uuid4().hex + '.tmp'
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
            target = temporary if directory is not None else self.path / temporary
            fd = os.open(target, flags, 0o600, dir_fd=directory) if directory is not None else os.open(target, flags, 0o600)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                self._stat(name, directory)
                if directory is not None:
                    os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                    os.fsync(directory)
                else:
                    os.replace(target, self.path / name)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory) if directory is not None else Path(target).unlink()
                except FileNotFoundError:
                    pass
            if name != 'catalogue.json':
                self._prune(directory)

    def _prune(self, directory):
        names = os.listdir(directory if directory is not None else self.path)
        files = []
        for name in names:
            if re.fullmatch(r'[a-f0-9]{64}\.json', name):
                info = self._stat(name, directory)
                if info is not None:
                    files.append((info.st_mtime_ns, name, info.st_size))
        total = sum(size for _, _, size in files)
        for _, name, size in sorted(files):
            if total <= CACHE_BYTES:
                break
            os.unlink(name, dir_fd=directory) if directory is not None else (self.path / name).unlink()
            total -= size


class Library:
    """Fixed website source; cache_dir=None preserves memory-only legacy use.

    A restart starts stale until a successful read. status() never performs
    network I/O. last_success describes the last successful catalogue fetch.
    """
    def __init__(self, *, cache_dir=None, ca_file=None):
        context = ssl.create_default_context()
        # Windows service accounts may lack the site's intermediate CA chain.
        # Keep certificate and hostname validation; supplement the public roots.
        bundle = Path(ca_file) if ca_file is not None else Path(__file__).with_name('svg-library-ca.pem')
        if bundle.is_file():
            context.load_verify_locations(cafile=str(bundle))
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect())
        self.lock = threading.RLock()
        self.items, self.paths, self.updated = [], {}, 0
        self.version, self.last_success = 0, None
        self.cache_state = 'empty'
        self.retry_after = 0
        self.cache = _Cache(cache_dir) if cache_dir is not None else None
        self.files = {}
        if self.cache is not None:
            try:
                data = self.cache.read('catalogue.json', CATALOGUE_BYTES)
                if data is not None:
                    self._validate_catalogue(data)
                    self.items, self.paths = data['items'], data['paths']
                    self.last_success = data['last_success']
                    self.version = self._version()
                    self.cache_state = 'stale'
            except CacheSecurityError:
                raise
            except (ValueError, KeyError, TypeError, UnicodeError):
                pass

    def _version(self):
        # 52 bits fit exactly into JavaScript's integer representation.
        return int(hashlib.sha256(_json_bytes({'items': self.items, 'paths': self.paths})).hexdigest()[:13], 16)

    @staticmethod
    def _validate_catalogue(data):
        items, paths = data['items'], data['paths']
        if not isinstance(items, list) or not 0 < len(items) <= 5000 or not isinstance(paths, dict) or len(paths) != len(items):
            raise ValueError('Ugyldigt cachekatalog.')
        if not isinstance(data['last_success'], str):
            raise ValueError('Ugyldig cachedato.')
        datetime.fromisoformat(data['last_success'])
        seen = set()
        for item in items:
            item_id = item['id']
            path = paths[item_id]
            if not isinstance(item_id, str) or not re.fullmatch('[a-f0-9]{64}', item_id) or item_id in seen:
                raise ValueError('Ugyldigt cache-id.')
            if not isinstance(path, str) or not path.startswith('/svg-bibliotek/'):
                raise ValueError('Ugyldig cachesti.')
            # Reconstruct using the same strict parser instead of trusting a
            # persisted URL. Only its canonical escaped relative paths pass.
            from urllib.parse import unquote
            parser = Catalogue()
            parser.handle_starttag('article', [('class', 'card'), ('data-url', unquote(path[len('/svg-bibliotek/'):]))])
            if parser.paths != {item_id: path}:
                raise ValueError('Ugyldig cachesti.')
            for field, maximum in [('title', 120), ('category', 100)]:
                if not isinstance(item[field], str) or len(item[field]) > maximum:
                    raise ValueError('Ugyldig katalogtekst.')
            seen.add(item_id)

    def status(self):
        with self.lock:
            if self.cache_state == 'ready' and time.monotonic() - self.updated >= CACHE_SECONDS:
                self.cache_state = 'stale'
            return {'enabled': True, 'version': self.version, 'source_url': ORIGIN + '/svg-bibliotek/', 'cache_state': self.cache_state, 'last_success': self.last_success}

    def catalogue(self):
        with self.lock:
            if self.items and (time.monotonic() - self.updated < CACHE_SECONDS or time.monotonic() < self.retry_after):
                return [dict(item) for item in self.items]
            try:
                parser = Catalogue()
                parser.feed(self.read('/svg-bibliotek/').decode('utf-8'))
                if not parser.items:
                    raise ValueError('Bibliotekets katalog kunne ikke læses.')
            except (OSError, ValueError):
                self.cache_state = 'stale' if self.items else 'empty'
                self.retry_after = time.monotonic() + 30
                if self.items:
                    return [dict(item) for item in self.items]
                raise
            self.items, self.paths = parser.items, parser.paths
            self.version = self._version()
            self.last_success = datetime.now(timezone.utc).isoformat()
            if self.cache is not None:
                self.cache.write('catalogue.json', {'items': self.items, 'paths': self.paths, 'last_success': self.last_success})
            self.updated = time.monotonic()
            self.retry_after = 0
            self.cache_state = 'ready'
            return [dict(item) for item in self.items]

    def svg(self, item_id):
        if not isinstance(item_id, str) or not re.fullmatch('[a-f0-9]{64}', item_id):
            raise ValueError('Ukendt biblioteksfil.')
        self.catalogue()
        with self.lock:
            path = self.paths.get(item_id)
        if path is None:
            raise ValueError('Ukendt biblioteksfil.')
        with self.lock:
            if self.cache_state == 'stale':
                return self._cached_svg(item_id)
            try:
                svg = safe_svg(self.read(path))
            except (OSError, ValueError):
                self.cache_state = 'stale'
                # A file request can fail before the catalogue TTL expires.
                # Force the next refresh after a short offline backoff.
                self.updated = 0
                self.retry_after = time.monotonic() + 30
                return self._cached_svg(item_id)
            if self.cache is not None:
                self.cache.write(item_id + '.json', {'id': item_id, 'svg': svg})
            else:
                # Keep memory-only mode bounded too.
                self.files[item_id] = svg
                while sum(len(value.encode('utf-8')) for value in self.files.values()) > CACHE_BYTES:
                    del self.files[next(iter(self.files))]
            return svg

    def _cached_svg(self, item_id):
        if self.cache is not None:
            try:
                data = self.cache.read(item_id + '.json', MAX_BYTES * 6 + 1024)
                if data is not None and data['id'] == item_id:
                    return safe_svg(data['svg'].encode('utf-8'))
            except CacheSecurityError:
                raise
            except (ValueError, KeyError, TypeError, UnicodeError):
                pass
        elif item_id in self.files:
            return self.files[item_id]
        raise ValueError('SVG-filen er ikke tilgængelig offline.') from None

    def read(self, path):
        if not path.startswith('/') or path.startswith('//') or '\\' in path or '..' in path or urlsplit(path).netloc:
            raise ValueError('Ugyldig bibliotekssti.')
        request = Request(ORIGIN + path, headers={'Accept': 'application/json, image/svg+xml', 'User-Agent': 'Elevhub-SVG-library/1'})
        with self.opener.open(request, timeout=8) as response:
            if response.status != 200 or response.geturl() != ORIGIN + path:
                raise ValueError('Biblioteket omdirigerede forespørgslen.')
            data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError('Bibliotekets svar er for stort.')
            return data


class Catalogue(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items, self.paths = [], {}

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag != 'article' or 'card' not in data.get('class', '').split():
            return
        relative = data.get('data-url', '')
        # Plain relative SVG paths only: no schemes, traversal, encoding,
        # queries, fragments, backslashes or absolute origins.
        if not re.fullmatch(r'[A-Za-z0-9_/() -]+\.svg', relative) or relative.startswith('/') or '..' in relative or len(relative) > 400:
            return
        path = '/svg-bibliotek/' + quote(relative, safe='/')
        item_id = hashlib.sha256(path.encode()).hexdigest()
        if item_id in self.paths:
            return
        if len(self.items) >= 5000:
            raise ValueError('Biblioteket har for mange filer.')
        self.paths[item_id] = path
        self.items.append({'id': item_id, 'title': data.get('data-title', data.get('data-file', 'SVG'))[:120], 'category': data.get('data-category', '')[:100]})
