"""Exact local project-shelf origins shared by pupil print and design services."""
import json
from urllib.parse import urlparse
import re

DEFAULT_PROJECT_ORIGINS = ('http://127.0.0.1:8791', 'http://localhost:8791')


def validate_project_origins(values):
    """Trust only explicitly named HTTP loopback parents, never a URL prefix."""
    if not isinstance(values, (list, tuple)) or len(values) > 32:
        raise ValueError('allowed_origins skal være en liste med højst 32 lokale origins.')
    origins = []
    for value in values:
        if not isinstance(value, str) or not re.fullmatch(
                r'http://(?:localhost|127\.0\.0\.1|\[::1\]):[1-9][0-9]{0,4}', value):
            raise ValueError('Projektserveren kræver en præcis HTTP-loopback-origin med port.')
        port = urlparse(value).port
        if not port or port > 65535:
            raise ValueError('Projektserverens port er ugyldig.')
        # Browser MessageEvent.origin omits HTTP's default port, even when
        # the configured URL explicitly names it.
        if port == 80:
            value = value.rsplit(':', 1)[0]
        if value not in origins:
            origins.append(value)
    return tuple(origins)


def configured_project_origins(path):
    if not path.exists():
        return DEFAULT_PROJECT_ORIGINS
    if path.stat().st_size > 8192:
        raise ValueError(f'{path.name} er for stor.')
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(config, dict) or set(config) != {'allowed_origins'}:
        raise ValueError(f'{path.name} skal indeholde allowed_origins.')
    return validate_project_origins(config['allowed_origins'])

