"""Authenticated, bounded adapter to the existing local print service."""
import hashlib
import hmac
import http.client
import json
from pathlib import Path
import re
import socket
import threading
import time

from makerspace_store import Conflict, public_queue

_BACKEND = re.compile(r'127\.0\.0\.1:([0-9]{1,5})')
_KEY = re.compile(r'mkbridge_[A-Za-z0-9_-]{43}')
_FIELDS = ('id', 'name', 'location', 'model', 'host', 'serial', 'enabled', 'bed_type', 'version')
_TIMEOUT = 2.0
_FINGERPRINT = re.compile(r'^[0-9a-f]{64}$')
_PRINTER_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$')
_REQUEST_ID = re.compile(r'^[a-f0-9]{32}$')


class PrinterAdministration:
    def __init__(self, config=None, base=Path('.')):
        self.backend = self.key_path = None
        if config is None:
            return
        if not isinstance(config, dict) or set(config) != {'backend', 'key_file'}:
            raise ValueError('Printeradministration kræver backend og key_file.')
        match = _BACKEND.fullmatch(config['backend']) if isinstance(config['backend'], str) else None
        if not match or not 1 <= int(match[1]) <= 65535:
            raise ValueError('Printeradministrationens backend skal være 127.0.0.1:port.')
        if not isinstance(config['key_file'], str) or not config['key_file']:
            raise ValueError('Printeradministrationen mangler lokal nøglefil.')
        self.backend = ('127.0.0.1', int(match[1]))
        self.key_path = Path(base) / config['key_file']

    def _key(self):
        if self.backend is None or self.key_path.is_symlink() or not self.key_path.is_file() or self.key_path.stat().st_size > 128:
            raise OSError('Printeradministrationen er ikke tilgængelig.')
        try:
            key = self.key_path.read_text(encoding='ascii').strip()
        except UnicodeError as error:
            raise OSError('Printeradministrationen er ikke tilgængelig.') from error
        if not _KEY.fullmatch(key):
            raise OSError('Printeradministrationen er ikke tilgængelig.')
        return key

    def fingerprint(self, envelope):
        return hmac.new(self._key().encode(), json.dumps(envelope, sort_keys=True,
            ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode(), hashlib.sha256).hexdigest()

    def _request(self, body=None):
        key = self._key()
        connection = http.client.HTTPConnection(*self.backend, timeout=_TIMEOUT)
        deadline, watchdog = time.monotonic() + _TIMEOUT, None
        try:
            path = '/api/makerspace/printers' + ('/commands' if body is not None else '')
            headers = {'Authorization': 'Bearer ' + key, 'Accept': 'application/json'}
            raw = None
            if body is not None:
                raw = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
                headers['Content-Type'] = 'application/json'
            connection.request('POST' if body is not None else 'GET', path, raw, headers)
            sock = connection.sock
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Printertjenesten svarede ikke i tide.')
            # getresponse() can receive trickling headers indefinitely despite
            # a socket timeout. Interrupt the captured socket at the total deadline.
            def expire():
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            watchdog = threading.Timer(remaining, expire)
            watchdog.daemon = True
            watchdog.start()
            response = connection.getresponse()
            data = bytearray()
            while len(data) <= 256 * 1024:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Printertjenesten svarede ikke i tide.')
                sock.settimeout(remaining)
                chunk = response.read1(min(8192, 256 * 1024 + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if response.length == 0:
                    break
            if len(data) > 256 * 1024:
                raise OSError('Printertjenestens svar var for stort.')
            if response.status == 409:
                raise Conflict('Printeropsætningen eller request-ID er ændret. Hent overblikket igen.')
            if response.status == 400:
                raise ValueError('Printeropsætningen blev afvist. Kontrollér model, adresse, serienummer og adgangskode.')
            if response.status == 403:
                raise PermissionError('Printerhandlingen kræver lærerens egen bekræftelse.')
            if response.status != 200:
                raise OSError('Printeradministrationen er ikke tilgængelig.')
            result = json.loads(data)
            if not isinstance(result, dict):
                raise OSError('Ugyldigt svar fra printertjenesten.')
            return result
        except (http.client.HTTPException, UnicodeError, json.JSONDecodeError) as error:
            raise OSError('Printertjenestens svar kunne ikke bekræftes.') from error
        finally:
            if watchdog is not None:
                watchdog.cancel()
            connection.close()

    @staticmethod
    def _certificate(value):
        if not isinstance(value, dict) or set(value) != {'identity', 'channels'}:
            raise OSError('Ugyldig printercertifikatstatus.')
        identity = value['identity']
        if (not isinstance(identity, dict) or set(identity) != {'host', 'serial', 'version'}
                or any(not isinstance(identity[key], str) for key in ('host', 'serial'))
                or type(identity['version']) is not int or identity['version'] < 1):
            raise OSError('Ugyldig printercertifikatidentitet.')
        channels = value['channels']
        if not isinstance(channels, dict) or set(channels) != {'mqtt', 'ftp'}:
            raise OSError('Ugyldige printercertifikatkanaler.')
        clean = {'identity': identity.copy(), 'channels': {}}
        for channel, original in channels.items():
            if not isinstance(original, dict) or set(original) - {'status', 'pinned_fingerprint', 'pending', 'error'}:
                raise OSError('Ugyldig printercertifikatstatus.')
            if original.get('status') not in ('trusted', 'untrusted', 'confirmation_required', 'error'):
                raise OSError('Ugyldig printercertifikatstatus.')
            pinned = original.get('pinned_fingerprint')
            if pinned is not None and (not isinstance(pinned, str) or not _FINGERPRINT.fullmatch(pinned)):
                raise OSError('Ugyldigt printercertifikatfingeraftryk.')
            item = {'status': original['status'], 'pinned_fingerprint': pinned}
            if original.get('error') is not None:
                if not isinstance(original['error'], str) or len(original['error']) > 300:
                    raise OSError('Ugyldig certifikatfejl.')
                item['error'] = original['error']
            pending = original.get('pending')
            if pending is not None:
                required = {'printer_id', 'channel', 'old_fingerprint', 'new_fingerprint',
                            'host', 'serial', 'version', 'observed_at'}
                if (not isinstance(pending, dict) or set(pending) != required
                        or pending.get('channel') != channel
                        or pending.get('old_fingerprint') is not None and not _FINGERPRINT.fullmatch(pending['old_fingerprint'])
                        or not isinstance(pending.get('new_fingerprint'), str)
                        or not _FINGERPRINT.fullmatch(pending['new_fingerprint'])
                        or pending.get('host') != identity['host'] or pending.get('serial') != identity['serial']
                        or pending.get('version') != identity['version']
                        or type(pending.get('observed_at')) not in (int, float)):
                    raise OSError('Ugyldig afventende printercertifikat.')
                item['pending'] = pending.copy()
            clean['channels'][channel] = item
        return clean

    @staticmethod
    def _public(value):
        if not isinstance(value, dict) or any(key not in value for key in _FIELDS):
            raise OSError('Ugyldig printerbeskrivelse.')
        if any(not isinstance(value[key], str) or len(value[key]) > 100 for key in ('id', 'name', 'location', 'model', 'host', 'serial', 'bed_type')):
            raise OSError('Ugyldig printerbeskrivelse.')
        if type(value['version']) is not int or value['version'] < 1 or type(value['enabled']) is not bool:
            raise OSError('Ugyldig printerversion.')
        result = {**{key: value[key] for key in _FIELDS}, 'kind': '3d_print', 'adapter': 'bambu',
                  'requires_restart': value.get('requires_restart') is True}
        workflow = value.get('workflow')
        if workflow is not None:
            if (not isinstance(workflow, dict) or type(workflow.get('id')) is not str or
                    type(workflow.get('revision')) is not int or workflow['revision'] < 1 or
                    type(workflow.get('name')) is not str):
                raise OSError('Ugyldig printerworkflow.')
            result['workflow'] = {key: workflow[key] for key in ('id', 'revision', 'name')}
        else:
            # Optional metadata is omitted when the backend has no binding.
            pass
        if 'certificate_trust' in value:
            result['certificate_trust'] = PrinterAdministration._certificate(value['certificate_trust'])
        return result

    @staticmethod
    def _public_job(value):
        required = ('id', 'printer_id', 'filename', 'status', 'version', 'workflow_id',
                    'workflow_revision', 'workflow_name', 'step', 'production_authorized')
        if not isinstance(value, dict) or any(key not in value for key in required):
            raise OSError('Ugyldigt printerjob.')
        if any(not isinstance(value[key], str) or not value[key].strip()
               for key in ('id', 'printer_id', 'filename', 'status')):
            raise OSError('Ugyldigt printerjob.')
        if type(value['version']) is not int or value['version'] < 1:
            raise OSError('Ugyldig printerjobversion.')
        if value['workflow_id'] is not None and not isinstance(value['workflow_id'], str):
            raise OSError('Ugyldigt printerworkflow.')
        if value['workflow_revision'] is not None and (type(value['workflow_revision']) is not int or value['workflow_revision'] < 1):
            raise OSError('Ugyldig printerworkflow.')
        if value['workflow_name'] is not None and not isinstance(value['workflow_name'], str):
            raise OSError('Ugyldig printerworkflow.')
        if type(value['production_authorized']) is not bool:
            raise OSError('Ugyldig produktionsstatus.')
        step = value['step']
        if step is not None:
            if not isinstance(step, dict) or set(step) != {'id', 'label', 'role', 'action'} or any(not isinstance(step[k], str) for k in step):
                raise OSError('Ugyldigt printerjobtrin.')
            step = {key: step[key] for key in ('id', 'label', 'role', 'action')}
        return {key: value[key] for key in required[:-2]} | {'step': step,
                'production_authorized': value['production_authorized']}

    def snapshot(self):
        if self.backend is None:
            return {'status': 'not_configured', 'printers': [], 'models': [], 'bed_types': []}
        try:
            result = self._request()
            if not isinstance(result.get('printers'), list) or len(result['printers']) > 128:
                raise OSError('Ugyldigt printersvar.')
            jobs = result.get('workflow_jobs', [])
            if not isinstance(jobs, list) or len(jobs) > 100:
                raise OSError('Ugyldigt printerjobsvar.')
            snapshot = {'status': 'ok', 'printers': [self._public(row) for row in result['printers']],
                    'models': ['A1 mini', 'P1S', 'H2D'],
                    'bed_types': ['Textured PEI Plate', 'High Temp Plate', 'Supertack Plate'],
                    }
            if 'workflow_jobs' in result:
                snapshot['workflow_jobs'] = [self._public_job(job) for job in jobs]
            if 'queue' in result:
                snapshot['queue'] = public_queue(result['queue'])
            if 'certificate_operations' in result:
                operations = result['certificate_operations']
                if not isinstance(operations, list) or len(operations) > 100:
                    raise OSError('Ugyldige afventende certifikatændringer.')
                clean_operations = []
                for operation in operations:
                    if (not isinstance(operation, dict)
                            or operation.get('status') not in ('pending', 'error')):
                        raise OSError('Ugyldig afventende certifikatændring.')
                    clean = {'status': operation['status']}
                    for field in ('id', 'channel', 'fingerprint', 'expected_version', 'repair', 'request_id', 'actor'):
                        if field in operation:
                            value = operation[field]
                            if field == 'id' and (not isinstance(value, str) or not _PRINTER_ID.fullmatch(value)):
                                raise OSError('Ugyldigt journal-printer-ID.')
                            if field == 'channel' and value not in ('mqtt', 'ftp'):
                                raise OSError('Ugyldig journal-kanal.')
                            if field == 'fingerprint' and value is not None and (
                                    not isinstance(value, str) or not _FINGERPRINT.fullmatch(value)):
                                raise OSError('Ugyldigt journalfingeraftryk.')
                            if field == 'expected_version' and (type(value) is not int or value < 1):
                                raise OSError('Ugyldig journalversion.')
                            if field == 'repair' and type(value) is not bool:
                                raise OSError('Ugyldig repair-status i journal.')
                            if field == 'request_id' and (not isinstance(value, str) or not _REQUEST_ID.fullmatch(value)):
                                raise OSError('Ugyldigt journal-request-ID.')
                            if field == 'actor' and (not isinstance(value, str) or
                                    not re.fullmatch(r'teacher:[a-f0-9]{32}', value)):
                                raise OSError('Ugyldig journalaktør.')
                            clean[field] = value
                    if operation.get('error') is not None:
                        if not isinstance(operation['error'], str) or len(operation['error']) > 300:
                            raise OSError('Ugyldig journalfejl.')
                        clean['error'] = operation['error']
                    clean_operations.append(clean)
                snapshot['certificate_operations'] = clean_operations
            return snapshot
        except (OSError, ValueError, TypeError):
            return {'status': 'unavailable', 'printers': [], 'models': [], 'bed_types': []}

    def execute(self, actor_id, envelope):
        result = self._request({'actor': actor_id, 'envelope': envelope})
        if result.get('ok') is not True or result.get('action') != envelope['action']:
            raise OSError('Printerændringen kunne ikke bekræftes.')
        if envelope['action'] == 'printer.queue.pause':
            try:
                public = public_queue(result.get('result'))
            except ValueError as error:
                raise OSError('Printkøens ændring kunne ikke bekræftes.') from error
            if public['paused'] is not envelope['params']['paused']:
                raise OSError('Printkøens ændring kunne ikke bekræftes.')
        else:
            if envelope['action'] == 'printer.job.advance':
                public = self._public_job(result.get('result'))
            elif envelope['action'] == 'printer.certificate.confirm':
                certificate = result.get('result')
                if (not isinstance(certificate, dict)
                        or set(certificate) != {'id', 'version', 'channel', 'fingerprint', 'reconnected'}
                        or not isinstance(certificate.get('id'), str)
                        or certificate.get('channel') not in ('mqtt', 'ftp')
                        or type(certificate.get('version')) is not int
                        or type(certificate.get('reconnected')) is not bool
                        or not isinstance(certificate.get('fingerprint'), str)
                        or not _FINGERPRINT.fullmatch(certificate['fingerprint'])):
                    raise OSError('Printercertifikatbekræftelsen kunne ikke bekræftes.')
                public = certificate
            elif envelope['action'] == 'printer.certificate.probe':
                probe = result.get('result')
                if (not isinstance(probe, dict)
                        or set(probe) != {'id', 'version', 'identity', 'channels'}
                        or not isinstance(probe.get('id'), str)
                        or type(probe.get('version')) is not int
                        or probe.get('version') < 1):
                    raise OSError('Printercertifikatproben kunne ikke bekræftes.')
                identity = probe['identity']
                if (not isinstance(identity, dict) or set(identity) != {'host', 'serial', 'version'}
                        or not isinstance(identity['host'], str) or not isinstance(identity['serial'], str)
                        or identity['version'] != probe['version']):
                    raise OSError('Printercertifikatproben havde en ugyldig identitet.')
                channels = probe['channels']
                if not isinstance(channels, dict) or set(channels) != {'mqtt', 'ftp'}:
                    raise OSError('Printercertifikatproben mangler en kanal.')
                clean_channels = {}
                for channel, value in channels.items():
                    if not isinstance(value, dict) or value.get('status') not in (
                            'trusted', 'confirmation_required', 'error'):
                        raise OSError('Printercertifikatproben havde ugyldig status.')
                    clean = {'status': value['status']}
                    for field in ('observed_fingerprint', 'old_fingerprint', 'new_fingerprint'):
                        if value.get(field) is not None:
                            if not isinstance(value[field], str) or not _FINGERPRINT.fullmatch(value[field]):
                                raise OSError('Printercertifikatproben havde et ugyldigt fingeraftryk.')
                            clean[field] = value[field]
                    if value.get('error') is not None:
                        if not isinstance(value['error'], str) or len(value['error']) > 300:
                            raise OSError('Printercertifikatproben havde en ugyldig fejl.')
                        clean['error'] = value['error']
                    clean_channels[channel] = clean
                public = {'id': probe['id'], 'version': probe['version'],
                          'identity': identity.copy(), 'channels': clean_channels}
            elif envelope['action'] == 'printer.certificate.cancel':
                cancelled = result.get('result')
                if (not isinstance(cancelled, dict)
                        or set(cancelled) != {'id', 'version', 'operation_request_id',
                                               'cancelled', 'trust_preserved'}
                        or not isinstance(cancelled.get('id'), str)
                        or type(cancelled.get('version')) is not int
                        or not isinstance(cancelled.get('operation_request_id'), str)
                        or type(cancelled.get('cancelled')) is not bool
                        or type(cancelled.get('trust_preserved')) is not bool
                        or cancelled.get('cancelled') is not True):
                    raise OSError('Printercertifikatforsøget kunne ikke afsluttes.')
                public = cancelled
            else:
                public = self._public(result.get('result'))
        expected_version = (envelope['expected_version'] if envelope['action'] in
                            ('printer.certificate.confirm', 'printer.certificate.probe',
                             'printer.certificate.cancel')
                            else envelope['expected_version'] + 1)
        if (public['version'] != expected_version or
                (envelope['action'] != 'printer.create' and public['id'] != envelope['params'].get('id'))):
            raise OSError('Printerændringens identitet eller version kunne ikke bekræftes.')
        return {'ok': True, 'action': envelope['action'], 'result': public}
