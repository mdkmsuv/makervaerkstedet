"""Local Bambu LAN adapter. No cloud login, camera access, or bed-image capture."""
import ftplib
from concurrent.futures import ThreadPoolExecutor
import hashlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import threading
import tempfile
import time

import paho.mqtt.client as mqtt

from .core import normalize_colour

log = logging.getLogger(__name__)

# Bambu uses the same FAILED state for a user stop and a printer fault.  This
# code is the documented/local-catalogued cancellation notification; it is
# only safe to dismiss when the corresponding HMS list is explicitly empty.
BAMBU_CANCEL_PRINT_ERROR = 0x0300400C


def merge_report(previous, update):
    """Bambu pushes deltas; keyed AMS/tray arrays can contain only changed entries."""
    result = dict(previous)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_report(result[key], value)
        elif isinstance(value, list) and value and all(isinstance(v, dict) and 'id' in v for v in value):
            old = {str(v['id']): v for v in result.get(key, []) if isinstance(v, dict) and 'id' in v}
            for item in value:
                old[str(item['id'])] = merge_report(old.get(str(item['id']), {}), item)
            result[key] = list(old.values())
        else:
            result[key] = value
    return result


def read_trays(report, config, profiles):
    result = []
    def add(tray, slot, ams_id, slot_id, extruder=0):
        colour = normalize_colour(tray.get('tray_color'))
        material = tray.get('tray_type', '')
        profile = profiles.find_filament(config['model'], tray.get('tray_info_idx', '')) if material == 'PLA' else None
        if profile and colour:
            # An A1's external holder cannot measure spool weight: firmware
            # sends remain=0 even during a successful print. It means unknown.
            remaining = -1 if slot == 254 else tray.get('remain', -1)
            result.append({'slot': slot, 'ams_id': ams_id, 'slot_id': slot_id, 'extruder': extruder, 'colour': colour, 'material': material, 'profile': profile, 'remaining': remaining})
    if config['model'] == 'A1 mini':
        # The value comes from the printer's manually registered external spool.
        external = report.get('vt_tray')
        if external is None:
            external = next((v for v in report.get('vir_slot', []) if int(v.get('id', -1)) in (254, 255)), {})
        add(external, 254, 255, 0)
    else:
        ams_data = report.get('ams', {})
        present = ams_data.get('tray_exist_bits')
        present = int(str(present), 16) if present is not None else None
        for ams in ams_data.get('ams', []):
            ams_id = int(ams['id'])
            if not 0 <= ams_id < 4:
                continue
            extruder = 0
            if config['model'] == 'H2D':
                try:
                    physical_extruder = (int(str(ams['info']), 16) >> 8) & 0xF
                except (KeyError, TypeError, ValueError):
                    continue
                if physical_extruder not in (0, 1):
                    # 0xE is a shared/switchable connection. Do not guess its routing.
                    continue
                # Firmware: 0 right, 1 left. H2D slicer physical_extruder_map: [1,0].
                extruder = 1 - physical_extruder
            for tray in ams.get('tray', []):
                slot_id = int(tray['id'])
                if not 0 <= slot_id <= 3:
                    continue
                slot = ams_id * 4 + slot_id
                if present is not None and not (present & (1 << slot)):
                    continue
                add(tray, slot, ams_id, slot_id, extruder)
    return result


CERTIFICATE_CHANNELS = ('mqtt', 'ftp')
_FINGERPRINT = re.compile(r'^[0-9a-f]{64}$')
_PRINTER_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$')
_MAX_PIN_BYTES = 128
_MAX_PENDING_BYTES = 16 * 1024
_PIN_LOCKS = {}
_PIN_LOCKS_GUARD = threading.Lock()


def _is_reparse(path):
    """Reject symlink/reparse points at certificate storage boundaries."""
    try:
        if path.is_symlink() or os.path.islink(path):
            return True
        if os.name == 'nt':
            attributes = getattr(os.lstat(path), 'st_file_attributes', 0)
            return bool(attributes & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))
    except OSError:
        return False
    return False


def _shared_pin_lock(folder, printer_id):
    key = (str(folder), printer_id)
    with _PIN_LOCKS_GUARD:
        return _PIN_LOCKS.setdefault(key, threading.RLock())


def _assert_no_reparse_ancestors(path):
    current = Path(path)
    while True:
        if current.exists() and _is_reparse(current):
            raise ValueError('Certifikatstien må ikke krydse et symbolsk link eller reparsepunkt.')
        parent = current.parent
        if parent == current:
            return
        current = parent


class CertificateTrustError(ssl.SSLError):
    """A printer certificate needs an explicit teacher decision."""

    def __init__(self, details):
        self.details = details
        channel = details.get('channel', 'printer')
        old = details.get('old_fingerprint') or 'ingen'
        new = details.get('new_fingerprint') or 'ukendt'
        super().__init__(
            f'Printerens {channel}-certifikat kræver lærerbekræftelse. '
            f'Gammelt fingeraftryk: {old}. Nyt fingeraftryk: {new}.')


class CertificatePins:
    """Store printer pins and pending observations without automatic trust changes.

    ``strict`` is used by the production Bambu driver. Legacy camera helpers
    retain their existing TOFU behavior because their separate certificate
    setup is outside the printer trust workflow.
    """

    def __init__(self, folder, printer_id, *, strict=False, identity=None):
        self.printer_id = printer_id
        if not isinstance(printer_id, str) or not _PRINTER_ID.fullmatch(printer_id):
            raise ValueError('Ugyldigt printer-ID til certifikatlagring.')
        self.folder = Path(folder)
        _assert_no_reparse_ancestors(self.folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        _assert_no_reparse_ancestors(self.folder)
        self.folder = self.folder.resolve()
        self.strict = bool(strict)
        self.identity = {
            'host': str((identity or {}).get('host', '')),
            'serial': str((identity or {}).get('serial', '')),
            'version': (identity or {}).get('version'),
        }
        # Bridge snapshots and driver instances must serialize through the
        # same process-wide lock for this printer, not their own instances.
        self.lock = _shared_pin_lock(self.folder, self.printer_id)

    def _safe_path(self, path):
        path = Path(path)
        try:
            resolved_parent = path.parent.resolve()
        except OSError as error:
            raise ValueError('Certifikatstien kunne ikke valideres.') from error
        if resolved_parent != self.folder or _is_reparse(path.parent) or (path.exists() and _is_reparse(path)):
            raise ValueError('Certifikatstien må ikke krydse et symbolsk link eller reparsepunkt.')
        return path

    def _path(self, channel):
        if channel not in CERTIFICATE_CHANNELS:
            raise ValueError('Ugyldig printercertifikatkanal.')
        return self._safe_path(self.folder / f'{self.printer_id}-{channel}.sha256')

    def _pending_path(self, channel):
        self._path(channel)
        return self._safe_path(self.folder / f'{self.printer_id}-{channel}.pending.json')

    @staticmethod
    def fingerprint(sock):
        value = sock.getpeercert(binary_form=True)
        if not isinstance(value, bytes) or not value:
            raise ssl.SSLError('Printerens TLS-certifikat kunne ikke læses.')
        return hashlib.sha256(value).hexdigest()

    def _atomic_write(self, path, text):
        path = self._safe_path(path)
        if not isinstance(text, str) or len(text.encode('utf-8')) > _MAX_PENDING_BYTES:
            raise ValueError('Certifikatdataene er for store.')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='',
                                             dir=self.folder, prefix=f'.{path.name}.', suffix='.tmp',
                                             delete=False) as stream:
                temporary = Path(stream.name)
                if _is_reparse(temporary):
                    raise ValueError('Midlertidig certifikatfil må ikke være et reparsepunkt.')
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass

    def _read_pin(self, channel):
        path = self._path(channel)
        if not path.exists():
            return None
        try:
            if path.stat().st_size > _MAX_PIN_BYTES:
                raise ssl.SSLError('Printerens gemte certifikat er for stort.')
        except OSError as error:
            raise ssl.SSLError('Printerens gemte certifikat kunne ikke læses.') from error
        try:
            value = path.read_text(encoding='ascii').strip().lower()
        except (OSError, UnicodeError) as error:
            raise ssl.SSLError('Printerens gemte certifikat kunne ikke læses.') from error
        if not _FINGERPRINT.fullmatch(value):
            raise ssl.SSLError('Printerens gemte certifikat er ugyldigt.')
        return value

    def _read_pending(self, channel):
        path = self._pending_path(channel)
        if not path.exists():
            return None
        try:
            if path.stat().st_size > _MAX_PENDING_BYTES:
                raise ssl.SSLError('Printerens afventende certifikat er for stort.')
        except OSError as error:
            raise ssl.SSLError('Printerens afventende certifikat kunne ikke læses.') from error
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ssl.SSLError('Printerens afventende certifikat kunne ikke læses.') from error
        if (not isinstance(value, dict) or value.get('channel') != channel
                or value.get('printer_id') != self.printer_id
                or not isinstance(value.get('new_fingerprint'), str)
                or not _FINGERPRINT.fullmatch(value['new_fingerprint'])):
            raise ssl.SSLError('Printerens afventende certifikat er ugyldigt.')
        return value

    def _write_pending(self, channel, old, new):
        value = {
            'printer_id': self.printer_id,
            'channel': channel,
            'old_fingerprint': old,
            'new_fingerprint': new,
            'host': self.identity['host'],
            'serial': self.identity['serial'],
            'version': self.identity['version'],
            'observed_at': time.time(),
        }
        # Keep this safe for direct callers as well as check(), which already
        # holds the re-entrant shared lock.
        with self.lock:
            self._atomic_write(self._pending_path(channel), json.dumps(value, ensure_ascii=False, sort_keys=True))
        return value

    def check(self, sock, channel):
        fingerprint = self.fingerprint(sock)
        with self.lock:
            old = self._read_pin(channel)
            if old == fingerprint:
                pending = self._pending_path(channel)
                if pending.exists():
                    pending.unlink()
                return fingerprint
            if self.strict:
                details = self._write_pending(channel, old, fingerprint)
                raise CertificateTrustError(details)
            if old is not None:
                raise ssl.SSLError('Printerens certifikat er ændret. Kontrollér adressen og certifikatet.')
            self._atomic_write(self._path(channel), fingerprint)
            return fingerprint

    def status(self):
        """Return teacher-safe state for the two production printer channels."""
        with self.lock:
            result = {}
            for channel in CERTIFICATE_CHANNELS:
                try:
                    pinned = self._read_pin(channel)
                    result[channel] = {
                        'status': 'trusted' if pinned else 'untrusted',
                        'pinned_fingerprint': pinned,
                    }
                    pending = self._read_pending(channel)
                except ssl.SSLError as error:
                    result[channel] = {'status': 'error', 'pinned_fingerprint': None,
                                        'error': str(error)}
                    pending = None
                if pending:
                    result[channel]['status'] = 'confirmation_required'
                    result[channel]['pending'] = pending
            return result

    def confirm(self, channel, fingerprint, *, allow_already=False):
        if not isinstance(fingerprint, str) or not _FINGERPRINT.fullmatch(fingerprint.lower()):
            raise ValueError('Ugyldigt printercertifikatfingeraftryk.')
        fingerprint = fingerprint.lower()
        with self.lock:
            pending = self._read_pending(channel)
            if pending is None:
                if allow_already and self._read_pin(channel) == fingerprint:
                    return fingerprint
                raise ValueError('Der er ikke et nyt printercertifikat at bekræfte.')
            if (pending.get('host'), pending.get('serial'), pending.get('version')) != (
                    self.identity['host'], self.identity['serial'], self.identity['version']):
                raise ValueError('Printerens identitet eller version er ændret. Hent den aktuelle observation.')
            if pending['new_fingerprint'] != fingerprint:
                raise ValueError('Bekræftelsen matcher ikke det observerede certifikat.')
            current = self._read_pin(channel)
            if current != pending.get('old_fingerprint'):
                raise ValueError('Printerens gamle certifikat er ændret. Hent en ny observation.')
            self._atomic_write(self._path(channel), fingerprint)
            try:
                self._pending_path(channel).unlink()
            except FileNotFoundError:
                pass
            return fingerprint


def _validated_probe_host(config):
    host = config.get('host') if isinstance(config, dict) else None
    try:
        address = ipaddress.ip_address(host)
    except (TypeError, ValueError):
        raise ValueError('Certifikatprobe kræver printerens lokale IPv4-adresse.') from None
    if (address.version != 4 or not address.is_private or address.is_loopback
            or address.is_multicast or address.is_unspecified):
        raise ValueError('Certifikatprobe kræver printerens lokale IPv4-adresse.')
    return str(address)


def _probe_certificate_channel(pins, host, channel, deadline):
    port = 8883 if channel == 'mqtt' else 990
    remaining = min(1.5, max(0.05, deadline - time.monotonic()))
    raw = socket.create_connection((host, port), timeout=remaining)
    try:
        tls = tls_context().wrap_socket(raw, server_hostname=host)
        try:
            try:
                observed = pins.check(tls, channel)
                state = 'trusted'
                details = {'old_fingerprint': observed, 'new_fingerprint': observed}
            except CertificateTrustError as error:
                observed = error.details.get('new_fingerprint')
                state = 'confirmation_required'
                details = dict(error.details)
            return {'status': state, 'observed_fingerprint': observed, **details}
        finally:
            tls.close()
    finally:
        try:
            raw.close()
        except OSError:
            pass


def probe_printer_certificates(config, certificates, *, timeout=5.0):
    """Observe MQTT/FTPS TLS certificates without credentials or commands."""
    host = _validated_probe_host(config)
    timeout = min(5.0, max(0.1, float(timeout)))
    deadline = time.monotonic() + timeout
    pins = CertificatePins(certificates, config['id'], strict=True,
                           identity={'host': host, 'serial': config.get('serial', ''),
                                     'version': config.get('version', 1)})
    result = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix='cert-probe') as pool:
        futures = {channel: pool.submit(_probe_certificate_channel, pins, host, channel, deadline)
                   for channel in CERTIFICATE_CHANNELS}
        for channel, future in futures.items():
            remaining = max(0.05, deadline - time.monotonic())
            try:
                result[channel] = future.result(timeout=remaining)
            except Exception as error:
                result[channel] = {'status': 'error', 'error': str(error)[:300]}
    return {'identity': {'host': host, 'serial': config.get('serial', ''),
                         'version': config.get('version', 1)}, 'channels': result}


def tls_context():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    # Private printer certificates are pinned before application credentials are sent.
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


class ImplicitFTPS(ftplib.FTP_TLS):
    def __init__(self, pins):
        super().__init__(context=tls_context(), timeout=30)
        self.pins = pins

    def connect(self, host='', port=990, timeout=30, source_address=None):
        self.host = host
        self.port = port
        self.sock = self.context.wrap_socket(socket.create_connection((host, port), timeout), server_hostname=host)
        self.pins.check(self.sock, 'ftp')
        self.af = self.sock.family
        self.file = self.sock.makefile('r', encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome

    def ntransfercmd(self, cmd, rest=None):
        # Bambu's implicit FTPS server requires the control TLS session on data sockets.
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
        return conn, size

    def storbinary(self, cmd, fp, blocksize=8192, callback=None, rest=None):
        self.voidcmd('TYPE I')
        with self.transfercmd(cmd, rest) as connection:
            while block := fp.read(blocksize):
                connection.sendall(block)
                if callback:
                    callback(block)
        # Bambu's data server does not complete TLS close_notify. Python's
        # FTP_TLS.storbinary waits in unwrap() after sending the entire file.
        # Close the data socket, then require FTP's final success response.
        # upload() additionally verifies the server-side file size.
        return self.voidresp()


def print_command(job, config, sequence_id=None):
    slot = job['slot']
    mapping = {'ams_id': 255, 'slot_id': 0} if slot == 254 else {'ams_id': slot // 4, 'slot_id': slot % 4}
    return {'print': {
        'sequence_id': str(sequence_id if sequence_id is not None else int(time.time() * 1000) % 2_000_000_000),
        'command': 'project_file', 'param': 'Metadata/plate_1.gcode',
        'url': f"ftp:///{job['remote_name']}",
        'md5': '',
        'subtask_name': job['remote_name'].removesuffix('.gcode.3mf'),
        'task_id': '0', 'project_id': '0', 'profile_id': '0', 'subtask_id': '0',
        'bed_type': config.get('bed_type', 'Textured PEI Plate'),
        'timelapse': False, 'bed_leveling': True, 'flow_cali': True,
        'vibration_cali': True, 'layer_inspect': False,
        'use_ams': slot != 254,
        'ams_mapping': [slot] if slot != 254 else [],
        'ams_mapping2': [mapping] if slot != 254 else [],
    }}


class BambuPrinter:
    def __init__(self, config, queue, profiles):
        self.config, self.queue, self.profiles = config, queue, profiles
        self.report = {}
        self._effective_print_error = None
        self.topology_seen = {}
        self.lock = threading.RLock()
        self.subscribed = threading.Event()
        self.pending_start = None
        self.pins = CertificatePins(
            queue.folder / 'certificates', config['id'], strict=True,
            identity={'host': config.get('host', ''), 'serial': config.get('serial', ''),
                      'version': config.get('version', 1)})
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"printvaerksted_{config['id']}", clean_session=True)
        self.client.username_pw_set('bblp', config['access_code'])
        self.client.tls_set_context(tls_context())
        self.client.on_socket_open = self._socket_open
        self.client.on_connect = self._connected
        self.client.on_disconnect = self._disconnected
        self.client.on_subscribe = self._subscribed
        self.client.on_message = self._message
        self.client.reconnect_delay_set(2, 30)
        self.last_refresh = 0
        self.topic = f"device/{config['serial']}/request"

    def _socket_open(self, client, userdata, sock):
        try:
            self.pins.check(sock, 'mqtt')
        except CertificateTrustError as error:
            self.queue.offline(self.config['id'], str(error))
            raise

    def start(self):
        self.client.connect_async(self.config['host'], 8883, 30)
        self.client.loop_start()

    def stop(self):
        self.client.disconnect()
        self.client.loop_stop()

    def _connected(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            self.queue.offline(self.config['id'], 'Printeren afviste forbindelsen. KontrollÃ©r den lokale adgangskode og netvÃ¦rksforbindelsen.')
            return
        with self.lock:
            self.report = {}
            self.topology_seen = {}
            self._effective_print_error = None
        self.subscribed.clear()
        client.subscribe(f"device/{self.config['serial']}/report", qos=0)

    def _subscribed(self, client, userdata, mid, reason_codes, properties):
        if any(reason.is_failure for reason in reason_codes):
            self.queue.offline(self.config['id'], 'Forbindelsen kan ikke modtage printerens svar.')
            return
        self.subscribed.set()
        self.refresh(force=True)

    def _disconnected(self, client, userdata, disconnect_flags, reason_code, properties):
        self.subscribed.clear()
        self.queue.offline(self.config['id'])

    def refresh(self, force=False):
        if self.client.is_connected() and (force or time.time() - self.last_refresh > 15):
            self.last_refresh = time.time()
            self.client.publish(self.topic, json.dumps({'pushing': {'sequence_id': '0', 'command': 'pushall'}}), qos=0)

    def _message(self, client, userdata, message):
        try:
            if len(message.payload) > 1024 * 1024:
                return
            update = json.loads(message.payload).get('print', {})
            # A1 acknowledgements can omit command. Correlate by our sequence,
            # never merge result/reason into telemetry or apply another client's ACK.
            if 'result' in update or ('err_code' in update and update.get('command') in (None, 'project_file')):
                with self.lock:
                    pending = dict(self.pending_start) if self.pending_start else None
                if pending and str(update.get('sequence_id')) == pending['sequence_id'] and update.get('command') in (None, 'project_file'):
                    result = str(update.get('result', '')).lower()
                    error_code = int(update.get('err_code') or 0)
                    detail = {k: update[k] for k in ('result', 'reason', 'errno', 'err_code') if k in update}
                    log.info('Print start reply printer=%s job=%s sequence=%s detail=%s', self.config['id'], pending['job_id'], pending['sequence_id'], json.dumps(detail, ensure_ascii=True)[:1000])
                    if result in ('fail', 'failed', 'error') or error_code:
                        with self.queue.lock:
                            job = self.queue.active(self.config['id'])
                            if job and job['id'] == pending['job_id']:
                                message = 'Programmet mangler Bambu-godkendelse til printstart. FiloverfÃ¸rsel og printerstatus virker.' if error_code == 84033543 else 'Printeren afviste programmets startkommando. Printforbindelsen skal kontrolleres.'
                                self.queue.fail(job['id'], message, uncertain=True)
                    # Even success only acknowledges the command. Actual matching
                    # RUNNING/PREPARE telemetry is still required by the queue.
                return
            if not update or update.get('command') not in (None, 'push_status'):
                return
            with self.lock:
                for ams in update.get('ams', {}).get('ams', []):
                    if 'info' in ams:
                        self.topology_seen[int(ams['id'])] = self.queue.clock()
                self.report = merge_report(self.report, update)
                report = self.report
                kwargs = {}
                # Only fields actually received refresh freshness; cached values cannot keep a printer ready.
                if 'gcode_state' in update:
                    kwargs['state'] = report['gcode_state']
                if any(k in update for k in ('ams', 'vt_tray', 'vir_slot')):
                    kwargs['trays'] = read_trays(report, self.config, self.profiles)
                    if self.config['model'] == 'H2D':
                        for tray in kwargs['trays']:
                            tray['topology_seen'] = self.topology_seen.get(tray['ams_id'], 0)
                if 'mc_percent' in update:
                    kwargs['progress'] = report['mc_percent']
                if 'mc_remaining_time' in update:
                    kwargs['remaining'] = report['mc_remaining_time']
                effective_error = self.effective_print_error(report)
                error_changed = effective_error != self._effective_print_error
                # print_error is normally forwarded only when fresh.  HMS is
                # also a relevant delta: it can turn a cached cancellation
                # code into either a safe zero or a real fault.  A state-only
                # packet must likewise revoke a previously normalized stop if
                # it changes the merged state to an active print.
                if ('print_error' in update
                        or ('hms' in update and error_changed)
                        or ('gcode_state' in update and error_changed)):
                    if effective_error is not None:
                        kwargs['error'] = effective_error
                self._effective_print_error = effective_error
                if any(k in update for k in ('gcode_file', 'subtask_name')):
                    # subtask_name is the identity supplied in our start command.
                    # Do not mix it with an old cached filename from a different job.
                    kwargs['task'] = str(update.get('subtask_name') or update.get('gcode_file') or '')
            self.queue.update_report(self.config['id'], **kwargs)
        except Exception:
            self.queue.offline(self.config['id'], 'Printerstatus kunne ikke lÃ¦ses. KontrollÃ©r opsÃ¦tningen.')
            log.exception('Could not parse report for %s', self.config['id'])

    @staticmethod
    def effective_print_error(report):
        """Return the safe error view for the merged Bambu report.

        ``print_error`` 0x0300400C is emitted for a user cancellation, but
        Bambu can retain it in FAILED, IDLE or FINISH after the task stops.  The cancellation code is
        normalized only when the merged report contains an explicitly known
        empty HMS list.  A missing HMS field, an unknown code, or any HMS
        entry keeps the nonzero error visible.
        """
        error = report.get('print_error')
        try:
            is_cancel = int(error) == BAMBU_CANCEL_PRINT_ERROR
        except (TypeError, ValueError):
            is_cancel = False
        if (is_cancel and report.get('gcode_state') in {'FAILED', 'IDLE', 'FINISH'}
                and 'hms' in report and report.get('hms') == []):
            return 0
        return error

    def upload(self, job, path):
        try:
            with ImplicitFTPS(self.pins) as ftp:
                ftp.connect(self.config['host'])
                ftp.login('bblp', self.config['access_code'])
                ftp.prot_p()
                with Path(path).open('rb') as source:
                    ftp.storbinary(f"STOR {job['remote_name']}", source, blocksize=64 * 1024)
                ftp.voidcmd('TYPE I')
                if ftp.size(job['remote_name']) != Path(path).stat().st_size:
                    raise ValueError('Printeren modtog ikke hele filen.')
        except CertificateTrustError:
            # FTPS trust failure belongs to this transfer only. MQTT
            # telemetry remains authoritative and the queue keeps its state.
            raise

    def send(self, job):
        if not self.client.is_connected() or not self.subscribed.is_set():
            raise ConnectionError('Printforbindelsen er ikke klar til at modtage printerens svar.')
        command = print_command(job, self.config)
        with self.lock:
            self.pending_start = {'job_id': job['id'], 'sequence_id': command['print']['sequence_id']}
        log.info('Sending print start once printer=%s job=%s sequence=%s', self.config['id'], job['id'], command['print']['sequence_id'])
        # QoS 0 deliberately avoids replaying a start packet after reconnect.
        info = self.client.publish(self.topic, json.dumps(command), qos=0, retain=False)
        info.wait_for_publish(timeout=10)
        if info.rc != mqtt.MQTT_ERR_SUCCESS or not info.is_published():
            raise TimeoutError('Startkommandoens afsendelse kunne ikke bekrÃ¦ftes.')
        log.info('Print start packet transmitted; awaiting printer reply printer=%s job=%s sequence=%s', self.config['id'], job['id'], command['print']['sequence_id'])
