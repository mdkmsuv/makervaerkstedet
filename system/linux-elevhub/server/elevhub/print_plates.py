"""Single-purpose print plate interface on the school's designated IoT network."""
import ipaddress
import json
import re
import ssl
from urllib.parse import urlsplit

from makerspace_store import Conflict


def plate_config(folder):
    path = folder / 'pladenet.json'
    try:
        if path.is_symlink() or path.stat().st_size > 256:
            return None, None
        config = json.loads(path.read_text(encoding='utf-8-sig'))
        network = ipaddress.ip_network(config['cidr'])
        if network.prefixlen >= (8 if network.version == 4 else 32):
            return network, teacher_url(config.get('laererside'))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None, None


def teacher_url(value):
    if not isinstance(value, str) or re.search(r'[\s\\?#]', value):
        return None
    try:
        url = urlsplit(value)
        if (url.scheme == 'https' and url.hostname and url.path == '/laerer'
                and url.username is None and url.password is None and url.port != 0):
            return value
    except ValueError:
        pass
    return None


def allowed_peer(address, network):
    return network is not None and ipaddress.ip_address(address) in network


def plate_request(printers, body):
    if body is None:
        rows = printers.plates()['plates']
        return {'plates': [{key: row[key] for key in ('id', 'name', 'status', 'token')} for row in rows]}
    if (set(body) != {'id', 'token'} or not isinstance(body['id'], str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', body['id'])
            or not isinstance(body['token'], str) or not re.fullmatch(r'[a-f0-9]{32}', body['token'])):
        raise ValueError('Vælg en aktuel printplade.')
    if printers.plates(body).get('ok') is not True:
        raise OSError('Kvitteringen kunne ikke bekræftes.')
    return {'ok': True}


def handle(handler, path, body=None):
    if path not in ('/ryd-plade', '/api/printplader'):
        return False
    network, review_url = plate_config(handler.server.tilstand.mappe)
    if (not isinstance(handler.connection, ssl.SSLSocket)
            or not allowed_peer(handler.client_address[0], network)):
        message = 'Forbind enheden til værkstedets IoT-netværk for at åbne printpladerne.'
        if path == '/ryd-plade':
            handler.fil('print-plates.html', status=403)
        else:
            handler.svar(403, {'error': message})
    elif path == '/ryd-plade' and body is None:
        handler.fil('print-plates.html')
    elif path != '/api/printplader':
        handler.svar(405, {'error': 'Brug pladesidens knap.'})
    elif body is not None and (handler.headers.get('Origin') != 'https://' + handler.headers.get('Host', '')
                               or handler.headers.get('Sec-Fetch-Site') == 'cross-site'):
        handler.svar(403, {'error': 'Brug pladesidens egen knap.'})
    else:
        try:
            result = plate_request(handler.server.makerspace.printers, body)
            if body is None and review_url:
                result['review_url'] = review_url
            handler.svar(200, result)
        except Conflict:
            handler.svar(409, {'error': 'Pladens status er ændret. Kontrollér printeren igen.'})
        except ValueError:
            handler.svar(400, {'error': 'Vælg en aktuel printplade.'})
        except (OSError, KeyError, TypeError):
            handler.svar(503, {'error': 'Printværkstedet svarer ikke. Prøv igen om lidt.'})
    return True
