import io
from pathlib import Path
import socket
import struct
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from app.camera import capture_jpeg


def frame(level):
    image = Image.new('RGB', (640, 480), (level, level, level))
    output = io.BytesIO()
    image.save(output, format='JPEG')
    return output.getvalue()


class FakeConnection:
    def __init__(self, payload):
        self.payload = bytearray(payload)
        self.timeouts = []
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def settimeout(self, value):
        self.timeouts.append(value)

    def recv(self, size):
        if not self.payload:
            raise socket.timeout('stream ended')
        chunk = self.payload[:size]
        del self.payload[:size]
        return bytes(chunk)

    def sendall(self, data):
        self.sent.append(data)

    def getpeercert(self, binary_form=False):
        return b'camera-certificate'


class FakeTLS:
    def __init__(self, connection):
        self.connection = connection

    def wrap_socket(self, raw, server_hostname=None):
        return self.connection


class CameraCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.certificates = Path(self.temp.name)
        self.config = {'id': 'printer', 'host': 'printer.local', 'model': 'P1S', 'access_code': '1234'}
        import hashlib
        fingerprint = hashlib.sha256(b'camera-certificate').hexdigest()
        (self.certificates / 'printer-mqtt.sha256').write_text(fingerprint)

    def tearDown(self):
        self.temp.cleanup()

    def capture(self, payload, connection=None):
        connection = connection or FakeConnection(payload)
        with patch('app.camera.socket.create_connection', return_value=connection), \
             patch('app.camera.tls_context', return_value=FakeTLS(connection)), \
             patch('app.camera.CertificatePins.check'):
            return capture_jpeg(self.config, self.certificates, timeout=18)

    @staticmethod
    def stream(*frames):
        return b''.join(struct.pack('<IIII', len(item), 0, 0, 0) + item for item in frames)

    def test_discards_unsettled_startup_frames(self):
        frames = [frame(level) for level in (30, 60, 90, 120, 150)]
        self.assertEqual(self.capture(self.stream(*frames)), frames[-1])

    def test_malformed_frame_fails_closed(self):
        frames = [frame(30), b'not-a-jpeg' * 20, frame(90), frame(120), frame(150)]
        with self.assertRaises(ValueError):
            self.capture(self.stream(*frames))

    def test_truncated_stream_fails(self):
        first = frame(30)
        payload = struct.pack('<IIII', len(first), 0, 0, 0) + first
        with self.assertRaises(socket.timeout):
            self.capture(payload)

    def test_all_frames_share_one_deadline(self):
        frames = [frame(level) for level in (30, 60, 90, 120, 150)]
        connection = FakeConnection(self.stream(*frames))
        ticks = iter(range(0, 30, 2))
        with patch('app.camera.time.monotonic', side_effect=lambda: next(ticks)), \
             patch('app.camera.socket.create_connection', return_value=connection), \
             patch('app.camera.tls_context', return_value=FakeTLS(connection)), \
             patch('app.camera.CertificatePins.check'):
            with self.assertRaises(TimeoutError):
                capture_jpeg(self.config, self.certificates, timeout=18)
        self.assertLessEqual(max(connection.timeouts), 18)


if __name__ == '__main__':
    unittest.main()
