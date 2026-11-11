"""Navigation only: no machine clients, queue workers or installation actions."""
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


def print_url(value, name='Printadressen'):
    """Validate an address the start page links to. Empty means not set up."""
    if value is None or value == '':
        return ''
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() for c in value):
        raise ValueError(f'{name} skal være en HTTP- eller HTTPS-adresse.')
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise ValueError(f'{name} må ikke indeholde login, søgeparametre eller fragmenter.')
    parsed.port  # Reject malformed port numbers.
    if any(c in value for c in '<>"\'\\;'):
        raise ValueError(f'{name} indeholder ugyldige tegn.')
    return value


def render(web, config_path=None):
    """The laser computer's start page: laser, 3D print for teachers, administration.

    The two teacher pages forbid framing, so the page links to them in the
    same tab; the browser's back button returns to the start page.
    Without ``admin_url`` the browser links to the queue server's control panel.
    """
    web = Path(web)
    urls = {'print_url': '', 'admin_url': ''}
    error = ''
    try:
        path = Path(config_path) if config_path else web.parent / 'makervaerkstedet.json'
        if not config_path and not path.is_file():
            # Installations from before the rename keep working.
            path = web.parent / 'megavaerkstedet.json'
        if path.is_file():
            if path.stat().st_size > 4096:
                raise ValueError('Makerværkstedets indstilling er for stor.')
            config = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(config, dict):
                raise ValueError('Makerværkstedets indstilling skal være et JSON-objekt.')
            urls = {'print_url': print_url(config.get('print_url')),
                    'admin_url': print_url(config.get('admin_url'), 'Administrationens adresse')}
    except (OSError, ValueError, UnicodeError):
        error = 'Adresserne skal kontrolleres i Makerværkstedets opsætning (makervaerkstedet.json).'
    # Read the installed UI at request time: preserve local laser UI changes.
    laser = web.joinpath('laserstation.html').read_text(encoding='utf-8')
    body = re.search(r'<body\b[^>]*>(.*?)</body\s*>', laser, re.S | re.I)
    if not body:
        raise ValueError('Den eksisterende laserflade kunne ikke indlæses.')
    station_body = body.group(1)
    if re.search(r'<script\b', station_body, re.I):
        raise ValueError('Laserfladens scripts skal ligge i dokumentets head før samling.')
    settings = json.dumps({'printUrl': urls['print_url'], 'adminUrl': urls['admin_url'], 'error': error},
                          ensure_ascii=False).replace('<', '\\u003c')
    page = web.joinpath('makervaerkstedet.html').read_text(encoding='utf-8')
    page = page.replace('<!--MAKER_SETTINGS-->', settings).replace('<!--LASER_BODY-->', station_body)
    policy = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
              "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "
              "frame-src 'none'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    return page, policy
