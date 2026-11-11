"""Bounded public appearance reader; also shipped beside the standalone hub."""
from __future__ import annotations

import hashlib
import html
import json
import mimetypes
import os
import re
from pathlib import Path, PurePosixPath
import stat
from functools import lru_cache

LOCAL_APPEARANCE_VERSION = 1
THEMES = frozenset({'red', 'yellow', 'green'})
MAX_MANIFEST_BYTES = 65536
MAX_CSS_BYTES = 262144
MAX_LOGO_BYTES = 4 * 1024 * 1024
MAX_STATIC_FILES = 128
STATIC_SUFFIXES = frozenset({'.css', '.png', '.jpg', '.jpeg', '.webp', '.svg',
                             '.ico', '.woff', '.woff2', '.ttf', '.otf'})
LOGO_SUFFIXES = STATIC_SUFFIXES - {'.css', '.woff', '.woff2', '.ttf', '.otf'}


def render_local_html(content, appearance):
    """Apply a validated local school profile to neutral public HTML.

    Only explicit identity hooks are changed.  In particular, the mandatory
    project credit on the about page must remain the public project's credit.
    """
    if not appearance or not isinstance(content, (bytes, bytearray)):
        return content
    name = appearance.get('school_name')
    if not isinstance(name, str) or not name.strip():
        return content
    escaped = html.escape(name, quote=True)
    text = bytes(content).decode('utf-8')

    def replace_element(match):
        return match.group(1) + escaped + match.group(3)

    # Neutral templates expose these hooks; the class form keeps older local
    # templates working while avoiding a global replacement in /om.
    text = re.sub(r'(<[^>]*\bdata-school-name\b[^>]*>)(.*?)(</[^>]+>)',
                  replace_element, text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'(<[^>]*\bclass=["\'][^"\']*\bbrand-school\b[^"\']*["\'][^>]*>)(.*?)(</[^>]+>)',
                  replace_element, text, flags=re.IGNORECASE | re.DOTALL)
    # Footer hooks often wrap the product label and its mandatory credit.
    # Replace only the local identity portion and retain the full credit.
    text = re.sub(r'(<[^>]*\bdata-school-footer(?:\s[^>]*)?>).*?(·\s*Kreditering:)',
                  lambda m: (m.group(1) + escaped + ' · Makerværkstedet · '
                             'Udviklet af Mathias Kusk Damgaard ' + m.group(2)), text,
                  flags=re.IGNORECASE | re.DOTALL)
    if appearance.get('style', {}).get('logo'):
        text = re.sub(
            r'<span(?=[^>]*\bclass=["\'][^"\']*\bbrand-mark\b[^"\']*["\'])[^>]*></span>',
            lambda m: '<img class="school-logo" src="/school-appearance/logo" alt="' + escaped + '">',
            text, flags=re.IGNORECASE)
    text = re.sub(r'(<[^>]*\bdata-school-logo(?:\s[^>]*)?\bsrc=["\'])([^"\']+)(["\'])',
                  lambda m: m.group(1) + '/school-appearance/logo' + m.group(3)
                  if appearance.get('style', {}).get('logo') else m.group(0),
                  text, flags=re.IGNORECASE)
    if appearance.get('style', {}).get('logo'):
        text = text.replace('src="/assets/klostermarksskolen-logo.png"',
                            'src="/school-appearance/logo"')
        text = text.replace("src='/assets/klostermarksskolen-logo.png'",
                            "src='/school-appearance/logo'")
    return text.encode('utf-8')


def _roots(data_folder):
    folder = Path(os.path.abspath(data_folder))
    return tuple(dict.fromkeys([folder] + ([folder.parent] if os.name == 'nt'
                                           else [Path('/etc/elevhub')])))


def _plain(path):
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Appearance links are not supported')
    return path


def _bytes(path, limit):
    path = _plain(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError('Appearance file missing or too large')
    return _cached_bytes(str(path), info.st_size, info.st_mtime_ns, info.st_ctime_ns, limit)


@lru_cache(maxsize=128)
def _cached_bytes(path, size, modified, changed, limit):
    # The directory is administrator-owned. Changed files invalidate this
    # bounded cache; the updater separately verifies every on-disk hash.
    with open(path, 'rb') as stream:
        result = stream.read(limit + 1)
    if len(result) > limit:
        raise ValueError('Appearance file grew during read')
    return result


def _relative(value):
    if (not isinstance(value, str) or not value or len(value) > 200
            or any(c in value for c in '\\:\x00')
            or any(ord(c) < 32 for c in value)):
        raise ValueError('Invalid appearance path')
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(p in ('.', '..') for p in path.parts):
        raise ValueError('Invalid appearance path')
    return path


def _asset(base, filename, digest, suffixes=STATIC_SUFFIXES):
    relative = _relative(filename)
    if relative.suffix.lower() not in suffixes:
        raise ValueError('Appearance asset type not allowed')
    if (not isinstance(digest, str) or len(digest) != 64
            or any(c not in '0123456789abcdef' for c in digest)):
        raise ValueError('Invalid appearance digest')
    path = _plain(base.joinpath(*relative.parts))
    content = _bytes(path, MAX_CSS_BYTES if path.suffix.lower() == '.css' else MAX_LOGO_BYTES)
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError('Appearance asset changed')
    return path, content


def _metadata(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid appearance object')
    name, theme = value.get('school_name'), value.get('theme')
    if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 80
            or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or not isinstance(theme, str) or theme not in THEMES):
        raise ValueError('Invalid school identity')
    return name, theme


def _manifest_base(data_folder):
    # An existing but damaged directory must not select release defaults.
    for root in _roots(data_folder):
        base = root / 'local-appearance'
        if os.path.lexists(base):
            return base
    return None


def _manifest(data_folder):
    base = _manifest_base(data_folder)
    if base is None:
        return None
    try:
        value = json.loads(_bytes(base / 'manifest.json', MAX_MANIFEST_BYTES))
        name, theme = _metadata(value)
        if value.get('schemaVersion') != LOCAL_APPEARANCE_VERSION:
            raise ValueError('Unsupported appearance version')
        style = value.get('style', {})
        allowed = {'website_css', 'website_css_sha256', 'logo', 'logo_sha256', 'static'}
        if not isinstance(style, dict) or set(style) - allowed:
            raise ValueError('Invalid appearance style')
        result = {'school_name': name, 'theme': theme, 'style': {}, '_static': {}}
        for key, public, suffixes in (
                ('website_css', '/school-appearance/website.css', {'.css'}),
                ('logo', '/school-appearance/logo', LOGO_SUFFIXES)):
            if key in style:
                result['_' + key] = _asset(base, style[key], style.get(key + '_sha256'), suffixes)
                result['style'][key] = public
        static_files = style.get('static', {})
        if not isinstance(static_files, dict) or len(static_files) > MAX_STATIC_FILES:
            raise ValueError('Invalid appearance static map')
        for url, descriptor in static_files.items():
            if not isinstance(url, str) or not url.startswith('/'):
                raise ValueError('Invalid appearance URL')
            relative = _relative(url[1:])
            if (relative.suffix.lower() not in STATIC_SUFFIXES
                    or not isinstance(descriptor, dict) or set(descriptor) != {'path', 'sha256'}):
                raise ValueError('Invalid appearance mapping')
            selected = _asset(base, descriptor['path'], descriptor['sha256'])
            if selected[0].suffix.lower() != relative.suffix.lower():
                raise ValueError('Appearance MIME types must agree')
            result['_static'][url] = selected
        return result
    except (OSError, ValueError, TypeError, UnicodeError):
        return None


def school_appearance_manifest_present(data_folder):
    return _manifest_base(data_folder) is not None


def school_appearance(data_folder):
    result = _manifest(data_folder)
    if result:
        return {key: result[key] for key in ('school_name', 'theme', 'style')}
    if school_appearance_manifest_present(data_folder):
        return None
    folder = Path(os.path.abspath(data_folder))
    path = folder.parent / 'school.json' if os.name == 'nt' else Path('/etc/elevhub/kiosk.json')
    try:
        value = json.loads(_bytes(path, MAX_MANIFEST_BYTES))
        name, theme = _metadata(value)
        return {'school_name': name, 'theme': theme}
    except (OSError, ValueError, TypeError, UnicodeError):
        return None


def school_appearance_asset(data_folder, asset):
    result = _manifest(data_folder)
    if not result:
        return None
    selected = result['_static'].get(asset)
    if selected is None:
        selected = result.get({'website.css': '_website_css', 'logo': '_logo'}.get(asset, ''))
    if selected is None:
        return None
    path, content = selected
    content_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    if path.suffix.lower() == '.css':
        content_type = 'text/css; charset=utf-8'
    return content, content_type
