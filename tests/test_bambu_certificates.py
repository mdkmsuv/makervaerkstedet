"""Actual local TLS certificate observations for the printer pin workflow."""
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.bambu import BambuPrinter, CertificatePins, CertificateTrustError, probe_printer_certificates
from app.core import Queue


def make_certificate(folder, label):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, label)])
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=1))
                   .not_valid_after(now + timedelta(days=1))
                   .sign(key, hashes.SHA256()))
    cert_path = Path(folder) / f'{label}.crt'
    key_path = Path(folder) / f'{label}.key'
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.TraditionalOpenSSL,
                                           serialization.NoEncryption()))
    return cert_path, key_path, hashlib.sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest()


class LocalTLSServer:
    def __init__(self, certificate, key):
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(1)
        self.listener.settimeout(3)
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(certificate, key)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.error = None

    @property
    def address(self):
        return self.listener.getsockname()

    def __enter__(self):
        self.thread.start()
        return self

    def _serve(self):
        try:
            connection, _ = self.listener.accept()
            with self.context.wrap_socket(connection, server_side=True) as tls:
                tls.settimeout(1)
                try:
                    tls.recv(1)
                except (socket.timeout, ssl.SSLError, ConnectionResetError):
                    pass
        except Exception as error:  # pragma: no cover - surfaced by the client assertion
            self.error = error

    def client(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        raw = socket.create_connection(self.address, timeout=3)
        return context.wrap_socket(raw, server_hostname='printer.local')

    def __exit__(self, *_):
        try:
            self.listener.close()
        finally:
            self.thread.join(timeout=3)
        if self.error:
            raise self.error


class CertificatePinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cert1 = make_certificate(self.temp.name, 'first')
        self.cert2 = make_certificate(self.temp.name, 'second')
        self.pins = CertificatePins(Path(self.temp.name) / 'pins', 'printer1', strict=True,
                                    identity={'host': '192.168.1.40', 'serial': 'SERIAL1', 'version': 1})

    def tearDown(self):
        self.temp.cleanup()

    def observe(self, certificate, channel='mqtt'):
        with LocalTLSServer(str(certificate[0]), str(certificate[1])) as server:
            with server.client() as socket_:
                self.pins.check(socket_, channel)

    def test_actual_tls_first_observation_requires_teacher_and_can_be_confirmed(self):
        with self.assertRaises(CertificateTrustError) as error:
            self.observe(self.cert1)
        self.assertIsNone(error.exception.details['old_fingerprint'])
        self.assertEqual(error.exception.details['new_fingerprint'], self.cert1[2])
        self.assertEqual(self.pins.status()['mqtt']['status'], 'confirmation_required')
        with self.assertRaises(ValueError):
            self.pins.confirm('mqtt', 'c' * 64)
        self.pins.confirm('mqtt', self.cert1[2])
        self.assertEqual(self.pins.status()['mqtt']['status'], 'trusted')
        self.observe(self.cert1)

    def test_actual_tls_change_reports_old_and_new_and_never_blindly_resets(self):
        self.pins._atomic_write(self.pins._path('mqtt'), self.cert1[2])
        with self.assertRaises(CertificateTrustError) as error:
            self.observe(self.cert2)
        self.assertEqual(error.exception.details['old_fingerprint'], self.cert1[2])
        self.assertEqual(error.exception.details['new_fingerprint'], self.cert2[2])
        with self.assertRaises(ValueError):
            self.pins.confirm('mqtt', self.cert1[2])
        self.assertEqual(self.pins._read_pin('mqtt'), self.cert1[2])
        changed_identity = CertificatePins(self.pins.folder, 'printer1', strict=True,
                                           identity={'host': '192.168.1.41', 'serial': 'SERIAL1', 'version': 2})
        with self.assertRaises(ValueError):
            changed_identity.confirm('mqtt', self.cert2[2])
        self.pins.confirm('mqtt', self.cert2[2])
        self.observe(self.cert2)

    def test_atomic_pin_failure_keeps_old_pin_and_pending_observation(self):
        self.pins._atomic_write(self.pins._path('mqtt'), self.cert1[2])
        with self.assertRaises(CertificateTrustError):
            self.observe(self.cert2)
        with patch('app.bambu.os.replace', side_effect=OSError('read-only')):
            with self.assertRaises(OSError):
                self.pins.confirm('mqtt', self.cert2[2])
        self.assertEqual(self.pins._read_pin('mqtt'), self.cert1[2])
        self.assertEqual(self.pins.status()['mqtt']['status'], 'confirmation_required')

    def test_separate_instances_share_pending_lock_and_use_no_fixed_temp_name(self):
        second = CertificatePins(self.pins.folder, 'printer1', strict=True,
                                 identity={'host': '192.168.1.40', 'serial': 'SERIAL1', 'version': 1})
        barrier = threading.Barrier(2)
        errors = []

        def write(pins, fingerprint):
            try:
                barrier.wait(timeout=2)
                pins._write_pending('mqtt', None, fingerprint)
            except Exception as error:  # pragma: no cover - assertion below reports races
                errors.append(error)

        threads = [threading.Thread(target=write, args=(self.pins, 'a' * 64)),
                   threading.Thread(target=write, args=(second, 'b' * 64))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        self.assertEqual(errors, [])
        pending = self.pins._read_pending('mqtt')
        self.assertIn(pending['new_fingerprint'], {'a' * 64, 'b' * 64})
        self.assertEqual(list(self.pins.folder.glob('*.tmp')), [])

    def test_certificate_storage_rejects_path_traversal_and_oversized_pin(self):
        with self.assertRaises(ValueError):
            CertificatePins(self.pins.folder, '../other', strict=True)
        self.pins._path('mqtt').write_text('x' * 129, encoding='ascii')
        with self.assertRaises(ssl.SSLError):
            self.pins._read_pin('mqtt')

    def test_probe_performs_real_mqtt_and_ftps_tls_handshakes_without_commands(self):
        config = {'id': 'printer1', 'host': '192.168.1.40', 'serial': 'SERIAL1', 'version': 1}
        with LocalTLSServer(str(self.cert1[0]), str(self.cert1[1])) as mqtt_server:
            with LocalTLSServer(str(self.cert2[0]), str(self.cert2[1])) as ftp_server:
                original_connect = socket.create_connection

                def route(address, timeout=None, source_address=None):
                    target = mqtt_server.address if address[1] == 8883 else ftp_server.address
                    return original_connect(target, timeout, source_address=source_address)

                with patch('app.bambu.socket.create_connection', side_effect=route):
                    result = probe_printer_certificates(config, self.pins.folder)
        self.assertEqual(result['channels']['mqtt']['status'], 'confirmation_required')
        self.assertEqual(result['channels']['ftp']['status'], 'confirmation_required')
        self.assertEqual(result['channels']['mqtt']['new_fingerprint'], self.cert1[2])
        self.assertEqual(result['channels']['ftp']['new_fingerprint'], self.cert2[2])
        self.assertEqual(self.pins.status()['mqtt']['pending']['new_fingerprint'], self.cert1[2])
        self.assertEqual(self.pins.status()['ftp']['pending']['new_fingerprint'], self.cert2[2])

    def test_ftps_certificate_failure_does_not_offline_fresh_mqtt_telemetry(self):
        config = {'id': 'printer1', 'name': 'Printer', 'location': '', 'model': 'P1S',
                  'host': '192.168.1.40', 'serial': 'SERIAL1', 'access_code': 'secret',
                  'enabled': True, 'version': 1, 'bed_type': 'Textured PEI Plate'}
        queue = Queue(Path(self.temp.name) / 'queue', [config])
        try:
            queue.update_report('printer1', state='RUNNING', task='pv_job.gcode.3mf', error=0)
            queue.printers['printer1']['bed_clear'] = False
            driver = BambuPrinter(config, queue, Mock())
            path = Path(self.temp.name) / 'job.3mf'; path.write_bytes(b'payload')

            class FailingFTPS:
                def __init__(self, _pins):
                    pass

                def __enter__(self):
                    raise CertificateTrustError({'channel': 'ftp', 'old_fingerprint': 'a' * 64,
                                                 'new_fingerprint': 'b' * 64})

                def __exit__(self, *_args):
                    return False

            with patch('app.bambu.ImplicitFTPS', FailingFTPS):
                with self.assertRaises(CertificateTrustError):
                    driver.upload({'remote_name': 'pv_job.gcode.3mf'}, path)
            self.assertEqual(queue.printers['printer1']['state'], 'RUNNING')
            self.assertEqual(queue.printers['printer1']['task'], 'pv_job.gcode.3mf')
            self.assertFalse(queue.printers['printer1']['bed_clear'])
        finally:
            queue.close()


if __name__ == '__main__':
    unittest.main()
