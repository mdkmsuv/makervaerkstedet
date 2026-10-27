"""End-to-end teacher-library checks against the real queue HTTP surface.

These tests intentionally construct a real Queue and Server in a temporary
directory.  They do not start the application worker, a printer driver, or a
live service, so failures point at the queue/library/HTTP contract itself.
"""

import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.backup_databases import validate_database
from app.core import Queue
from app.printer_config import ConfigConflict
from app.server import Handler, Server
from app.stl import inspect_stl
from app.teacher_auth import TeacherAuth
from scripts.smoke_slice import cube


class _HTTP:
    def __init__(self, server):
        self.server = server

    def request(self, method, path, body=None, cookie=None, authorization=None):
        raw = None
        headers = {"Host": "127.0.0.1:%d" % self.server.server_address[1]}
        if body is not None:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers.update({"Content-Type": "application/json",
                            "Content-Length": str(len(raw))})
        if cookie:
            headers["Cookie"] = cookie
        if authorization:
            headers["Authorization"] = "Bearer " + authorization
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        try:
            connection.request(method, path, raw, headers)
            response = connection.getresponse()
            payload = response.read()
            return response.status, dict(response.getheaders()), payload
        finally:
            connection.close()

    def json(self, method, path, body=None, cookie=None, authorization=None):
        status, headers, payload = self.request(method, path, body, cookie, authorization)
        return status, headers, json.loads(payload.decode("utf-8")) if payload else None


class LibraryRetentionHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.queue = Queue(self.folder, [], demo=True, clock=lambda: 1000.0)
        self.auth = TeacherAuth("teacher-secret")
        self.app = type("TestApp", (), {})()
        self.app.auth = self.auth
        self.app.queue = self.queue
        self.app.folder = self.folder
        self.app.demo = True
        self.server = Server(("127.0.0.1", 0), Handler)
        self.server.app = self.app
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.http = _HTTP(self.server)
        status, headers, _ = self.http.request("POST", "/api/auth/login", {"code": "teacher-secret"})
        self.assertEqual(status, 200)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.queue.close()
        self.temp.cleanup()

    def _submit(self, data, filename, request_id):
        job = self.queue.submit(data, filename, "any", request_id, source="teacher:test")
        self.queue.fail(job["id"], "integration test")
        return job

    def _archive(self, data, filename, request_id):
        return self._submit(data, filename, request_id)["digest"]

    def _generation(self, digest):
        return self.queue.library.get(digest)["generation"]

    def test_teacher_session_student_safe_state_and_library_auth(self):
        status, _, _ = self.http.request("GET", "/api/library")
        self.assertEqual(status, 401)
        status, _, _ = self.http.request("GET", "/api/library/storage")
        self.assertEqual(status, 401)
        job = self.queue.submit(cube(20), "private-name.stl", "any", "student-safe-request-1", source="student:test")
        status, _, state = self.http.json("GET", "/api/state")
        self.assertEqual(status, 200)
        public_job = next(item for item in state["jobs"] if item["id"] == job["id"])
        self.assertNotIn("filename", public_job)
        status, _, auth_status = self.http.json("GET", "/api/auth/status")
        self.assertEqual(status, 200)
        self.assertFalse(auth_status["teacher"])
        status, _, auth_status = self.http.json("GET", "/api/auth/status", cookie=self.cookie)
        self.assertEqual(status, 200)
        self.assertTrue(auth_status["teacher"])
        status, _, page = self.http.json("GET", "/api/library", cookie=self.cookie)
        self.assertEqual(status, 200)
        self.assertIn("files", page)
        self.queue.fail(job["id"], "cleanup")

    def test_pagination_search_alias_and_download_do_not_scan_list(self):
        first = self._submit(cube(20), "alpha.stl", "pagination-request-1")
        self._submit(cube(20), "needle-alias.stl", "pagination-request-2")
        other = self._archive(cube(21), "other.stl", "pagination-request-3")
        status, _, page = self.http.json("GET", "/api/library?page=1&page_size=1", cookie=self.cookie)
        self.assertEqual(status, 200)
        self.assertEqual(page["page_size"], 1)
        self.assertTrue(page["has_more"])
        status, _, searched = self.http.json("GET", "/api/library?page_size=25&query=needle-alias", cookie=self.cookie)
        self.assertEqual(status, 200)
        self.assertEqual(searched["total"], 1)
        self.assertEqual(searched["files"][0]["digest"], first["digest"])
        self.assertEqual(searched["files"][0]["filename"], "alpha.stl")
        self.assertIn("needle-alias.stl", searched["files"][0]["aliases"])
        with patch.object(self.queue.library, "list", side_effect=AssertionError("list scan")):
            status, headers, payload = self.http.request("GET", "/api/library/%s/download" % other, cookie=self.cookie)
        self.assertEqual(status, 200)
        self.assertEqual(payload, cube(21))
        self.assertIn("filename*=UTF-8''other.stl", headers["Content-Disposition"])

    def test_policy_version_conflict_and_old_request_retry_preserves_new_policy(self):
        status, _, current = self.http.json("GET", "/api/library/storage", cookie=self.cookie)
        self.assertEqual(status, 200)
        old = {"request_id": "policy-request-0001", "expected_version": current["policy"]["version"],
               "retention_days": 30, "quota_bytes": 25 * 1024 ** 2}
        status, _, applied = self.http.json("POST", "/api/library/policy", old, cookie=self.cookie)
        self.assertEqual(status, 200)
        self.assertEqual(applied["policy"]["retention_days"], 30)
        newer = {"request_id": "policy-request-0002", "expected_version": applied["policy"]["version"],
                 "retention_days": 31, "quota_bytes": 26 * 1024 ** 2}
        status, _, applied_new = self.http.json("POST", "/api/library/policy", newer, cookie=self.cookie)
        self.assertEqual(status, 200)
        self.assertEqual(applied_new["policy"]["version"], old["expected_version"] + 2)
        status, _, retry = self.http.json("POST", "/api/library/policy", old, cookie=self.cookie)
        self.assertEqual(status, 200)
        self.assertEqual(retry["policy"]["retention_days"], 30)
        status, _, storage = self.http.json("GET", "/api/library/storage", cookie=self.cookie)
        self.assertEqual(status, 200)
        self.assertEqual(storage["policy"]["retention_days"], 31)
        stale = dict(newer, request_id="policy-request-0003")
        status, _, _ = self.http.json("POST", "/api/library/policy", stale, cookie=self.cookie)
        self.assertEqual(status, 409)

    def test_delete_requires_confirmation_generation_and_preserves_blocked_jobs(self):
        data = cube(20)
        job = self.queue.submit(data, "active.stl", "any", "delete-active-request", source="student:test")
        digest = job["digest"]
        generation = self._generation(digest)
        body = {"request_id": "delete-request-0001", "generation": generation, "confirmed": False}
        status, _, _ = self.http.json("POST", "/api/library/%s/delete" % digest, body, cookie=self.cookie)
        self.assertEqual(status, 400)
        stale = {"request_id": "delete-request-0002", "generation": "0" * 32, "confirmed": True}
        status, _, _ = self.http.json("POST", "/api/library/%s/delete" % digest, stale, cookie=self.cookie)
        self.assertEqual(status, 409)
        body["confirmed"] = True
        status, _, _ = self.http.json("POST", "/api/library/%s/delete" % digest, body, cookie=self.cookie)
        self.assertEqual(status, 409)
        self.assertIsNotNone(self.queue.library.get(digest))
        self.queue.fail(job["id"], "terminal but unresolved workflow")
        self.queue.db.execute("UPDATE jobs SET workflow=?, workflow_complete=0 WHERE id=?", ('{"id":"workflow"}', job["id"]))
        self.queue.db.commit()
        status, _, _ = self.http.json("POST", "/api/library/%s/delete" % digest, body, cookie=self.cookie)
        self.assertEqual(status, 409)
        self.queue.db.execute("UPDATE jobs SET workflow=NULL, workflow_complete=1, isolated=1 WHERE id=?", (job["id"],))
        self.queue.db.commit()
        status, _, _ = self.http.json("POST", "/api/library/%s/delete" % digest, body, cookie=self.cookie)
        self.assertEqual(status, 409)
        self.queue.db.execute("UPDATE jobs SET isolated=0 WHERE id=?", (job["id"],))
        self.queue.db.commit()
        status, _, deleted = self.http.json("POST", "/api/library/%s/delete" % digest, body, cookie=self.cookie)
        self.assertEqual(status, 200)
        self.assertTrue(deleted["deleted"])
        self.assertTrue(self.queue.get_job(job["id"])["filename"].startswith("slettet-model-"))
        status, _, retry = self.http.json("POST", "/api/library/%s/delete" % digest, body, cookie=self.cookie)
        self.assertEqual(status, 200)
        self.assertEqual(retry, deleted)
        with self.assertRaises(ValueError):
            self.queue.library.get(digest)

    def test_old_delete_receipt_cannot_delete_rearchived_generation(self):
        data = cube(22)
        digest = self._archive(data, "old-name.stl", "rearchive-request-1")
        old_generation = self._generation(digest)
        delete_body = {"request_id": "delete-receipt-0001", "generation": old_generation, "confirmed": True}
        status, _, _ = self.http.json("POST", "/api/library/%s/delete" % digest, delete_body, cookie=self.cookie)
        self.assertEqual(status, 200)
        self.queue.library.archive(data, filename="new-name.stl", colour=None, quality="standard",
                                   meta=inspect_stl(data), created=1001)
        new_generation = self._generation(digest)
        self.assertNotEqual(old_generation, new_generation)
        status, _, receipt = self.http.json("POST", "/api/library/%s/delete" % digest, delete_body, cookie=self.cookie)
        self.assertEqual(status, 200)
        self.assertEqual(receipt["request_id"], delete_body["request_id"])
        self.assertEqual(self._generation(digest), new_generation)
        self.assertEqual(self.queue.library.download(digest), data)

    def test_restart_recovery_redacts_history_and_backup_accepts_optional_tables(self):
        data = cube(23)
        job = self.queue.submit(data, "recover-name.stl", "any", "recovery-request-1", source="student:test")
        digest = job["digest"]
        self.queue.fail(job["id"], "terminal")
        generation = self._generation(digest)
        with patch.object(self.queue.library, "_unlink_object", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.queue.library.delete(digest, expected_generation=generation)
        self.assertIsNotNone(self.queue.db.execute("SELECT 1 FROM library_deletion_journal WHERE digest=?", (digest,)).fetchone())
        self.queue.close()
        reopened = Queue(self.folder, [], demo=True, clock=lambda: 1000.0)
        try:
            with self.assertRaises(ValueError):
                reopened.library.get(digest)
            self.assertTrue(reopened.get_job(job["id"])["filename"].startswith("slettet-model-"))
            self.assertIsNone(reopened.db.execute("SELECT 1 FROM library_deletion_journal WHERE digest=?", (digest,)).fetchone())
            result = validate_database(self.folder / "queue.sqlite3", "print")
            self.assertEqual(result["role"], "print")
            for table in ("library_deletion_journal", "library_delete_receipts", "library_policy_receipts", "library_settings"):
                self.assertIn(table, result["tables"])
        finally:
            reopened.close()

    def test_reprint_delete_race_honours_library_generation_at_commit(self):
        data = cube(24)
        digest = self._archive(data, "race.stl", "race-archive-request")
        generation = self._generation(digest)
        entered = threading.Event()
        release = threading.Event()
        original = __import__("app.core", fromlist=["inspect_stl"]).inspect_stl

        def blocked(payload):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(payload)

        outcome = []
        errors = []
        with patch("app.core.inspect_stl", side_effect=blocked):
            def reprint():
                try:
                    outcome.append(self.queue.library_reprint(digest, "race-reprint-request-1"))
                except BaseException as error:
                    errors.append(error)

            worker = threading.Thread(target=reprint, daemon=True)
            worker.start()
            self.assertTrue(entered.wait(3))
            deleted = self.queue.library_delete(digest, "race-delete-request-1", generation, True)
            self.assertTrue(deleted["deleted"])
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome, [])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ConfigConflict)
        self.assertFalse(self.queue.db.execute("SELECT 1 FROM jobs WHERE request_id=?", ("race-reprint-request-1",)).fetchone())
        with self.assertRaises(ValueError):
            self.queue.library.get(digest)


if __name__ == "__main__":
    unittest.main()
