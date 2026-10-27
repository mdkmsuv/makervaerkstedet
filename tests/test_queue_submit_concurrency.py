import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import Queue
from scripts.smoke_slice import cube


META = {
    "size": [10.0, 10.0, 10.0],
    "height": 10.0,
    "triangles": 12,
}


class QueueSubmitConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.queue = Queue(Path(self.temp.name), [], demo=True)
        self.data = cube(5)

    def tearDown(self):
        self.queue.close()
        self.temp.cleanup()

    def test_inspection_does_not_block_snapshot(self):
        started = threading.Event()
        release = threading.Event()
        result = []
        errors = []

        def inspect(_data):
            started.set()
            release.wait(3)
            return dict(META)

        def submit():
            try:
                result.append(self.queue.submit(self.data, "model.stl", "any", "concurrent-snapshot-0001"))
            except BaseException as exc:
                errors.append(exc)

        with patch("app.core.inspect_stl", side_effect=inspect):
            worker = threading.Thread(target=submit)
            worker.start()
            reader_errors = []
            snapshot_done = threading.Event()

            def snapshot():
                try:
                    self.queue.snapshot()
                    snapshot_done.set()
                except BaseException as exc:
                    reader_errors.append(exc)

            reader = threading.Thread(target=snapshot)
            try:
                self.assertTrue(started.wait(2))
                reader.start()
                self.assertTrue(snapshot_done.wait(1), "snapshot waited for STL inspection")
            finally:
                release.set()
                worker.join(3)
                reader.join(3)
        self.assertFalse(errors)
        self.assertFalse(reader_errors)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "queued")

    def test_same_request_is_inspected_concurrently_but_committed_once(self):
        entered = threading.Event()
        release = threading.Event()
        entered_count = [0]
        entered_lock = threading.Lock()
        results = []
        errors = []

        def inspect(_data):
            with entered_lock:
                entered_count[0] += 1
                if entered_count[0] == 2:
                    entered.set()
            release.wait(3)
            return dict(META)

        def submit():
            try:
                results.append(self.queue.submit(
                    self.data, "model.stl", "any", "same-request-0000001"))
            except BaseException as exc:  # surface thread failures in the test
                errors.append(exc)

        with patch("app.core.inspect_stl", side_effect=inspect) as parser:
            threads = [threading.Thread(target=submit) for _ in range(2)]
            try:
                for thread in threads:
                    thread.start()
                self.assertTrue(entered.wait(2))
            finally:
                release.set()
                for thread in threads:
                    thread.join(5)
        self.assertFalse(errors)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["id"], results[1]["id"])
        self.assertEqual(parser.call_count, 2)
        self.assertEqual(self.queue.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 1)
        self.assertEqual(len(list((Path(self.temp.name) / "uploads").glob("*.stl"))), 1)

    def test_capacity_is_checked_after_inspection(self):
        now = time.time()
        rows = [
            (f"job-{index:03d}", f"filled-request-{index:03d}", f"digest-{index:03d}",
             f"old-{index}.stl", None, json.dumps(META), now, now, "queued", "standard", "local")
            for index in range(99)
        ]
        self.queue.db.executemany(
            "INSERT INTO jobs (id,request_id,digest,filename,colour,meta,created,updated,status,quality,source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.queue.db.commit()
        started = threading.Event()
        release = threading.Event()
        submit_result = []
        submit_errors = []
        filler = None

        def inspect(_data):
            started.set()
            release.wait(3)
            return dict(META)

        def submit():
            try:
                submit_result.append(self.queue.submit(
                    self.data, "new.stl", "any", "capacity-request-0001"))
            except BaseException as exc:
                submit_errors.append(exc)

        worker = threading.Thread(target=submit)
        with patch("app.core.inspect_stl", side_effect=inspect) as parser:
            try:
                worker.start()
                self.assertTrue(started.wait(2))
                inserted = threading.Event()

                def fill_last_slot():
                    with self.queue.lock:
                        self.queue.db.execute(
                            "INSERT INTO jobs (id,request_id,digest,filename,colour,meta,created,updated,status,quality,source) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            ("job-099", "filled-request-099", "digest-099", "old-99.stl", None,
                             json.dumps(META), now, now, "queued", "standard", "local"))
                        self.queue.db.commit()
                        inserted.set()

                filler = threading.Thread(target=fill_last_slot)
                filler.start()
                self.assertTrue(inserted.wait(2))
            finally:
                release.set()
                worker.join(3)
                if filler is not None:
                    filler.join(3)
        self.assertFalse(worker.is_alive())
        if filler is not None:
            self.assertFalse(filler.is_alive())
        self.assertFalse(submit_result)
        self.assertEqual(len(submit_errors), 1)
        self.assertIsInstance(submit_errors[0], ValueError)
        self.assertIn("Køen er fuld", str(submit_errors[0]))
        self.assertEqual(parser.call_count, 1)
        self.assertEqual(self.queue.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 100)
        self.assertEqual(list((Path(self.temp.name) / "uploads").glob("*.stl")), [])

    def test_same_request_different_payload_is_conflict_after_inspection(self):
        entered = threading.Event()
        release = threading.Event()
        entered_count = [0]
        entered_lock = threading.Lock()
        results = []
        errors = []

        def inspect(_data):
            with entered_lock:
                entered_count[0] += 1
                if entered_count[0] == 2:
                    entered.set()
            release.wait(3)
            return dict(META)

        def submit(data, filename):
            try:
                results.append(self.queue.submit(data, filename, "any", "conflict-request-0001"))
            except BaseException as exc:
                errors.append(exc)

        with patch("app.core.inspect_stl", side_effect=inspect):
            threads = [threading.Thread(target=submit, args=(self.data, "first.stl")),
                       threading.Thread(target=submit, args=(cube(6), "second.stl"))]
            try:
                for thread in threads:
                    thread.start()
                self.assertTrue(entered.wait(2))
            finally:
                release.set()
                for thread in threads:
                    thread.join(5)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ValueError)
        self.assertIn("allerede brugt", str(errors[0]))
        self.assertEqual(self.queue.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 1)
        self.assertEqual(len(list((Path(self.temp.name) / "uploads").glob("*.stl"))), 1)


if __name__ == "__main__":
    unittest.main()
