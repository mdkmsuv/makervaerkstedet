"""End-to-end, hardware-free checks for the managed laser makerspace path.

The fixture uses the real teacher HTTP handler and the real Inbox workflow
database.  The station only writes a local BVG; no FLUX process or device is
started.
"""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
import sys

from app.laser_server import Inbox, create_server
from app.laser_station import QueueClient, Station

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "linux-elevhub" / "server"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "linux-elevhub" / "server" / "elevhub"))
from makerspace_api import MakerspaceService  # noqa: E402
from makerspace_laser_admin import LaserAdministration  # noqa: E402
from makerspace_store import Conflict  # noqa: E402


TEACHER = "teacher:" + "1" * 32
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" data-laservaerksted="1" '
       'width="20mm" height="20mm" viewBox="0 0 20 20"><g data-operation="cut">'
       '<path data-operation="cut" d="M1 1H19V19H1Z"/></g></svg>')


def binding():
    steps = [("submit", "operator"), ("prepare", "teacher"),
             ("approve", "teacher"), ("produce", "teacher"),
             ("collect", "operator"), ("clear", "teacher")]
    return {"id": "a" * 32, "revision": 1, "definition": {
        "name": "Laserforløb", "kind": "laser",
        "steps": [{"id": a, "label": a, "role": r, "action": a}
                   for a, r in steps]}}


class LaserMakerspaceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.inbox = Inbox(self.temp.name, code="integration-code")
        self.teacher = create_server(self.temp.name, port=0, code="integration-code",
                                     surface="teacher", inbox=self.inbox)
        self.teacher.RequestHandlerClass.log_message = lambda *args: None
        self.thread = threading.Thread(target=self.teacher.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.teacher.server_port}"
        self.service = MakerspaceService(
            str(Path(self.temp.name) / "hub.sqlite3"),
            laser_administration=LaserAdministration(
                {"backend": f"127.0.0.1:{self.teacher.server_port}",
                 "key_file": "makerspace-laser.key"}, base=self.temp.name))
        self.teacher_actor = TEACHER

    def tearDown(self):
        self.teacher.shutdown()
        self.teacher.server_close()
        self.temp.cleanup()

    def command(self, actor, action, params, expected, request=None):
        return self.service.execute({"id": actor, "role": "teacher",
                                     "scopes": ["makerspace:read", "machines:write", "workflows:write", "jobs:write", "audit:read"]}, {
            "action": action, "params": params, "expected_version": expected,
            "request_id": request or uuid.uuid4().hex})

    def login(self):
        q = QueueClient(self.base)
        q.login("integration-code")
        return q

    def setup_job(self):
        definition = binding()["definition"]
        created = self.command(TEACHER, "workflow.create", {"definition": definition}, 0, "f" * 32)
        workflow_id = created["result"]["id"]
        published = self.command(TEACHER, "workflow.publish", {"id": workflow_id}, 1, "e" * 32)
        revision = published["result"]["active_revision"]
        self.command(TEACHER, "laser.station.create", {
            "id": "laser-fbb2", "name": "Beambox", "location": "Rum 1",
            "model": "fbb2", "enabled": False}, 0, "0" * 32)
        self.command(TEACHER, "laser.station.update", {
            "id": "laser-fbb2", "name": "Beambox", "location": "Rum 1",
            "model": "fbb2", "enabled": True}, 1, "1" * 32)
        self.command(TEACHER, "laser.workflow.assign", {
            "id": "laser-fbb2", "workflow_id": workflow_id, "revision": revision}, 2, "2" * 32)
        job = self.inbox.submit({"elevnavn": "Elev", "klasse": "5.A",
                                 "projekt": "Skilt", "requestId": uuid.uuid4().hex,
                                 "svg": SVG})
        self.command(TEACHER, "laser.job.bind",
                     {"id": job["id"], "station_id": "laser-fbb2"}, 0, "3" * 32)
        return job["id"]

    def advance(self, job, step, expected, actor=TEACHER):
        return self.command(actor, "laser.job.advance",
                            {"id": job, "step_id": step, "confirmed": True},
                            expected, uuid.uuid4().hex)

    def test_teacher_workflow_station_bvg_and_cleanup(self):
        job = self.setup_job()
        queue = self.login()
        opened = []
        station = Station(queue, Path(self.temp.name) / "plates", opener=opened.append,
                           ghost=None, station_id="laser-fbb2")
        placements = station.auto_place({"ids": [job]})["placements"]
        payload = {"material": "wood_3mm", "items": placements, "open": False}
        with self.assertRaises(ValueError):
            station.open_plate(payload)
        self.advance(job, "prepare", 1)
        self.advance(job, "approve", 2)
        self.advance(job, "produce", 3)
        result = station.open_plate(payload)
        self.assertTrue(Path(result["file"]).is_file())
        self.assertEqual(opened, [])
        queue.set_status(job, "done")
        row = self.inbox.workflows.snapshot()["jobs"][0]
        self.assertTrue(row["physical_done"])
        self.assertIsNotNone(self.inbox.svg(job))
        self.assertTrue(Path(result["file"]).is_file())
        self.advance(job, "collect", 5, self.teacher_actor)
        with self.assertRaises(PermissionError):
            self.advance(job, "clear", 6, "agent:" + "b" * 32)
        self.advance(job, "clear", 6)
        self.assertEqual(self.inbox.workflows.snapshot()["jobs"], [])
        self.assertFalse(self.inbox.jobs()[0]["payload_available"])

    def test_teacher_scope_and_retry_after_lost_reply(self):
        job = self.setup_job()
        prepare = {"action": "laser.job.advance",
                   "params": {"id": job, "step_id": "prepare", "confirmed": True},
                   "expected_version": 1, "request_id": "b" * 32}
        with self.assertRaises(PermissionError):
            self.service.execute({"id": "agent:" + "b" * 32, "role": "agent", "scopes": ["jobs:write"]}, prepare)

        read_actor = {"id": TEACHER, "role": "teacher", "scopes": ["makerspace:read"]}
        with self.assertRaises(PermissionError):
            self.service.execute(read_actor, {**prepare, 'action': 'laser.job.bind',
                'params': {'id': job, 'station_id': 'laser-fbb2'}, 'expected_version': 0})

        original = self.service.laser_admin.execute
        calls = []

        def commit_then_lose(actor, envelope):
            calls.append(1)
            result = original(actor, envelope)
            if len(calls) == 1:
                raise OSError("forbindelsen blev afbrudt efter commit")
            return result

        self.service.laser_admin.execute = commit_then_lose
        with self.assertRaises(OSError):
            self.service.execute({"id": TEACHER, "role": "teacher", "scopes": ["machines:write", "workflows:write", "jobs:write"]}, prepare)
        retried = self.service.execute({"id": TEACHER, "role": "teacher", "scopes": ["machines:write", "workflows:write", "jobs:write"]}, prepare)
        self.assertEqual(retried["result"]["version"], 2)
        self.assertEqual(len(calls), 2)
        with self.inbox.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM laser_workflow_audit WHERE request_id=?", (prepare["request_id"],)).fetchone()[0], 1)
        with self.assertRaises(Conflict):
            self.service.execute({"id": TEACHER, "role": "teacher", "scopes": ["jobs:write"]},
                                 dict(prepare, params={"id": job, "step_id": "approve", "confirmed": True}))

        reopened = MakerspaceService(
            str(Path(self.temp.name) / "hub.sqlite3"),
            laser_administration=LaserAdministration(
                {"backend": f"127.0.0.1:{self.teacher.server_port}", "key_file": "makerspace-laser.key"}, base=self.temp.name))
        self.assertEqual(reopened.execute({"id": TEACHER, "role": "teacher", "scopes": ["jobs:write"]}, prepare), retried)

    def test_snapshot_has_zero_pupil_computers_and_public_laser_metadata(self):
        self.setup_job()
        actor = {"id": TEACHER, "role": "teacher", "scopes": ["makerspace:read", "machines:write", "workflows:write", "jobs:write", "audit:read"]}
        snapshot = self.service.snapshot(actor)
        self.assertEqual(snapshot["computers"], [])
        self.assertEqual(snapshot["computers_status"], "unknown")
        self.assertTrue(snapshot["laser_administration"]["stations"])
        station = snapshot["laser_administration"]["stations"][0]
        self.assertEqual(set(station), {"id", "name", "location", "model", "enabled", "version", "workflow"})
        self.assertFalse(any("Elev" in json.dumps(value, ensure_ascii=False) for value in snapshot.values()))

    def test_real_teacher_bridge_rejects_non_local_surface_headers(self):
        key = self.inbox.makerspace_key

        def request(headers):
            c = http.client.HTTPConnection("127.0.0.1", self.teacher.server_port)
            c.request("GET", "/api/makerspace/laser", headers=headers)
            r = c.getresponse(); r.read(); c.close()
            return r.status

        good = {"Authorization": "Bearer " + key,
                "Host": f"127.0.0.1:{self.teacher.server_port}"}
        self.assertEqual(request(good), 200)
        self.assertEqual(request(dict(good, Origin="http://evil.example")), 401)
        self.assertEqual(request(dict(good, Cookie="laser_teacher=x")), 401)
        self.assertEqual(request(dict(good, Cookie="")), 401)
        self.assertEqual(request(dict(good, Authorization="Bearer wrong")), 401)

        c = http.client.HTTPConnection("127.0.0.1", self.teacher.server_port)
        c.request("POST", "/api/laser/submit", json.dumps({"svg": SVG}),
                  {"Content-Type": "application/json"})
        response = c.getresponse(); response.read(); c.close()
        self.assertEqual(response.status, 404)

    def test_model_is_refreshed_from_central_configuration_before_operation(self):
        self.command(TEACHER, "laser.station.create", {
            "id": "laser-fbb2", "name": "Hexa", "location": "Rum 1",
            "model": "fhexa1", "enabled": False}, 0, "4" * 32)
        self.command(TEACHER, "laser.station.update", {
            "id": "laser-fbb2", "name": "Hexa", "location": "Rum 1",
            "model": "fhexa1", "enabled": True}, 1, "5" * 32)
        queue = self.login()
        station = Station(queue, Path(self.temp.name) / "hexa", machine="fbb2",
                          ghost=None, opener=lambda path: None, station_id="laser-fbb2")
        with self.assertRaises(ValueError):
            station.open_plate({"material": "wood_3mm", "items": [], "open": False})
        self.assertEqual(station.machine["id"], "fhexa1")
        self.assertEqual(station.bed, (730, 410))


if __name__ == "__main__":
    unittest.main()
