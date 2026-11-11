"""Authenticated loopback adapter for teacher-managed laser stations.

This adapter only carries configuration/workflow commands to the local teacher
backend.  It does not probe or control a physical laser itself.
"""
import hashlib
import hmac
import http.client
import json
from pathlib import Path
import re
import socket
import threading
import time

from makerspace_store import Conflict

_BACKEND = re.compile(r'^127\.0\.0\.1:([0-9]{1,5})$')
_KEY = re.compile(r'^mkbridge_[A-Za-z0-9_-]{43}$')
_ID = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$')
_HEX = re.compile(r'^[a-f0-9]{32}$')
_MODELS = frozenset(('fbb2', 'fhexa1'))
_ROLES = frozenset(('operator', 'teacher'))
_ACTIONS = frozenset(('submit', 'prepare', 'approve', 'produce', 'collect', 'clear'))
_MAX = 1024 * 1024
_TIMEOUT = 2.0


class LaserAdministration:
    def __init__(self, config=None, base=Path('.')):
        self.backend = self.key_path = None
        if config is None:
            return
        if not isinstance(config, dict) or set(config) != {'backend', 'key_file'}:
            raise ValueError('Laseradministrationen kræver backend og key_file.')
        match = _BACKEND.fullmatch(config['backend']) if isinstance(config.get('backend'), str) else None
        if not match or not 1 <= int(match[1]) <= 65535:
            raise ValueError('Laseradministrationens backend skal være 127.0.0.1:port.')
        if not isinstance(config.get('key_file'), str) or not config['key_file']:
            raise ValueError('Laseradministrationen mangler lokal nøglefil.')
        self.backend = ('127.0.0.1', int(match[1]))
        self.key_path = Path(base) / config['key_file']

    def _key(self):
        if self.backend is None or self.key_path.is_symlink() or not self.key_path.is_file() or self.key_path.stat().st_size > 128:
            raise OSError('Laseradministrationen er ikke tilgængelig.')
        try:
            key = self.key_path.read_text(encoding='ascii').strip()
        except (OSError, UnicodeError) as error:
            raise OSError('Laseradministrationen er ikke tilgængelig.') from error
        if not _KEY.fullmatch(key):
            raise OSError('Laseradministrationen er ikke tilgængelig.')
        return key

    def fingerprint(self, envelope):
        return hmac.new(self._key().encode(), json.dumps(envelope, sort_keys=True,
            ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode(), hashlib.sha256).hexdigest()

    def _request(self, body=None):
        key = self._key()
        deadline = time.monotonic() + _TIMEOUT
        connection = http.client.HTTPConnection(*self.backend, timeout=max(0.001, deadline - time.monotonic()))
        watchdog = None
        try:
            raw = None
            headers = {'Authorization': 'Bearer ' + key, 'Accept': 'application/json'}
            if body is not None:
                raw = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
                if len(raw) > _MAX:
                    raise ValueError('Laseradministrationens forespørgsel er for stor.')
                headers['Content-Type'] = 'application/json'
            connection.request('POST' if body is not None else 'GET',
                               '/api/makerspace/laser' + ('/commands' if body is not None else ''), raw, headers)
            sock = connection.sock
            if sock is not None:
                def expire():
                    try: sock.shutdown(socket.SHUT_RDWR)
                    except OSError: pass
                watchdog = threading.Timer(max(0, deadline - time.monotonic()), expire); watchdog.daemon = True; watchdog.start()
            response = connection.getresponse()
            data = bytearray()
            while len(data) <= _MAX:
                remaining = deadline - time.monotonic()
                if remaining <= 0: raise TimeoutError('Laseradministrationen svarede ikke i tide.')
                if sock is not None:
                    # HTTP/1.0 can close the connection after read1 consumed
                    # the final body bytes. The response can still report EOF.
                    try: sock.settimeout(remaining)
                    except OSError: pass
                chunk = response.read1(min(8192, _MAX + 1 - len(data)))
                if not chunk: break
                data.extend(chunk)
            if len(data) > _MAX: raise OSError('Laseradministrationens svar var for stort.')
            if response.status == 409: raise Conflict('Laseropsætningen eller request-ID er ændret.')
            if response.status == 400: raise ValueError('Laserændringen blev afvist.')
            if response.status == 403: raise PermissionError('Laseradministrationen afviste ændringen.')
            if response.status != 200: raise OSError('Laseradministrationen er ikke tilgængelig.')
            result = json.loads(data)
            if not isinstance(result, dict): raise OSError('Ugyldigt svar fra laseradministrationen.')
            return result
        except (http.client.HTTPException, UnicodeError, json.JSONDecodeError) as error:
            raise OSError('Laseradministrationens svar kunne ikke bekræftes.') from error
        finally:
            if watchdog is not None: watchdog.cancel()
            connection.close()

    @staticmethod
    def _station(value):
        fields = ('id', 'name', 'location', 'model', 'enabled', 'version', 'workflow')
        if not isinstance(value, dict) or any(k not in value for k in fields): raise OSError('Ugyldig laserstation.')
        if (not isinstance(value['id'], str) or not _ID.fullmatch(value['id']) or
                any(not isinstance(value[k], str) for k in ('name', 'location', 'model')) or
                len(value['name']) > 80 or len(value['location']) > 80 or len(value['model']) > 40 or value['model'] not in _MODELS):
            raise OSError('Ugyldig laserstation.')
        if type(value['enabled']) is not bool or type(value['version']) is not int or value['version'] < 1:
            raise OSError('Ugyldig laserstation.')
        workflow = value['workflow']
        if workflow is not None:
            if not isinstance(workflow, dict) or set(workflow) != {'id', 'revision', 'name'} or not isinstance(workflow['id'], str) or not _HEX.fullmatch(workflow['id']) or type(workflow['revision']) is not int or workflow['revision'] < 1 or not isinstance(workflow['name'], str) or len(workflow['name']) > 80:
                raise OSError('Ugyldig laserworkflow.')
            workflow = {'id': workflow['id'], 'revision': workflow['revision'], 'name': workflow['name']}
        return {'id': value['id'], 'name': value['name'], 'location': value['location'], 'model': value['model'],
                'enabled': value['enabled'], 'version': value['version'], 'workflow': workflow}

    @staticmethod
    def _job(value):
        fields = ('id', 'project', 'status', 'station_id', 'version', 'workflow_id', 'workflow_revision',
                  'workflow_name', 'step', 'production_authorized', 'physical_done', 'handoff_claimed')
        if not isinstance(value, dict) or any(k not in value for k in fields): raise OSError('Ugyldigt laserjob.')
        if (not isinstance(value['id'], str) or not _HEX.fullmatch(value['id']) or
                any(not isinstance(value[k], str) or len(value[k]) > 160 for k in ('project', 'status')) or
                type(value['version']) is not int or value['version'] < 0):
            raise OSError('Ugyldigt laserjob.')
        if value['station_id'] is not None and (not isinstance(value['station_id'], str) or not _ID.fullmatch(value['station_id'])): raise OSError('Ugyldig station.')
        if value['workflow_id'] is not None and (not isinstance(value['workflow_id'], str) or not _HEX.fullmatch(value['workflow_id'])): raise OSError('Ugyldigt workflow.')
        if value['workflow_revision'] is not None and (type(value['workflow_revision']) is not int or value['workflow_revision'] < 1): raise OSError('Ugyldigt workflow.')
        if value['workflow_name'] is not None and (not isinstance(value['workflow_name'], str) or len(value['workflow_name']) > 80): raise OSError('Ugyldigt workflow.')
        if any(type(value[k]) is not bool for k in ('production_authorized', 'physical_done', 'handoff_claimed')): raise OSError('Ugyldig laserjobstatus.')
        step = value['step']
        if step is not None:
            if (not isinstance(step, dict) or set(step) != {'id', 'label', 'role', 'action'} or
                    any(not isinstance(step[k], str) or len(step[k]) > 100 for k in step) or
                    not _ID.fullmatch(step['id']) or step['role'] not in _ROLES or step['action'] not in _ACTIONS): raise OSError('Ugyldigt laserjobtrin.')
            step = {k: step[k] for k in ('id', 'label', 'role', 'action')}
        return {'id': value['id'], 'project': value['project'], 'status': value['status'], 'station_id': value['station_id'],
                'version': value['version'], 'workflow_id': value['workflow_id'], 'workflow_revision': value['workflow_revision'],
                'workflow_name': value['workflow_name'], 'step': step, 'production_authorized': value['production_authorized'],
                'physical_done': value['physical_done'], 'handoff_claimed': value['handoff_claimed']}

    def snapshot(self):
        if self.backend is None: return {'status': 'not_configured', 'stations': [], 'jobs': []}
        try:
            data = self._request()
            stations, jobs = data.get('stations'), data.get('jobs')
            if not isinstance(stations, list) or not isinstance(jobs, list) or len(stations) > 100 or len(jobs) > 100: raise OSError('Ugyldigt lasersnapshot.')
            return {'status': 'ok', 'stations': [self._station(v) for v in stations], 'jobs': [self._job(v) for v in jobs]}
        except PermissionError:
            raise
        except (OSError, ValueError, TypeError): return {'status': 'unavailable', 'stations': [], 'jobs': []}

    def execute(self, actor_id, envelope):
        result = self._request({'actor': actor_id, 'envelope': envelope})
        if result.get('ok') is not True or result.get('action') != envelope['action']: raise OSError('Laserændringen kunne ikke bekræftes.')
        value = self._job(result.get('result')) if envelope['action'].startswith('laser.job.') else self._station(result.get('result'))
        if value['id'] != envelope['params'].get('id') or value['version'] != envelope['expected_version'] + 1:
            raise OSError('Laserændringens identitet eller version kunne ikke bekræftes.')
        return {'ok': True, 'action': envelope['action'], 'result': value}
