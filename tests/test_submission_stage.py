import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.file_library import FileLibrary
from app import submission_stage
from app.submission_stage import (
    SubmissionStageError,
    finish_upload,
    recover_uploads,
    write_upload,
)


class SubmissionStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.db = sqlite3.connect(self.folder / "queue.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE jobs (
            id TEXT PRIMARY KEY, digest TEXT NOT NULL, filename TEXT NOT NULL,
            status TEXT NOT NULL, workflow TEXT, workflow_complete INTEGER,
            created REAL NOT NULL, quality TEXT, colour TEXT
        )""")
        self.library = FileLibrary(self.db, self.folder)
        self.payload = b"solid staged\nendsolid staged\n"
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.job_id = "0123456789abcdef0123456789abcdef"

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def archive(self):
        return self.library.archive(
            self.payload, filename="model.stl", colour=None, quality="standard",
            meta={"size": [1, 1, 1]}, created=1,
        )

    def insert_job(self, status="queued", digest=None):
        self.db.execute(
            "INSERT INTO jobs(id,digest,filename,status,workflow,workflow_complete,created,quality,colour) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (self.job_id, digest or self.digest, "model.stl", status, None, 0, 1, "standard", None),
        )
        self.db.commit()

    def marker_path(self):
        return self.folder / "submission-stages" / f"{self.job_id}.json"

    def upload_path(self):
        return self.folder / "uploads" / f"{self.job_id}.stl"

    def test_marker_precedes_payload_and_finish_only_removes_marker(self):
        write_upload(self.folder, self.job_id, self.digest, self.payload)
        marker = self.marker_path()
        self.assertTrue(marker.is_file())
        self.assertEqual(self.upload_path().read_bytes(), self.payload)
        self.insert_job()
        self.assertTrue(finish_upload(self.folder, self.job_id, self.digest))
        self.assertFalse(marker.exists())
        self.assertEqual(self.upload_path().read_bytes(), self.payload)

    def test_orphaned_submission_removes_only_matching_transient_and_keeps_library(self):
        self.archive()
        write_upload(self.folder, self.job_id, self.digest, self.payload)
        summary = recover_uploads(self.folder, self.db, self.library)
        self.assertIn(self.job_id, summary["cleaned"])
        self.assertFalse(self.marker_path().exists())
        self.assertFalse(self.upload_path().exists())
        self.assertEqual(self.library.download(self.digest), self.payload)

    def test_nonterminal_job_repairs_missing_upload_from_library(self):
        self.archive()
        write_upload(self.folder, self.job_id, self.digest, self.payload)
        self.upload_path().unlink()
        self.insert_job()
        summary = recover_uploads(self.folder, self.db, self.library)
        self.assertIn(self.job_id, summary["repaired"])
        self.assertEqual(self.upload_path().read_bytes(), self.payload)
        self.assertFalse(self.marker_path().exists())

    def test_priority_job_is_repaired_before_marker_flood(self):
        self.archive()
        write_upload(self.folder, self.job_id, self.digest, self.payload)
        self.upload_path().unlink()
        self.insert_job()
        stages = self.folder / "submission-stages"
        for index in range(submission_stage.MAX_RECOVERY_MARKERS + 1):
            marker_id = f"{index + 1:032x}"
            (stages / f"{marker_id}.json").write_text("{}", encoding="utf-8")

        summary = recover_uploads(self.folder, self.db, self.library)

        self.assertIn(self.job_id, summary["repaired"])
        self.assertEqual(self.upload_path().read_bytes(), self.payload)
        self.assertFalse(self.marker_path().exists())
        self.assertTrue(summary["deferred"])
        self.assertEqual(len(summary["preserved"]), submission_stage.MAX_RECOVERY_MARKERS)

    def test_terminal_job_does_not_require_transient_repair(self):
        self.archive()
        write_upload(self.folder, self.job_id, self.digest, self.payload)
        self.upload_path().unlink()
        self.insert_job(status="done")
        summary = recover_uploads(self.folder, self.db, self.library)
        self.assertIn(self.job_id, summary["cleaned"])
        self.assertFalse(self.marker_path().exists())
        self.assertFalse(self.upload_path().exists())

    def test_changed_job_digest_preserves_marker_and_upload(self):
        self.archive()
        write_upload(self.folder, self.job_id, self.digest, self.payload)
        other = hashlib.sha256(b"other").hexdigest()
        self.insert_job(digest=other)
        summary = recover_uploads(self.folder, self.db, self.library)
        self.assertIn(self.marker_path().name, summary["preserved"])
        self.assertTrue(self.marker_path().exists())
        self.assertEqual(self.upload_path().read_bytes(), self.payload)

    def test_wrong_existing_upload_is_never_overwritten_or_removed(self):
        self.archive()
        write_upload(self.folder, self.job_id, self.digest, self.payload)
        self.upload_path().write_bytes(b"operator file")
        self.insert_job()
        summary = recover_uploads(self.folder, self.db, self.library)
        self.assertIn(self.marker_path().name, summary["preserved"])
        self.assertEqual(self.upload_path().read_bytes(), b"operator file")

    def test_invalid_marker_and_unrelated_upload_are_preserved(self):
        stages = self.folder / "submission-stages"
        uploads = self.folder / "uploads"
        stages.mkdir()
        uploads.mkdir()
        (stages / f"{self.job_id}.json").write_text('{"schemaVersion":1,"job_id":"bad"}', encoding="utf-8")
        unrelated = uploads / "unrelated.stl"
        unrelated.write_bytes(b"keep")
        summary = recover_uploads(self.folder, self.db, self.library)
        self.assertIn(f"{self.job_id}.json", summary["preserved"])
        self.assertTrue((stages / f"{self.job_id}.json").exists())
        self.assertEqual(unrelated.read_bytes(), b"keep")

    def test_ids_and_digest_are_strict(self):
        with self.assertRaises(SubmissionStageError):
            write_upload(self.folder, "../escape", self.digest, self.payload)
        with self.assertRaises(SubmissionStageError):
            write_upload(self.folder, self.job_id, self.digest.upper(), self.payload)
        with self.assertRaises(SubmissionStageError):
            write_upload(self.folder, self.job_id, self.digest, "not bytes")

    def test_marker_duplicate_keys_are_rejected_without_deletion(self):
        self.archive()
        write_upload(self.folder, self.job_id, self.digest, self.payload)
        marker = self.marker_path()
        raw = marker.read_text(encoding="utf-8")
        marker.write_text(raw[:-1] + ',"digest":"' + self.digest + '"}', encoding="utf-8")
        summary = recover_uploads(self.folder, self.db, self.library)
        self.assertIn(marker.name, summary["preserved"])
        self.assertTrue(marker.exists())
        self.assertTrue(self.upload_path().exists())

    def test_payload_match_rejects_oversize_before_hashing(self):
        path = self.folder / "uploads" / "oversize.stl"
        path.parent.mkdir()
        with path.open("wb") as stream:
            stream.truncate(submission_stage.MAX_STAGED_BYTES + 1)
        with patch.object(submission_stage, "_sha256", side_effect=AssertionError("must not hash")):
            self.assertFalse(submission_stage._payload_matches(
                path, self.digest, submission_stage.MAX_STAGED_BYTES + 1))

    def test_owned_upload_replacement_after_hash_is_preserved(self):
        self.archive()
        write_upload(self.folder, self.job_id, self.digest, self.payload)
        upload = self.upload_path()
        replacement = upload.with_name("replacement.stl")
        replacement.write_bytes(b"operator replacement")
        original_match = submission_stage._payload_matches

        def replace_after_hash(path, digest, size):
            matched = original_match(path, digest, size)
            replacement.replace(path)
            return matched

        with patch.object(submission_stage, "_payload_matches", side_effect=replace_after_hash):
            self.assertFalse(submission_stage._remove_owned(upload, self.digest, len(self.payload)))
        self.assertEqual(upload.read_bytes(), b"operator replacement")

    def test_marker_replacement_before_unlink_is_preserved(self):
        write_upload(self.folder, self.job_id, self.digest, self.payload)
        marker = self.marker_path()
        original_identity = submission_stage._regular_identity(marker, missing_ok=False)
        replacement = marker.with_name("replacement.json")
        replacement.write_text('{"operator":true}', encoding="utf-8")
        replacement.replace(marker)
        with self.assertRaises(SubmissionStageError):
            submission_stage._remove_marker_if_unchanged(marker, original_identity)
        self.assertEqual(json.loads(marker.read_text(encoding="utf-8")), {"operator": True})

    def test_recovery_defers_after_bounded_marker_batch(self):
        stages = self.folder / "submission-stages"
        stages.mkdir()
        for index in range(submission_stage.MAX_RECOVERY_MARKERS + 1):
            job_id = f"{index:032x}"
            (stages / f"{job_id}.json").write_text("{}", encoding="utf-8")
        summary = recover_uploads(self.folder, self.db, self.library)
        self.assertTrue(summary["deferred"])
        self.assertEqual(len(summary["preserved"]), submission_stage.MAX_RECOVERY_MARKERS)
        self.assertEqual(len(list(stages.glob("*.json"))), submission_stage.MAX_RECOVERY_MARKERS + 1)


if __name__ == "__main__":
    unittest.main()
