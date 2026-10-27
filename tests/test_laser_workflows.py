import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app.laser_server import Inbox
from app.laser_workflows import LaserWorkflows
from app.printer_config import ConfigConflict
from tests.test_laser_inbox import image_svg


TEACHER = "teacher:" + "1" * 32
AGENT = "agent:" + "2" * 32
SVG = '<svg xmlns="http://www.w3.org/2000/svg" data-laservaerksted="1" width="20mm" height="20mm" viewBox="0 0 20 20"><g data-operation="cut"><path data-operation="cut" d="M1 1H19V19H1Z"/></g></svg>'


def binding():
    steps = [("submit", "operator"), ("prepare", "teacher"), ("approve", "teacher"),
             ("produce", "teacher"), ("collect", "operator"), ("clear", "teacher")]
    return {"id": "a" * 32, "revision": 1, "definition": {
        "name": "Laserforløb", "kind": "laser", "steps": [
            {"id": i, "label": i, "role": role, "action": i} for i, role in steps]}}


class LaserWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.inbox = Inbox(self.temp.name, code="test-code")
        self.workflows = LaserWorkflows(self.inbox)
        self.workflows.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def env(self, action, params, expected, request):
        return {"action": action, "params": params, "expected_version": expected, "request_id": request}

    def execute(self, action, params, expected, request, actor=TEACHER):
        return self.workflows.execute(actor, self.env(action, params, expected, request))

    def setup_bound_job(self):
        self.execute("laser.station.create", {"id": "laser-a", "name": "Laser A", "location": "Rum 1",
            "model": "fbb2", "enabled": False}, 0, "1" * 32)
        self.execute("laser.station.update", {"id": "laser-a", "name": "Laser A", "location": "Rum 1",
            "model": "fbb2", "enabled": True}, 1, "2" * 32)
        self.execute("laser.workflow.assign", {"id": "laser-a", "workflow": binding()}, 2, "3" * 32)
        job = self.inbox.submit({"elevnavn": "Elev", "klasse": "3.A", "projekt": "Skilt",
                                 "requestId": "laser-job-request-0001", "svg": SVG})
        self.execute("laser.job.bind", {"id": job["id"], "station_id": "laser-a"}, 0, "4" * 32)
        return job["id"]

    def setup_bound_image_job(self):
        self.execute("laser.station.create", {"id": "laser-a", "name": "Laser A", "location": "Rum 1",
            "model": "fbb2", "enabled": False}, 0, "1" * 32)
        self.execute("laser.station.update", {"id": "laser-a", "name": "Laser A", "location": "Rum 1",
            "model": "fbb2", "enabled": True}, 1, "2" * 32)
        self.execute("laser.workflow.assign", {"id": "laser-a", "workflow": binding()}, 2, "3" * 32)
        job = self.inbox.submit({"elevnavn": "Elev", "klasse": "3.A", "projekt": "Billede",
                                 "requestId": "laser-image-request-0001", "svg": image_svg()})
        self.execute("laser.job.bind", {"id": job["id"], "station_id": "laser-a"}, 0, "4" * 32)
        return job["id"]

    def test_binding_roles_steps_finish_clear_and_payload_cleanup(self):
        job_id = self.setup_bound_job()
        with self.assertRaises(PermissionError):
            self.execute("laser.job.advance", {"id": job_id, "step_id": "prepare", "confirmed": True}, 1, "5" * 32, AGENT)
        self.execute("laser.job.advance", {"id": job_id, "step_id": "prepare", "confirmed": True}, 1, "6" * 32)
        self.execute("laser.job.advance", {"id": job_id, "step_id": "approve", "confirmed": True}, 2, "7" * 32)
        self.execute("laser.job.advance", {"id": job_id, "step_id": "produce", "confirmed": True}, 3, "8" * 32)
        self.workflows.check_export("laser-a", "fbb2", [job_id])
        # The binding is pinned on the job; removing the station's current
        # assignment must not invalidate this already authorized handoff.
        self.execute("laser.workflow.assign", {"id": "laser-a", "workflow": None}, 3, "8a" * 16)
        self.execute("laser.job.finish", {"id": job_id, "confirmed": True}, 4, "9" * 32)
        self.execute("laser.job.advance", {"id": job_id, "step_id": "collect", "confirmed": True}, 5, "a" * 32, TEACHER)
        self.execute("laser.job.advance", {"id": job_id, "step_id": "clear", "confirmed": True}, 6, "b" * 32)
        row = self.inbox.jobs()[0]
        with self.inbox.db() as db:
            finished_at = db.execute("SELECT finished_at FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
        self.assertEqual(row["status"], "done")
        self.assertFalse(row["payload_available"])
        self.assertTrue(finished_at)
        self.assertEqual(self.workflows.snapshot()["jobs"], [])

    def test_version_retries_rollback_and_double_claim_guard(self):
        job_id = self.setup_bound_job()
        command = self.env("laser.job.advance", {"id": job_id, "step_id": "prepare", "confirmed": True}, 1, "c" * 32)
        first = self.workflows.execute(TEACHER, command)
        self.assertEqual(self.workflows.execute(TEACHER, command), first)
        with self.assertRaises(ConfigConflict):
            self.workflows.execute(TEACHER, {**command, "params": {"id": job_id, "step_id": "approve", "confirmed": True}})
        with self.assertRaises(ConfigConflict):
            self.execute("laser.job.advance", {"id": job_id, "step_id": "approve", "confirmed": True}, 1, "d" * 32)
        self.execute("laser.job.advance", {"id": job_id, "step_id": "approve", "confirmed": True}, 2, "d1" * 16)
        self.execute("laser.job.advance", {"id": job_id, "step_id": "produce", "confirmed": True}, 3, "d2" * 16)
        self.workflows.check_export("laser-a", "fbb2", [job_id])
        self.execute("laser.job.finish", {"id": job_id, "confirmed": True}, 4, "d3" * 16)
        # A different open job cannot claim the already claimed station batch.
        second = self.inbox.submit({"elevnavn": "Elev", "klasse": "3.A", "projekt": "Skilt2",
                                    "requestId": "laser-job-request-0002", "svg": SVG})
        self.execute("laser.job.bind", {"id": second["id"], "station_id": "laser-a"}, 0, "e" * 32)
        for version, step in enumerate(('prepare', 'approve', 'produce'), 1):
            self.execute('laser.job.advance', {'id': second['id'], 'step_id': step, 'confirmed': True},
                         version, f'{100 + version:032x}')
        with self.assertRaises(ValueError):
            self.workflows.check_export("laser-a", "fbb2", [second["id"]])
        current = {item["id"]: item for item in self.workflows.snapshot()["jobs"]}
        self.assertEqual(current[job_id]["version"], 5)
        # A future unassignment must not change either pinned job.
        self.execute('laser.workflow.assign', {'id': 'laser-a', 'workflow': None}, 3, 'ef' * 16)
        self.execute('laser.job.advance', {'id': job_id, 'step_id': 'collect', 'confirmed': True}, 5, 'ed' * 16)
        with self.assertRaises(ValueError):
            self.workflows.check_export('laser-a', 'fbb2', [second['id']])
        self.execute('laser.job.advance', {'id': job_id, 'step_id': 'clear', 'confirmed': True}, 6, 'ec' * 16)
        self.assertTrue(self.workflows.check_export('laser-a', 'fbb2', [second['id']])['managed'])

    def test_model_and_station_validation_and_restart_persistence(self):
        job_id = self.setup_bound_job()
        with self.assertRaises(ValueError):
            self.workflows.check_export("laser-a", "fhexa1", [job_id])
        reopened = LaserWorkflows(Inbox(self.temp.name, code="test-code"))
        reopened.initialize()
        self.assertEqual(reopened.station("laser-a")["workflow"]["name"], "Laserforløb")
        self.assertEqual(reopened.snapshot()["jobs"][0]["station_id"], "laser-a")

    def test_legacy_done_is_atomic_idempotent_and_keeps_payload_until_clear(self):
        job_id = self.setup_bound_job()
        self.execute("laser.job.advance", {"id": job_id, "step_id": "prepare", "confirmed": True}, 1, "f" * 32)
        self.execute("laser.job.advance", {"id": job_id, "step_id": "approve", "confirmed": True}, 2, "f1" * 16)
        self.execute("laser.job.advance", {"id": job_id, "step_id": "produce", "confirmed": True}, 3, "f2" * 16)
        self.workflows.check_export("laser-a", "fbb2", [job_id])
        self.assertTrue(self.workflows.legacy_status(job_id, "done", TEACHER))
        self.assertTrue(self.workflows.legacy_status(job_id, "done", TEACHER))
        with self.inbox.db() as db:
            physical, payload, audits = db.execute(
                "SELECT physical_done,payload_available,(SELECT count(*) FROM laser_workflow_audit WHERE target=? AND action='laser.job.finish') FROM jobs WHERE id=?",
                (job_id, job_id)).fetchone()
        self.assertEqual((physical, payload, audits), (1, 1, 1))

    def test_input_limits_and_unhashable_action_are_controlled_errors(self):
        with self.assertRaises(ValueError):
            self.workflows.execute(TEACHER, {"action": [], "params": {}, "expected_version": 0, "request_id": "a" * 32})
        with self.assertRaises(ValueError):
            self.workflows.check_export("bad id", "fbb2", [])

    def test_inbox_legacy_status_preserves_unbound_ready_and_blocks_bound_ready(self):
        job = self.inbox.submit({"elevnavn": "Elev", "klasse": "3.A", "projekt": "Fri",
                                 "requestId": "legacy-ready-request-0001", "svg": SVG})["id"]
        self.inbox.status(job, "ready")
        self.assertEqual(self.inbox.jobs()[0]["status"], "ready")
        bound = self.setup_bound_job()
        with self.assertRaises(ValueError):
            self.inbox.status(bound, "ready")
        with self.inbox.db() as db:
            self.assertEqual(db.execute("SELECT status FROM jobs WHERE id=?", (bound,)).fetchone()[0], "received")

    def test_bound_done_is_idempotent_through_inbox_status_and_export_rejects_completed(self):
        job = self.setup_bound_job()
        for step, expected, request in (("prepare", 1, "c" * 32), ("approve", 2, "c1" * 16), ("produce", 3, "c2" * 16)):
            self.execute("laser.job.advance", {"id": job, "step_id": step, "confirmed": True}, expected, request)
        self.workflows.check_export("laser-a", "fbb2", [job])
        self.inbox.status(job, "done", TEACHER)
        self.inbox.status(job, "done", TEACHER)
        with self.assertRaises(ValueError):
            self.workflows.check_export("laser-a", "fbb2", [job])
        with self.inbox.db() as db:
            physical, payload = db.execute("SELECT physical_done,payload_available FROM jobs WHERE id=?", (job,)).fetchone()
        self.assertEqual((physical, payload), (1, 1))

    def test_image_mode_remains_editable_until_approve(self):
        job = self.setup_bound_image_job()
        self.inbox.image_mode(job, "test-image-1", "vector")
        self.execute("laser.job.advance", {"id": job, "step_id": "prepare", "confirmed": True}, 1, "d" * 32)
        self.inbox.image_mode(job, "test-image-1", "raster")
        self.execute("laser.job.advance", {"id": job, "step_id": "approve", "confirmed": True}, 2, "d1" * 16)
        with self.assertRaises(ValueError):
            self.inbox.image_mode(job, "test-image-1", "vector")

    def test_approve_requires_image_choices_without_publishing_workflow_state(self):
        job = self.setup_bound_image_job()
        self.execute("laser.job.advance", {"id": job, "step_id": "prepare", "confirmed": True}, 1, "da" * 16)
        approval = self.env("laser.job.advance", {"id": job, "step_id": "approve", "confirmed": True}, 2, "db" * 16)
        with self.assertRaisesRegex(ValueError, "Vælg raster- eller vektorgravering"):
            self.workflows.execute(TEACHER, approval)
        current = self.workflows.snapshot()["jobs"][0]
        self.assertEqual((current["step"]["id"], current["version"], current["production_authorized"]), ("approve", 2, False))
        with self.inbox.db() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM laser_workflow_commands WHERE request_id=?", ("db" * 16,)).fetchone())
            self.assertIsNone(db.execute("SELECT 1 FROM laser_workflow_audit WHERE request_id=?", ("db" * 16,)).fetchone())

        self.inbox.image_mode(job, "test-image-1", "raster")
        approved = self.workflows.execute(TEACHER, approval)
        self.assertEqual(self.workflows.execute(TEACHER, approval), approved)
        self.execute("laser.job.advance", {"id": job, "step_id": "produce", "confirmed": True}, 3, "dc" * 16)
        self.assertTrue(self.workflows.check_export("laser-a", "fbb2", [job])["managed"])
        self.execute("laser.job.finish", {"id": job, "confirmed": True}, 4, "dd" * 16)
        self.execute("laser.job.advance", {"id": job, "step_id": "collect", "confirmed": True}, 5, "de" * 16, TEACHER)
        self.execute("laser.job.advance", {"id": job, "step_id": "clear", "confirmed": True}, 6, "df" * 16)
        self.assertEqual(self.inbox.jobs()[0]["status"], "done")

    def test_clear_commit_survives_cleanup_failure_and_retry_repeats_cleanup(self):
        job = self.setup_bound_job()
        for step, expected, request in (("prepare", 1, "e" * 32), ("approve", 2, "e1" * 16), ("produce", 3, "e2" * 16)):
            self.execute("laser.job.advance", {"id": job, "step_id": step, "confirmed": True}, expected, request)
        self.workflows.check_export("laser-a", "fbb2", [job])
        self.execute("laser.job.finish", {"id": job, "confirmed": True}, 4, "e3" * 16)
        self.execute("laser.job.advance", {"id": job, "step_id": "collect", "confirmed": True}, 5, "e4" * 16, TEACHER)
        clear = self.env("laser.job.advance", {"id": job, "step_id": "clear", "confirmed": True}, 6, "e5" * 16)
        from app.laser_inbox_lifecycle import cleanup_terminal_payload as real_cleanup
        calls = [0]
        def flaky_cleanup(db, job_id):
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError("simulated cleanup")
            return real_cleanup(db, job_id)
        with mock.patch("app.laser_workflows.cleanup_terminal_payload", side_effect=flaky_cleanup) as cleanup:
            with self.assertRaises(RuntimeError):
                self.workflows.execute(TEACHER, clear)
            self.assertEqual(self.workflows.execute(TEACHER, clear)["result"]["status"], "done")
            self.assertEqual(cleanup.call_count, 2)
        with self.inbox.db() as db:
            self.assertEqual(db.execute("SELECT status,payload_available FROM jobs WHERE id=?", (job,)).fetchone(), ("done", 0))


if __name__ == "__main__":
    unittest.main()
