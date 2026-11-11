"""Hard-process-abort coverage for Queue.submit's staged upload protocol."""

import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest

from app.core import Queue
from scripts.smoke_slice import cube


CHILD = textwrap.dedent(
    r'''
    import os
    from pathlib import Path
    from unittest.mock import patch

    from app import core
    from app.core import Queue

    phase, folder, payload_path = os.sys.argv[1:]
    folder = Path(folder)
    payload = Path(payload_path).read_bytes()
    digest = core.hashlib.sha256(payload).hexdigest()

    if phase == "before-job-commit":
        original = core.write_upload

        def staged_then_abort(*args, **kwargs):
            result = original(*args, **kwargs)
            os._exit(71)
            return result

        with patch.object(core, "write_upload", side_effect=staged_then_abort):
            Queue(folder, [], demo=True).submit(
                payload, "hard-abort-before.stl", "any",
                "hard-abort-before-request", source="child:test")
    elif phase == "after-job-commit":
        def committed_then_abort(*args, **kwargs):
            os._exit(72)

        with patch.object(core, "finish_upload", side_effect=committed_then_abort):
            Queue(folder, [], demo=True).submit(
                payload, "hard-abort-after.stl", "any",
                "hard-abort-after-request", source="child:test")
    else:
        raise SystemExit("unknown phase")
    raise SystemExit("the patched operation did not abort")
    '''
)


class SubmissionStageProcessRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.payload = cube(20)
        self.payload_file = self.folder / "payload.bin"
        self.payload_file.write_bytes(self.payload)
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.project = Path(__file__).resolve().parents[1]

    def tearDown(self):
        self.temp.cleanup()

    def _hard_abort(self, phase):
        result = subprocess.run(
            [sys.executable, "-c", CHILD, phase, str(self.folder), str(self.payload_file)],
            cwd=self.project, capture_output=True, text=True, timeout=20,
        )
        expected = 71 if phase == "before-job-commit" else 72
        self.assertEqual(result.returncode, expected,
                         "child stderr=%s stdout=%s" % (result.stderr, result.stdout))

    def _queue(self):
        return Queue(self.folder, [], demo=True, clock=lambda: 1000.0)

    def _job_count(self):
        db = sqlite3.connect(self.folder / "queue.sqlite3")
        try:
            return db.execute("SELECT count(*) FROM jobs").fetchone()[0]
        finally:
            db.close()

    def test_abort_after_staging_before_job_commit_is_cleaned_and_idempotent(self):
        self._hard_abort("before-job-commit")
        self.assertEqual(len(list((self.folder / "submission-stages").glob("*.json"))), 1)
        self.assertEqual(len(list((self.folder / "uploads").glob("*.stl"))), 1)
        queue = self._queue()
        try:
            self.assertEqual(self._job_count(), 0)
            self.assertEqual(queue.library.download(self.digest), self.payload)
            self.assertEqual(list((self.folder / "submission-stages").iterdir()), [])
            self.assertEqual(list((self.folder / "uploads").glob("*.stl")), [])
        finally:
            queue.close()

        # A second startup must not invent a job or alter the durable model.
        queue = self._queue()
        try:
            self.assertEqual(self._job_count(), 0)
            self.assertEqual(queue.library.download(self.digest), self.payload)
            self.assertEqual(list((self.folder / "submission-stages").iterdir()), [])
        finally:
            queue.close()

    def test_abort_after_job_commit_repairs_removed_upload_on_restart(self):
        self._hard_abort("after-job-commit")
        self.assertEqual(len(list((self.folder / "submission-stages").glob("*.json"))), 1)
        db = sqlite3.connect(self.folder / "queue.sqlite3")
        try:
            job_id, digest = db.execute("SELECT id,digest FROM jobs").fetchone()
        finally:
            db.close()
        self.assertEqual(digest, self.digest)
        upload = self.folder / "uploads" / (job_id + ".stl")
        self.assertTrue(upload.is_file())
        upload.unlink()

        queue = self._queue()
        try:
            self.assertEqual(self._job_count(), 1)
            self.assertEqual(queue.library.download(self.digest), self.payload)
            self.assertEqual(upload.read_bytes(), self.payload)
            self.assertEqual(list((self.folder / "submission-stages").iterdir()), [])
        finally:
            queue.close()

        # Recovery is idempotent once the marker has been removed.
        queue = self._queue()
        try:
            self.assertEqual(self._job_count(), 1)
            self.assertEqual(upload.read_bytes(), self.payload)
            self.assertEqual(queue.library.download(self.digest), self.payload)
        finally:
            queue.close()


if __name__ == "__main__":
    unittest.main()
