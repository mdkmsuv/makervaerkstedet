"""Bounded app/profile routes on the existing teacher and root listeners."""
import os
import re
import sqlite3
import ssl
from urllib.parse import urlsplit

from app_admin import AppAdmin
from school_profile_admin import SchoolProfileAdmin, SchoolProfileError
from simple_updates import UpdateError

PROFILE_LIMIT = 3 * 1024**2
APP_LIMIT = 128 * 1024
PROFILE = '/api/admin/school-profile'
TEACHER = frozenset(('/api/admin/apps', '/api/admin/apps/order', '/api/admin/apps/defaults', PROFILE))


def handle(handler, path, length=0):
    raw = urlsplit(handler.path).path
    catalog = re.fullmatch(r'/apt/apps/catalog/([a-f0-9]{64})\.(json|minisig)', raw)
    school_device = path == '/api/device/school-profile' or raw.startswith('/api/device/school-profile/assets/')
    if path not in TEACHER and path != '/api/device/apps' and not raw.startswith('/apt/apps/catalog/') and not school_device:
        return False
    try:
        profile = path == PROFILE or school_device
        service = getattr(handler.server, 'simple_updates', None)
        if not profile and service is None:
            raise UpdateError('simple_updates_unavailable', 503)
        if profile:
            admin = getattr(handler.server, 'school_profile_admin', None)
            if admin is None:
                admin = handler.server.school_profile_admin = SchoolProfileAdmin(handler.server.tilstand.mappe)
        else:
            admin = getattr(service, 'app_admin', None)
            if admin is None:
                admin = service.app_admin = AppAdmin(service)
        if school_device:
            if handler.command not in ('GET', 'HEAD'):
                raise UpdateError('method_not_allowed', 405)
            if handler.headers.get('Origin') is not None or not hasattr(handler, 'enhed'):
                raise UpdateError('native_device_required', 403)
            if handler.enhed('root') is None:
                return True
            if path == '/api/device/school-profile':
                handler.svar(200, admin.distribution())
            else:
                match = re.fullmatch(r'/api/device/school-profile/assets/([a-f0-9]{64})(\.(?:png|ttf|css))', raw)
                if match is None:
                    raise UpdateError('profile_asset_not_found', 404)
                handler.svar(200, admin.distributed_asset(*match.groups()), 'application/octet-stream')
            return True
        if raw.startswith('/apt/apps/catalog/'):
            if catalog is None:
                raise UpdateError('invalid_app_catalog_path', 404)
            if handler.command not in ('GET', 'HEAD'):
                raise UpdateError('method_not_allowed', 405)
            file = admin.catalog_file(*catalog.groups())
            with file.open('rb') as stream:
                handler.send_response(200)
                handler.send_header('Content-Type', 'application/octet-stream')
                handler.send_header('Content-Length', str(os.fstat(stream.fileno()).st_size))
                handler.send_header('X-Content-Type-Options', 'nosniff')
                handler.end_headers()
                if handler.command == 'GET':
                    while chunk := stream.read(64 * 1024):
                        handler.wfile.write(chunk)
            return True
        if path in TEACHER:
            if not hasattr(handler, 'logget_ind') or not handler.logget_ind():
                raise UpdateError('teacher_login_required', 401)
            if handler.command in ('GET', 'HEAD'):
                if path.endswith(('/order', '/defaults')):
                    raise UpdateError('method_not_allowed', 405)
                response = admin.overview()
            elif handler.command == 'POST':
                origins, hosts = handler.headers.get_all('Origin', []), handler.headers.get_all('Host', [])
                scheme = 'https://' if isinstance(handler.connection, ssl.SSLSocket) else 'http://'
                if len(origins) != 1 or len(hosts) != 1 or origins[0] != scheme + hosts[0]:
                    raise UpdateError('teacher_origin_required', 403)
                if path == '/api/admin/apps':
                    raise UpdateError('method_not_allowed', 405)
                body = _body(handler, length, PROFILE_LIMIT if profile else APP_LIMIT)
                response = admin.save(body) if profile else admin.defaults(body) if path.endswith('/defaults') else admin.order(body)
            else:
                raise UpdateError('method_not_allowed', 405)
        else:
            if handler.command != 'POST':
                raise UpdateError('method_not_allowed', 405)
            if handler.headers.get('Origin') is not None or not hasattr(handler, 'enhed'):
                raise UpdateError('native_device_required', 403)
            device = handler.enhed('root')
            if device is None:
                return True
            response = admin.poll(device, _body(handler, length, APP_LIMIT))
        handler.svar(200, response)
    except (UpdateError, SchoolProfileError) as error:
        handler.close_connection = True
        handler.svar(error.status, {'error': error.code})
    except (ValueError, TypeError, KeyError, OverflowError):
        handler.close_connection = True
        handler.svar(400, {'error': 'invalid_app_request'})
    except (OSError, sqlite3.Error):
        handler.close_connection = True
        handler.svar(503, {'error': 'app_administration_unavailable'})
    return True


def _body(handler, length, limit):
    if handler.headers.get_content_type() != 'application/json':
        raise UpdateError('json_required', 415)
    if type(length) is not int or not 0 < length <= limit:
        raise UpdateError('invalid_app_body_size', 413)
    from release_feed import strict_json
    value = strict_json(handler.laes_krop(length))
    if not isinstance(value, dict):
        raise UpdateError('invalid_app_request')
    return value
