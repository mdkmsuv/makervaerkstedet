import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.laser_transfer import FluxTransfer, TransferError


UUID = 'a' * 32
CONTENT = b'locally compiled fixture only'
JOB_ID = 'b' * 32


class FakeSocket:
    def settimeout(self, value):
        self.timeout = value


class FakeWebSocket:
    def __init__(self, host, port, path, timeout, responses):
        self.address = (host, port, path, timeout)
        self.responses = iter(responses)
        self.text = []
        self.binary = []
        self.closed = False
        self.sock = FakeSocket()

    def send_text(self, value):
        self.text.append(value)

    def send_binary(self, value):
        self.binary.append(value)

    def recv_json_or_bytes(self):
        try:
            return next(self.responses)
        except StopIteration:
            raise OSError('simulated connection loss') from None

    def close(self):
        self.closed = True


class TransferProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.key = Path(self.temp.name) / 'trusted.pem'
        self.key.write_text('-----BEGIN RSA PRIVATE KEY-----\nfixture\n', encoding='ascii')
        self.config = {'enabled': True, 'commissioned': True, 'port': 8282,
                       'device_uuid': UUID, 'model': 'fbb2', 'client_key_file': str(self.key)}
        self.sockets = []

    def transfer(self, responses):
        def factory(host, port, path, timeout):
            ws = FakeWebSocket(host, port, path, timeout, responses)
            self.sockets.append(ws)
            return ws
        return FluxTransfer(self.config, websocket_factory=factory)

    def good_responses(self):
        return [{'status': 'connecting'}, {'status': 'connected'},
                {'status': 'ok', 'uuid': UUID, 'model': 'fbb2'},
                {'status': 'ok', 'device_status': {'st_id': 0}},
                {'status': 'continue'}, {'status': 'uploading', 'sent': len(CONTENT)},
                {'status': 'ok'},
                {'status': 'ok', 'md5': hashlib.md5(CONTENT, usedforsecurity=False).hexdigest()}]

    def test_disabled_or_uncommissioned_config_never_creates_socket(self):
        for field in ('enabled', 'commissioned'):
            with self.subTest(field=field):
                config = dict(self.config, **{field: False})
                transfer = FluxTransfer(config, websocket_factory=lambda *args, **kwargs: self.fail('socket opened'))
                self.assertFalse(transfer.status()['available'])
                with self.assertRaises(TransferError):
                    transfer.upload(CONTENT, job_id=JOB_ID)
        for config in (dict(self.config, port=True), dict(self.config, model='beamo'),
                       dict(self.config, device_uuid='wrong'), dict(self.config, client_key_file='missing')):
            with self.subTest(config=config):
                self.assertFalse(FluxTransfer(config).status()['available'])

    def test_upload_uses_only_allowed_commands_and_verifies_integrity(self):
        transfer = self.transfer(self.good_responses())
        receipt = transfer.upload(CONTENT, job_id=JOB_ID)
        ws = self.sockets[0]
        path = f'/SD/laserstation-{JOB_ID}.fc'
        self.assertEqual(ws.address, ('127.0.0.1', 8282, '/ws/control/' + UUID, 10))
        self.assertEqual(ws.text[1:], ['deviceinfo', 'play report',
                                       f'file upload application/fcode {len(CONTENT)} {path}',
                                       'file md5 ' + path])
        self.assertEqual(b''.join(ws.binary), CONTENT)
        self.assertTrue(ws.closed)
        self.assertEqual(receipt, {'path': path, 'bytes': len(CONTENT), 'verified': True, 'started': False})
        self.assertNotIn('start', ' '.join(ws.text[1:]))

    def test_wrong_device_or_busy_laser_stops_before_upload(self):
        cases = [([{'status': 'connected'}, {'status': 'ok', 'uuid': 'c' * 32, 'model': 'fbb2'}],
                  'deviceinfo'),
                 ([{'status': 'connected'}, {'status': 'ok', 'uuid': UUID, 'model': 'fbb2'},
                   {'status': 'ok', 'device_status': {'st_id': 16}}], 'play report')]
        for responses, last_command in cases:
            with self.subTest(last_command=last_command):
                self.sockets.clear()
                transfer = self.transfer(responses)
                with self.assertRaises(TransferError):
                    transfer.upload(CONTENT, job_id=JOB_ID)
                self.assertEqual(self.sockets[0].text[-1], last_command)
                self.assertEqual(self.sockets[0].binary, [])

    def test_failed_md5_and_post_upload_disconnect_report_uncertain_state(self):
        bad = self.good_responses()
        bad[-1] = {'status': 'ok', 'md5': '0' * 32}
        for responses in (bad, self.good_responses()[:-1]):
            with self.subTest(responses=responses):
                self.sockets.clear()
                transfer = self.transfer(responses)
                with self.assertRaisesRegex(TransferError, 'kan ligge på laseren'):
                    transfer.upload(CONTENT, job_id=JOB_ID)
                self.assertEqual(len(self.sockets[0].binary), 1)
                self.assertTrue(self.sockets[0].closed)

    def test_pre_upload_error_never_sends_bytes(self):
        transfer = self.transfer([{'status': 'fatal'}])
        with self.assertRaises(TransferError):
            transfer.upload(CONTENT, job_id=JOB_ID)
        self.assertEqual(self.sockets[0].binary, [])
        with self.assertRaises(TransferError):
            transfer.upload(CONTENT, job_id='bad')
        self.assertEqual(len(self.sockets), 1)

    def test_default_config_and_bad_payload_do_not_connect(self):
        self.assertFalse(FluxTransfer().status()['available'])
        transfer = FluxTransfer(self.config, websocket_factory=lambda *args, **kwargs: self.fail('socket opened'))
        for content, job_id in ((b'', JOB_ID), (bytearray(CONTENT), JOB_ID),
                                (CONTENT, 'invalid'), (CONTENT, 'B' * 32)):
            with self.subTest(content=content, job_id=job_id), self.assertRaises(TransferError):
                transfer.upload(content, job_id=job_id)

    def test_wrong_model_and_missing_idle_state_stop_before_upload(self):
        cases = [
            [{'status': 'connected'}, {'status': 'ok', 'uuid': UUID, 'model': 'beamo'}],
            [{'status': 'connected'}, {'status': 'ok', 'uuid': UUID, 'model': 'fbb2'},
             {'status': 'ok', 'device_status': {'st_id': True}}],
            [{'status': 'connected'}, {'status': 'ok', 'uuid': UUID, 'model': 'fbb2'},
             {'status': 'ok', 'device_status': {}}],
        ]
        for responses in cases:
            with self.subTest(responses=responses):
                self.sockets.clear()
                with self.assertRaises(TransferError):
                    self.transfer(responses).upload(CONTENT, job_id=JOB_ID)
                self.assertFalse(self.sockets[0].binary)
                self.assertTrue(self.sockets[0].closed)

    def test_timeout_and_disconnect_never_retry(self):
        # A closed socket after bytes may mean the complete file exists remotely.
        responses = self.good_responses()[:-2]
        transfer = self.transfer(responses)
        with self.assertRaisesRegex(TransferError, 'kan ligge på laseren'):
            transfer.upload(CONTENT, job_id=JOB_ID)
        self.assertEqual(len(self.sockets), 1)
        self.assertEqual(b''.join(self.sockets[0].binary), CONTENT)
        self.assertTrue(self.sockets[0].closed)

        # Elapsed deadline during the binary phase has the same uncertain outcome.
        self.sockets.clear()
        with patch('app.laser_transfer.time.monotonic', side_effect=[0, 0, 0, 0, 0, 0, 121]):
            with self.assertRaisesRegex(TransferError, 'kan ligge på laseren'):
                self.transfer(self.good_responses()).upload(CONTENT, job_id=JOB_ID)
        self.assertEqual(len(self.sockets), 1)
        self.assertTrue(self.sockets[0].closed)


if __name__ == '__main__':
    unittest.main()
