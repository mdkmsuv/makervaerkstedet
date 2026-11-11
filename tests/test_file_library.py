import tempfile
import unittest
from pathlib import Path

from app.core import Queue
from scripts.smoke_slice import cube


class FileLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = cube(20)

    def tearDown(self):
        self.temp.cleanup()

    def queue(self):
        return Queue(self.temp.name, [], demo=True, clock=lambda: 1000.0)

    def test_terminal_cleanup_and_restart_preserve_exact_bytes(self):
        queue = self.queue()
        job = queue.submit(self.data, "klasse/model.stl", "any", "library-test-request-1")
        digest = job["digest"]
        queue.fail(job["id"], "test")
        self.assertFalse((Path(self.temp.name) / "uploads" / (job["id"] + ".stl")).exists())
        self.assertEqual(queue.library_download(digest), self.data)
        queue.close()

        queue = self.queue()
        self.assertEqual(queue.library_download(digest), self.data)
        models = queue.library_list()
        self.assertEqual(models[0]["filename"], "model.stl")
        self.assertEqual(models[0]["digest"], digest)
        queue.close()

    def test_reprint_request_is_idempotent_and_does_not_replay_original(self):
        queue = self.queue()
        original = queue.submit(self.data, "model.stl", "any", "library-test-request-2")
        replay = queue.library_reprint(original["digest"], "library-reprint-request-1")
        same = queue.library_reprint(original["digest"], "library-reprint-request-1")
        self.assertEqual(replay["id"], same["id"])
        self.assertNotEqual(original["id"], replay["id"])
        self.assertEqual(queue.get_job(original["id"])["status"], "queued")
        self.assertEqual(queue.get_job(replay["id"])["status"], "queued")
        queue.close()

    def test_duplicate_payloads_have_one_library_object(self):
        queue = self.queue()
        first = queue.submit(self.data, "first.stl", "any", "library-test-request-3")
        queue.submit(self.data, "second.stl", "any", "library-test-request-4")
        self.assertEqual(len(queue.library_list()), 1)
        self.assertEqual(queue.library_download(first["digest"]), self.data)
        queue.close()

    def test_startup_migrates_surviving_legacy_upload_before_cleanup(self):
        queue = self.queue()
        job = queue.submit(self.data, "legacy.stl", "any", "library-test-request-5")
        digest = job["digest"]
        queue.db.execute("UPDATE jobs SET status='done', colour='#3678DC' WHERE id=?", (job["id"],))
        queue.db.execute("DELETE FROM library_models WHERE digest=?", (digest,))
        (Path(self.temp.name) / "library" / (digest + ".stl")).unlink()
        queue.db.commit()
        queue.close()

        queue = self.queue()
        self.assertEqual(queue.library_download(digest), self.data)
        self.assertFalse((Path(self.temp.name) / "uploads" / (job["id"] + ".stl")).exists())
        self.assertEqual(queue.library_list()[0]["colour"], "#3678DC")
        queue.close()

    def test_aliases_and_corrupt_archive_are_reported(self):
        queue = self.queue()
        first = queue.submit(self.data, "one.stl", "any", "library-test-request-6")
        queue.submit(self.data, "two.stl", "any", "library-test-request-7")
        item = queue.library_list()[0]
        self.assertEqual(item["aliases"], ["two.stl"])
        self.assertEqual(item["jobs"][0]["filename"], "two.stl")
        path = Path(self.temp.name) / "library" / (first["digest"] + ".stl")
        path.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            queue.library_download(first["digest"])
        with self.assertRaises(ValueError):
            queue.library_download("../outside")
        queue.close()


if __name__ == "__main__":
    unittest.main()
