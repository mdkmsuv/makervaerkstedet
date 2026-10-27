"""Versioned printer configuration in the existing print-service process.

settings.json owns printer configuration; the existing queue owns print jobs.
Each authority commits its own mutation, receipt and audit together. Physical
dispatch remains behind the queue's existing checks and durable send barrier.
"""
import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import time

from .hardware_workflows import validate_binding
from .bambu import CERTIFICATE_CHANNELS, CertificatePins, probe_printer_certificates
from .printer_config import ConfigConflict, validate_printer, public_printer
from .profiles import MODELS

BED_TYPES = ['Textured PEI Plate', 'High Temp Plate', 'Supertack Plate']
_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$')
_REQUEST_ID = re.compile(r'^[a-f0-9]{32}$')
_ACTOR = re.compile(r'teacher:[a-f0-9]{32}')


class PrinterBridge:
    def __init__(self, app, driver_factory):
        self.app, self.driver_factory = app, driver_factory
        key_path = app.folder / 'makerspace-bridge.key'
        if key_path.is_symlink():
            raise ValueError('Bridge-nøglen må ikke være et symbolsk link.')
        if not key_path.exists():
            descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w', encoding='ascii') as stream:
                stream.write('mkbridge_' + secrets.token_urlsafe(32))
        if key_path.stat().st_size > 128:
            raise ValueError('Ugyldig bridge-nøgle.')
        self.token = key_path.read_text(encoding='ascii').strip()
        if not re.fullmatch(r'mkbridge_[A-Za-z0-9_-]{43}', self.token):
            raise ValueError('Ugyldig bridge-nøgle.')
        if os.name != 'nt':
            key_path.chmod(0o600)
        self._recovery_errors = {}
        self._recover_certificate_operations()

    def _certificate_operations_public(self):
        operations = self.app.settings.get('_certificate_operations', {})
        if not isinstance(operations, dict):
            return [{'status': 'error', 'error': 'Afventende certifikatændringer kunne ikke læses.'}]
        result = []
        for key, operation in operations.items():
            if not isinstance(operation, dict):
                result.append({'status': 'error', 'error': 'Afventende certifikatændring kunne ikke læses.'})
                continue
            actor, _, request_id = str(key).rpartition(':')
            item = {field: operation.get(field) for field in
                    ('id', 'channel', 'fingerprint', 'expected_version', 'repair')}
            item.update(actor=actor, request_id=request_id, status='pending')
            if key in self._recovery_errors:
                item.update(status='error', error=self._recovery_errors[key])
            result.append(item)
        return result

    def _recover_certificate_operations(self):
        """Finish durable, already teacher-authorized operations before drivers."""
        operations = self.app.settings.get('_certificate_operations', {})
        if not isinstance(operations, dict):
            self._recovery_errors['_certificate_operations'] = 'Afventende certifikatændringer kunne ikke læses.'
            return
        for key, journal in list(operations.items()):
            try:
                actor, request_id = key.rsplit(':', 1)
                if not _ACTOR.fullmatch(actor) or not _REQUEST_ID.fullmatch(request_id):
                    raise ValueError('Ugyldigt request-ID i certifikatjournalen.')
                if not isinstance(journal, dict):
                    raise ValueError('Certifikatjournalen er ugyldig.')
                printer_id = journal.get('id')
                config = next((p for p in self.app.settings.get('printers', [])
                               if p.get('id') == printer_id), None)
                if config is None:
                    raise ConfigConflict('Printeren i certifikatjournalen findes ikke længere.')
                channel, fingerprint = journal.get('channel'), journal.get('fingerprint')
                expected = journal.get('expected_version')
                repair = journal.get('repair', False)
                if (channel not in CERTIFICATE_CHANNELS or not isinstance(fingerprint, str)
                        or not re.fullmatch(r'[0-9a-f]{64}', fingerprint)
                        or type(expected) is not int or type(repair) is not bool
                        or config.get('version', 1) != expected
                        or config.get('host', '') != journal.get('host', '')
                        or config.get('serial', '') != journal.get('serial', '')):
                    raise ConfigConflict('Certifikatjournalens printeridentitet eller version er forældet.')
                params = {'id': printer_id, 'channel': channel, 'fingerprint': fingerprint}
                if repair:
                    params['repair'] = True
                envelope = {'action': 'printer.certificate.confirm', 'params': params,
                            'expected_version': expected, 'request_id': request_id}
                digest = hmac.new(self.token.encode(), json.dumps(
                    envelope, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                    allow_nan=False).encode(), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(digest, journal.get('digest', '')):
                    # Compatibility with the brief window where an explicit
                    # repair:false was included in the original envelope.
                    legacy_params = dict(params)
                    if not repair:
                        legacy_params['repair'] = False
                    legacy = {'action': envelope['action'], 'params': legacy_params,
                              'expected_version': expected, 'request_id': request_id}
                    legacy_digest = hmac.new(self.token.encode(), json.dumps(
                        legacy, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                        allow_nan=False).encode(), hashlib.sha256).hexdigest()
                    if not hmac.compare_digest(legacy_digest, journal.get('digest', '')):
                        raise ConfigConflict('Certifikatjournalens lærerbeslutning kunne ikke valideres.')
                self._certificate_preflight(config, repair=repair)
                pins = self._certificate_pins(config)
                pins.confirm(channel, fingerprint, allow_already=True)
                self._invalidate_bed_for_certificate(config)
                response = {'ok': True, 'action': envelope['action'], 'result': {
                    'id': printer_id, 'version': expected, 'channel': channel,
                    'fingerprint': fingerprint, 'reconnected': False}}
                settings = copy.deepcopy(self.app.settings)
                settings.setdefault('_certificate_operations', {}).pop(key, None)
                settings.setdefault('_makerspace_receipts', {})[key] = {
                    'digest': digest, 'response': response}
                settings.setdefault('_makerspace_audit', []).append({
                    'at': time.time(), 'actor': actor, 'action': envelope['action'],
                    'target': printer_id, 'request_id': request_id, 'outcome': 'recovered',
                    'fields': [channel]})
                self._persist(settings)
                self.app.settings = settings
            except Exception as error:
                self._recovery_errors[str(key)] = str(error)[:300]

    def snapshot(self):
        with self.app.settings_lock, self.app.queue.lock:
            rows = []
            for row in self.app.settings['printers']:
                public = public_printer(row)
                # Settings written by older pilot versions have no bed_type.
                # Keep the persisted row untouched, but make the public
                # contract complete for the teacher-panel adapter.
                public.setdefault('bed_type', BED_TYPES[0])
                public['certificate_trust'] = self._certificate_status(row)
                public.update(kind='3d_print', adapter='bambu', requires_restart=bool(
                    self.app.queue.printers.get(row['id'], {}).get('configuration_error')))
                rows.append(public)
            workflow_jobs = self.app.queue.workflow_jobs()
            return {'printers': rows, 'models': list(MODELS), 'bed_types': BED_TYPES,
                    'workflow_jobs': workflow_jobs,
                    'certificate_operations': self._certificate_operations_public(),
                    'queue': {'id': 'print', 'paused': self.app.queue.paused,
                              'version': self.app.queue.queue_control_version}}

    def _certificate_pins(self, config):
        return CertificatePins(
            self.app.folder / 'certificates', config['id'], strict=True,
            identity={'host': config.get('host', ''), 'serial': config.get('serial', ''),
                      'version': config.get('version', 1)})

    def _certificate_status(self, config):
        pins = self._certificate_pins(config)
        status = pins.status()
        return {'identity': {'host': config.get('host', ''), 'serial': config.get('serial', ''),
                             'version': config.get('version', 1)},
                'channels': {channel: status[channel] for channel in CERTIFICATE_CHANNELS}}

    def certificate_snapshot(self):
        with self.app.settings_lock, self.app.queue.lock:
            return {'printers': [
                {'id': row['id'], 'name': row['name'], 'certificate_trust': self._certificate_status(row)}
                for row in self.app.settings['printers']
            ], 'certificate_operations': self._certificate_operations_public()}

    def _restart_after_certificate(self, config):
        printer_id = config['id']
        with self.app.queue.lock:
            old_driver = self.app.drivers.get(printer_id)
        if old_driver is None:
            return False
        try:
            old_driver.stop()
            driver = self.driver_factory(config, self.app.queue, self.app.slicer.profiles)
            with self.app.queue.lock:
                self.app.drivers[printer_id] = driver
            driver.start()
            return True
        except Exception:
            with self.app.queue.lock:
                self.app.queue.offline(printer_id, 'Certifikatbekræftelsen er gemt, men forbindelsen kunne ikke genstartes.')
            return False

    def _certificate_preflight(self, config, *, repair=False):
        """Reject trust changes while physical printer state is unresolved."""
        printer_id = config['id']
        with self.app.queue.lock:
            active = self.app.queue.active(printer_id)
            blocked = self.app.queue.workflow_blocks_bed(printer_id)
            if (active or blocked) and not (repair and self.app.queue.paused):
                raise ConfigConflict('Vent, til printerens aktuelle job og eventuelle rydning er afsluttet.')
            # An isolated job without a workflow is a teacher-marked physical
            # uncertainty. A certificate change cannot silently turn that into
            # a trusted printer; the teacher must resolve it first.
            unresolved_isolated = self.app.queue.db.execute('''SELECT 1 FROM jobs
                WHERE printer_id=? AND isolated=1 AND physical_resolved=0
                AND workflow IS NULL LIMIT 1''',
                (printer_id,)).fetchone()
            if unresolved_isolated and not (repair and self.app.queue.paused):
                raise ConfigConflict('Lærerens isolerede job skal afklares, før printercertifikatet ændres.')
            if repair and not self.app.queue.paused:
                raise ConfigConflict('Repair kræver, at printkøen er sat på pause.')

    def _invalidate_bed_for_certificate(self, config):
        """Invalidate physical-clearance evidence before any trust restart."""
        with self.app.queue.lock:
            printer = self.app.queue.printers.get(config['id'])
            if printer is not None:
                printer['bed_clear'] = False
                printer['bed_invalidated'] = self.app.queue.clock()
                printer.pop('acknowledged_stop_task', None)

    def _confirm_certificate(self, config, channel, fingerprint, expected, *, repair=False, resuming=False):
        if channel not in CERTIFICATE_CHANNELS:
            raise ValueError('Ugyldig printercertifikatkanal.')
        self._certificate_preflight(config, repair=repair)
        # A trust change invalidates physical-clearance evidence even if the
        # subsequent pin or reconnect operation fails. Enabled/paused/workflow
        # state is deliberately preserved.
        self._invalidate_bed_for_certificate(config)
        pins = self._certificate_pins(config)
        pins.confirm(channel, fingerprint, allow_already=resuming)
        reconnected = self._restart_after_certificate(config)
        return {'id': config['id'], 'version': expected, 'channel': channel,
                'fingerprint': fingerprint.lower(), 'reconnected': reconnected}

    def _persist(self, settings):
        # No runtime state is changed until the complete replacement succeeds.
        path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.app.folder,
                                             prefix='.settings-', suffix='.tmp', delete=False) as stream:
                path = stream.name
                if os.name != 'nt':
                    os.chmod(path, 0o600)
                json.dump(settings, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(path, self.app.settings_path)
            path = None
        finally:
            if path is not None:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass

    def _commit(self, config, settings):
        printer_id = config['id']
        with self.app.queue.lock:
            blocked = self.app.queue.workflow_blocks_bed(printer_id)
            # An isolated offline job must retain its physical clear workflow,
            # but a host/access-code repair must be possible to reach it again.
            old = self.app.queue.configs.get(printer_id, {})
            connection_only = all(config.get(key, default) == old.get(key, default)
                for key, default in (('model', None), ('serial', None), ('bed_type', BED_TYPES[0]),
                                     ('workflow', None), ('name', None), ('location', ''), ('enabled', False)))
            blocked_unisolated = self.app.queue.db.execute('''SELECT 1 FROM jobs
                WHERE printer_id=? AND workflow IS NOT NULL AND workflow_complete=0
                AND status IN ('done','failed','cancelled') AND isolated=0 LIMIT 1''',
                (printer_id,)).fetchone()
            offline = not self.app.queue.online(self.app.queue.printers.get(printer_id, {})) if old else False
            repair = blocked and not blocked_unisolated and offline and connection_only
            if self.app.queue.active(printer_id) or (blocked and not repair):
                raise ConfigConflict('Vent, til printerens aktuelle job og eventuelle rydning er afsluttet.')
            # Construction is local and cannot race a newly scheduled job.
            driver = self.driver_factory(config, self.app.queue, self.app.slicer.profiles)
            self._persist(settings)
            self.app.settings = settings
            old_driver = self.app.drivers.get(printer_id)
            # Pause scheduling before reconnecting; callbacks cannot re-enable it.
            self.app.queue.configs[printer_id] = config
            self.app.queue.printers[printer_id] = {
                'id': printer_id, 'name': config['name'], 'state': 'OFFLINE', 'seen': 0,
                'state_seen': 0, 'tray_seen': 0, 'bed_clear': False, 'trays': [],
                'progress': 0, 'remaining': None, 'error': 0, 'task': '', 'enabled': False,
                'message': 'Forbinder til printeren'}
        # Never join a network callback while holding the queue's lock.
        try:
            if old_driver:
                old_driver.stop()
            self.app.drivers[printer_id] = driver
            driver.start()
            with self.app.queue.lock:
                self.app.queue.printers[printer_id]['enabled'] = config['enabled']
        except Exception:
            # Configuration is durably committed. Keep scheduling off and expose
            # the connection issue; retrying a receipt must not reconnect again.
            with self.app.queue.lock:
                self.app.queue.printers[printer_id].update(enabled=False, configuration_error=True,
                    message='Opsætningen er gemt, men forbindelsen kunne ikke startes. Genstart printertjenesten.')

    def execute(self, actor_id, envelope):
        if not isinstance(actor_id, str) or not _ACTOR.fullmatch(actor_id):
            raise PermissionError('Ugyldig aktør.')
        allowed = {'action', 'params', 'request_id', 'expected_version'}
        if (not isinstance(envelope, dict)
                or not set(envelope) <= allowed | {'confirmed'}
                or set(envelope) - allowed - {'confirmed'}
                or ('confirmed' in envelope and envelope.get('action') != 'printer.certificate.confirm')
                or ('confirmed' in envelope and envelope['confirmed'] is not True)
                or not allowed <= envelope.keys()):
            raise ValueError('Ugyldig printerkommando.')
        action, request_id, expected = envelope['action'], envelope['request_id'], envelope['expected_version']
        data = envelope['params']
        if not isinstance(data, dict):
            raise ValueError('Ugyldige printerfelter.')
        if action not in ('printer.create', 'printer.update', 'printer.workflow.assign',
                          'printer.job.advance', 'printer.queue.pause'):
            if action not in ('printer.certificate.confirm', 'printer.certificate.probe',
                              'printer.certificate.cancel'):
                raise ValueError('Ukendt printerhandling.')
        if action == 'printer.certificate.probe':
            if set(data) != {'id'}:
                raise ValueError('Ugyldig printercertifikatprobe.')
            if not isinstance(data['id'], str) or not _ID.fullmatch(data['id']):
                raise ValueError('Ugyldig printercertifikatprobe.')
        if action == 'printer.certificate.confirm':
            if set(data) not in ({'id', 'channel', 'fingerprint'},
                                 {'id', 'channel', 'fingerprint', 'repair'}):
                raise ValueError('Ugyldig printercertifikatbekræftelse.')
        if action == 'printer.certificate.cancel':
            if set(data) not in ({'id', 'operation_request_id'},
                                 {'id', 'operation_request_id', 'operation_actor'}):
                raise ValueError('Ugyldig annullering af printercertifikatforsøg.')
            if (not isinstance(data['id'], str) or not _ID.fullmatch(data['id'])
                    or not isinstance(data['operation_request_id'], str)
                    or not _REQUEST_ID.fullmatch(data['operation_request_id'])
                    or ('operation_actor' in data and
                        (not isinstance(data['operation_actor'], str) or
                         not _ACTOR.fullmatch(data['operation_actor'])))):
                raise ValueError('Ugyldig annullering af printercertifikatforsøg.')
        if action == 'printer.certificate.confirm':
            if (not isinstance(data['id'], str) or not _ID.fullmatch(data['id'])
                    or data['channel'] not in CERTIFICATE_CHANNELS
                    or not isinstance(data['fingerprint'], str)
                    or not re.fullmatch(r'[0-9a-fA-F]{64}', data['fingerprint'])
                    or ('repair' in data and type(data['repair']) is not bool)):
                raise ValueError('Ugyldig printercertifikatbekræftelse.')
        if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id) or type(expected) is not int or expected < 0:
            raise ValueError('Ugyldigt request-ID eller version.')
        if action in ('printer.job.advance', 'printer.queue.pause'):
            with self.app.settings_lock, self.app.queue.lock:
                # Both stores share the actor/request namespace, including a
                # retry after the hub lost the first successful response.
                if actor_id + ':' + request_id in self.app.settings.get('_makerspace_receipts', {}):
                    raise ConfigConflict('Request-ID er brugt til en anden ændring.')
                if action == 'printer.job.advance':
                    return self.app.queue.advance_workflow(actor_id, envelope)
                return self.app.queue.control_queue(actor_id, envelope)
        # HMAC avoids persisting a guessable digest of a short LAN access code.
        digest_envelope = envelope
        if action == 'printer.certificate.confirm' and 'confirmed' in digest_envelope:
            digest_envelope = copy.deepcopy(digest_envelope)
            digest_envelope.pop('confirmed', None)
        if (action == 'printer.certificate.confirm' and isinstance(data, dict)
                and data.get('repair') is False):
            digest_envelope = copy.deepcopy(digest_envelope)
            digest_envelope['params'].pop('repair', None)
        digest = hmac.new(self.token.encode(), json.dumps(digest_envelope, sort_keys=True,
            separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode(), hashlib.sha256).hexdigest()
        key = actor_id + ':' + request_id
        with self.app.settings_lock:
            if self.app.demo and action != 'printer.workflow.assign':
                raise ValueError('Rigtige printere kan ikke registreres i demoen.')
            with self.app.queue.lock:
                if self.app.queue.db.execute('SELECT 1 FROM workflow_commands WHERE actor=? AND request_id=?',
                                             (actor_id, request_id)).fetchone():
                    raise ConfigConflict('Request-ID er brugt til en anden ændring.')
            receipts = self.app.settings.get('_makerspace_receipts', {})
            cancelled = self.app.settings.get('_certificate_cancelled', {})
            if (action == 'printer.certificate.confirm'
                    and isinstance(cancelled, dict) and key in cancelled):
                raise ConfigConflict('Dette certifikatforsøg er allerede afsluttet uden tillidsændring.')
            if key in receipts:
                if not hmac.compare_digest(receipts[key]['digest'], digest):
                    raise ConfigConflict('Request-ID er brugt til en anden ændring.')
                return copy.deepcopy(receipts[key]['response'])
            if action == 'printer.certificate.cancel':
                config = next((p for p in self.app.settings['printers'] if p.get('id') == data['id']), None)
                if config is None or type(config.get('version', 1)) is not int or expected != config.get('version', 1):
                    raise ConfigConflict('Printeropsætningen er ændret. Hent den aktuelle version.')
                original_actor = data.get('operation_actor', actor_id)
                original_key = original_actor + ':' + data['operation_request_id']
                operations = self.app.settings.get('_certificate_operations', {})
                journal = operations.get(original_key) if isinstance(operations, dict) else None
                if (not isinstance(journal, dict) or journal.get('id') != data['id']
                        or journal.get('channel') not in CERTIFICATE_CHANNELS):
                    raise ConfigConflict('Det gamle certifikatforsøg findes ikke længere.')
                trust_preserved = False
                try:
                    pins = self._certificate_pins(config)
                    trust_preserved = pins._read_pin(journal['channel']) == journal.get('fingerprint')
                except (OSError, ValueError):
                    # A stale journal can still be closed; the UI reports that
                    # the final trust state could not be inferred here.
                    trust_preserved = False
                response = {'ok': True, 'action': action, 'result': {
                    'id': data['id'], 'version': expected,
                    'operation_request_id': data['operation_request_id'],
                    'cancelled': True, 'trust_preserved': trust_preserved}}
                settings = copy.deepcopy(self.app.settings)
                settings.setdefault('_certificate_operations', {}).pop(original_key, None)
                settings.setdefault('_certificate_cancelled', {})[original_key] = {
                    'id': data['id'], 'channel': journal['channel'],
                    'cancelled_by': actor_id, 'at': time.time(),
                    'trust_preserved': trust_preserved}
                settings.setdefault('_makerspace_receipts', {})[key] = {
                    'digest': digest, 'response': response}
                settings.setdefault('_makerspace_audit', []).extend([
                    {'at': time.time(), 'actor': original_actor, 'action': 'printer.certificate.confirm',
                     'target': data['id'], 'request_id': data['operation_request_id'],
                     'outcome': 'cancelled', 'fields': [journal['channel']]},
                    {'at': time.time(), 'actor': actor_id, 'action': action,
                     'target': data['id'], 'request_id': request_id, 'outcome': 'applied',
                     'fields': ['operation_request_id']}])
                self._persist(settings)
                self.app.settings = settings
                return copy.deepcopy(response)
            if action == 'printer.certificate.probe':
                config = next((p for p in self.app.settings['printers'] if p.get('id') == data['id']), None)
                if config is None or expected != config.get('version', 1):
                    raise ConfigConflict('Printeropsætningen er ændret. Hent den aktuelle version.')
                result = probe_printer_certificates(config, self.app.folder / 'certificates')
                return {'ok': True, 'action': action, 'result': {
                    'id': config['id'], 'version': expected, **result}}
            if action == 'printer.certificate.confirm':
                config = next((p for p in self.app.settings['printers'] if p.get('id') == data['id']), None)
                if config is None or expected != config.get('version', 1):
                    raise ConfigConflict('Printeropsætningen er ændret. Hent den aktuelle version.')
                operations = self.app.settings.get('_certificate_operations', {})
                if not isinstance(operations, dict):
                    raise ConfigConflict('Afventende certifikatændringer kunne ikke valideres.')
                journal = operations.get(key)
                resuming = journal is not None
                if journal is not None:
                    if not isinstance(journal, dict):
                        raise ConfigConflict('Afventende certifikatændring kunne ikke valideres.')
                    expected_journal = {
                        'digest': digest, 'id': data['id'], 'channel': data['channel'],
                        'fingerprint': data['fingerprint'].lower(), 'expected_version': expected,
                        'host': config.get('host', ''), 'serial': config.get('serial', ''),
                        'repair': bool(data.get('repair', False)),
                    }
                    if any(journal.get(field, False if field == 'repair' else None) != value
                           for field, value in expected_journal.items()):
                        raise ConfigConflict('Request-ID har en uforenelig afventende certifikatændring.')
                else:
                    self._certificate_preflight(config, repair=bool(data.get('repair', False)))
                    journal = {
                        'digest': digest, 'id': data['id'], 'channel': data['channel'],
                        'fingerprint': data['fingerprint'].lower(), 'expected_version': expected,
                        'host': config.get('host', ''), 'serial': config.get('serial', ''),
                        'repair': bool(data.get('repair', False)),
                        'phase': 'prepared', 'created_at': time.time(),
                    }
                    prepared = copy.deepcopy(self.app.settings)
                    prepared.setdefault('_certificate_operations', {})[key] = journal
                    # The durable journal precedes pin mutation and driver
                    # restart. If this write fails, no trust has changed.
                    self._persist(prepared)
                    self.app.settings = prepared
                result = self._confirm_certificate(config, data['channel'], data['fingerprint'], expected,
                                                   repair=bool(data.get('repair', False)), resuming=resuming)
                response = {'ok': True, 'action': action, 'result': result}
                settings = copy.deepcopy(self.app.settings)
                settings.setdefault('_certificate_operations', {}).pop(key, None)
                settings.setdefault('_makerspace_receipts', {})[key] = {'digest': digest, 'response': response}
                settings.setdefault('_makerspace_audit', []).append({'at': time.time(), 'actor': actor_id,
                    'action': action, 'target': config['id'], 'request_id': request_id, 'outcome': 'applied',
                    'fields': [data['channel']]})
                self._persist(settings)
                self.app.settings = settings
                return copy.deepcopy(response)
            if action == 'printer.workflow.assign':
                if set(data) != {'id', 'workflow'} or expected < 1:
                    raise ValueError('Ugyldig workflowtildeling.')
                config = next((p for p in self.app.settings['printers'] if p.get('id') == data['id']), None)
                current_version = config.get('version', 1) if config is not None else None
                if config is None or type(current_version) is not int or current_version != expected:
                    raise ConfigConflict('Printeropsætningen er ændret. Hent den aktuelle version.')
                workflow = validate_binding(data['workflow'])
                updated = copy.deepcopy(config)
                updated['workflow'] = workflow
                updated['version'] = current_version + 1
                response = {'ok': True, 'action': action, 'result': public_printer(updated)}
                settings = copy.deepcopy(self.app.settings)
                settings['printers'] = [updated if p.get('id') == config['id'] else p for p in settings['printers']]
                settings.setdefault('_makerspace_receipts', {})[key] = {'digest': digest, 'response': response}
                settings.setdefault('_makerspace_audit', []).append({'at': time.time(), 'actor': actor_id,
                    'action': action, 'target': config['id'], 'request_id': request_id, 'outcome': 'applied',
                    'fields': ['workflow']})
                with self.app.queue.lock:
                    self._persist(settings)
                    self.app.settings = settings
                    self.app.queue.configs[config['id']] = updated
                return copy.deepcopy(response)
            old = None
            if action == 'printer.create':
                if expected != 0 or 'id' in data:
                    raise ConfigConflict('En ny printer kræver version 0 og får et nyt ID.')
                if data.get('enabled', False) is not False:
                    raise ValueError('En ny printer skal registreres inaktiv. Aktivér efter kontrol.')
            else:
                old = next((p for p in self.app.settings['printers'] if p['id'] == data.get('id')), None)
                if old is None:
                    raise ConfigConflict('Printeren findes ikke længere. Hent overblikket igen.')
                if expected != old.get('version', 1):
                    raise ConfigConflict('Printeropsætningen er ændret. Hent den aktuelle version.')
            config = validate_printer(data, old, self.app.settings['printers'])
            response = {'ok': True, 'action': action, 'result': public_printer(config)}
            settings = copy.deepcopy(self.app.settings)
            settings['printers'] = [p for p in settings['printers'] if p['id'] != config['id']] + [config]
            settings.setdefault('_makerspace_receipts', {})[key] = {'digest': digest, 'response': response}
            settings.setdefault('_makerspace_audit', []).append({'at': time.time(), 'actor': actor_id,
                'action': action, 'target': config['id'], 'request_id': request_id, 'outcome': 'applied',
                'fields': sorted(data.keys())})
            self._commit(config, settings)
            return copy.deepcopy(response)

    def save_legacy(self, data):
        """The existing teacher form uses the same validation and persistence."""
        if not isinstance(data, dict):
            raise ValueError('Ugyldige printerfelter.')
        with self.app.settings_lock:
            if self.app.demo:
                raise ValueError('Skolens printere tilføjes i den almindelige version.')
            old = next((p for p in self.app.settings['printers'] if p['id'] == data.get('id')), None)
            config = validate_printer(data, old, self.app.settings['printers'])
            settings = copy.deepcopy(self.app.settings)
            settings['printers'] = [p for p in settings['printers'] if p['id'] != config['id']] + [config]
            self._commit(config, settings)
            return {'ok': True}
