"""Read-only, fixed-origin SVG library. Never a general URL proxy."""
import json
import hashlib
import math
import re
import ssl
import threading
import time
from pathlib import Path
from html.parser import HTMLParser
import xml.etree.ElementTree as ET
from urllib.error import HTTPError
from urllib.parse import urlsplit, quote
from urllib.request import Request, ProxyHandler, HTTPSHandler, HTTPRedirectHandler, build_opener

ORIGIN = 'https://xn--skolevrkstedet-5ib.dk'
MAX_BYTES = 2_000_000
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
    return ET.tostring(root, encoding='unicode')


class Library:
    def __init__(self):
        context = ssl.create_default_context()
        # Windows service accounts may lack the site's intermediate CA chain.
        # Keep certificate and hostname validation; supplement the public roots.
        bundle = Path(__file__).with_name('svg-library-ca.pem')
        if bundle.is_file():
            context.load_verify_locations(cafile=str(bundle))
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect())
        self.lock = threading.Lock()
        self.items, self.paths, self.updated = [], {}, 0

    def catalogue(self):
        with self.lock:
            if time.monotonic() - self.updated < 300 and self.items:
                return self.items
            parser = Catalogue()
            parser.feed(self.read('/svg-bibliotek/').decode('utf-8'))
            if not parser.items:
                raise ValueError('Bibliotekets katalog kunne ikke læses.')
            self.items, self.paths = parser.items, parser.paths
            self.updated = time.monotonic()
            return self.items

    def svg(self, item_id):
        if not re.fullmatch('[a-f0-9]{64}', item_id):
            raise ValueError('Ukendt biblioteksfil.')
        self.catalogue()
        with self.lock:
            path = self.paths.get(item_id)
        if path is None:
            raise ValueError('Ukendt biblioteksfil.')
        return safe_svg(self.read(path))

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
