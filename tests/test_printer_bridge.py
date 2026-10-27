import copy
import email.message
import http.client
import json
from pathlib import Path
import threading
import tempfile
import types
import unittest
from unittest.mock import patch

from app.bambu import CertificatePins
from app.core import Queue
from app.printer_bridge import PrinterBridge
from app.printer_config import ConfigConflict
from app.server import Handler, Server
from app.teacher_auth import TeacherAuth


ACTOR = "teacher:" + "1" * 32
BASE = {
    "name": "Printer", "location": "Lokale 1", "model": "P1S",
    "host": "192.168.1.40", "serial": "SERIAL1", "access_code": "secret",
    "enabled": False, "bed_type": "Textured PEI Plate",
}


class FakeDriver:
    instances = []
    fail_start = False

    def __init__(self, config, queue, profiles):
        self.config = config
        self.queue = queue
        self.starts = 0
        self.stops = 0
        type(self).instances.append(self)

    def start(self):
        self.starts += 1
        if type(self).fail_start:
            raise RuntimeError("no hardware in unit test")

    def stop(self):
        self.stops += 1


class PrinterBridgeTests(unittest.TestCase):
    def setUp(self):
        FakeDriver.instances = []
        FakeDriver.fail_start = False
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.app = self.make_app()
        self.bridge = PrinterBridge(self.app, FakeDriver)

    def tearDown(self):
        self.app.queue.close()
        self.temp.cleanup()

    def make_app(self):
        folder = self.path
        settings_path = folder / "settings.json"
        settings = json.loads(settings_path.read_text()) if settings_path.exists() else {"printers": []}
        queue = Queue(folder, settings.get("printers", []))
        return types.SimpleNamespace(
            folder=folder, settings_path=settings_path, settings=settings,
            settings_lock=threading.RLock(), queue=queue,
            slicer=types.SimpleNamespace(profiles=types.SimpleNamespace()),
            drivers={}, demo=False,
        )

    @staticmethod
    def command(action, params, expected=0, request_id=None):
        return {"action": action, "params": params,
                "expected_version": expected,
                "request_id": request_id or ("a" * 32)}

    def create(self, request_id="a" * 32):
        return self.bridge.execute(ACTOR, self.command("printer.create", dict(BASE), request_id=request_id))

    def test_legacy_agent_actor_is_rejected(self):
        with self.assertRaises(PermissionError):
            self.bridge.execute("agent:" + "1" * 32, self.command("printer.create", dict(BASE)))

    def test_legacy_snapshot_supplies_default_bed_type_without_rewriting_settings(self):
        legacy = {
            "id": "legacy-printer", "name": "Gammel printer", "location": "Lokale 1",
            "model": "P1S", "host": "192.168.1.40", "serial": "SERIAL1",
            "access_code": "secret", "enabled": True,
        }
        self.app.settings["printers"] = [legacy]

        snapshot = self.bridge.snapshot()
        public = snapshot["printers"][0]

        self.assertEqual(public["bed_type"], "Textured PEI Plate")
        self.assertEqual(public["version"], 1)
        self.assertNotIn("access_code", public)
        self.assertEqual(self.app.settings["printers"], [legacy])

    def test_create_snapshot_idempotency_and_reopen_preserve_same_key(self):
        response = self.create()
        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["version"], 1)
        self.assertNotIn("access_code", response["result"])
        self.assertNotIn("access_code", json.dumps(self.bridge.snapshot()))
        persisted = json.loads((self.path / "settings.json").read_text())
        self.assertEqual(persisted["printers"][0]["access_code"], "secret")
        self.assertIn("_makerspace_receipts", persisted)

        previous_snapshot = self.bridge.snapshot()
        self.app.queue.close()
        reopened_app = self.make_app()
        reopened = PrinterBridge(reopened_app, FakeDriver)
        self.assertEqual(reopened.snapshot(), previous_snapshot)
        self.assertEqual(reopened.execute(ACTOR, self.command("printer.create", dict(BASE)) ), response)
        reopened_app.queue.close()

    def test_changed_request_payload_conflicts_and_stale_update_does_not_write(self):
        created = self.create()
        printer_id = created["result"]["id"]
        before = (self.path / "settings.json").read_bytes()
        changed = self.command("printer.create", {**BASE, "name": "Changed"})
        with self.assertRaises(ConfigConflict):
            self.bridge.execute(ACTOR, changed)
        update = {**BASE, "id": printer_id, "name": "Updated", "access_code": ""}
        with self.assertRaises(ConfigConflict):
            self.bridge.execute(ACTOR, self.command("printer.update", update, expected=0,
                                                    request_id="b" * 32))
        self.assertEqual((self.path / "settings.json").read_bytes(), before)

    def test_concurrent_same_version_updates_have_one_winner(self):
        created = self.create()
        printer_id = created["result"]["id"]
        results = []

        def update(name, request_id):
            params = {**BASE, "id": printer_id, "name": name, "access_code": ""}
            try:
                results.append(self.bridge.execute(ACTOR, self.command(
                    "printer.update", params, expected=1, request_id=request_id)))
            except ConfigConflict:
                results.append("conflict")

        threads = [threading.Thread(target=update, args=("A", "b" * 32)),
                   threading.Thread(target=update, args=("B", "c" * 32))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        self.assertEqual(sum(result != "conflict" for result in results), 1)
        self.assertEqual(sum(result == "conflict" for result in results), 1)
        self.assertEqual(self.bridge.snapshot()["printers"][0]["version"], 2)

    def test_active_job_does_not_mutate_or_construct_driver(self):
        before = copy.deepcopy(self.app.settings)
        self.app.queue.active = lambda _printer_id: {"id": "active"}
        with self.assertRaises(ConfigConflict):
            self.create()
        self.assertEqual(self.app.settings, before)
        self.assertEqual(FakeDriver.instances, [])

    def seed_existing(self):
        config = {**BASE, "id": "legacy-printer", "version": 1, "enabled": True}
        self.app.settings["printers"] = [config]
        self.app.queue.configs[config["id"]] = config
        self.app.queue.printers[config["id"]] = {
            "id": config["id"], "name": config["name"], "state": "OFFLINE",
            "seen": 0, "state_seen": 0, "tray_seen": 0, "bed_clear": False,
            "trays": [], "progress": 0, "remaining": None, "error": 0, "task": "",
            "enabled": True, "message": "old",
        }
        old = FakeDriver(config, self.app.queue, self.app.slicer.profiles)
        self.app.drivers[config["id"]] = old
        return config

    def update_params(self, config):
        return {**BASE, "id": config["id"], "name": "Updated", "access_code": ""}

    def test_constructor_failure_and_write_failure_leave_old_runtime_untouched(self):
        old = self.seed_existing()
        before = copy.deepcopy(self.app.settings)
        FakeDriver.fail_start = False
        factory = lambda *args: (_ for _ in ()).throw(RuntimeError("construct"))
        failing = PrinterBridge(self.app, factory)
        with self.assertRaises(RuntimeError):
            failing.execute(ACTOR, self.command("printer.update", self.update_params(old),
                                                expected=1, request_id="d" * 32))
        self.assertEqual(self.app.settings, before)
        self.assertEqual(self.app.drivers[old["id"]].stops, 0)
        with patch.object(self.bridge, "_persist", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.bridge.execute(ACTOR, self.command("printer.update", self.update_params(old),
                                                        expected=1, request_id="e" * 32))
        self.assertEqual(self.app.settings, before)
        self.assertEqual(self.app.drivers[old["id"]].stops, 0)

    def test_start_failure_commits_offline_and_retry_receipt_does_not_restart(self):
        FakeDriver.fail_start = True
        response = self.create("f" * 32)
        self.assertTrue(response["ok"])
        printer_id = response["result"]["id"]
        self.assertFalse(self.app.queue.printers[printer_id]["enabled"])
        self.assertTrue(self.app.queue.printers[printer_id]["configuration_error"])
        starts = len(FakeDriver.instances)
        self.assertEqual(self.bridge.execute(ACTOR, self.command(
            "printer.create", dict(BASE), request_id="f" * 32)), response)
        self.assertEqual(len(FakeDriver.instances), starts)

    def test_certificate_confirmation_is_explicit_version_bound_and_retryable(self):
        created = self.create('1' * 32)
        printer_id = created['result']['id']
        config = self.app.settings['printers'][0]
        pins = CertificatePins(self.path / 'certificates', printer_id, strict=True,
                               identity=config)
        pins._atomic_write(pins._path('mqtt'), 'a' * 64)
        pins._write_pending('mqtt', 'a' * 64, 'b' * 64)
        command = self.command('printer.certificate.confirm', {}, request_id='2' * 32)
        command['params'] = {'id': printer_id, 'channel': 'mqtt', 'fingerprint': 'b' * 64}
        command['expected_version'] = config['version']
        response = self.bridge.execute(ACTOR, command)
        self.assertEqual(response['result'], {'id': printer_id, 'version': 1,
                                               'channel': 'mqtt', 'fingerprint': 'b' * 64,
                                               'reconnected': True})
        self.assertEqual((self.path / 'certificates' / f'{printer_id}-mqtt.sha256').read_text(), 'b' * 64)
        self.assertFalse((self.path / 'certificates' / f'{printer_id}-mqtt.pending.json').exists())
        starts = len(FakeDriver.instances)
        self.assertEqual(self.bridge.execute(ACTOR, command), response)
        self.assertEqual(len(FakeDriver.instances), starts)

    def test_certificate_confirmation_is_blocked_by_active_job_and_identity_change(self):
        created = self.create('3' * 32)
        printer_id = created['result']['id']
        config = self.app.settings['printers'][0]
        pins = CertificatePins(self.path / 'certificates', printer_id, strict=True,
                               identity=config)
        pins._atomic_write(pins._path('mqtt'), 'a' * 64)
        pins._write_pending('mqtt', 'a' * 64, 'b' * 64)
        command = self.command('printer.certificate.confirm', {}, request_id='4' * 32)
        command.update(
                       params={'id': printer_id, 'channel': 'mqtt', 'fingerprint': 'b' * 64},
                       expected_version=config['version'])
        self.app.queue.active = lambda _printer_id: {'id': 'active'}
        with self.assertRaises(ConfigConflict):
            self.bridge.execute(ACTOR, command)
        self.app.queue.active = lambda _printer_id: None
        self.app.settings['printers'][0]['host'] = '192.168.1.41'
        with self.assertRaises(ValueError):
            self.bridge.execute(ACTOR, command)

    def test_certificate_confirmation_invalidates_bed_and_resumes_after_final_persist_failure(self):
        created = self.create('5' * 32)
        printer_id = created['result']['id']
        config = self.app.settings['printers'][0]
        self.app.queue.printers[printer_id]['bed_clear'] = True
        self.app.queue.printers[printer_id]['enabled'] = True
        self.app.queue.paused = True
        pins = CertificatePins(self.path / 'certificates', printer_id, strict=True,
                               identity=config)
        pins._atomic_write(pins._path('mqtt'), 'a' * 64)
        pins._write_pending('mqtt', 'a' * 64, 'b' * 64)
        command = self.command('printer.certificate.confirm',
                               {'id': printer_id, 'channel': 'mqtt', 'fingerprint': 'b' * 64},
                               expected=config['version'], request_id='6' * 32)
        original_persist = self.bridge._persist
        calls = []

        def fail_final(settings):
            calls.append(settings)
            if len(calls) == 2:
                raise OSError('simulated final settings failure')
            return original_persist(settings)

        with patch.object(self.bridge, '_persist', side_effect=fail_final):
            with self.assertRaises(OSError):
                self.bridge.execute(ACTOR, command)
        self.assertEqual(self.app.queue.printers[printer_id]['bed_clear'], False)
        self.assertEqual(self.app.queue.printers[printer_id]['enabled'], True)
        self.assertEqual(self.app.queue.paused, True)
        operation_key = ACTOR + ':' + '6' * 32
        self.assertIn(operation_key, self.app.settings['_certificate_operations'])
        persisted = json.loads(self.app.settings_path.read_text(encoding='utf-8'))
        self.assertIn(operation_key, persisted['_certificate_operations'])
        self.assertNotIn(operation_key, self.app.settings.get('_makerspace_receipts', {}))
        self.assertEqual(pins._read_pin('mqtt'), 'b' * 64)
        response = self.bridge.execute(ACTOR, command)
        self.assertEqual(response['result']['fingerprint'], 'b' * 64)
        self.assertIn(operation_key, self.app.settings['_makerspace_receipts'])
        self.assertNotIn(operation_key, self.app.settings.get('_certificate_operations', {}))

    def test_certificate_journal_write_failure_does_not_invalidate_bed_or_pin(self):
        created = self.create('7' * 32)
        printer_id = created['result']['id']
        config = self.app.settings['printers'][0]
        self.app.queue.printers[printer_id]['bed_clear'] = True
        pins = CertificatePins(self.path / 'certificates', printer_id, strict=True,
                               identity=config)
        pins._atomic_write(pins._path('mqtt'), 'a' * 64)
        pins._write_pending('mqtt', 'a' * 64, 'b' * 64)
        command = self.command('printer.certificate.confirm',
                               {'id': printer_id, 'channel': 'mqtt', 'fingerprint': 'b' * 64},
                               expected=config['version'], request_id='8' * 32)
        with patch.object(self.bridge, '_persist', side_effect=OSError('simulated journal failure')):
            with self.assertRaises(OSError):
                self.bridge.execute(ACTOR, command)
        self.assertEqual(self.app.queue.printers[printer_id]['bed_clear'], True)
        self.assertEqual(pins._read_pin('mqtt'), 'a' * 64)
        self.assertNotIn('_certificate_operations', self.app.settings)

    def test_isolated_nonworkflow_job_blocks_certificate_change(self):
        created = self.create('9' * 32)
        printer_id = created['result']['id']
        config = self.app.settings['printers'][0]
        self.app.queue.db.execute('''INSERT INTO jobs
            (id, request_id, digest, filename, meta, created, updated, status, printer_id, isolated)
            VALUES (?,?,?,?,?,?,?,?,?,1)''',
            ('job-' + '1' * 8, 'request-' + '1' * 8, 'd' * 64, 'job.gcode', '{}',
             1, 1, 'cancelled', printer_id))
        self.app.queue.db.commit()
        command = self.command('printer.certificate.confirm',
                               {'id': printer_id, 'channel': 'mqtt', 'fingerprint': 'b' * 64},
                               expected=config['version'], request_id='a' * 32)
        with self.assertRaises(ConfigConflict):
            self.bridge.execute(ACTOR, command)
        self.assertNotIn(ACTOR + ':' + 'a' * 32, self.app.settings.get('_certificate_operations', {}))

    def test_certificate_receipt_cannot_be_reused_for_changed_fingerprint(self):
        created = self.create('b' * 32)
        printer_id = created['result']['id']
        config = self.app.settings['printers'][0]
        pins = CertificatePins(self.path / 'certificates', printer_id, strict=True,
                               identity=config)
        pins._atomic_write(pins._path('mqtt'), 'a' * 64)
        pins._write_pending('mqtt', 'a' * 64, 'b' * 64)
        command = self.command('printer.certificate.confirm',
                               {'id': printer_id, 'channel': 'mqtt', 'fingerprint': 'b' * 64},
                               expected=config['version'], request_id='c' * 32)
        self.bridge.execute(ACTOR, command)
        changed = copy.deepcopy(command)
        changed['params']['fingerprint'] = 'c' * 64
        with self.assertRaises(ConfigConflict):
            self.bridge.execute(ACTOR, changed)

    def test_certificate_probe_observes_without_receipt_or_credentials(self):
        created = self.create('d' * 32)
        printer_id = created['result']['id']
        config = self.app.settings['printers'][0]
        observed = {'identity': {'host': config['host'], 'serial': config['serial'], 'version': 1},
                    'channels': {
                        'mqtt': {'status': 'confirmation_required', 'observed_fingerprint': 'a' * 64,
                                 'old_fingerprint': None, 'new_fingerprint': 'a' * 64},
                        'ftp': {'status': 'error', 'error': 'timeout'}}}
        with patch('app.printer_bridge.probe_printer_certificates', return_value=observed) as probe:
            command = self.command('printer.certificate.probe', {'id': printer_id},
                                   expected=config['version'], request_id='e' * 32)
            response = self.bridge.execute(ACTOR, command)
        probe.assert_called_once_with(config, self.path / 'certificates')
        self.assertEqual(response['result']['id'], printer_id)
        self.assertEqual(response['result']['version'], 1)
        self.assertNotIn(ACTOR + ':' + 'e' * 32, self.app.settings.get('_makerspace_receipts', {}))

    def test_paused_repair_changes_trust_without_mutating_active_job(self):
        created = self.create('f' * 32)
        printer_id = created['result']['id']
        config = self.app.settings['printers'][0]
        pins = CertificatePins(self.path / 'certificates', printer_id, strict=True,
                               identity=config)
        pins._atomic_write(pins._path('mqtt'), 'a' * 64)
        pins._write_pending('mqtt', 'a' * 64, 'b' * 64)
        self.app.queue.paused = True
        active = {'id': 'job', 'status': 'printing'}
        self.app.queue.active = lambda _printer_id: active
        command = self.command('printer.certificate.confirm',
                               {'id': printer_id, 'channel': 'mqtt', 'fingerprint': 'b' * 64,
                                'repair': True}, expected=config['version'], request_id='1' * 32)
        response = self.bridge.execute(ACTOR, command)
        self.assertEqual(response['result']['fingerprint'], 'b' * 64)
        self.assertEqual(active, {'id': 'job', 'status': 'printing'})
        self.assertFalse(self.app.queue.printers[printer_id]['bed_clear'])
        self.assertTrue(self.app.queue.paused)

    def test_legacy_safe_slug_id_is_accepted_for_certificate_confirmation(self):
        config = {**BASE, 'id': 'mini', 'version': 1}
        self.app.settings['printers'] = [config]
        self.app.queue.configs['mini'] = config
        self.app.queue.printers['mini'] = {'id': 'mini', 'name': 'Mini', 'state': 'OFFLINE',
            'seen': 0, 'state_seen': 0, 'tray_seen': 0, 'bed_clear': False, 'trays': [],
            'progress': 0, 'remaining': None, 'error': 0, 'task': '', 'enabled': False,
            'message': 'old'}
        self.app.drivers['mini'] = FakeDriver(config, self.app.queue, self.app.slicer.profiles)
        pins = CertificatePins(self.path / 'certificates', 'mini', strict=True, identity=config)
        pins._atomic_write(pins._path('mqtt'), 'a' * 64)
        pins._write_pending('mqtt', 'a' * 64, 'b' * 64)
        command = self.command('printer.certificate.confirm',
                               {'id': 'mini', 'channel': 'mqtt', 'fingerprint': 'b' * 64},
                               expected=1, request_id='2' * 32)
        response = self.bridge.execute(ACTOR, command)
        self.assertEqual(response['result']['id'], 'mini')

    def test_certificate_journal_recovers_after_new_bridge_instance(self):
        created = self.create('3' * 32)
        printer_id = created['result']['id']
        config = self.app.settings['printers'][0]
        pins = CertificatePins(self.path / 'certificates', printer_id, strict=True,
                               identity=config)
        pins._atomic_write(pins._path('mqtt'), 'a' * 64)
        pins._write_pending('mqtt', 'a' * 64, 'b' * 64)
        command = self.command('printer.certificate.confirm',
                               {'id': printer_id, 'channel': 'mqtt', 'fingerprint': 'b' * 64,
                                'repair': False},
                               expected=1, request_id='4' * 32)
        command['confirmed'] = True
        original_persist = self.bridge._persist
        calls = []

        def fail_final(settings):
            calls.append(True)
            if len(calls) == 2:
                raise OSError('final commit interrupted')
            return original_persist(settings)

        with patch.object(self.bridge, '_persist', side_effect=fail_final):
            with self.assertRaises(OSError):
                self.bridge.execute(ACTOR, command)
        self.app.queue.close()
        reopened = self.make_app()
        recovered = PrinterBridge(reopened, FakeDriver)
        key = ACTOR + ':' + '4' * 32
        self.assertIn(key, reopened.settings['_makerspace_receipts'])
        self.assertNotIn(key, reopened.settings.get('_certificate_operations', {}))
        self.assertFalse(reopened.queue.printers[printer_id]['bed_clear'])
        reopened.queue.close()

    def test_certificate_journal_can_be_cancelled_then_new_probe_and_confirm(self):
        created = self.create('5' * 32)
        printer_id = created['result']['id']
        config = self.app.settings['printers'][0]
        pins = CertificatePins(self.path / 'certificates', printer_id, strict=True,
                               identity=config)
        pins._atomic_write(pins._path('mqtt'), 'a' * 64)
        pins._write_pending('mqtt', 'a' * 64, 'b' * 64)
        original = self.command('printer.certificate.confirm',
                                {'id': printer_id, 'channel': 'mqtt', 'fingerprint': 'b' * 64},
                                expected=1, request_id='6' * 32)
        original_persist = self.bridge._persist
        calls = []

        def fail_final(settings):
            calls.append(True)
            if len(calls) == 2:
                raise OSError('simulated interrupted final commit')
            return original_persist(settings)

        with patch.object(self.bridge, '_persist', side_effect=fail_final):
            with self.assertRaises(OSError):
                self.bridge.execute(ACTOR, original)
        self.assertEqual(pins._read_pin('mqtt'), 'b' * 64)
        original_key = ACTOR + ':' + '6' * 32
        self.assertIn(original_key, self.app.settings['_certificate_operations'])

        cancel = self.command('printer.certificate.cancel',
                              {'id': printer_id, 'operation_request_id': '6' * 32,
                               'operation_actor': ACTOR}, expected=1, request_id='7' * 32)
        other_teacher = 'teacher:' + '2' * 32
        cancelled = self.bridge.execute(other_teacher, cancel)
        self.assertEqual(cancelled['result']['operation_request_id'], '6' * 32)
        self.assertTrue(cancelled['result']['cancelled'])
        self.assertTrue(cancelled['result']['trust_preserved'])
        self.assertNotIn(original_key, self.app.settings.get('_certificate_operations', {}))
        self.assertIn(original_key, self.app.settings['_certificate_cancelled'])
        with self.assertRaises(ConfigConflict):
            self.bridge.execute(ACTOR, original)
        self.assertEqual(pins._read_pin('mqtt'), 'b' * 64)

        pins._write_pending('mqtt', 'b' * 64, 'c' * 64)
        with patch('app.printer_bridge.probe_printer_certificates', return_value={
                'identity': {'host': config['host'], 'serial': config['serial'], 'version': 1},
                'channels': {'mqtt': {'status': 'confirmation_required',
                                      'observed_fingerprint': 'c' * 64,
                                      'old_fingerprint': 'b' * 64,
                                      'new_fingerprint': 'c' * 64},
                             'ftp': {'status': 'trusted', 'observed_fingerprint': 'd' * 64}}}):
            probe = self.command('printer.certificate.probe', {'id': printer_id},
                                 expected=1, request_id='8' * 32)
            self.assertEqual(self.bridge.execute(ACTOR, probe)['result']['channels']['mqtt']['new_fingerprint'],
                             'c' * 64)
        fresh = self.command('printer.certificate.confirm',
                             {'id': printer_id, 'channel': 'mqtt', 'fingerprint': 'c' * 64},
                             expected=1, request_id='9' * 32)
        self.assertEqual(self.bridge.execute(ACTOR, fresh)['result']['fingerprint'], 'c' * 64)
        self.assertEqual(pins._read_pin('mqtt'), 'c' * 64)


class PrinterBridgeHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        settings_path = self.path / "settings.json"
        queue = Queue(self.path, [])
        app = types.SimpleNamespace(
            folder=self.path, settings_path=settings_path, settings={"printers": []},
            settings_lock=threading.RLock(), queue=queue,
            slicer=types.SimpleNamespace(profiles=types.SimpleNamespace()),
            drivers={}, demo=False, auth=TeacherAuth("testteacher"),
        )
        app.printer_bridge = PrinterBridge(app, FakeDriver)
        self.app = app
        self.server = Server(("127.0.0.1", 0), Handler)
        self.server.app = app
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.queue.close()
        self.temp.cleanup()

    def call(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            payload = json.dumps(body).encode() if body is not None else None
            connection.request(method, path, payload, headers or {})
            response = connection.getresponse()
            raw = response.read()
            try:
                raw = json.loads(raw)
            except ValueError:
                pass
            return response.status, raw
        finally:
            connection.close()

    def command(self, request_id="a" * 32):
        return {"actor": ACTOR, "envelope": {
            "action": "printer.create", "params": dict(BASE),
            "expected_version": 0, "request_id": request_id,
        }}

    def valid_headers(self):
        return {"Authorization": "Bearer " + self.app.printer_bridge.token}

    def test_get_and_post_require_exact_bridge_key_and_return_contract(self):
        status, snapshot = self.call("GET", "/api/makerspace/printers",
                                     headers=self.valid_headers())
        self.assertEqual(status, 200)
        self.assertIn("printers", snapshot)
        status, result = self.call("POST", "/api/makerspace/printers/commands",
                                   self.command(), self.valid_headers())
        self.assertEqual(status, 200)
        self.assertNotIn("access_code", result["result"])
        self.assertEqual(result["result"]["version"], 1)

    def test_wrong_teacher_code_is_not_bridge_auth(self):
        status, _ = self.call("GET", "/api/settings",
                              headers={"Authorization": "Bearer wrongteacher"})
        self.assertEqual(status, 401)
        status, _ = self.call("GET", "/api/makerspace/printers",
                              headers={"Authorization": "Bearer testteacher"})
        self.assertEqual(status, 401)

    def test_non_ascii_authorization_is_rejected_without_handler_failure(self):
        status, _ = self.call('GET', '/api/makerspace/printers',
                              headers={'Authorization': 'Bearer æøå'})
        self.assertEqual(status, 401)

    def test_cookie_origin_and_print_source_are_rejected(self):
        for extra in ({"Cookie": "print_teacher=x"},
                      {"Origin": "http://127.0.0.1"},
                      {"X-Print-Source": "192.168.1.50"}):
            headers = self.valid_headers()
            headers.update(extra)
            with self.subTest(extra=extra):
                self.assertEqual(self.call("GET", "/api/makerspace/printers",
                                           headers=headers)[0], 401)

    def test_duplicate_authorization_headers_are_rejected(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.putrequest("GET", "/api/makerspace/printers")
            connection.putheader("Authorization", "Bearer " + self.app.printer_bridge.token)
            connection.putheader("Authorization", "Bearer " + self.app.printer_bridge.token)
            connection.endheaders()
            self.assertEqual(connection.getresponse().status, 401)
        finally:
            connection.close()

    def test_remote_peer_is_rejected_by_require_bridge(self):
        handler = object.__new__(Handler)
        handler.client_address = ("192.168.1.50", 12345)
        handler.command = "GET"
        handler.headers = email.message.Message()
        handler.headers["Authorization"] = "Bearer " + self.app.printer_bridge.token
        handler.server = self.server
        with self.assertRaises(PermissionError):
            handler.require_bridge()

    def test_command_wrapper_and_mutable_config_contract(self):
        headers = self.valid_headers()
        status, _ = self.call("POST", "/api/makerspace/printers/commands",
                              {"actor": ACTOR, "envelope": {"action": "printer.create",
                               "params": {**BASE, "id": "a" * 32},
                               "expected_version": 0, "request_id": "b" * 32}}, headers)
        self.assertEqual(status, 409)
        status, _ = self.call("POST", "/api/makerspace/printers/commands",
                              {"actor": ACTOR, "envelope": {"action": "printer.create",
                               "params": dict(BASE), "expected_version": 0,
                               "request_id": "c" * 32, "extra": True}}, headers)
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
