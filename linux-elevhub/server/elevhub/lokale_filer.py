"""Sikker opslagning af lokale editorfiler; ingen uploads eller mappevisning."""
from pathlib import Path
from urllib.parse import unquote, urlsplit

FILTYPER = {'.html', '.js', '.css', '.json', '.svg', '.png', '.jpg', '.jpeg', '.gif',
            '.wav', '.mp3', '.ogg', '.woff', '.woff2', '.ttf', '.eot', '.wasm',
            '.mp4', '.webm', '.webp', '.txt', '.md', '.ico', '.sb3', '.hex', '.bin', '.zip'}


def find_fil(mappe, raa_sti):
    if not mappe or not raa_sti.startswith('/') or raa_sti.startswith('//'):
        return None
    try:
        sti = unquote(urlsplit(raa_sti).path, errors='strict')
        if (any(ord(c) < 32 for c in sti) or any(c in sti for c in ('\\', ':', '%'))
                or any(del_ in ('.', '..') or del_.endswith((' ', '.')) for del_ in sti.split('/') if del_)):
            return None
        rod = Path(mappe).resolve(strict=True)
        fil = (rod / (sti.lstrip('/') or 'index.html')).resolve(strict=True)
        if not fil.is_relative_to(rod) or not fil.is_file() or fil.suffix.lower() not in FILTYPER:
            return None
        return fil
    except (OSError, RuntimeError, ValueError, UnicodeError):
        return None
