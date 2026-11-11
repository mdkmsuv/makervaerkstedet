"""Keep an A1 mini's built-in chamber light on over the local MQTT link."""
import hashlib
import json
import re
import ssl
import hmac
from pathlib import Path
import threading
import time
import uuid

import paho.mqtt.client as mqtt

from .bambu import CertificatePins, tls_context


LIGHT_NODE = 'chamber_light'
LIGHT_FRESH_SECONDS = 90
COMMAND_INTERVAL = 60
PUSHALL_INTERVAL = 30


class PrinterLight:
    """A read-only-status plus idempotent light command MQTT client.

    This client deliberately has no queue or print command access. MQTT
    callbacks record state; the subscription callback only sends the initial
    nonblocking status request, while light corrections happen from ``tick``.
    """

    def __init__(self, config, certificates, clock=time.monotonic):
        self.config = dict(config)
        self.clock = clock
        pin = Path(certificates) / f"{self.config['id']}-mqtt.sha256"
        try:
            pinned = pin.read_text(encoding='ascii').strip().lower()
        except (OSError, UnicodeError):
            pinned = ''
        if not re.fullmatch(r'[0-9a-f]{64}', pinned):
            raise ValueError('Printerens eksisterende MQTT-certifikat mangler.')
        self.pinned_fingerprint = pinned
        self.pins = CertificatePins(certificates, self.config['id'])
        self.lock = threading.RLock()
        self.subscribed = threading.Event()
        self.connected = False
        self.mode = None
        self.last_seen = None
        self.last_command = None
        self.last_pushall = None
        self.last_command_at = None
        self.error = ''
        self.sequence = None
        client_id = f"pv_light_{self.config['id']}_{uuid.uuid4().hex}"
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=True)
        self.client.username_pw_set('bblp', self.config['access_code'])
        self.client.tls_set_context(tls_context())
        self.client.on_socket_open = self._socket_open
        self.client.on_connect = self._connected
        self.client.on_disconnect = self._disconnected
        self.client.on_subscribe = self._subscribed
        self.client.on_message = self._message
        self.client.reconnect_delay_set(2, 30)
        self.topic = f"device/{self.config['serial']}/request"
        self.report_topic = f"device/{self.config['serial']}/report"

    def _socket_open(self, client, userdata, sock):
        fingerprint = hashlib.sha256(sock.getpeercert(binary_form=True)).hexdigest()
        if not hmac.compare_digest(fingerprint, self.pinned_fingerprint):
            raise ssl.SSLError('Printerens certifikat er ændret. Kontrollér adressen og certifikatet.')

    def start(self):
        self.client.connect_async(self.config['host'], 8883, 30)
        self.client.loop_start()

    def stop(self):
        self.subscribed.clear()
        with self.lock:
            self.connected = False
            self.mode = None
            self.last_seen = None
        self.client.disconnect()
        self.client.loop_stop()

    def _connected(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            with self.lock:
                self.connected = False
                self.error = 'Printeren afviste lysforbindelsen.'
            return
        with self.lock:
            self.connected = True
            self.mode = None
            self.last_seen = None
            self.last_command_at = None
            self.last_pushall = None
            self.sequence = None
            self.error = ''
        self.subscribed.clear()
        client.subscribe(self.report_topic, qos=0)

    def _subscribed(self, client, userdata, mid, reason_codes, properties):
        if any(reason.is_failure for reason in reason_codes):
            with self.lock:
                self.error = 'Lysforbindelsen kan ikke modtage printerens svar.'
            self.subscribed.clear()
            return
        self.subscribed.set()
        self._publish_pushall()

    def _disconnected(self, client, userdata, disconnect_flags, reason_code, properties):
        self.subscribed.clear()
        with self.lock:
            self.connected = False
            self.mode = None
            self.last_seen = None
            self.last_command_at = None
            self.last_pushall = None
            self.sequence = None

    def _publish(self, payload, kind, *, record_command=True):
        try:
            info = self.client.publish(self.topic, json.dumps(payload), qos=0, retain=False)
            if getattr(info, 'rc', mqtt.MQTT_ERR_SUCCESS) != mqtt.MQTT_ERR_SUCCESS:
                with self.lock:
                    self.error = f'{kind}-kommandoet kunne ikke sendes.'
                return False
            with self.lock:
                self.error = ''
                if record_command:
                    self.last_command = self.clock()
            return True
        except Exception:
            with self.lock:
                self.error = f'{kind}-kommandoet kunne ikke sendes.'
            return False

    def _publish_pushall(self):
        if not self.connected or not self.subscribed.is_set():
            return False
        with self.lock:
            self.last_pushall = self.clock()
        ok = self._publish({'pushing': {'sequence_id': '0', 'command': 'pushall'}}, 'Status', record_command=False)
        return ok

    def _message(self, client, userdata, message):
        try:
            if len(message.payload) > 256 * 1024:
                return
            envelope = json.loads(message.payload)
            if not isinstance(envelope, dict):
                return
            system = envelope.get('system')
            if isinstance(system, dict) and system.get('command') == 'ledctrl':
                with self.lock:
                    matches = self.sequence is not None and str(system.get('sequence_id')) == self.sequence
                    if matches and str(system.get('result', '')).lower() in ('fail', 'failed', 'error'):
                        self.error = 'Printeren afviste lyskommandoet.'
                return
            report = envelope.get('print')
            if not isinstance(report, dict):
                return
            if (report.get('command') not in (None, 'push_status')
                    or any(key in report for key in ('result', 'reason', 'err_code'))):
                return
            # A ledctrl reply acknowledges a request, but is not a physical
            # state report. Only lights_report is allowed to confirm "on".
            lights = report.get('lights_report')
            if not isinstance(lights, list):
                return
            for entry in lights:
                if not isinstance(entry, dict) or entry.get('node') != LIGHT_NODE:
                    continue
                mode = entry.get('mode')
                if mode not in ('on', 'off', 'flashing'):
                    continue
                with self.lock:
                    self.mode = mode
                    self.last_seen = self.clock()
                return
        except Exception:
            with self.lock:
                self.error = 'Printerens lysstatus kunne ikke læses.'

    def tick(self):
        """Perform throttled nonblocking status polling and light correction."""
        now = self.clock()
        with self.lock:
            connected = self.connected
            subscribed = self.subscribed.is_set()
            mode = self.mode
            last_seen = self.last_seen
            last_pushall = self.last_pushall
            last_command_at = self.last_command_at
        if not connected or not subscribed:
            return
        if last_pushall is None or now - last_pushall >= PUSHALL_INTERVAL:
            self._publish_pushall()
        if mode not in ('off', 'flashing'):
            return
        if last_seen is None or not 0 <= now - last_seen < LIGHT_FRESH_SECONDS:
            return
        if last_command_at is not None and now - last_command_at < COMMAND_INTERVAL:
            return
        sequence = str(int(uuid.uuid4().hex, 16) % 2_000_000_000)
        payload = {'system': {'sequence_id': sequence, 'command': 'ledctrl',
                              'led_node': LIGHT_NODE, 'led_mode': 'on',
                              'led_on_time': 500, 'led_off_time': 500,
                              'loop_times': 0, 'interval_time': 0}}
        with self.lock:
            self.last_command_at = now
        if self._publish(payload, 'Lys'):
            with self.lock:
                self.sequence = sequence

    def snapshot(self):
        now = self.clock()
        with self.lock:
            fresh_on = bool(self.connected and self.mode == 'on' and self.last_seen is not None
                            and 0 <= now - self.last_seen < LIGHT_FRESH_SECONDS)
            return {'mode': self.mode, 'connected': self.connected,
                    'confirmed_on': fresh_on, 'last_seen': self.last_seen,
                    'last_command': self.last_command, 'error': self.error}
