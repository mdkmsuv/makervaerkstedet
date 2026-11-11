"""Camera compatibility without sockets, hardware, or mocking the pin checker."""
import hashlib
from pathlib import Path
import ssl
import struct
import tempfile
import unittest
from unittest.mock import patch

from app.bambu import CERTIFICATE_CHANNELS, CertificatePins
from app.camera import capture_jpeg
import test_camera_capture as fixtures


class CameraCertificateCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fingerprint = hashlib.sha256(b'camera-certificate').hexdigest()
        self.config = {'id': 'printer', 'host': 'synthetic.invalid', 'model': 'P1S', 'access_code': '1234'}
        self.camera_pin = self.root / 'printer-camera.sha256'
        self.camera_pin.write_text(self.fingerprint)
        (self.root / 'printer-mqtt.sha256').write_text(self.fingerprint)

    def test_existing_camera_pin_matches_without_changing_trust(self):
        pins = CertificatePins(self.root, 'printer')
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        self.assertEqual(pins.check(fixtures.FakeConnection(b''), 'camera'), self.fingerprint)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_camera_mismatch_is_not_automatically_retrusted(self):
        self.camera_pin.write_text('0' * 64)
        pins = CertificatePins(self.root, 'printer')
        with self.assertRaises(ssl.SSLError):
            pins.check(fixtures.FakeConnection(b''), 'camera')
        self.assertEqual(self.camera_pin.read_text(), '0' * 64)
        self.assertFalse((self.root / 'printer-camera.pending.json').exists())

    def test_strict_printer_channels_still_reject_camera(self):
        self.assertEqual(CERTIFICATE_CHANNELS, ('mqtt', 'ftp'))
        pins = CertificatePins(self.root, 'printer', strict=True)
        before = self.camera_pin.read_bytes()
        with self.assertRaises(ValueError):
            pins.check(fixtures.FakeConnection(b''), 'camera')
        self.assertEqual(self.camera_pin.read_bytes(), before)
        self.assertEqual(set(pins.status()), {'mqtt', 'ftp'})

    def test_capture_call_chain_uses_real_pin_checker_before_credentials(self):
        frames = [fixtures.frame(level) for level in (30, 60, 90, 120, 150)]
        payload = b''.join(struct.pack('<IIII', len(item), 0, 0, 0) + item for item in frames)
        connection = fixtures.FakeConnection(payload)
        with patch('app.camera.socket.create_connection', return_value=connection), \
             patch('app.camera.tls_context', return_value=fixtures.FakeTLS(connection)):
            self.assertEqual(capture_jpeg(self.config, self.root, timeout=18), frames[-1])
        self.assertTrue(connection.sent)
        self.camera_pin.write_text('0' * 64)
        rejected = fixtures.FakeConnection(payload)
        with patch('app.camera.socket.create_connection', return_value=rejected), \
             patch('app.camera.tls_context', return_value=fixtures.FakeTLS(rejected)), \
             self.assertRaises(ssl.SSLError):
            capture_jpeg(self.config, self.root, timeout=18)
        self.assertEqual(rejected.sent, [], 'Credentials must not be sent after a pin mismatch')


if __name__ == '__main__':
    unittest.main()
