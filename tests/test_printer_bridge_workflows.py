import json
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from app.core import Queue
from app.printer_bridge import PrinterBridge
from app.printer_config import ConfigConflict


BASE = {"name": "Printer", "location": "", "model": "P1S", "host": "192.168.1.40",
        "serial": "SERIAL1", "access_code": "secret", "enabled": False,
        "bed_type": "Textured PEI Plate"}
ACTOR = "teacher:" + "1" * 32


def workflow():
    steps = [("submit", "operator"), ("approve", "teacher"), ("produce", "teacher"),
             ("collect", "operator"), ("clear", "teacher")]
    return {"id": "a" * 32, "revision": 1, "definition": {
        "name": "Print", "kind": "3d_print", "steps": [
            {"id": action, "label": action, "role": role, "action": action}
            for action, role in steps]}}


class Driver:
    instances = []
    def __init__(self, *args): type(self).instances.append(self)
    def start(self): pass
    def stop(self): pass


class PrinterWorkflowBridgeTests(unittest.TestCase):
    def setUp(self):
        Driver.instances = []
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.queue = Queue(root, [])
        self.app = types.SimpleNamespace(folder=root, settings_path=root / "settings.json",
            settings={"printers": []}, settings_lock=threading.RLock(), queue=self.queue,
            slicer=types.SimpleNamespace(profiles=types.SimpleNamespace()), drivers={}, demo=False)
        self.bridge = PrinterBridge(self.app, Driver)

    def tearDown(self):
        self.queue.close()
        self.temp.cleanup()

    @staticmethod
    def envelope(action, params, expected, request_id):
        return {"action": action, "params": params, "expected_version": expected,
                "request_id": request_id}

    def test_assign_increments_version_allows_active_job_and_retries_without_driver(self):
        created = self.bridge.execute(ACTOR, self.envelope("printer.create", BASE, 0, "a" * 32))
        printer_id = created["result"]["id"]
        self.queue.active = lambda _id: {"id": "active"}
        command = self.envelope("printer.workflow.assign", {"id": printer_id, "workflow": workflow()}, 1, "b" * 32)
        assigned = self.bridge.execute(ACTOR, command)
        self.assertEqual(assigned["result"]["version"], 2)
        self.assertEqual(assigned["result"]["workflow"]["id"], "a" * 32)
        self.assertEqual(len(Driver.instances), 1)
        self.assertEqual(self.bridge.execute(ACTOR, command), assigned)
        with self.assertRaises(ConfigConflict):
            self.bridge.execute(ACTOR, self.envelope("printer.workflow.assign",
                {"id": printer_id, "workflow": None}, 1, "c" * 32))
        persisted = json.loads((self.app.settings_path).read_text())
        self.assertEqual(persisted["printers"][0]["version"], 2)

    def test_job_advance_delegates_without_settings_receipt(self):
        called = []
        self.queue.advance_workflow = lambda actor, envelope: called.append((actor, envelope)) or {"ok": True, "status": "approved"}
        command = self.envelope("printer.job.advance", {"id": "b" * 32, "step_id": "approve", "confirmed": True}, 1, "d" * 32)
        result = self.bridge.execute(ACTOR, command)
        self.assertEqual(result, {"ok": True, "status": "approved"})
        self.assertEqual(called, [(ACTOR, command)])
        self.assertNotIn("_makerspace_receipts", self.app.settings)

    def test_assignment_storage_failure_does_not_change_live_binding_or_receipt(self):
        created = self.bridge.execute(ACTOR, self.envelope('printer.create', BASE, 0, 'a' * 32))
        command = self.envelope('printer.workflow.assign',
            {'id': created['result']['id'], 'workflow': workflow()}, 1, 'b' * 32)
        before = self.app.settings_path.read_bytes()
        with patch.object(self.bridge, '_persist', side_effect=OSError('simulated disk failure')):
            with self.assertRaises(OSError):
                self.bridge.execute(ACTOR, command)
        self.assertEqual(self.app.settings_path.read_bytes(), before)
        self.assertNotIn('workflow', self.queue.configs[created['result']['id']])
        self.assertNotIn(ACTOR + ':' + 'b' * 32, self.app.settings['_makerspace_receipts'])
        self.assertEqual(len(Driver.instances), 1)
        self.assertEqual(self.bridge.execute(ACTOR, command)['result']['version'], 2)


if __name__ == "__main__":
    unittest.main()
