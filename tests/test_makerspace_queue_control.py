"""Integration tests for the versioned makerspace print-queue control."""

import http.client
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

ELEVHUB = Path(__file__).parents[1] / "linux-elevhub" / "server" / "elevhub"
if str(ELEVHUB) not in sys.path:
    sys.path.insert(0, str(ELEVHUB))
from makerspace_api import MakerspaceService  # noqa: E402
from makerspace_printers import PrinterAdministration  # noqa: E402
from makerspace_store import Conflict  # noqa: E402


TEACHER = "teacher:" + "9" * 32
READ_SCOPES = ["makerspace:read"]
WRITE_SCOPES = ["makerspace:read", "queues:write"]


class QueueControlIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = types.SimpleNamespace(
            folder=self.root,
            settings_path=self.root / "settings.json",
            settings={"printers": []},
            settings_lock=threading.RLock(),
            queue=Queue(self.root, []),
            slicer=types.SimpleNamespace(profiles=types.SimpleNamespace()),
            drivers={}, demo=False,
        )
        self.bridge = PrinterBridge(self.app, lambda *args: None)
        self.app.printer_bridge = self.bridge
        self.server = Server(("127.0.0.1", 0), Handler)
        self.server.app = self.app
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        port = self.server.server_address[1]
        admin = PrinterAdministration(
            {"backend": f"127.0.0.1:{port}", "key_file": "makerspace-bridge.key"},
            base=self.root,
        )
        self.service = MakerspaceService(
            self.root / "makerspace.sqlite3",
            overview=lambda: {"printers": {"status": "ok", "items": []},
                              "computers": [], "computers_status": "ok"},
            printer_administration=admin,
        )
        self.writer = {"id": TEACHER, "name": "Lærer", "role": "teacher", "scopes": WRITE_SCOPES}
        self.reader = {"id": TEACHER, "name": "Lærer", "role": "teacher", "scopes": READ_SCOPES}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.app.queue.close()
        self.temp.cleanup()

    @staticmethod
    def envelope(paused, expected, request_id):
        return {"action": "printer.queue.pause",
                "params": {"id": "print", "paused": paused},
                "expected_version": expected, "request_id": request_id}

    def command(self, actor, paused, expected, request_id):
        return self.service.store.execute(
            actor, self.envelope(paused, expected, request_id), external=self.service.printers)

    def http_command(self, actor_id, paused, expected, request_id):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            body = json.dumps({"actor": actor_id,
                               "envelope": self.envelope(paused, expected, request_id)}).encode()
            connection.request("POST", "/api/makerspace/printers/commands", body,
                               {"Authorization": "Bearer " + self.bridge.token,
                                "Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_initial_snapshot_and_local_http_command_are_versioned(self):
        snapshot = self.bridge.snapshot()
        self.assertEqual(snapshot["queue"], {"id": "print", "paused": False, "version": 1})
        self.assertEqual(self.service.snapshot(self.reader)["computers"], [])
        status, response = self.http_command(self.writer["id"], True, 1, "3" * 32)
        self.assertEqual(status, 200)
        self.assertEqual(response["result"], {"id": "print", "paused": True, "version": 2})
        self.assertEqual(self.bridge.snapshot()["queue"]["version"], 2)
        self.assertEqual(self.bridge.snapshot()["queue"]["paused"], True)

    def test_makerspace_scope_boundary_and_revocation(self):
        with self.assertRaises(PermissionError):
            self.command(self.reader, True, 1, "4" * 32)
        applied = self.command(self.writer, True, 1, "5" * 32)
        self.assertEqual(applied["result"]["version"], 2)
        with self.assertRaises(PermissionError):
            self.command({"id": "agent:" + "b" * 32, "role": "agent", "scopes": WRITE_SCOPES}, False, 2, "8" * 32)

    def test_stale_version_and_legacy_pause_increment_are_safe(self):
        self.app.queue.pause(True)
        self.assertEqual(self.bridge.snapshot()["queue"],
                         {"id": "print", "paused": True, "version": 2})
        with self.assertRaises(Conflict):
            self.command(self.writer, False, 1, "9" * 32)
        self.assertEqual(self.bridge.snapshot()["queue"]["version"], 2)

    def test_lost_backend_response_replays_without_second_version_or_flag(self):
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
        request_id = "a" * 32
        with self.assertRaises(OSError):
            self.command(self.writer, True, 1, request_id)
        self.assertEqual(self.bridge.snapshot()["queue"],
                         {"id": "print", "paused": True, "version": 2})
        retried = self.command(self.writer, True, 1, request_id)
        self.assertEqual(retried["result"], {"id": "print", "paused": True, "version": 2})
        self.assertEqual(self.bridge.snapshot()["queue"]["version"], 2)
        audits = self.app.queue.db.execute(
            "SELECT count(*) FROM workflow_audit WHERE request_id=?", (request_id,)).fetchone()[0]
        self.assertEqual(audits, 1)

    def test_replay_of_old_pause_after_resume_does_not_reapply_it(self):
        first = self.command(self.writer, True, 1, "b" * 32)
        self.assertEqual(first["result"]["version"], 2)
        resumed = self.command(self.writer, False, 2, "c" * 32)
        self.assertEqual(resumed["result"]["version"], 3)
        replay = self.command(self.writer, True, 1, "b" * 32)
        self.assertEqual(replay, first)
        self.assertEqual(self.bridge.snapshot()["queue"],
                         {"id": "print", "paused": False, "version": 3})


if __name__ == "__main__":
    unittest.main()
