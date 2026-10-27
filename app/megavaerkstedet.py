"""Navigation only: no machine clients, queue workers or installation actions."""
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


def print_url(value):
    if value is None or value == '':
        return ''
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() for c in value):
        raise ValueError('Angiv en HTTP- eller HTTPS-adresse til det eksisterende printværksted.')
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise ValueError('Printadressen må ikke indeholde login, søgeparametre eller fragmenter.')
    parsed.port  # Reject malformed port numbers.
    if any(c in value for c in '<>"\'\\;'):
        raise ValueError('Printadressen indeholder ugyldige tegn.')
    return value


def render(web, config_path=None):
    web = Path(web)
    url = ''
    error = ''
    try:
        path = Path(config_path) if config_path else web.parent / 'megavaerkstedet.json'
        if path.is_file():
            if path.stat().st_size > 4096:
                raise ValueError('Makerværkstedets indstilling er for stor.')
            config = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(config, dict):
                raise ValueError('Makerværkstedets indstilling skal være et JSON-objekt.')
            url = print_url(config.get('print_url'))
    except (OSError, ValueError, UnicodeError):
        error = 'Printværkstedets adresse skal kontrolleres i Makerværkstedets opsætning.'
    # Read the installed UI at request time: preserve local laser UI changes.
    laser = web.joinpath('laserstation.html').read_text(encoding='utf-8')
    body = re.search(r'<body\b[^>]*>(.*?)</body\s*>', laser, re.S | re.I)
    if not body:
        raise ValueError('Den eksisterende laserflade kunne ikke indlæses.')
    station_body = body.group(1)
    if re.search(r'<script\b', station_body, re.I):
        raise ValueError('Laserfladens scripts skal ligge i dokumentets head før samling.')
    settings = json.dumps({'printUrl': url, 'error': error}, ensure_ascii=False).replace('<', '\\u003c')
    page = web.joinpath('megavaerkstedet.html').read_text(encoding='utf-8')
    page = page.replace('<!--MEGA_SETTINGS-->', settings).replace('<!--LASER_BODY-->', station_body)
    origin = f'{urlsplit(url).scheme}://{urlsplit(url).netloc}' if url else "'none'"
    policy = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
              "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "
              f"frame-src {origin}; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    return page, policy
