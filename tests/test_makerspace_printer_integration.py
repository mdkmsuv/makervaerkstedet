"""Integration coverage for the makerspace printer administration boundary.

The test deliberately uses the real makerspace store/service, loopback HTTP
handler and bridge.  The driver is a counter-only fake: no printer is touched.
"""

import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest

from app.core import Queue
from app.printer_bridge import PrinterBridge
from app.server import Handler, Server
from scripts.smoke_slice import cube

ELEVHUB = Path(__file__).parents[1] / "linux-elevhub" / "server" / "elevhub"
if str(ELEVHUB) not in sys.path:
    sys.path.insert(0, str(ELEVHUB))
from makerspace_api import MakerspaceService  # noqa: E402
from makerspace_printers import PrinterAdministration  # noqa: E402
from makerspace_store import Conflict  # noqa: E402
from makerspace_workflows import templates  # noqa: E402


TEACHER = "teacher:" + "9" * 32
SCOPES = ["makerspace:read", "machines:write", "workflows:write", "jobs:write", "audit:read"]
TEACHER_ACTOR = {"id": TEACHER, "name": "Lærer", "role": "teacher", "scopes": SCOPES}
BASE = {
    "name": "Integration printer", "location": "Lokale 1", "model": "P1S",
    "host": "192.168.1.40", "serial": "SERIAL1", "access_code": "secret",
    "enabled": False, "bed_type": "Textured PEI Plate",
}


class FakeDriver:
    instances = []

    def __init__(self, config, queue, profiles):
        self.config, self.queue, self.profiles = config, queue, profiles
        self.starts = self.stops = 0
        type(self).instances.append(self)

    def start(self):
        self.starts += 1

    def stop(self):
        self.stops += 1


def envelope(action, params, expected=0, request_id="a" * 32):
    return {"action": action, "params": params, "expected_version": expected, "request_id": request_id}


class MakerspacePrinterIntegrationTests(unittest.TestCase):
    def setUp(self):
        FakeDriver.instances = []
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        settings_path = self.root / "settings.json"
        self.app = types.SimpleNamespace(
            folder=self.root, settings_path=settings_path, settings={"printers": []},
            settings_lock=threading.RLock(), queue=Queue(self.root, []),
            slicer=types.SimpleNamespace(profiles=types.SimpleNamespace()), drivers={}, demo=False,
        )
        self.app.printer_bridge = PrinterBridge(self.app, FakeDriver)
        self.http = Server(("127.0.0.1", 0), Handler)
        self.http.app = self.app
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        port = self.http.server_address[1]
        admin = PrinterAdministration({"backend": f"127.0.0.1:{port}", "key_file": "makerspace-bridge.key"}, base=self.root)
        self.service = MakerspaceService(
            self.root / "makerspace.sqlite3",
            overview=lambda: {"printers": {"status": "ok", "checked_at": 1000, "items": []},
                              "computers": [], "computers_status": "ok"},
            printer_administration=admin,
        )
        self.teacher_actor = TEACHER_ACTOR

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=3)
        self.service.store  # keep the object alive until server shutdown
        self.app.queue.close()
        self.temp.cleanup()

    def create(self, request_id="a" * 32):
        return self.service.store.execute(self.teacher_actor, envelope("printer.create", dict(BASE), request_id=request_id),
                                          external=self.service.printers)

    def command(self, actor, action, params, expected, request_id):
        return self.service.store.execute(actor, envelope(action, params, expected, request_id),
                                          external=self.service.printers)

    def setup_workflow_printer(self):
        definition = templates()["3d_print"]
        workflow = self.command(self.teacher_actor, "workflow.create", {"definition": definition}, 0, "1" * 32)
        workflow_id = workflow["result"]["id"]
        published = self.command(self.teacher_actor, "workflow.publish", {"id": workflow_id}, 1, "2" * 32)
        created = self.create("3" * 32)
        printer_id = created["result"]["id"]
        enabled = self.command(self.teacher_actor, "printer.update", {**BASE, "id": printer_id,
            "enabled": True, "access_code": ""}, 1, "4" * 32)
        assigned = self.command(self.teacher_actor, "printer.workflow.assign", {
            "id": printer_id, "workflow_id": workflow_id,
            "revision": published["result"]["active_revision"]}, enabled["result"]["version"], "5" * 32)
        self.assertEqual(assigned["result"]["workflow"]["revision"], 1)
        return printer_id, workflow_id, assigned["result"]["version"]

    def ready_printer(self, printer_id):
        self.app.queue.update_report(printer_id, state="IDLE", task="", error=0, trays=[{
            "slot": 254, "extruder": 0, "colour": "#3678DC", "material": "PLA",
            "profile": "Bambu PLA Basic", "remaining": 80}])
        self.app.queue.release_bed(printer_id)

    def job_advance(self, actor, job, step_id, expected, request_id):
        return self.command(actor, "printer.job.advance", {
            "id": job["id"], "step_id": step_id, "confirmed": True}, expected, request_id)

    def test_published_workflow_runs_through_teacher_steps(self):
        printer_id, workflow_id, printer_version = self.setup_workflow_printer()
        self.ready_printer(printer_id)
        job = self.app.queue.submit(cube(20), "workflow-cube.stl", "any", "workflow-cube-request-01")
        self.assertIsNone(self.app.queue.reserve())
        current = self.app.queue.get_job(job["id"])
        self.assertEqual((current["status"], current["workflow_step"], current["workflow_version"]),
                         ("workflow_wait", 1, 1))
        pinned = self.app.queue.workflow_jobs()[0]
        self.assertEqual((pinned["workflow_id"], pinned["workflow_revision"], pinned["workflow_name"]),
                         (workflow_id, 1, "3D-printforløb"))
        self.assertEqual((pinned["step"]["id"], pinned["status"], pinned["production_authorized"]),
                         ("prepare", "workflow_wait", False))
        snapshot = self.app.printer_bridge.snapshot()
        self.assertEqual(snapshot["workflow_jobs"][0]["id"], job["id"])
        # The settings receipt and queue receipt share the hub request namespace.
        with self.assertRaises(Conflict):
            self.job_advance(self.teacher_actor, job, "prepare", 1, "5" * 32)
        with self.assertRaises(PermissionError):
            self.job_advance({"id": "agent:" + "b" * 32, "role": "agent", "scopes": SCOPES}, job, "prepare", 1, "6" * 32)
        self.job_advance(TEACHER_ACTOR, job, "prepare", 1, "7" * 32)
        self.job_advance(TEACHER_ACTOR, job, "approve", 2, "8" * 32)
        self.job_advance(TEACHER_ACTOR, job, "produce", 3, "9" * 32)
        self.assertEqual(self.app.queue.get_job(job["id"])["workflow_authorized"], 1)
        prepared = self.app.queue.reserve()
        self.assertEqual(prepared["status"], "preparing")
        self.assertTrue(self.app.queue.begin_dispatch(job["id"]))
        self.app.queue.dispatched(job["id"])
        self.app.queue.update_report(printer_id, state="RUNNING", task=prepared["remote_name"], error=0)
        self.app.queue.update_report(printer_id, state="FINISH", task=prepared["remote_name"], error=0)
        self.assertEqual(self.app.queue.get_job(job["id"])["status"], "done")
        settings_before = json.dumps(self.app.settings, sort_keys=True)
        drivers_before = len(FakeDriver.instances)
        with self.assertRaises(Conflict):
            self.command(self.teacher_actor, "printer.update", {**BASE, "id": printer_id,
                "name": "Må ikke ændres endnu", "enabled": True, "access_code": ""},
                3, "0" * 32)
        self.assertEqual(json.dumps(self.app.settings, sort_keys=True), settings_before)
        self.assertEqual(len(FakeDriver.instances), drivers_before)
        self.job_advance(self.teacher_actor, job, "collect", 4, "a" * 32)
        self.job_advance(TEACHER_ACTOR, job, "clear", 5, "b" * 32)
        final = self.app.queue.get_job(job["id"])
        self.assertEqual((final["workflow_complete"], final["workflow_step"]), (1, 6))
        self.assertTrue(self.app.queue.printers[printer_id]["bed_clear"])

    def test_advance_lost_response_retries_queue_receipt_without_extra_audit(self):
        printer_id, workflow_id, printer_version = self.setup_workflow_printer()
        self.ready_printer(printer_id)
        job = self.app.queue.submit(cube(20), "workflow-cube.stl", "any", "workflow-cube-request-02")
        self.app.queue.reserve()
        request_id = "c" * 32
        real = self.service.printers
        class LostResponse:
            calls = 0
            def fingerprint(self, value): return real.fingerprint(value)
            def execute(self, actor, value):
                result = real.execute(actor, value)
                self.calls += 1
                if self.calls == 1: raise OSError("simulated lost response")
                return result
        self.service.printers = LostResponse()
        with self.assertRaises(OSError):
            self.job_advance(TEACHER_ACTOR, job, "prepare", 1, request_id)
        queue_audit = lambda: self.app.queue.db.execute(
            "SELECT count(*) FROM workflow_audit WHERE request_id=?", (request_id,)).fetchone()[0]
        self.assertEqual(queue_audit(), 1)
        after_lost = len([row for row in self.service.store.audit() if row["request_id"] == request_id])
        response = self.job_advance(TEACHER_ACTOR, job, "prepare", 1, request_id)
        after_retry = len([row for row in self.service.store.audit() if row["request_id"] == request_id])
        self.assertEqual(response["result"]["version"], 2)
        self.assertEqual((after_lost, after_retry), (0, 1))
        self.assertEqual(queue_audit(), 1)
        with self.assertRaises(Conflict):
            self.command(TEACHER_ACTOR, "printer.workflow.assign", {
                "id": printer_id, "workflow_id": workflow_id, "revision": 1}, printer_version, request_id)
        after_conflict = len([row for row in self.service.store.audit() if row["request_id"] == request_id])
        self.assertEqual(after_conflict, 2)

    def test_new_printer_workflow_revision_does_not_change_pinned_job(self):
        printer_id, workflow_id, printer_version = self.setup_workflow_printer()
        self.ready_printer(printer_id)
        job = self.app.queue.submit(cube(20), "workflow-cube.stl", "any", "workflow-cube-request-03")
        self.app.queue.reserve()
        edited = dict(templates()["3d_print"], name="Ny printrevision")
        self.command(self.teacher_actor, "workflow.edit", {"id": workflow_id, "definition": edited}, 2, "d" * 32)
        published = self.command(self.teacher_actor, "workflow.publish", {"id": workflow_id}, 3, "e" * 32)
        self.command(self.teacher_actor, "printer.workflow.assign", {"id": printer_id, "workflow_id": workflow_id,
            "revision": published["result"]["active_revision"]}, printer_version, "f" * 32)
        pinned = self.app.queue.workflow_jobs()[0]
        self.assertEqual((pinned["workflow_id"], pinned["workflow_revision"]), (workflow_id, 1))

    def test_legacy_agent_actor_is_rejected_by_teacher_service(self):
        with self.assertRaises(PermissionError):
            self.service.store.execute({"id": "agent:" + "b" * 32, "role": "agent", "scopes": SCOPES},
                                       envelope("printer.create", dict(BASE), request_id="b" * 32),
                                       external=self.service.printers)
        self.assertEqual(FakeDriver.instances, [])

    def test_create_and_retry_same_request_is_exact_and_constructs_once(self):
        first = self.create()
        second = self.create()
        self.assertEqual(second, first)
        self.assertEqual(len(FakeDriver.instances), 1)
        self.assertNotIn("access_code", json.dumps(first))
        self.assertEqual(len(self.app.settings["printers"]), 1)

    def test_lost_response_retry_recovers_backend_receipt_without_second_driver(self):
        real = self.service.printers

        class LostResponse:
            def __init__(self):
                self.calls = 0
            def fingerprint(self, value):
                return real.fingerprint(value)
            def execute(self, actor, value):
                result = real.execute(actor, value)
                self.calls += 1
                if self.calls == 1:
                    raise OSError("simulated lost response")
                return result

        self.service.printers = LostResponse()
        request = envelope("printer.create", dict(BASE), request_id="d" * 32)
        with self.assertRaises(OSError):
            self.service.store.execute(self.teacher_actor, request, external=self.service.printers)
        response = self.service.store.execute(self.teacher_actor, request, external=self.service.printers)
        self.assertTrue(response["ok"])
        self.assertEqual(len(FakeDriver.instances), 1)
        self.assertEqual(len(self.app.settings["printers"]), 1)

    def test_version_conflict_after_legacy_edit_does_not_overwrite(self):
        created = self.create()
        printer_id = created["result"]["id"]
        legacy = dict(BASE, id=printer_id, name="Legacy edit", access_code="")
        self.app.printer_bridge.save_legacy(legacy)
        update = dict(BASE, id=printer_id, name="Hub edit", access_code="")
        with self.assertRaises(Conflict):
            self.service.store.execute(self.teacher_actor, envelope("printer.update", update, expected=1,
                                                            request_id="e" * 32), external=self.service.printers)
        self.assertEqual(self.app.settings["printers"][0]["name"], "Legacy edit")
        self.assertEqual(self.app.settings["printers"][0]["version"], 2)
        conflict_audit = [row for row in self.service.store.audit() if row.get("request_id") == "e" * 32]
        self.assertEqual(len(conflict_audit), 1)
        self.assertEqual(conflict_audit[0]["outcome"], "conflict")

    def test_zero_pupils_and_secret_never_cross_public_or_hub_boundaries(self):
        self.create()
        state = self.service.snapshot(self.teacher_actor)
        encoded = json.dumps(state, ensure_ascii=False)
        self.assertEqual(state["computers"], [])
        self.assertNotIn("access_code", encoded)
        self.assertNotIn("secret", encoded)
        self.assertNotIn(b"secret", (self.root / "makerspace.sqlite3").read_bytes())
        self.assertNotIn("secret", json.dumps(self.service.store.audit()))

    def test_unified_inventory_keeps_authoritative_ids_without_manual_duplicates(self):
        created = self.create()['result']
        manual = self.service.store.execute(self.teacher_actor, envelope('machine.create', {
            'name': 'Prototypebord', 'location': 'Rum 2', 'enabled': False, 'workflow_id': None},
            request_id='f' * 32))['result']
        self.service.overview = lambda: {'computers': [], 'printers': {'status': 'ok',
            'checked_at': 1000, 'items': [{**created, 'online': False, 'status': 'disabled'}]}}
        first = self.service.snapshot(self.teacher_actor)
        second = self.service.snapshot(self.teacher_actor)
        self.assertEqual(first['inventory'], second['inventory'])
        self.assertEqual({row['id'] for row in first['inventory']}, {
            'bambu:' + created['id'], 'manual:' + manual['id']})
        self.assertEqual(len(first['inventory']), 2)
        self.assertEqual([row['id'] for row in first['machines']], [manual['id']])


if __name__ == "__main__":
    unittest.main()
