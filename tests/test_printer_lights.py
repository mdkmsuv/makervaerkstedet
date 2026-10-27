import json
from pathlib import Path
import hashlib
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.printer_lights import PrinterLight


class FakeClock:
    def __init__(self): self.now = 1000.0
    def __call__(self): return self.now


class FakeInfo:
    rc = 0


class PrinterLightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.certificates = Path(self.temp.name)
        (self.certificates / 'mini-mqtt.sha256').write_text('a' * 64, encoding='ascii')
        self.clock = FakeClock()
        self.client = Mock()
        self.client.publish.return_value = FakeInfo()
        self.client.is_connected.return_value = True
        self.reason = Mock(is_failure=False)
        self.config = {'id': 'mini', 'serial': 'SERIAL', 'host': 'printer',
                       'access_code': 'test-only', 'model': 'A1 mini'}
        patcher = patch('app.printer_lights.mqtt.Client', return_value=self.client)
        self.client_patch = patcher.start()
        self.addCleanup(patcher.stop)
        self.light = PrinterLight(self.config, self.certificates, self.clock)

    def tearDown(self): self.temp.cleanup()

    def connect(self):
        self.light._connected(self.client, None, None, self.reason, None)
        self.light._subscribed(None, None, None, [self.reason], None)
        self.client.publish.reset_mock()

    def report(self, mode, node='chamber_light'):
        message = Mock(payload=json.dumps({'print': {'command': 'push_status', 'lights_report': [{'node': node, 'mode': mode}]}}).encode())
        self.light._message(None, None, message)

    def test_requires_existing_mqtt_pin(self):
        (self.certificates / 'mini-mqtt.sha256').unlink()
        with self.assertRaises(ValueError): PrinterLight(self.config, self.certificates, self.clock)

    def test_rejects_malformed_mqtt_pin(self):
        (self.certificates / 'mini-mqtt.sha256').write_text('not-a-fingerprint', encoding='ascii')
        with self.assertRaises(ValueError): PrinterLight(self.config, self.certificates, self.clock)

    def test_unique_client_and_tls_setup(self):
        self.assertTrue(self.client_patch.call_args.kwargs['client_id'].startswith('pv_light_mini_'))
        self.client.tls_set_context.assert_called_once()

    def test_off_report_throttles_led_commands_and_never_sends_print(self):
        self.connect(); self.report('off'); self.light.tick()
        self.assertEqual(self.client.publish.call_count, 1)
        payload = json.loads(self.client.publish.call_args.args[1])
        self.assertEqual(payload['system']['command'], 'ledctrl')
        self.assertEqual(payload['system']['led_mode'], 'on')
        self.assertEqual(payload['system']['led_on_time'], 500)
        self.assertEqual(payload['system']['led_off_time'], 500)
        self.assertEqual(payload['system']['loop_times'], 0)
        self.assertEqual(payload['system']['interval_time'], 0)
        self.assertTrue(payload['system']['sequence_id'].isdigit())
        self.assertEqual(self.client.publish.call_args.kwargs, {'qos': 0, 'retain': False})
        self.light.tick(); self.assertEqual(self.client.publish.call_count, 1)
        self.clock.now += 60; self.light.tick(); self.assertEqual(self.client.publish.call_count, 3)
        commands = [json.loads(call.args[1]) for call in self.client.publish.call_args_list]
        self.assertEqual(sum('system' in payload for payload in commands), 2)
        self.assertFalse(any('print' in payload for payload in commands))

    def test_on_report_confirms_only_fresh_physical_state(self):
        self.connect(); self.report('on')
        self.assertTrue(self.light.snapshot()['confirmed_on'])
        self.clock.now += 89; self.assertTrue(self.light.snapshot()['confirmed_on'])
        self.clock.now += 1; self.assertFalse(self.light.snapshot()['confirmed_on'])

    def test_unknown_node_and_ack_do_not_confirm_or_command(self):
        self.connect(); self.report('off', node='work_light'); self.light.tick()
        self.assertIsNone(self.light.snapshot()['mode']); self.assertEqual(self.client.publish.call_count, 0)
        ack = Mock(payload=json.dumps({'system': {'sequence_id': 'none', 'command': 'ledctrl', 'result': 'ok'}}).encode())
        self.light._message(None, None, ack)
        self.assertIsNone(self.light.snapshot()['mode'])

    def test_led_ack_does_not_confirm_and_ack_telemetry_is_ignored(self):
        self.connect()
        ack = Mock(payload=json.dumps({'system': {'sequence_id': '1', 'command': 'ledctrl', 'result': 'ok'},
                                        'print': {'command': 'ledctrl', 'lights_report': [{'node': 'chamber_light', 'mode': 'on'}]}}).encode())
        self.light._message(None, None, ack)
        self.assertIsNone(self.light.snapshot()['mode'])

    def test_publish_failure_is_throttled(self):
        self.connect(); self.report('off'); self.client.publish.side_effect = OSError('mqtt')
        self.light.tick(); self.clock.now += 1; self.light.tick()
        light_attempts = lambda: sum('system' in json.loads(call.args[1]) for call in self.client.publish.call_args_list)
        self.assertEqual(light_attempts(), 1)
        self.clock.now += 59; self.light.tick()
        self.assertEqual(light_attempts(), 2)

    def test_snapshot_has_status_only_schema(self):
        keys = set(self.light.snapshot())
        self.assertEqual(keys, {'mode', 'connected', 'confirmed_on', 'last_seen', 'last_command', 'error'})
        self.assertNotIn('serial', self.light.snapshot())
        self.assertNotIn('access_code', self.light.snapshot())

    def test_reconnect_resets_observation_and_allows_one_new_retry(self):
        self.connect(); self.report('on'); self.assertTrue(self.light.snapshot()['confirmed_on'])
        self.light._disconnected(None, None, None, None, None)
        self.assertFalse(self.light.snapshot()['confirmed_on'])
        self.light._connected(self.client, None, None, self.reason, None)
        self.light._subscribed(None, None, None, [self.reason], None)
        self.client.publish.reset_mock(); self.report('off'); self.light.tick()
        self.assertEqual(self.client.publish.call_count, 1)

    def test_publish_failure_is_reported_without_disconnect(self):
        self.connect(); self.report('off'); self.client.publish.side_effect = OSError('mqtt')
        self.light.tick(); self.assertTrue(self.light.snapshot()['error']); self.assertTrue(self.light.snapshot()['connected'])

    def test_pin_is_fingerprint_validated_without_tofu_after_construction(self):
        class Socket:
            def getpeercert(self, binary_form=False): return b'peer'
        fingerprint = hashlib.sha256(b'peer').hexdigest()
        (self.certificates / 'mini-mqtt.sha256').write_text(fingerprint, encoding='ascii')
        self.light.pinned_fingerprint = fingerprint
        self.light._socket_open(None, None, Socket())
        (self.certificates / 'mini-mqtt.sha256').unlink()
        self.light._socket_open(None, None, Socket())

    def test_pin_mismatch_is_rejected(self):
        class Socket:
            def getpeercert(self, binary_form=False): return b'other'
        with self.assertRaises(Exception): self.light._socket_open(None, None, Socket())


if __name__ == '__main__': unittest.main()
